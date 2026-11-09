// 模型 / Provider 默认值的**前端唯一入口**。
//
// 默认值本体在后端 `services/config_store.py` 的 `PROVIDER_CATALOG`，通过
// `GET /api/model-defaults` 下发；前端不再硬编码任何模型名，避免再次出现
// "两页各写一份、且与后端默认值漂移"的问题。
//
// 读取失败时返回空目录（配置弹窗字段留空，用户可手填），不让整页脚本崩掉。

const EMPTY = { providers: {}, modelOptions: [], defaultProvider: 'claude' };

let cache = null;

/** 读取并缓存后端下发的默认值。 */
export async function loadModelDefaults() {
  if (cache) return cache;

  const resp = await fetch('/api/model-defaults', { cache: 'no-store' });
  if (!resp.ok) throw new Error(`读取默认模型失败（HTTP ${resp.status}）`);
  const data = await resp.json();

  const providers = {};
  for (const p of data.providers || []) {
    providers[p.id] = {
      url: p.default_base_url || '',
      model: p.default_model || '',
      placeholder: p.placeholder || '',
    };
  }

  cache = {
    providers,
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
