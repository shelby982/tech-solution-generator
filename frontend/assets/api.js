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
    upload: (projectId, file, role = 'requirement') => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('material_role', role);
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
    mapSources: (projectId) =>
      fetch(`/api/projects/${projectId}/map-sources`, { method: 'POST' }).then(r => r.json()),
  },

  blocks: {
    list: (projectId) =>
      fetch(`/api/projects/${projectId}/blocks`).then(r => r.json()),
    clear: (projectId) =>
      fetch(`/api/projects/${projectId}/blocks`, { method: 'DELETE' }).then(r => r.json()),
    update: (id, content) =>
      fetch(`/api/blocks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }).then(r => r.json()),
    ai: (id, action) =>
      fetch(`/api/blocks/${id}/ai`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      }).then(r => r.json()),
    updateRequirement: (blockId, requirement) =>
      fetch(`/api/blocks/${blockId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requirement }),
      }).then(r => r.json()),
  },

  revisions: {
    list: (blockId) =>
      fetch(`/api/blocks/${blockId}/revisions`).then(r => r.json()),
    restore: (blockId, rn) =>
      fetch(`/api/blocks/${blockId}/revisions/${rn}/restore`, {
        method: 'POST',
      }).then(r => r.json()),
    listByProject: (projectId) =>
      fetch(`/api/projects/${projectId}/revisions`).then(r => r.json()),
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

  workflow: {
    start: (projectId, config = {}) =>
      fetch('/api/workflow/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, config }),
      }).then(r => r.json()),

    resume: (threadId, userChoice = '', edits = {}) =>
      fetch(`/api/workflow/${threadId}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_choice: userChoice, edits }),
      }).then(r => r.json()),

    regen: (threadId, blockIds) =>
      fetch(`/api/workflow/${threadId}/regen`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ block_ids: blockIds }),
      }).then(r => r.json()),

    abort: (threadId) =>
      fetch(`/api/workflow/${threadId}/abort`, { method: 'POST' }).then(r => r.json()),

    pause: (threadId) =>
      fetch(`/api/workflow/${threadId}/pause`, { method: 'POST' }).then(r => r.json()),

    skipToReview: (threadId) =>
      fetch(`/api/workflow/${threadId}/skip-to-review`, { method: 'POST' }).then(r => r.json()),

    resumeGeneration: (threadId) =>
      fetch(`/api/workflow/${threadId}/resume-generation`, { method: 'POST' }).then(r => r.json()),

    rerunMatch: (threadId) =>
      fetch(`/api/workflow/${threadId}/rerun-match`, { method: 'POST' }).then(r => r.json()),

    recover: (threadId) =>
      fetch(`/api/workflow/${threadId}/recover`, { method: 'POST' }).then(r => r.json()),

    state: (threadId) =>
      fetch(`/api/workflow/${threadId}/state`).then(r => r.json()),

    // SSE — 用 EventSource。返回 EventSource，调用方可主动 .close()
    stream: (threadId, handlers = {}) => {
      const es = new EventSource(`/api/workflow/${threadId}/stream`);
      const wire = (eventName, handler) => {
        if (!handler) return;
        es.addEventListener(eventName, (ev) => {
          let data = {};
          try { data = JSON.parse(ev.data); } catch {}
          handler(data);
        });
      };
      wire('stage_change',     handlers.onStageChange);
      wire('parse_progress',   handlers.onParseProgress);
      wire('outline_extract_start', handlers.onOutlineExtractStart);
      wire('outline_extract',  handlers.onOutlineExtract);
      wire('match_start',      handlers.onMatchStart);
      wire('match_progress',   handlers.onMatchProgress);
      wire('block_start',      handlers.onBlockStart);
      wire('block_token',      handlers.onBlockToken);
      wire('block_done',       handlers.onBlockDone);
      wire('review_block_start', handlers.onReviewBlockStart);
      wire('review_finding',   handlers.onReviewFinding);
      wire('report_ready',     handlers.onReportReady);
      wire('gate_open',        handlers.onGateOpen);
      wire('error',            handlers.onError);
      wire('checkpoint',       handlers.onCheckpoint);
      wire('done',             handlers.onDone);
      wire('aborted',          handlers.onAborted);
      wire('paused',           handlers.onPaused);
      return es;
    },
  },

  review: {
    get: (threadId) =>
      fetch(`/api/review/${threadId}`).then(r => r.json()),
  },
};
