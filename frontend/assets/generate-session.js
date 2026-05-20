// frontend/assets/generate-session.js
// SSE 订阅 + token 分发 + 生成状态机
// 通过 CustomEvent 向外广播，不直接操作 DOM

export class GenerateSession {
  constructor(projectId) {
    this.projectId = projectId;
    this._reader = null;
    this._running = false;
  }

  get running() { return this._running; }

  start() {
    if (this._running) return;
    this._running = true;
    this._startFetch();
  }

  stop() {
    this._running = false;
    if (this._reader) this._reader.cancel();
  }

  async _startFetch() {
    try {
      const resp = await fetch(`/api/projects/${this.projectId}/generate-all`, { method: "POST" });
      if (!resp.ok) { this._running = false; return; }

      this._reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (this._running) {
        const { done, value } = await this._reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        this._processLines(lines);
      }
    } finally {
      this._running = false;
      window.dispatchEvent(new CustomEvent("session:done"));
    }
  }

  _processLines(lines) {
    let eventName = "", dataStr = "";
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataStr = line.slice(5).trim();
      else if (line === "" && eventName) {
        try {
          const data = JSON.parse(dataStr);
          this._dispatch(eventName, data);
        } catch (_) {}
        eventName = ""; dataStr = "";
      }
    }
  }

  _dispatch(eventName, data) {
    const map = {
      generate_start: "session:start",
      generate_block_start: "block:start",
      generate_block_token: "block:token",
      generate_block_done: "block:done",
      generate_done: "session:done",
      error: "block:error",
    };
    const name = map[eventName];
    if (name) {
      window.dispatchEvent(new CustomEvent(name, { detail: data }));
      if (eventName === "generate_done") this._running = false;
    }
  }
}
