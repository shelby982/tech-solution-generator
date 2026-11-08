"""Confirmed response chapters own all document heading levels."""
import re


async def is_locked(db, block: dict) -> bool:
    from services.split_answers import get_answers
    answers = await get_answers(db, block['project_id'], block.get('run_thread_id'))
    return bool((answers.get('response_plan') or {}).get('confirmed'))


def body_only(content: str, chapter_title: str = '') -> str:
    """Retain text and code, rendering body sublabels outside the Word TOC."""
    lines = []
    fence = None
    for line in str(content or '').splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r'(`{3,}|~{3,})', stripped)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append(line)
            continue
        if fence is None and re.match(r'^ {0,3}(?:=+|-+)[ \t]*(?:\r?\n)?$', line) and lines and lines[-1].strip() and not lines[-1].lstrip().startswith('|'):
            title = lines.pop().strip()
            if title != chapter_title:
                lines.append('**' + title + '**' + ('\n' if line.endswith('\n') else ''))
            continue
        heading = re.match(r'^ {0,3}((?:>[ \t]*)*)#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*(\r?\n)?$', line) if fence is None else None
        if heading:
            title = heading[2].strip()
            # The confirmed chapter title is already rendered by the document tree.
            if title == chapter_title:
                continue
            lines.append(heading[1] + '**' + title + '**' + (heading[3] or ''))
        else:
            lines.append(line)
    return ''.join(lines)
