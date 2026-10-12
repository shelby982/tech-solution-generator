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
import { MODES, MODE_LABEL, DEFAULT_MODE, parseMode, isValidMode } from './workbench-modes.mjs';

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
  document.dispatchEvent(new CustomEvent('workspace:mode', { detail: { mode: next } }));
  return next;
}

export function currentMode() {
  return document.body.dataset.view || readMode();
}

/** 顶栏导航：三个模式原地切换。质量检查仍是独立页面。 */
function renderNav() {
  const nav = document.getElementById('workspace-nav');
  if (!nav) return;
  const mode = currentMode();
  const link = (m) =>
    `<a href="#" role="tab" data-mode="${m}" aria-current="${mode === m ? 'page' : 'false'}">${MODE_LABEL[m]}</a>`;
  const review = `<a href="/review?${new URLSearchParams(location.search)}" data-external="review">质量检查</a>`;
  nav.innerHTML = MODES.map(link).join('') + review;
}

function init() {
  // 首次进入：以 URL 为准；URL 没带就沿用上次（sessionStorage），都没有则默认。
  const urlMode = new URLSearchParams(location.search).get('view');
  const mode = isValidMode(urlMode) ? urlMode : readMode();
  document.body.dataset.view = mode;
  try { sessionStorage.setItem(VIEW_KEY, mode); } catch {}
  renderNav();

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
