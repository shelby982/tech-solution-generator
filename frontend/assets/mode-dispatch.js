/**
 * 三模式调度：把 URL 上的 view 参数落到 `body[data-view]`，并让顶栏导航原地切换。
 *
 * 合并背景见 `workbench-modes.mjs`。这里只管**模式层**：
 * - 三个模式各自的逻辑在 `mode-outline.js`（要求与大纲 + 素材库）
 *   与 `mode-editor.js`（方案编辑）里，**各自一个模块 = 各自一个作用域**
 *   （实测两段原内联脚本有 90 个同名顶层标识符，同页拼接必然互相覆盖）。
 * - 本模块不碰那两个模块的内部函数，只负责"当前是哪个模式"这件事。
 *
 * ⚠️ 注意别和编辑器工具栏里的 `#ced-mode-switch`（编辑/预览）搞混：
 * 那个是**编辑器内部**的视图切换，与页面三模式无关。
 */
import { DEFAULT_MODE, isValidMode } from './workbench-modes.mjs?v=20261016-v57';
import { renderWorkspaceChrome } from './workspace.js?v=20261016-v57';

const VIEW_KEY = 'aibidding:workspace-view';

function readMode() {
  const fromUrl = new URLSearchParams(location.search).get('view');
  if (isValidMode(fromUrl)) return fromUrl;
  try {
    const saved = sessionStorage.getItem(VIEW_KEY);
    if (isValidMode(saved)) return saved;
  } catch {}
  return DEFAULT_MODE;
}

/** 切换模式：只改 body 标记与 URL，不整页跳转（选中章节与滚动位置都保留）。 */
export function setMode(mode, { push = false } = {}) {
  const next = isValidMode(mode) ? mode : DEFAULT_MODE;
  document.body.dataset.view = next;
  try { sessionStorage.setItem(VIEW_KEY, next); } catch {}
  try {
    const url = new URL(location.href);
    url.searchParams.set('view', next);
    const blockId = document.body.dataset.selectedBlock;
    if (blockId) url.searchParams.set('blockId', blockId);
    if (push) history.pushState({ mode: next }, '', url);
    else history.replaceState({ mode: next }, '', url);
  } catch {}
  renderNav();
  mountTreeHost(next);
  document.dispatchEvent(new CustomEvent('workspace:mode', { detail: { mode: next } }));
  renderActiveTree(next);
  return next;
}

export function currentMode() {
  return document.body.dataset.view || readMode();
}

/** 导航的渲染权在 workspace.js（它还负责状态条）：合并页里它会输出 data-mode 链接，
 *  本模块只绑定点击、维护 body[data-view]。包一层是为了调用处更好读。 */
function renderNav() {
  try { renderWorkspaceChrome(); } catch {}
}

/**
 * 把**唯一的**章节树宿主 `#blocks-list` 搬到当前模式的左栏位置。
 *
 * 为什么搬而不是各留一个：归一之前两侧各渲一棵（实测 .ot-node 120 个 = 两棵各 60），
 * 同一批章节被渲染两遍、两份折叠状态、两套选中。搬 DOM 节点不会丢子节点与监听器，
 * 所以这是"一份实例、两种栏位"的最小代价做法。
 */
function mountTreeHost(mode) {
  const host = document.getElementById('blocks-list');
  if (!host) return;
  const slot = mode === 'editor'
    ? document.querySelector('.outline-pane')      // 方案编辑：编辑器左栏
    : document.getElementById('blocks-scroll');    // 要求与大纲 / 素材库
  if (slot && host.parentElement !== slot) slot.appendChild(host);
}

/** 让**当前模式**的模块重绘共享宿主（另一模式的模块不参与，避免互相清空）。 */
function renderActiveTree(mode) {
  try {
    if (mode === 'editor') window.__renderEditorTree?.();
    else window.__reloadOutlineTree?.();
  } catch (err) {
    console.warn('[mode] 重绘章节树失败', err);
  }
}

function init() {
  // 首次进入：以 URL 为准；URL 没带就沿用上次（sessionStorage），都没有则默认。
  const urlMode = new URLSearchParams(location.search).get('view');
  const mode = isValidMode(urlMode) ? urlMode : readMode();
  document.body.dataset.view = mode;
  try { sessionStorage.setItem(VIEW_KEY, mode); } catch {}
  renderNav();
  // 初次进入也要派发一次：各模式模块靠这个事件做"模式激活"（编辑器刷新工作区、
  // 其余两模式重推视图）。只改 body 标记而不发事件，编辑器模式的左栏目录会是空的
  // —— 实测就是这么来的。
  mountTreeHost(mode);
  document.dispatchEvent(new CustomEvent('workspace:mode', { detail: { mode, initial: true } }));
  renderActiveTree(mode);

  document.getElementById('workspace-nav')?.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-mode]');
    if (!a) return;
    e.preventDefault();
    setMode(a.dataset.mode, { push: true });
  });

  // 浏览器前进/后退：回到对应模式，不重刷
  window.addEventListener('popstate', () => setMode(readMode()));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}
