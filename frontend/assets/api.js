// frontend/assets/api.js
// ES Module — 统一 API 封装层，供各页面 import { api } from './api.js?v=20261016-v373' 使用

// workflow 动作的统一调用：非 2xx 时抛出**带 code 的** Error。
// code 来自后端响应头 X-Error-Code（如 workflow_aborted）—— 前端据此决定
// 「切到最近一次运行 / 新建一次」这类恢复动作，而不是把用户丢在一个死线程上。
async function workflowAction(url, body, fallbackMessage) {
  const options = { method: 'POST' };
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data?.detail || `${fallbackMessage}（HTTP ${response.status}）`);
    error.status = response.status;
    error.code = response.headers.get('X-Error-Code')
      || (response.status === 404 ? 'unknown_thread' : '');
    throw error;
  }
  return data;
}

/** 统一解析普通 JSON API；任何非 2xx 都进入调用方的 catch。 */
async function parseJsonResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data?.detail || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    error.code = response.headers.get('X-Error-Code') || '';
    error.payload = data;
    throw error;
  }
  return data;
}

export const api = {
  projects: {
    intake: (projectId) =>
      fetch(`/api/projects/${projectId}/intake`).then(parseJsonResponse),
    identifyPackages: (projectId) =>
      fetch(`/api/projects/${projectId}/packages/identify`, { method: 'POST' }).then(parseJsonResponse),
    createPackage: (projectId, payload) =>
      fetch(`/api/projects/${projectId}/packages`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(parseJsonResponse),
    confirmPackage: (projectId, packageId, payload = {}) =>
      fetch(`/api/projects/${projectId}/packages/${packageId}/confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(parseJsonResponse),
    packageWorkspace: (projectId, packageId) =>
      fetch(`/api/projects/${projectId}/packages/${packageId}/workspace`).then(parseJsonResponse),
    startPackageRun: (projectId, packageId, config = {}) =>
      fetch(`/api/projects/${projectId}/packages/${packageId}/runs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
      }).then(parseJsonResponse),
    // 标包分析：从招标文件正文抽出的标包事实（含原文出处）
    packageAnalysis: (projectId, q = '') =>
      fetch(`/api/projects/${projectId}/package-analysis${q}`).then(parseJsonResponse),
    // 素材完整性：文件丢失 / 解析完成却没有正文
    materialsIntegrity: (projectId) =>
      fetch(`/api/projects/${projectId}/materials/integrity`).then(parseJsonResponse),
    // 某份文件的标包归属（多对多；可空 = 未指定）
    setMaterialPackages: (projectId, materialId, packages) =>
      fetch(`/api/projects/${projectId}/materials/${materialId}/packages`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ packages: packages || [] }),
      }).then(parseJsonResponse),
    /** 按标包列出各应答类型的目录草案：(标包, 类型) → 草案线程（应答策划的切换依据） */
    responseDrafts: (projectId, packageId) =>
      fetch(`/api/projects/${projectId}/response-drafts?packageId=${encodeURIComponent(packageId)}`).then(parseJsonResponse),
    // 一键对齐：把解析出来的评分点按已确认范围/类别落库（不调用模型）
    syncPoints: (projectId, payload) =>
      fetch(`/api/projects/${projectId}/sync-points`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }).then(parseJsonResponse),
    list: (status) =>
      fetch(`/api/projects${status ? '?status=' + status : ''}`).then(parseJsonResponse),
    create: (data) =>
      fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then(parseJsonResponse),
    get: (id) =>
      fetch(`/api/projects/${id}`).then(parseJsonResponse),
    /** 清空该项目的拆解结果（评分点/规范要求/章节/评审/作答），保留材料与项目本身 */
    resetAnalysis: (id, packageId) =>
      fetch(`/api/projects/${id}/reset-analysis${packageId ? `?packageId=${encodeURIComponent(packageId)}` : ''}`, { method: 'POST' }).then(parseJsonResponse),
    patch: (id, data) =>
      fetch(`/api/projects/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then(parseJsonResponse),
    lock: (id) =>
      fetch(`/api/projects/${id}/lock`, { method: 'POST' }).then(parseJsonResponse),
  },

  materials: {
    upload: (projectId, file, role = 'requirement', { replace = false } = {}) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('material_role', role);
      // replace：同角色的旧文件先删掉（重新选择文件 = 替换，不让旧文件继续参与解析）
      fd.append('replace', replace ? 'true' : 'false');
      return fetch(`/api/projects/${projectId}/materials`, {
        method: 'POST', body: fd,
      }).then(parseJsonResponse);
    },
    list: (projectId) =>
      fetch(`/api/projects/${projectId}/materials`).then(parseJsonResponse),
    // 章节 → 应答要求条目（后端从 8 字段展开，零模型调用）。前端据此把依据按**条目**
    // 分组展示；拆分规则只在后端一份（domain/spec/response_items.py），避免两处漂移。
    responseItems: (projectId, threadId) =>
      fetch(`/api/projects/${projectId}/response-items${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(parseJsonResponse),
    // 匹配总览：章节 / 应答条目 / 依据 / 素材使用的聚合（后端零调用算，前端只渲染）。
    // 顶部总览条、章节树徽标、覆盖矩阵都读它 —— 数字只有一份来源，不会两处不一致。
    matchOverview: (projectId, threadId) =>
      fetch(`/api/projects/${projectId}/match-overview${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(parseJsonResponse),
    // 素材切片（"找素材"选择器的数据源）：只回我方 source 素材
    materialChunks: (projectId, { materialId, q, limit } = {}) => {
      const p = new URLSearchParams();
      if (materialId != null) p.set('materialId', materialId);
      if (q) p.set('q', q);
      if (limit != null) p.set('limit', limit);
      const qs = p.toString();
      return fetch(`/api/projects/${projectId}/material-chunks${qs ? `?${qs}` : ''}`).then(parseJsonResponse);
    },
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
      fetch(`/api/projects/${projectId}/map-sources`, { method: 'POST' }).then(parseJsonResponse),
  },

  blocks: {
    list: (projectId, threadId) =>
      fetch(`/api/projects/${projectId}/blocks${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(parseJsonResponse),
    clear: (projectId) =>
      fetch(`/api/projects/${projectId}/blocks`, { method: 'DELETE' }).then(parseJsonResponse),
    update: (id, content) =>
      fetch(`/api/blocks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }).then(parseJsonResponse),
    // 局部更新提炼字段（工作台的参考卡片：改一个字段不该清空别的）。
    // 与 update 的区别：body 直接就是字段字典，且**非 2xx 会抛**（422 非法枚举等
    // 必须让调用方看见，否则下拉框会显示成已保存）。
    updateFields: async (id, fields) => {
      const r = await fetch(`/api/blocks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data?.detail || `保存失败（HTTP ${r.status}）`);
      return data;
    },
    // 人工挂接 / 解绑 / 改挂载条目（写 blocks.source + checkpoint）。
    // 非 2xx 会抛：409（运行中/已作废）必须让调用方看见，否则用户以为挂上了。
    updateEvidence: async (id, { action, chunkId, itemIds, threadId } = {}) => {
      const r = await fetch(`/api/blocks/${id}/evidence`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action, chunk_id: chunkId, item_ids: itemIds ?? null, threadId: threadId ?? null,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const err = new Error(data?.detail || `素材挂接失败（HTTP ${r.status}）`);
        err.code = r.headers.get('X-Error-Code') || '';
        throw err;
      }
      return data;
    },
    ai: (id, action) =>
      fetch(`/api/blocks/${id}/ai`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      }).then(parseJsonResponse),
    updateRequirement: (blockId, requirement) =>
      fetch(`/api/blocks/${blockId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requirement }),
      }).then(parseJsonResponse),
  },

  revisions: {
    list: (blockId) =>
      fetch(`/api/blocks/${blockId}/revisions`).then(parseJsonResponse),
    restore: (blockId, rn) =>
      fetch(`/api/blocks/${blockId}/revisions/${rn}/restore`, {
        method: 'POST',
      }).then(parseJsonResponse),
    listByProject: (projectId, packageId) =>
      fetch(`/api/projects/${projectId}/revisions${packageId ? `?packageId=${encodeURIComponent(packageId)}` : ''}`).then(parseJsonResponse),
  },

  export: {
    diff: (projectId, packageId) =>
      fetch(`/api/projects/${projectId}/diff${packageId ? `?packageId=${encodeURIComponent(packageId)}` : ''}`).then(parseJsonResponse),
    applyDiff: (projectId, data, packageId) =>
      fetch(`/api/projects/${projectId}/diff/apply${packageId ? `?packageId=${encodeURIComponent(packageId)}` : ''}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then(parseJsonResponse),
    word: (projectId, packageId, threadId) => {
      const query = new URLSearchParams();
      if (packageId) query.set('packageId', packageId);
      if (threadId) query.set('threadId', threadId);
      window.open(`/api/projects/${projectId}/export?${query}`, '_blank');
    },
  },

  workflow: {
    start: (projectId, config = {}) =>
      fetch('/api/workflow/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, config }),
      }).then(parseJsonResponse),

    // 走 workflowAction：非 2xx 必须**抛出**。闸门续跑失败最典型的是后端 409
    // 「当前任务仍在运行」（同一 thread 已有后台 task），旧写法 `fetch().then(r=>r.json())`
    // 会把 409 当成功解析掉 —— 页面于是接着演"提炼中"，而后端一步都没跑。
    resume: (threadId, userChoice = '', edits = {}) =>
      workflowAction(`/api/workflow/${threadId}/resume`,
                     { user_choice: userChoice, edits },
                     '续跑失败'),

    regen: (threadId, blockIds) =>
      fetch(`/api/workflow/${threadId}/regen`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ block_ids: blockIds }),
      }).then(parseJsonResponse),

    abort: (threadId) =>
      fetch(`/api/workflow/${threadId}/abort`, { method: 'POST' }).then(parseJsonResponse),

    // 项目级投标范围（投标人视角）：我投哪个标的/标包
    getScope: (projectId) =>
      fetch(`/api/projects/${projectId}/scope`).then(parseJsonResponse),
    setScope: (projectId, payload) =>
      fetch(`/api/projects/${projectId}/scope`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(parseJsonResponse),
    // 拆分确认项的作答（落库后下次拆分生效）
    getSplitAnswers: (projectId, threadId) =>
      fetch(`/api/projects/${projectId}/split-answers${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(parseJsonResponse),
    answerSplit: (projectId, payload) =>
      fetch(`/api/projects/${projectId}/split-answers`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(parseJsonResponse),

    // 拆分报告：拆分依据（待确认项）+ 评分点/评分单元/技术要求矩阵/映射矩阵
    splitReport: (projectId, threadId) =>
      fetch(`/api/projects/${projectId}/split-report${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(parseJsonResponse),

    // 得分风险：把逐章审查结论按评分点聚合（审查对齐评分口径后的决策视图）
    scoringRisk: (projectId, threadId) =>
      fetch(`/api/projects/${projectId}/scoring-risk${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(parseJsonResponse),

    // 重出目录前的预检：原料变过 / 已过闸门时，后端会说明该做什么。
    // 前端拿它决定"重拆"还是"必须重新提炼"，避免点下去 200 却什么都没变。
    redraftCheck: (threadId) =>
      fetch(`/api/workflow/${threadId}/redraft-check`).then(parseJsonResponse),

    pause: (threadId) =>
      fetch(`/api/workflow/${threadId}/pause`, { method: 'POST' }).then(parseJsonResponse),

    skipToReview: (threadId) =>
      fetch(`/api/workflow/${threadId}/skip-to-review`, { method: 'POST' }).then(parseJsonResponse),

    resumeGeneration: (threadId) =>
      fetch(`/api/workflow/${threadId}/resume-generation`, { method: 'POST' }).then(parseJsonResponse),

    // 取消/暂停后接着提炼：复用同一条 run，跳过已完成的章节（不重新解析、不重新派生目录）
    resumeExtract: (threadId) =>
      workflowAction(`/api/workflow/${threadId}/resume-extract`, undefined, '继续提炼失败'),

    // 同上：409（已有任务在跑）要抛出来，前端才能提示而不是空转
    // full=false（默认）增量：只归属新素材；full=true 清空重匹配
    rerunMatch: (threadId, full = false) =>
      workflowAction(`/api/workflow/${threadId}/rerun-match${full ? '?full=true' : ''}`,
                     undefined, '触发匹配失败'),

    // 确认内容大纲及要求：把提炼结果上的编辑写回（blocks 表 + 图里的矩阵）
    confirmOutline: (threadId, matrix) =>
      fetch(`/api/workflow/${threadId}/confirm-outline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ matrix }),
      }).then(parseJsonResponse),

    // 整版重出应答目录：换一份提炼要求，跳过 parse 只重跑目录派生
    redraftOutline: (threadId, instruction = '') =>
      workflowAction(`/api/workflow/${threadId}/redraft-outline`, { instruction },
                     '重出目录失败'),

    // 闸门 1 的目录结构编辑（增删章节 / 改标题 / 上下移动 / 缩进）。
    // nodes 是**有序**列表，顺序即目录顺序；层级由顺序推导（没有 parent 字段）。
    updateOutlineStructure: (threadId, nodes, purge = false, removedIds = []) =>
      workflowAction(`/api/workflow/${threadId}/outline-structure`,
                     { nodes, purge, removed_ids: removedIds },
                     '目录保存失败'),

    recover: (threadId) =>
      fetch(`/api/workflow/${threadId}/recover`, { method: 'POST' }).then(parseJsonResponse),

    state: (threadId) =>
      fetch(`/api/workflow/${threadId}/state`).then(parseJsonResponse),

    // 项目最近一次 run 的 stage（读 workflow_runs 表）。
    // 与 /state 的关键区别：checkpoint 只在**节点结束时**才变，而这张表会被 runner
    // 在运行期更新（闸门 1 放行后先标 extracting、匹配前标 matching）。判断
    // 「提炼/匹配到底还在不在跑」只能看它。
    latestRunStage: async (projectId) => {
      const r = await fetch(`/api/projects/${projectId}/workflow-runs`);
      if (!r.ok) return null;
      const runs = await r.json().catch(() => []);
      return Array.isArray(runs) && runs.length ? (runs[0]?.stage || null) : null;
    },

    // 项目最近一次 run 的 thread_id。
    // 为什么需要：页面首次加载时 URL 往往没带 threadId、sessionStorage 也是空的，
    // 而 /blocks 接口会回退到"最新 run"（目录照样显示），于是依赖 tid 的面板
    // （覆盖率、目录未派生提示）会静默什么都不做 —— 实测就是这个坑。
    // 项目全部 run（含 stage）：用于避开已作废/失败的线程
    workflowRuns: async (projectId) => {
      const r = await fetch(`/api/projects/${projectId}/workflow-runs`);
      if (!r.ok) return [];
      const runs = await r.json().catch(() => []);
      return Array.isArray(runs) ? runs : [];
    },

    latestRunThreadId: async (projectId) => {
      const r = await fetch(`/api/projects/${projectId}/workflow-runs`);
      if (!r.ok) return null;
      const runs = await r.json().catch(() => []);
      if (!Array.isArray(runs) || !runs.length) return null;
      // 跳过已作废 / 失败的 run：作废的运行 checkpoint 可能停在中间态，
      // 页面跟过去只会显示"降级/半成品"数据（实测用户看到 25 个评分点混着商务报价）。
      const alive = runs.find(x => x.stage !== 'aborted' && x.stage !== 'failed');
      return (alive || runs[0])?.thread_id || null;
    },

    // SSE — 用 EventSource。返回 EventSource，调用方可主动 .close()
    stream: (threadId, handlers = {}) => {
      const es = new EventSource(`/api/workflow/${threadId}/stream`);
      const wire = (eventName, handler) => {
        if (!handler) return;
        es.addEventListener(eventName, (ev) => {
          // 浏览器自己的连接失败（404 / 断线 / 重连中）也是 'error' 事件，但它是 Event
          // 而不是 MessageEvent，没有 data。必须与后端发来的工作流错误帧分开处理，
          // 否则「服务重启后该 thread 不在 runner 内存里」会被显示成「工作流出错」，
          // 用户看不到任何原因。
          if (eventName === 'error' && !(ev instanceof MessageEvent)) {
            handlers.onStreamError?.(ev);
            return;
          }
          let data = {};
          try { data = JSON.parse(ev.data); } catch {}
          // 收到任何一帧都说明连接是好的：调用方用它重置"断流恢复额度"。
          // 只靠 stage_change 重置是不够的 —— 逐节提炼期间没有闸门事件，
          // 连着跑完两轮（每轮收尾后端都会主动关流）之后第三次关流就会被误判成
          // 「后端已重启」，弹出那条假的"事件流已断开"。
          handlers.onAnyEvent?.(eventName);
          handler(data);
        });
      };
      wire('stage_change',     handlers.onStageChange);
      wire('parse_progress',   handlers.onParseProgress);
      wire('outline_draft_start', handlers.onOutlineDraftStart);
      wire('outline_draft',    handlers.onOutlineDraft);
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
      wire('iteration_start',  handlers.onIterationStart);
      wire('gaps_collecting',  handlers.onGapsCollecting);
      wire('gaps_done',        handlers.onGapsDone);
      wire('convergence',      handlers.onConvergence);
      wire('feedback_ready',   handlers.onFeedbackReady);
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
      fetch(`/api/review/${threadId}`).then(parseJsonResponse),
  },
};
