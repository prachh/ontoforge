import json
import httpx
import pytest
from backend import ai, store

@pytest.fixture
def provider(monkeypatch,tmp_path):
    # Saved settings and usage rows go to a throwaway database, never the workspace one.
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'test.db');store.init()
    monkeypatch.setattr(ai,'SESSION_KEYS',{})
    monkeypatch.setenv('ONTO_AI_PROVIDER','ollama')
    monkeypatch.setenv('ONTO_AI_MODEL','local-test:latest')
    monkeypatch.setenv('ONTO_AI_BASE_URL','http://127.0.0.1:11434')
    monkeypatch.delenv('ONTO_AI_API_KEY',raising=False)
    monkeypatch.delenv('ONTO_AI_TIMEOUT_SECONDS',raising=False)
    return monkeypatch


def transport(monkeypatch,handler):
    client=httpx.Client
    monkeypatch.setattr(ai.httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))


def proposals():
    return {'proposals':[{'name':'Payment','description':'A payment contract','evidence':['valid','invented']} ]}


def test_ollama_native_schema_and_provenance(provider):
    def handler(req):
        assert req.url.path=='/api/chat'
        assert 'authorization' not in req.headers
        payload=json.loads(req.content)
        assert payload['model']=='local-test:latest' and payload['stream'] is False
        assert payload['format']['properties']['proposals']['type']=='array'
        assert payload['messages'][0]['role']=='system'
        assert 'untrusted_evidence' in payload['messages'][1]['content']
        return httpx.Response(200,json={'done':True,'message':{'content':json.dumps(proposals())}})
    transport(provider,handler)
    output=ai.generate_proposals([{'id':'valid','name':'Payment','excerpt':'Untrusted code'}])
    assert output[0]['evidence']==['valid'] and output[0]['properties']==[]
    assert ai.public_config()['configured'] and not ai.public_config()['api_key_required']


@pytest.mark.parametrize('status,body,match',[
    (404,{'error':'not found'},'Ollama model or API route not found'),
    (500,{'error':'private diagnostic'},'returned HTTP 500'),
    (200,{'done':False,'message':{'content':'{}'}},'incomplete generation'),
    (200,{'done':True,'message':{'content':'not JSON'}},'invalid proposal JSON'),
    (200,{'error':'internal error'},'provider reported a generation error'),
])
def test_provider_failures_remain_failures(provider,status,body,match):
    transport(provider,lambda req:httpx.Response(status,json=body))
    with pytest.raises(ValueError,match=match):ai.generate_proposals([{'id':'valid'}])


def test_timeout_and_unreachable_are_actionable(provider):
    def timed_out(req):raise httpx.ReadTimeout('private transport details',request=req)
    transport(provider,timed_out)
    with pytest.raises(ValueError,match='ONTO_AI_TIMEOUT_SECONDS'):ai.generate_proposals([])
    assert ai.connection_status()['status']=='unavailable'


def test_ollama_discovery_and_missing_model(provider):
    transport(provider,lambda req:httpx.Response(200,json={'models':[{'name':'local-test:latest'},{'name':'other:7b'}]}))
    assert ai.connection_status()['status']=='available'
    provider.setenv('ONTO_AI_MODEL','not-installed')
    assert ai.connection_status()['status']=='model_missing'
    provider.delenv('ONTO_AI_MODEL')
    assert ai.connection_status()['status']=='not_configured'


def test_openai_compatibility_and_redaction(provider):
    provider.setenv('ONTO_AI_PROVIDER','openai');provider.setenv('ONTO_AI_BASE_URL','https://provider.example/v1');provider.setenv('ONTO_AI_API_KEY','test-secret')
    def handler(req):
        assert req.url.path=='/v1/chat/completions'
        assert req.headers['authorization']=='Bearer test-secret'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(proposals())}}]})
    transport(provider,handler)
    assert ai.generate_proposals([{'id':'valid'}])[0]['name']=='Payment'
    assert 'test-secret' not in json.dumps(ai.public_config())
    provider.setenv('ONTO_AI_BASE_URL','https://username:password@provider.example/v1')
    public=ai.public_config()
    assert not public['configured'] and 'password' not in json.dumps(public)
    provider.setenv('ONTO_AI_BASE_URL','http://provider.example/v1')
    assert not ai.public_config()['configured']


def test_bedrock_api_key_tool_output_and_metering(provider):
    from backend import usage
    ai.save_config({'provider':'bedrock','region':'eu-west-1','model':'anthropic.claude-test-v1'}); ai.set_key('bedrock','bedrock-secret')
    def handler(req):
        assert req.url.host=='bedrock-runtime.eu-west-1.amazonaws.com' and req.url.path=='/model/anthropic.claude-test-v1/converse'
        assert req.headers['authorization']=='Bearer bedrock-secret'
        body=json.loads(req.content)
        assert body['toolConfig']['toolChoice']=={'tool':{'name':'record_proposals'}} and '$defs' not in json.dumps(body['toolConfig'])
        return httpx.Response(200,json={'output':{'message':{'content':[{'toolUse':{'name':'record_proposals','input':proposals()}}]}},'usage':{'inputTokens':1200,'outputTokens':300}})
    transport(provider,handler)
    out=ai.generate_proposals([{'id':'valid'}],{'job':'job_1','source':'src_1'})
    assert out[0]['name']=='Payment'
    public=ai.public_config()
    assert public['provider']=='bedrock' and public['key_source']=='session' and 'bedrock-secret' not in json.dumps(public)
    usage.set_price('bedrock','anthropic.claude-test-v1',3,15)
    s=usage.summary(30)
    assert s['totals']['input']==1200 and s['totals']['output']==300 and abs(s['totals']['cost']-(1200*3+300*15)/1e6)<1e-12
    assert s['by_model'][0]['model']=='anthropic.claude-test-v1' and s['by_job'][0]['job']=='job_1'


def test_bedrock_aws_credentials_and_text_json(provider,monkeypatch):
    import boto3
    ai.save_config({'provider':'bedrock','region':'us-east-1','model':'meta.llama-test','profile':'analytics'})
    seen={}
    class Client:
        def converse(self,modelId,**request):
            seen.update(model=modelId,request=request)
            return {'output':{'message':{'content':[{'text':'Here you go:\n```json\n'+json.dumps(proposals())+'\n```'}]}},'usage':{'inputTokens':10,'outputTokens':5}}
    class Session:
        def __init__(self,profile_name=None,region_name=None): seen.update(profile=profile_name,region=region_name)
        def client(self,name,config=None): assert name=='bedrock-runtime'; return Client()
    monkeypatch.setattr(boto3,'Session',Session)
    assert ai.generate_proposals([{'id':'valid'}])[0]['evidence']==['valid']
    assert seen['profile']=='analytics' and seen['region']=='us-east-1' and seen['model']=='meta.llama-test' and 'toolConfig' not in seen['request']
    assert ai.public_config()['key_source']=='aws-credential-chain'


def test_saved_settings_override_env_and_validate(provider):
    ai.save_config({'provider':'openai','model':'gpt-test','base_url':'https://api.openai.com/v1','timeout_seconds':300})
    c=ai.config()
    assert c['provider']=='openai' and not c['configured'] and 'API key' in c['error'] and c['timeout']==300
    ai.set_key('openai','sk-session'); assert ai.config()['configured'] and ai.config()['key_source']=='session'
    with pytest.raises(ValueError): ai.save_config({'provider':'gemini'})
    with pytest.raises(ValueError): ai.save_config({'provider':'openai','timeout_seconds':0})
    ai.save_config({'provider':'bedrock','region':'nowhere','model':'x'}); assert 'region' in ai.config()['error']


def test_usage_projection_for_unfinished_live_job(provider):
    from backend import usage, store as s
    s.put('source',{'id':'src_p','name':'erp'})
    s.put('job',{'id':'job_p','source_id':'src_p','mode':'live','status':'running','ai_offset':16,'ai_total_entities':40,'ai_provider':'ollama','ai_model':'m'})
    for _ in range(2): usage.record('ollama','m',{'job':'job_p','source':'src_p'},{'input':1000,'output':200},60000,'success')
    p=usage.summary(30)['projections'][0]
    assert p['remaining_batches']==3 and p['remaining_input']==3000 and p['remaining_output']==600 and p['remaining_cost']==0 and p['remaining_seconds']==180


def test_bedrock_retries_without_temperature_when_model_rejects_it(provider):
    ai.save_config({'provider':'bedrock','region':'us-east-1','model':'us.anthropic.claude-new'}); ai.set_key('bedrock','k')
    monkeypatch_calls=[]
    def handler(req):
        body=json.loads(req.content); monkeypatch_calls.append('temperature' in body['inferenceConfig'])
        if 'temperature' in body['inferenceConfig']:
            return httpx.Response(400,json={'message':'The model returned the following errors: `temperature` is deprecated for this model.'})
        return httpx.Response(200,json={'output':{'message':{'content':[{'toolUse':{'input':proposals()}}]}},'usage':{'inputTokens':5,'outputTokens':2}})
    transport(provider,handler)
    assert ai.generate_proposals([{'id':'valid'}])[0]['name']=='Payment'
    assert ai.generate_proposals([{'id':'valid'}])[0]['name']=='Payment'
    assert monkeypatch_calls==[True,False,False]  # learned once, then omitted


def test_bedrock_falls_back_to_unforced_tool_choice(provider):
    ai.save_config({'provider':'bedrock','region':'us-east-1','model':'us.anthropic.claude-no-forced-tool'}); ai.set_key('bedrock','k')
    seen=[]
    def handler(req):
        choice=json.loads(req.content)['toolConfig']['toolChoice']; seen.append(next(iter(choice)))
        if 'tool' in choice: return httpx.Response(400,json={'message':'The model returned the following errors: tool_choice: type "tool" and "any" are not supported for this model.'})
        return httpx.Response(200,json={'output':{'message':{'content':[{'text':'thinking...'},{'toolUse':{'input':proposals()}}]}},'usage':{'inputTokens':5,'outputTokens':2}})
    transport(provider,handler)
    assert ai.generate_proposals([{'id':'valid'}])[0]['name']=='Payment' and ai.generate_proposals([{'id':'valid'}])[0]['name']=='Payment'
    assert seen==['tool','auto','auto']
