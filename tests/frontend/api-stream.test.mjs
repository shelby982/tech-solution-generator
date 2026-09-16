// SSE 两类 'error' 的分流：浏览器自己的连接失败（Event，无 data）必须走后端错误帧之外的
// 通道，否则「服务重启后 thread 不在 runner 内存里」会被显示成「工作流出错」，看不到原因。
import test from 'node:test';
import assert from 'node:assert/strict';

class FakeEventSource {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  close() { this.closed = true; }
  emit(type, ev) { for (const fn of this.listeners[type] || []) fn(ev); }
}
globalThis.EventSource = FakeEventSource;

const {api} = await import('../../frontend/assets/api.js');

const wireUp = () => {
  const seen = {dropped: 0, wfErrors: [], stages: []};
  const es = api.workflow.stream('tid-1', {
    onStreamError: () => { seen.dropped++; },
    onError: (data) => { seen.wfErrors.push(data); },
    onStageChange: (data) => { seen.stages.push(data); },
  });
  return {seen, es};
};

test('native connection failure goes to onStreamError, not the workflow error channel', () => {
  const {seen, es} = wireUp();
  es.emit('error', new Event('error')); // 浏览器连接失败：Event，没有 data
  assert.equal(seen.dropped, 1);
  assert.deepEqual(seen.wfErrors, []);
});

test('backend error frame keeps its message on the workflow error channel', () => {
  const {seen, es} = wireUp();
  es.emit('error', new MessageEvent('error', {data: JSON.stringify({message: '模型超时', retryable: false})}));
  assert.deepEqual(seen.wfErrors, [{message: '模型超时', retryable: false}]);
  assert.equal(seen.dropped, 0);
});

test('malformed error frame degrades to an empty payload instead of throwing', () => {
  const {seen, es} = wireUp();
  es.emit('error', new MessageEvent('error', {data: 'not json'}));
  assert.deepEqual(seen.wfErrors, [{}]);
  assert.equal(seen.dropped, 0);
});

test('named events still reach their handlers', () => {
  const {seen, es} = wireUp();
  es.emit('stage_change', new MessageEvent('stage_change', {data: JSON.stringify({stage: 'outline_review'})}));
  assert.deepEqual(seen.stages, [{stage: 'outline_review'}]);
});
