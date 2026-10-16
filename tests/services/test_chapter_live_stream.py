import json
from types import SimpleNamespace
import pytest
from agents import chapter_team as team


def test_partial_json_preserves_escaped_text_and_never_exposes_json_markup():
    raw=json.dumps({'pieces':[{'task_id':'t1','markdown':'第一行\n含"引号"与中文'},{'task_id':'t2','markdown':'第二节'}]},ensure_ascii=True)
    assert team.partial_texts(raw)=={'t1':'第一行\n含"引号"与中文','t2':'第二节'}
    for end in range(raw.index('markdown')+12,len(raw)):
        pieces=team.partial_texts(raw[:end])
        for key,value in pieces.items():
            assert {'t1':'第一行\n含"引号"与中文','t2':'第二节'}[key].startswith(value)


async def test_text_uses_actual_provider_stream_and_reports_before_completion(monkeypatch):
    from services.config_store import config_store
    from infra.llm import clients
    monkeypatch.setattr(config_store,'get_configs_and_next_index',lambda:([SimpleNamespace(model='test',provider='openai')],0))
    observed=[]
    async def stream(*args,**kwargs):
        yield '{"pieces":[{"task_id":"t1","markdown":"首段'
        assert observed and observed[0]=={'t1':'首段'}
        yield '完成"}]}'
    async def sink(pieces):observed.append(pieces)
    monkeypatch.setattr(clients,'stream_openai',stream)
    token=team.STREAM_SINK.set(sink)
    try:
        raw,model=await team.call_role('text','结构化文字',{}, {'default':'test'})
    finally:team.STREAM_SINK.reset(token)
    assert json.loads(raw)['pieces'][0]['markdown']=='首段完成'
    assert observed[-1]=={'t1':'首段完成'} and model=='test'
