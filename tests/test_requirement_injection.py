import asyncio, json

def test_specialist_prompt_contains_chapter_requirement(monkeypatch):
    """多 Agent 整章编写：specialist 的 system prompt 必须带「本章统一要求」。"""
    from agents import chapter_team

    captured = {}

    async def fake_call_role(role, system, payload, settings):
        captured['role'] = role
        captured['system'] = system
        captured['payload'] = payload
        return json.dumps({"pieces": [{"task_id": "t1", "markdown": "正文内容"}]}), "m"

    monkeypatch.setattr(chapter_team, 'call_role', fake_call_role)
    agent = chapter_team.SpecialistAgent('text')
    plan = {'tasks': [{'id': 't1', 'kind': 'text', 'title': '背景', 'purpose': '说明'}]}
    asyncio.run(agent.produce({'chapter_requirement': '全章口径统一；禁止待补充'}, plan, {}))

    assert '本章统一要求' in captured['system']
    assert '禁止待补充' in captured['system']
    assert captured['payload']['chapter_requirement'] == '全章口径统一；禁止待补充'
