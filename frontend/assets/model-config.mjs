// 模型 / Provider 默认值的**前端唯一入口**。
//
// 默认值本体在后端 `services/config_store.py` 的 `PROVIDER_CATALOG`，通过
// `GET /api/model-defaults` 下发；前端不再硬编码任何模型名，避免再次出现
// "两页各写一份、且与后端默认值漂移"的问题。
//
// 读取失败时返回空目录（配置弹窗字段留空，用户可手填），不让整页脚本崩掉。

const EMPTY = { providers: {}, providerList: [], modelOptions: [], defaultProvider: 'claude' };

let cache = null;

/** 读取并缓存后端下发的默认值。 */
export async function loadModelDefaults() {
  if (cache) return cache;

  const resp = await fetch('/api/model-defaults', { cache: 'no-store' });
  if (!resp.ok) throw new Error(`读取默认模型失败（HTTP ${resp.status}）`);
  const data = await resp.json();

  const providerList = (data.providers || []).map((p) => ({
    id: p.id,
    label: p.chip_label || p.name || p.id,
    url: p.default_base_url || '',
    model: p.default_model || '',
    placeholder: p.placeholder || '',
  }));
  const providers = {};
  for (const p of providerList) {
    providers[p.id] = { url: p.url, model: p.model, placeholder: p.placeholder };
  }

  cache = {
    providers,
    providerList,
    modelOptions: data.model_options || [],
    defaultProvider: data.default_provider || 'claude',
  };
  return cache;
}

/** 测试用：清缓存。 */
export function resetModelDefaultsCache() {
  cache = null;
}

export const EMPTY_MODEL_DEFAULTS = EMPTY;

const _esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

/**
 * 用后端下发的 provider 列表渲染弹窗按钮（不再在 HTML 里写死）。
 * 点击事件继续由页面的 #provider-chips 容器委托处理。
 */
export function renderProviderChips(container, providerList, activeProvider = '') {
  if (!container) return;
  container.innerHTML = (providerList || []).map((p) => {
    const active = p.id === activeProvider ? ' active' : '';
    return `<button type="button" class="provider-chip${active}" data-provider="${_esc(p.id)}">${_esc(p.label)}</button>`;
  }).join('');
}
