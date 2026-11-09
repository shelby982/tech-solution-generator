// 前端模型/Provider 默认值的**唯一来源**。
//
// 背景：此前 mode-outline.js 与 projects.html 各自硬编码一份 PROVIDER_DEFAULTS，
// 两份已经漂移（deepseek 一处 v4-pro、一处 chat；Opus 标签 4.8/4.7），且都与后端
// services/config_store.py 的 DEFAULT_MODELS 不一致。这里收敛为一份共享表；
// 页面只负责渲染，不再各写一套。
//
// 注：这些是「新建配置时预填」的建议值，用户可改；后端仍以自己的默认值为准。

export const PROVIDER_DEFAULTS = {
  claude:   { url: 'https://api.anthropic.com',                 model: 'claude-sonnet-4-6', placeholder: 'sk-ant-…' },
  openai:   { url: 'https://api.openai.com/v1',                 model: 'gpt-4o',            placeholder: 'sk-…' },
  deepseek: { url: 'https://api.deepseek.com',                  model: 'deepseek-v4-pro',   placeholder: 'sk-…' },
  kimi:     { url: 'https://api.moonshot.cn/v1',                model: 'moonshot-v1-8k',    placeholder: 'sk-…' },
  minimax:  { url: 'https://api.minimax.chat/v1',               model: 'abab6.5s-chat',     placeholder: '' },
  doubao:   { url: 'https://ark.cn-beijing.volces.com/api/v3',  model: '',                  placeholder: '' },
};

/** 没有已保存配置时，模型下拉默认展示的几档。 */
export const DEFAULT_MODEL_OPTIONS = [
  { model: 'claude-opus-4-8', label: 'Opus 4.8' },
  { model: 'claude-sonnet-4-6', label: 'Sonnet 4.6' },
  { model: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5' },
];

/** 新建设置弹窗的默认 provider。 */
export const DEFAULT_PROVIDER = 'claude';
