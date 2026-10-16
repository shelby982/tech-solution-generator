"""规范条款重点标记：基于同文件原文规则，不把符号直接等同于否决项。"""
import re

SYMBOLS = re.compile(r'[★☆▲△※]|(?m:^\s*[*＊](?!\s*\d))')
DENIAL = re.compile(r'否决(?:其)?投标|投标(?:将)?被否决|废标|无效投标|投标无效')
NEGATION = re.compile(r'不(?:作为|构成|属于|视为).{0,10}(?:否决|废标|无效)|不(?:予|会)?否决投标')


def original_topic_title(topic, source_files, sections):
    """仅还原此标题自身的原文标记，不能把父章或子条款标记传播到标题。"""
    def clean(value):
        return re.sub(r'^[\s\d.、章节★☆▲△※*＊]+', '', str(value or '')).strip()
    matches = [s for s in sections or [] if isinstance(s, dict)
               and s.get('source_file','') in source_files
               and clean(s.get('title')) == clean(topic)]
    marks = [list(s.get('special_marks') or []) for s in matches]
    if not matches or any(m != marks[0] for m in marks):
        return topic
    title = str(matches[0].get('title') or topic)
    marker = ''.join(marks[0])
    if not marker or SYMBOLS.search(title):
        return title
    number = re.match(r'^(\s*\d+(?:\.\d+)*[.、]?\s*)(.*)$', title)
    return (number.group(1) + marker + number.group(2)) if number else marker + title


def annotate_requirements(requirements, source_sections=None):
    documents = {}
    marked_sections = []
    for sec in source_sections or []:
        if not isinstance(sec, dict):
            continue
        name = str(sec.get('source_file') or '')
        documents.setdefault(name, []).append(str(sec.get('title') or '')+'\n'+str(sec.get('raw_content') or ''))
        marks = ''.join(map(str, sec.get('special_marks') or []))
        heading = str(sec.get('title') or '')
        symbols = [m.group().strip() for m in SYMBOLS.finditer(marks + heading)]
        if symbols:
            # 标题在解析器中已去掉星号，原始标记独立保存在 special_marks。
            clean_heading = re.sub(r'^[\s\d.、章节★☆▲△※*＊]+', '', heading).strip()
            if clean_heading:
                marked_sections.append((name, clean_heading, symbols))
    for row in requirements:
        documents.setdefault(str(row.get('source_file') or ''), []).append(str(row.get('text') or ''))
    rules = {}
    for name, parts in documents.items():
        rules[name] = [s.strip() for s in re.split(r'[。；\n]', '\n'.join(parts))
                       if SYMBOLS.search(s) and re.search(r'表示|标记|标注|标有|带|条款|要求|条目', s)]
    output = []
    for row in requirements:
        text, title = str(row.get('text') or ''), str(row.get('title') or '')
        direct = list(dict.fromkeys(m.group().strip() for m in SYMBOLS.finditer(text)))
        inherited = list(dict.fromkeys(m.group().strip() for m in SYMBOLS.finditer(title) if m.group().strip() not in direct))
        for name, heading, symbols in marked_sections:
            if name == str(row.get('source_file') or '') and any(
                re.sub(r'^[\s\d.、章节★☆▲△※*＊]+', '', part).strip() == heading
                for part in re.split(r'\s*>\s*| · ', title)
            ):
                inherited.extend(symbol for symbol in symbols if symbol not in direct and symbol not in inherited)
        markers = direct + inherited
        flags, evidence = [], []
        sentences = re.split(r'[。；\n]', text)
        denial = next((s.strip() for s in sentences if DENIAL.search(s) and not NEGATION.search(s)), None)
        substantial = next((s.strip() for s in sentences if re.search(r'不得负偏离|不允许负偏离|实质性要求', s)), None)
        if denial:
            flags.append('veto'); evidence.append({'text':denial,'basis':'条款原文'})
        if substantial:
            flags.append('substantive'); evidence.append({'text':substantial,'basis':'条款原文'})
        for marker in markers:
            matched = [r for r in rules.get(str(row.get('source_file') or ''), []) if marker in r]
            explicit = [r for r in matched if not NEGATION.search(r) and (DENIAL.search(r) or re.search(r'不得负偏离|不允许负偏离|实质性要求',r))]
            # 标题上的标记只有原文明确覆盖下属所有条款时才继承风险。
            applicable = [r for r in explicit if marker in direct or re.search(r'(?:章节|子条款|下属).{0,12}(?:全部|所有|均)|(?:全部|所有).{0,12}(?:子条款|条款)',r)]
            for rule in applicable:
                flag = 'veto' if DENIAL.search(rule) else 'substantive'
                flags.append(flag); evidence.append({'text':rule,'basis':'同文件标记说明'})
            if not applicable and not denial and not substantial:
                flags.append('check')
                evidence.extend({'text':r,'basis':'标记说明待核对'} for r in matched[:2])
        if markers: flags.append('marked')
        if re.search(r'(?:必须|须|应).{0,10}(?:提供|提交|附).{0,15}(?:证明|证书|材料|报告)',text):
            flags.append('proof')
        output.append({**row,'attention':{'flags':list(dict.fromkeys(flags)), 'markers':markers,
            'inherited':bool(inherited),'evidence':evidence,
            'note':'章节标记，适用范围待核对' if inherited and 'check' in flags else ('标记含义待核对' if 'check' in flags else '')}})
    return output
