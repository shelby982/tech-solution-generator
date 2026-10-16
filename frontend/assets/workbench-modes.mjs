/**
 * 三模式工作台：模式定义与 URL 契约（共享一份，页面与 node --test 都从这里取）。
 *
 * 背景（2026-10-13 用户拍板"一步到位 合成三模式页面"）：
 *   此前是三个模块分在两个页面 —— `project-init`（要求与大纲 / 素材库两个视图）
 *   与 `workbench`（方案编辑）。两页共享了 CSS 变量、依据渲染、章节状态投影，
 *   但**壳是两份**：章节树有 `.ot-row` 与 `.doc-outline-item` 两套实现，
 *   模型选择器 / API 配置弹窗 / 顶栏各自又写了一份（实测 18 个 DOM id 冲突）。
 *   合并成一个页面 + 三个模式后，左栏章节树与顶栏只有一份。
 *
 * 这个模块只放"能被单测的纯部分"：模式定义、URL 解析、章节上下文传递。
 * DOM 与各模式自己的逻辑留在页面里（它们各自作用域，互不污染 ——
 * 实测两段内联脚本有 90 个同名顶层标识符，不能直接拼接）。
 */

/** 三个模式。顺序即顶栏顺序。 */
export const MODES = ['outline', 'matching', 'editor'];

export const MODE_LABEL = {
  outline: '要求与大纲',
  matching: '素材库',
  editor: '方案编辑',
};

/** 质量检查是独立页面（评审结果展示与 diff），不属于本页三模式。 */
export const REVIEW_PATH = '/review';

/** 各模式的"栏目职责"，用于切换时决定中/右两栏挂什么。 */
export const MODE_PANEL = {
  outline: { center: 'outline', right: '' },
  matching: { center: 'match', right: 'evidence' },
  editor: { center: 'editor', right: 'context' },
};

const MODE_SET = new Set(MODES);

/** 默认模式：没有 view 参数时进入"要求与大纲"（项目从这里起步）。 */
export const DEFAULT_MODE = 'outline';

/**
 * 从 URL 查询串解析模式。
 *
 * 兼容旧链接：合并前 `?view=outline|matching` 走 project-init、
 * `/workbench` 走方案编辑页；现在都收敛到本页的 view 参数。
 */
export function parseMode(search) {
  const q = new URLSearchParams(search || '');
  const v = (q.get('view') || '').trim();
  return MODE_SET.has(v) ? v : DEFAULT_MODE;
}

/**
 * 构造本页 URL（保留 projectId / threadId / blockId）。
 *
 * 合并的收益之一：切模式只是改 view 参数，**不整页跳转**，
 * 因此 selectedBlockId 与滚动位置都不会丢。
 */
export function modeUrl(mode, { projectId, packageId, threadId, blockId } = {}) {
  const q = new URLSearchParams();
  if (projectId) q.set('projectId', String(projectId));
  if (packageId) q.set('packageId', String(packageId));
  if (threadId) q.set('threadId', String(threadId));
  if (blockId) q.set('blockId', String(blockId));
  q.set('view', MODE_SET.has(mode) ? mode : DEFAULT_MODE);
  return '/three-mode?' + q.toString();
}

/**
 * 章节上下文：三模式共用同一个"当前章节"。
 *
 * 为什么单独抽：合并前跨模块跳转靠 URL 传 blockId，页面刷新/整页跳转时容易丢；
 * 合并后模式切换是同一页面内的事，选中章节必须**原地保持**。
 * 这里给出纯函数，页面只负责存。
 */
export function resolveSelectedBlock(mode, { selectedBlockId = '', blocks = [] } = {}) {
  const ids = (blocks || []).map(b => String(b?.block_id ?? b ?? '')).filter(Boolean);
  if (selectedBlockId && ids.includes(String(selectedBlockId))) return String(selectedBlockId);
  if (!ids.length) return '';
  return ids[0];
}

/**
 * 切模式时，该不该重新渲染某一栏。
 *
 * 纯函数，便于单测：只有"这个模式的这一栏用得到"才渲染 ——
 * 合并后三个模式共用 DOM 容器，全量重渲染会白做很多工作，
 * 也会把用户在别的模式里的输入冲掉。
 */
export function panelVisible(mode, panel) {
  const spec = MODE_PANEL[mode] || MODE_PANEL[DEFAULT_MODE];
  return spec.center === panel || spec.right === panel;
}

export function isValidMode(mode) {
  return MODE_SET.has(mode);
}
