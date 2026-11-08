/** Revert one AI operation while retaining subsequent edits outside its range. */
export function reverseAIChange(before, after, current) {
  if (before === after) return {content: current};
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start += 1;
  let suffix = 0;
  while (suffix < before.length - start && suffix < after.length - start &&
         before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix += 1;
  const oldText = before.slice(start, before.length - suffix);
  const newText = after.slice(start, after.length - suffix);
  if (current === after) return {content: before};
  const left = after.slice(Math.max(0, start - 48), start);
  const right = after.slice(after.length - suffix, after.length - suffix + 48);
  const anchor = left + newText + right;
  const index = anchor ? current.indexOf(anchor) : -1;
  if (index >= 0 && current.indexOf(anchor, index + 1) < 0) {
    const position = index + left.length;
    return {content: current.slice(0, position) + oldText + current.slice(position + newText.length)};
  }
  return {conflict: true};
}

export function selectedRange(text, start, end) {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > text.length) return null;
  return {before: text.slice(0, start), selection: text.slice(start, end), after: text.slice(end)};
}

export function rebaseSelection(range, text) {
  const original = range.before + range.selection + range.after;
  if (text === original) return range;
  const start = text.indexOf(range.selection);
  if (start < 0 || text.indexOf(range.selection, start + 1) >= 0) return null;
  return selectedRange(text, start, start + range.selection.length);
}
