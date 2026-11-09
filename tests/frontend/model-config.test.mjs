/**
 * 模型默认值加载器：前端不再硬编码模型值，只消费后端 /api/model-defaults。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  loadModelDefaults,
  resetModelDefaultsCache,
  EMPTY_MODEL_DEFAULTS,
} from '../../frontend/assets/model-config.mjs';

function fakeResponse(payload) {
  return { ok: true, json: async () => payload };
}

test('loadModelDefaults 读取并映射后端默认值', async () => {
  resetModelDefaultsCache();
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(url);
    return fakeResponse({
      default_provider: 'claude',
      providers: [
        {
          id: 'claude',
          name: 'Anthropic Claude',
          default_base_url: 'https://api.anthropic.com',
          default_model: 'claude-3-5-sonnet-20241022',
          placeholder: 'sk-ant-…',
        },
      ],
      model_options: [{ model: 'claude-opus-4-8', label: 'Opus 4.8' }],
    });
  };
  try {
    const data = await loadModelDefaults();
    assert.equal(calls.length, 1);
    assert.equal(calls[0], '/api/model-defaults');
    assert.equal(data.defaultProvider, 'claude');
    assert.deepEqual(data.providers.claude, {
      url: 'https://api.anthropic.com',
      model: 'claude-3-5-sonnet-20241022',
      placeholder: 'sk-ant-…',
    });
    assert.deepEqual(data.modelOptions, [{ model: 'claude-opus-4-8', label: 'Opus 4.8' }]);

    // 第二次调用命中缓存，不再请求
    await loadModelDefaults();
    assert.equal(calls.length, 1);
  } finally {
    globalThis.fetch = realFetch;
    resetModelDefaultsCache();
  }
});

test('读取失败时抛出，由页面降级为空目录', async () => {
  resetModelDefaultsCache();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
  try {
    await assert.rejects(loadModelDefaults(), /HTTP 503/);
    assert.deepEqual(EMPTY_MODEL_DEFAULTS.providers, {});
  } finally {
    globalThis.fetch = realFetch;
    resetModelDefaultsCache();
  }
});
