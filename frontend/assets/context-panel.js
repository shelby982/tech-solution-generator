// frontend/assets/context-panel.js
// 右侧上下文面板：要求文本 + 匹配素材 + 原文溯源

export function initContextPanel() {
  const reqSection = document.querySelector("[data-panel-requirement]");
  const srcSection = document.querySelector("[data-panel-sources]");

  function renderRequirement(requirement) {
    if (!reqSection) return;
    if (!requirement) { reqSection.innerHTML = "<p class='panel-empty'>暂无应标要求</p>"; return; }
    const items = requirement.split(/[；;。\n]/).map(s => s.trim()).filter(Boolean);
    reqSection.innerHTML = items.map(s => `<p class="req-item">· ${s}</p>`).join("");
  }

  function renderSources(sources, materials) {
    if (!srcSection) return;
    if (!sources || sources.length === 0) {
      srcSection.innerHTML = "<p class='panel-empty'>暂无匹配素材</p>"; return;
    }
    srcSection.innerHTML = sources.map(s => {
      const matName = materials?.[s.material_id] ?? `素材 #${s.material_id}`;
      const snippet = s.snippet.replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return `
        <div class="source-card">
          <div class="source-meta">来自：${matName}</div>
          <p class="source-snippet">${snippet}</p>
          <button class="source-origin-btn" type="button"
            data-material-id="${s.material_id}"
            data-chunk-index="${s.chunk_index}">原文 →</button>
        </div>`;
    }).join("");

    srcSection.querySelectorAll(".source-origin-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const mid = btn.dataset.materialId;
        const cidx = btn.dataset.chunkIndex;
        const name = materials?.[mid] ?? `素材 #${mid}`;
        alert(`原文位置：${name}，第 ${cidx} 段`);
      });
    });
  }

  document.addEventListener("click", e => {
    const block = e.target.closest(".editor-block");
    if (!block) return;
    renderRequirement(block.dataset.requirement || "");
    try {
      const sources = JSON.parse(block.dataset.source || "[]");
      renderSources(sources, window.__materialsMap);
    } catch (_) {
      renderSources([], null);
    }
  });

  window.addEventListener("block:done", e => {
    const { block_id, sources } = e.detail;
    const blockEl = document.getElementById(`block-${block_id}`);
    if (blockEl) {
      blockEl.dataset.source = JSON.stringify(sources);
    }
  });
}
