"""AI usage metering: one row per model call, prices per model (USD per 1M tokens), summaries and projections.

Prices are entered by the user and never shipped as defaults: provider prices change and differ by region,
tier and contract. Local Ollama calls cost nothing in API fees unless a price is set for them.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from . import store as s

BATCH = 8  # entities per AI batch; mirrors pipeline.proposals


def init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS ai_usage(id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT,provider TEXT,model TEXT,source TEXT,job TEXT,operation TEXT,
        input_tokens INTEGER,output_tokens INTEGER,cached_tokens INTEGER,duration_ms INTEGER,status TEXT,error TEXT);
    CREATE INDEX IF NOT EXISTS ai_usage_time ON ai_usage(time);
    CREATE INDEX IF NOT EXISTS ai_usage_job ON ai_usage(job);
    ''')
    columns = {r[1] for r in c.execute('PRAGMA table_info(ai_usage)')}
    for column, kind in (('cache_write_tokens', 'INTEGER'), ('tier', 'TEXT')):
        if column not in columns: c.execute(f'ALTER TABLE ai_usage ADD COLUMN {column} {kind}')


def record(provider, model, context, tokens, duration_ms, status, error=None):
    context = context or {}; tokens = tokens or {}
    num = lambda v: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    with s.connect() as c:
        init(c)
        c.execute('INSERT INTO ai_usage(time,provider,model,source,job,operation,input_tokens,output_tokens,cached_tokens,cache_write_tokens,tier,duration_ms,status,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (s.now(), provider, model, context.get('source', ''), context.get('job', ''), context.get('operation', 'proposals'),
                   num(tokens.get('input')), num(tokens.get('output')), num(tokens.get('cached')), num(tokens.get('cache_write')), context.get('tier', ''), duration_ms, status, error))


def model_key(provider, model): return f'{provider}:{model}'


def prices():
    return (s.get('setting', 'ai_prices') or {}).get('prices', {})


def job_cost(job_id):
    """Spend so far for one job (None if a model it used has no price)."""
    table = prices()
    with s.connect() as c:
        init(c)
        rows = c.execute('SELECT provider,model,input_tokens,output_tokens,cached_tokens,cache_write_tokens FROM ai_usage WHERE job=?', (job_id,)).fetchall()
    total = 0.0
    for p, m, i, o, r, w in rows:
        value = cost(price_for(p, m, table), i, o, r, w)
        if value is None: return None
        total += value
    return total


def set_price(provider, model, input_price, output_price):
    for v in (input_price, output_price):
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or v > 10000): raise ValueError('Prices must be between 0 and 10,000 USD per 1M tokens')
    table = prices(); key = model_key(provider, model)
    if input_price is None and output_price is None: table.pop(key, None)
    else: table[key] = {'input': input_price or 0, 'output': output_price or 0}
    s.put('setting', {'id': 'ai_prices', 'prices': table})
    return table


def price_for(provider, model, table):
    p = table.get(model_key(provider, model))
    if p: return {**p, 'source': 'user'}
    if provider == 'ollama': return {'input': 0, 'output': 0, 'source': 'local'}
    return None


# Cached prompt reads and writes, relative to the input price, unless the price table says otherwise.
# Typical for Anthropic models (Bedrock): reads 10%, writes 125%; OpenAI bills no write surcharge.
CACHE_READ, CACHE_WRITE = 0.1, 1.25

def cost(price, input_tokens, output_tokens, cached=0, cache_write=0):
    if price is None: return None
    read = price.get('cached', price['input'] * CACHE_READ); write = price['input'] * CACHE_WRITE
    return ((input_tokens or 0) * price['input'] + (output_tokens or 0) * price['output'] + (cached or 0) * read + (cache_write or 0) * write) / 1_000_000


def summary(days=30, source_id=None):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() if days else ''
    where, args = 'WHERE time>=?', [since]
    if source_id: where += ' AND source=?'; args.append(source_id)
    with s.connect() as c:
        init(c)
        rows = [dict(r) for r in c.execute(f'SELECT * FROM ai_usage {where} ORDER BY id', args)]
    table = prices(); sources = {x['id']: x['name'] for x in s.records('source')}
    def bucket(): return {'calls': 0, 'failed': 0, 'input': 0, 'output': 0, 'cached': 0, 'duration_ms': 0, 'cost': 0.0, 'unpriced_calls': 0}
    def add(b, r):
        b['calls'] += 1; b['failed'] += r['status'] != 'success'
        b['input'] += r['input_tokens'] or 0; b['output'] += r['output_tokens'] or 0; b['cached'] += r['cached_tokens'] or 0; b['duration_ms'] += r['duration_ms'] or 0
        value = cost(price_for(r['provider'], r['model'], table), r['input_tokens'], r['output_tokens'], r['cached_tokens'], r.get('cache_write_tokens'))
        if value is None: b['unpriced_calls'] += 1
        else: b['cost'] += value
    totals, models, day_rows, jobs, operations = bucket(), {}, {}, {}, {}
    for r in rows:
        add(totals, r); add(operations.setdefault((r['operation'] or 'proposals', r.get('tier') or ''), bucket()), r)
        add(models.setdefault((r['provider'], r['model']), bucket()), r)
        add(day_rows.setdefault(r['time'][:10], bucket()), r)
        if r['job']: add(jobs.setdefault(r['job'], {**bucket(), 'source': r['source'], 'first': r['time']}), r); jobs[r['job']]['last'] = r['time']
    by_model = [{'provider': p, 'model': m, **b, 'price': price_for(p, m, table), 'avg_ms': round(b['duration_ms'] / b['calls']) if b['calls'] else 0} for (p, m), b in models.items()]
    by_job = [{'job': j, 'source_name': sources.get(b['source'], b['source']), **b} for j, b in jobs.items()]
    recent = [{**r, 'cost': cost(price_for(r['provider'], r['model'], table), r['input_tokens'], r['output_tokens'], r['cached_tokens'], r.get('cache_write_tokens')), 'source_name': sources.get(r['source'], r['source'])} for r in rows[-100:][::-1]]
    by_operation = [{'operation': o, 'tier': t, **b} for (o, t), b in sorted(operations.items(), key=lambda x: -x[1]['calls'])]
    return {'days': days, 'totals': totals, 'by_model': sorted(by_model, key=lambda x: -x['calls']), 'by_operation': by_operation,
            'by_day': [{'day': d, **b} for d, b in sorted(day_rows.items())], 'by_job': sorted(by_job, key=lambda x: x['last'], reverse=True),
            'recent': recent, 'projections': projections(table, sources), 'prices': table}


def projections(table, sources):
    """Remaining AI work of unfinished live jobs, priced from that job's own average tokens per batch."""
    out = []
    for job in s.records('job'):
        total, done = job.get('ai_total_entities'), job.get('ai_offset') or 0
        if job.get('mode') != 'live' or not total or done >= total or job.get('status') in ('complete', 'cancelled'): continue
        with s.connect() as c:
            init(c)
            r = c.execute("SELECT COUNT(*),AVG(input_tokens),AVG(output_tokens),AVG(duration_ms) FROM ai_usage WHERE job=? AND status='success' AND operation='proposals'", (job['id'],)).fetchone()
        calls, avg_in, avg_out, avg_ms = r[0], r[1], r[2], r[3]
        remaining = math.ceil((total - done) / BATCH)
        provider, model = job.get('ai_provider', ''), job.get('ai_model', '')
        price = price_for(provider, model, table)
        item = {'job': job['id'], 'source_name': sources.get(job['source_id'], job['source_id']), 'status': job['status'], 'provider': provider, 'model': model,
                'done': done, 'total': total, 'remaining_batches': remaining, 'measured_batches': calls}
        if calls:
            item.update(avg_input=round(avg_in or 0), avg_output=round(avg_out or 0), remaining_input=round((avg_in or 0) * remaining), remaining_output=round((avg_out or 0) * remaining),
                        remaining_seconds=round((avg_ms or 0) * remaining / 1000))
            item['remaining_cost'] = cost(price, item['remaining_input'], item['remaining_output'])
        out.append(item)
    return out
