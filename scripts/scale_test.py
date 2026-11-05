"""Measured synthetic corpus. Uses actual snapshot/parser pipeline; no repository code executes."""
import sys,os,tempfile,time,platform,resource,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import store as s,pipeline as p

def main():
 count=int(sys.argv[1]) if len(sys.argv)>1 else 1000
 with tempfile.TemporaryDirectory() as d:
  root=Path(d); repo=root/'corpus';repo.mkdir();s.DATA=root/'state';s.DATA.mkdir();s.DB=s.DATA/'ontoforge.db';os.environ['ONTO_LOCAL_ROOTS']=str(root);s.init()
  for i in range(count):
   f=repo/f'area_{i//100}'/f'module_{i}.py';f.parent.mkdir(exist_ok=True);f.write_text(f'class Item{i}:\n    def total(self, amount: float) -> float:\n        return amount * 1.2\n\ndef test_item_{i}():\n    assert 1 + 1 == 2\n')
  source={'id':'scale-source','kind':'local','name':'Measured synthetic corpus','location':str(repo),'exclusions':[]};s.put('source',source)
  results=[]
  for cycle in ['cold','cached']:
   snap={'id':cycle,'source_id':source['id'],'status':'new','created_at':s.now(),'commit':None};s.put('snapshot',snap,source['id'])
   job={'id':cycle+'job','source_id':source['id'],'snapshot_id':cycle,'status':'queued','stage':'Acquisition','mode':'parsing','created_at':s.now()};s.put('job',job,source['id'],cycle)
   t=time.perf_counter();p.run(job['id']);elapsed=time.perf_counter()-t
   with s.connect() as c: entities=c.execute('SELECT COUNT(*) FROM entities WHERE snapshot=?',(cycle,)).fetchone()[0]
   results.append({'cycle':cycle,'seconds':round(elapsed,3),'counts':s.counts(cycle),'entities':entities,'job_status':s.get('job',job['id'])['status'],'cache_hits':s.get('job',job['id']).get('cache_hits',0)})
  # Darwin reports bytes; Linux reports KiB.
  rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1048576 if sys.platform=='darwin' else 1024)
  result={'environment':{'os':platform.platform(),'architecture':platform.machine(),'python':platform.python_version(),'cpu_count':os.cpu_count()},'input_files':count,'peak_process_rss_mib':round(rss,2),'database_bytes':s.DB.stat().st_size,'runs':results,'limitations':'Generated small Python files, local SSD, one analysis job. RSS is process high-water mark across both runs. Not a distributed or production capacity benchmark.'}
  output=Path(__file__).resolve().parents[1]/'artifacts'/'scale-results.json';output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
