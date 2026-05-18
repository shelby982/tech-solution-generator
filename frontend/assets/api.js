// frontend/assets/api.js
// ES Module — 统一 API 封装层，供各页面 import { api } from './api.js' 使用

export const api = {
  projects: {
    list: (status) =>
      fetch(`/api/projects${status ? '?status=' + status : ''}`).then(r => r.json()),
    create: (data) =>
      fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then(r => r.json()),
    get: (id) =>
      fetch(`/api/projects/${id}`).then(r => r.json()),
    patch: (id, data) =>
      fetch(`/api/projects/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then(r => r.json()),
    lock: (id) =>
      fetch(`/api/projects/${id}/lock`, { method: 'POST' }).then(r => r.json()),
  },

  materials: {
    upload: (projectId, file, role = 'main') => {
      const fd = new FormData();
      fd.append('file', file);
      return fetch(`/api/projects/${projectId}/materials`, {
        method: 'POST', body: fd,
      }).then(r => r.json());
    },
    list: (projectId) =>
      fetch(`/api/projects/${projectId}/materials`).then(r => r.json()),
    // outline 后端为 POST + SSE，使用 fetch + ReadableStream
    outline: async (projectId, { onBlock, onDone, onError } = {}) => {
      const resp = await fetch(`/api/projects/${projectId}/outline`, { method: 'POST' });
      if (!resp.ok || !resp.body) {
        onError?.(new Error(`outline 请求失败: ${resp.status}`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop(); // 保留未完成行
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try {
            const data = JSON.parse(raw);
            if (data.block_id) onBlock?.(data);
            if ('block_count' in data) onDone?.(data);
          } catch {}
        }
      }
      if (buf) onDone?.({});
    },
  },

  blocks: {
    list: (projectId) =>
      fetch(`/api/projects/${projectId}/blocks`).then(r => r.json()),
    update: (id, content) =>
      fetch(`/api/blocks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }).then(r => r.json()),
    generate: (id) =>
      new EventSource(`/api/blocks/${id}/generate`),
    ai: (id, action) =>
      fetch(`/api/blocks/${id}/ai`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      }).then(r => r.json()),
  },

  revisions: {
    list: (blockId) =>
      fetch(`/api/blocks/${blockId}/revisions`).then(r => r.json()),
    restore: (blockId, rn) =>
      fetch(`/api/blocks/${blockId}/revisions/${rn}/restore`, {
        method: 'POST',
      }).then(r => r.json()),
  },

  export: {
    diff: (projectId) =>
      fetch(`/api/projects/${projectId}/diff`).then(r => r.json()),
    applyDiff: (projectId, data) =>
      fetch(`/api/projects/${projectId}/diff/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then(r => r.json()),
    word: (projectId) =>
      window.open(`/api/projects/${projectId}/export`, '_blank'),
  },
};
