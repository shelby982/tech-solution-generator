"""技术应答三段目录：评分点、重点要求、原文点对点应答。"""
import re
from infra.parser.outline_builder import OutlineNode, build_outline, _topic_keys, _normalize, _subtree_len
from services.spec_attention import annotate_requirements
from services.spec_structure import build_spec_structure
from services.point_store import stable_requirement_id


def build_technical_response(points, sections, requirements, *, subpoints=None, package_no=''):
    sections = list(sections)
    rows = annotate_requirements([dict(
        requirement_id=getattr(r,'stable_id',None) or stable_requirement_id(r), title=r.source_location, text=r.requirement_text,
        source_file=r.source_file, mandatory_level=r.mandatory_level,
    ) for r in requirements], sections)
    files = list(dict.fromkeys(s.get('source_file', '') for s in sections))
    tree = build_spec_structure(rows, sections, source_files=files)
    if any(n['topic'] == '原文位置待核对' for n in tree):
        raise ValueError('部分规范要求无法定位到原文章节，请核对解析结果后生成点对点目录')

    scoring = build_outline(points, category='', package_no=package_no,
                            subpoints_by_criterion=subpoints)
    for node in scoring:
        node.level += 1
        node.metadata = {'response_part': 'scoring'}
    nodes = [OutlineNode(1, '技术评分项应答', metadata={'response_part': 'scoring'})] + scoring
    priorities, original = [], []

    def descendants(node):
        return [node] + [d for c in node['children'] for d in descendants(c)]

    def visit(node, inherited_priority=False):
        reqs = node['requirements']
        important = bool(node['special_marks']) or any(
            r.get('mandatory_level') == 'mandatory' or
            set(r.get('attention', {}).get('flags', [])) & {'veto', 'substantive'} for r in reqs)
        metadata = dict(source_file=node['source_files'][0], source_path=node['source_path'],
                        original_level=node['level'], original_title=node['topic'])
        original.append(OutlineNode(
            node['level'] + (2 if len(files) > 1 else 1), node['display_title'], source='spec',
            spec_requirement_ids=[r['requirement_id'] for r in reqs],
            metadata={**metadata, 'response_part': 'point_to_point'},
            source_content=node['original_content'], source_marks=node['special_marks']))
        if important and not inherited_priority:
            scope = descendants(node)
            priorities.append(OutlineNode(
                2, node['display_title'], source='marked',
                spec_requirement_ids=list(dict.fromkeys(r['requirement_id'] for n in scope for r in n['requirements'])),
                metadata={**metadata, 'response_part': 'priority'},
                source_content='\n\n'.join(n['display_title']+'\n'+n['original_content'] for n in scope),
                source_marks=node['special_marks']))
        for child in node['children']:
            visit(child, inherited_priority or important)

    # 每份文件单独作为容器，文件内保持原文顺序，不做同名去重。
    for filename in files:
        if len(files) > 1:
            original.append(OutlineNode(2, filename, source='spec', metadata={'response_part': 'point_to_point', 'source_file': filename, 'file_container': True}))
        for root in tree:
            if root['source_files'] == [filename]:
                visit(root)

    remaining = []
    for priority in priorities:
        clean_title = re.sub(r'^[\d.\s、★☆▲△※*＊]+', '', priority.title)
        keys = _topic_keys(clean_title)
        core = re.sub(r'(?:技术要求|要求|方案)$', '', clean_title)
        if len(core) >= 3 and core not in keys:
            keys.append(core)
        matches = [(sum(k in _normalize(' '.join([n.title,n.factor,n.item,n.criterion])) for k in keys), n.level, i, n)
                   for i,n in enumerate(scoring)]
        best = max(matches, key=lambda x: x[:3], default=None)
        if best and best[0]:
            host = best[3]
            priority.level = host.level + 1
            priority.review_point_id = host.review_point_id
            nodes.insert(nodes.index(host) + 1 + _subtree_len(nodes, host), priority)
        else:
            remaining.append(priority)
    if remaining:
        nodes.append(OutlineNode(1, '重点及必须响应要求', metadata={'response_part': 'priority'}))
        nodes.extend(remaining)
    nodes.append(OutlineNode(1, '技术规范书点对点应答', source='spec', metadata={'response_part': 'point_to_point'}))
    nodes.extend(original)
    return nodes


def check_response_structure(toc, source_sections, requirements, source_files, priority_paths=None):
    from services.spec_attention import original_topic_title
    expected = [s for s in source_sections if s.get('source_file','') in source_files]
    actual = [s for s in toc if (s.get('chain') or {}).get('response_part') == 'point_to_point'
              and (s.get('chain') or {}).get('source_path')]
    offset = 2 if len(source_files) > 1 else 1
    matches = len(actual) == len(expected) and all(
        a.get('title') == original_topic_title(e.get('title'), [e.get('source_file','')], [e])
        and a.get('level') == int(e.get('level') or 1) + offset
        and (a.get('chain') or {}).get('source_file') == e.get('source_file','')
        for a,e in zip(actual,expected))
    ids = {rid for a in actual for rid in (a.get('chain') or {}).get('spec_requirement_ids',[])}
    required_ids = {r['requirement_id'] for r in requirements}
    parts = [(s.get('chain') or {}).get('response_part') for s in toc]
    start = next((i for i,p in enumerate(parts) if p=='point_to_point'),len(parts))
    visible = {(s.get('chain',{}).get('source_file'), tuple(s.get('chain',{}).get('source_path') or []))
               for s in toc[:start] if s.get('chain',{}).get('response_part')=='priority'}
    expected_priorities = {(s['source_file'],tuple(s['source_path'])) for s in priority_paths or []}
    # 依据条款 ID 判断承接，而非要求生成时的重点章节名称仍然存在。
    # 用户可把多条要求合并进评分章节；原文符号本身不构成否决风险。
    annotated = annotate_requirements(requirements, source_sections)
    front_ids = {rid for s in toc[:start] for rid in (s.get('chain') or {}).get('spec_requirement_ids', [])}
    all_ids = {rid for s in toc for rid in (s.get('chain') or {}).get('spec_requirement_ids', [])}
    focus = [r for r in annotated if r.get('mandatory_level') == 'mandatory' or
             set((r.get('attention') or {}).get('flags', [])) & {'marked','veto','substantive'}]
    risks = [r for r in annotated if set((r.get('attention') or {}).get('flags', [])) & {'veto','substantive'}]
    missing_risks = [r for r in risks if r['requirement_id'] not in all_ids]
    warnings = [dict(requirement_id=r['requirement_id'],title=r.get('title') or '',
                     reason='已在点对点应答中安排，建议在评分章节或前部重点要求中突出',
                     critical=bool(set((r.get('attention') or {}).get('flags', [])) & {'veto','substantive'}))
                for r in focus if r['requirement_id'] not in front_ids and r['requirement_id'] in ids]
    structure_matches = matches and all(p=='point_to_point' for p in parts[start:])
    blockers = []
    if not structure_matches:
        blockers.append('点对点目录的标题、层级、顺序或末尾位置与原文不一致')
    if required_ids - ids:
        blockers.append(f'点对点应答缺少 {len(required_ids - ids)} 条要求的归属')
    if missing_risks:
        blockers.append(f'{len(missing_risks)} 条明确风险要求没有承接位置')
    return dict(original_sections_total=len(expected), original_sections_preserved=len(actual),
                focus_requirements_total=len(focus), focus_requirements_in_front=sum(r['requirement_id'] in front_ids for r in focus),
                risk_requirements_total=len(risks), risk_requirements_landed=len(risks)-len(missing_risks),
                missing_risk_requirements=[dict(requirement_id=r['requirement_id'], title=r.get('title') or '') for r in missing_risks],
                warnings=warnings, blocking_issues=blockers, can_confirm=not blockers,
                priority_sections_visible=len(expected_priorities & visible), priority_sections_total=len(expected_priorities),
                missing_priority_sections=[{'source_file':f,'source_path':list(p)} for f,p in sorted(expected_priorities-visible)],
                original_structure_matches=structure_matches,
                point_to_point_requirements=len(ids & required_ids),
                point_to_point_requirements_total=len(required_ids))
