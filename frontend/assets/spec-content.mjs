// 仅识别解析器生成的管道表格与换行；原文中的其他 HTML 一律转义。
export function specContentHtml(value) {
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cell = value => escape(value).replace(/&lt;br\s*\/?&gt;/gi, '<br>');
  const lines = String(value || '').split('\n');
  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(x => x.trim().replace(/\\\|/g, '|'));
  const separator = line => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const html = [];
  for (let i = 0; i < lines.length;) {
    if (lines[i].includes('|') && i + 1 < lines.length && separator(lines[i + 1])) {
      const header = cells(lines[i]);
      const rows = [];
      i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
      html.push(`<div class="spec-table-scroll" tabindex="0" aria-label="原文表格，可横向滚动"><table class="spec-source-table"><thead><tr>${header.map(x=>`<th scope="col">${cell(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(x=>`<td>${cell(x)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    } else {
      const text = [];
      do { text.push(lines[i++]); } while (i < lines.length && !(lines[i].includes('|') && i + 1 < lines.length && separator(lines[i + 1])));
      if (text.join('\n').trim()) html.push(`<div class="spec-source-text">${cell(text.join('\n'))}</div>`);
    }
  }
  return html.join('');
}
