// 目录结构编辑纯函数的用例（闸门 1：增删章节 / 上下移动 / 缩进）。
//
// 结构 = 有序数组 [{id, level, title}]，层级由顺序推导。这几个函数是「用户在目录树
// 上点按钮」的全部逻辑，DOM 只是渲染 —— 所以这里覆盖住了，交互就不会把目录改坏。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  newOutlineId,
  outlineSubtreeRange,
  moveOutlineNode,
  indentOutlineNode,
  outdentOutlineNode,
  removeOutlineSubtree,
  insertSiblingAfter,
  insertChildOf,
  moveOutlineNodeTo,
  moveOutlineNodeToLevel,
  moveOutlineNodesToLevel,
  levelRangeAt,
  moveOutlineNodesTo,
  moveManyOutlineNodes,
  indentManyOutlineNodes,
  outdentManyOutlineNodes,
} from '../../frontend/assets/workspace-model.mjs';

const N = (id, level, title = id) => ({id, level, title});
// a / a1 / a1x / b / b1  —— a 有两层子级，b 有一个子级
const tree = () => [N('a', 1), N('a1', 2), N('a1x', 3), N('b', 1), N('b1', 2)];
const ids = (nodes) => nodes.map(n => n.id);
// 同级平铺的小树（批量用例用）：flat(['s1','s2']) → 三个 1 级节点
const flat = (list) => list.map((id) => ({ id, level: 1, title: id }));
const levels = (nodes) => nodes.map(n => n.level);

// ── id 生成 ──
test('newOutlineId 不与已有 id 冲突', () => {
  const existing = ['n1', 'n2', 'n3'];
  for (let i = 0; i < 50; i++) assert.ok(!existing.includes(newOutlineId(existing)));
});

test('newOutlineId 在随机空间耗尽时仍给唯一 id', () => {
  const existing = Array.from({length: 200}, (_, i) => 'n' + (i + 1));
  const id = newOutlineId(existing);
  assert.ok(!existing.includes(id));
});

// ── 子树范围 ──
test('outlineSubtreeRange 覆盖整棵子树', () => {
  assert.deepEqual(outlineSubtreeRange(tree(), 'a'), [0, 3]);
  assert.deepEqual(outlineSubtreeRange(tree(), 'a1'), [1, 3]);
  assert.deepEqual(outlineSubtreeRange(tree(), 'b1'), [4, 5]);
  assert.deepEqual(outlineSubtreeRange(tree(), 'nope'), [-1, -1]);
});

// ── 上移 / 下移（带子树）──
test('上移把整棵子树移到上一个同级之前', () => {
  const out = moveOutlineNode(tree(), 'b', -1);
  assert.deepEqual(ids(out), ['b', 'b1', 'a', 'a1', 'a1x']);
  assert.deepEqual(levels(out), [1, 2, 1, 2, 3]);
});

test('下移把整棵子树移到下一个同级之后', () => {
  const out = moveOutlineNode(tree(), 'a', +1);
  assert.deepEqual(ids(out), ['b', 'b1', 'a', 'a1', 'a1x']);
});

test('同级之间上下移动：子级跟着父级走，不会掉到别人家里', () => {
  const nodes = [N('a', 1), N('a1', 2), N('b', 1), N('b1', 2), N('b2', 2)];
  const out = moveOutlineNode(nodes, 'b', -1);
  assert.deepEqual(ids(out), ['b', 'b1', 'b2', 'a', 'a1']);
  assert.deepEqual(levels(out), [1, 2, 2, 1, 2]);
});

test('已是第一个 / 最后一个同级时返回 null（空操作不发请求）', () => {
  assert.equal(moveOutlineNode(tree(), 'a', -1), null);
  assert.equal(moveOutlineNode(tree(), 'b', +1), null);
});

test('没有同级邻居的子节点不能上移（它已是第一个子级）', () => {
  const nodes = [N('a', 1), N('a1', 2), N('a2', 2)];
  assert.equal(moveOutlineNode(nodes, 'a1', -1), null);
  assert.deepEqual(ids(moveOutlineNode(nodes, 'a2', -1)), ['a', 'a2', 'a1']);
});

test('未知 id 返回 null', () => {
  assert.equal(moveOutlineNode(tree(), 'zzz', -1), null);
});

// ── 缩进 / 取消缩进 ──
test('缩进：成为上一个同级章节的子级，整棵子树一起降', () => {
  const nodes = [N('a', 1), N('b', 1), N('b1', 2)];
  const out = indentOutlineNode(nodes, 'b');
  assert.deepEqual(ids(out), ['a', 'b', 'b1']);
  assert.deepEqual(levels(out), [1, 2, 3]);
});

test('找不到前驱章节时不能缩进', () => {
  assert.equal(indentOutlineNode(tree(), 'a'), null);
});

test('上一个节点不是同级时不能缩进（避免钻到别人子树里）', () => {
  // a(1) → a1(2)：a1 的前驱 a 是上级，不是同级 → 不能缩进
  const nodes = [N('a', 1), N('a1', 2), N('a1x', 3)];
  assert.equal(indentOutlineNode(nodes, 'a1'), null);
  assert.equal(indentOutlineNode(nodes, 'a1x'), null);
  // 同级前驱则允许
  const siblings = [N('a', 1), N('a1', 2), N('a2', 2)];
  assert.deepEqual(levels(indentOutlineNode(siblings, 'a2')), [1, 2, 3]);
});

test('4 级节点不能再缩进', () => {
  const nodes = [N('x', 1), N('y', 2), N('z', 3), N('w', 4), N('v', 4)];
  assert.equal(indentOutlineNode(nodes, 'v'), null);   // 前驱同级，但已到最深
});

test('取消缩进：整棵子树一起升，一级节点无操作', () => {
  const nodes = [N('a', 1), N('b', 2), N('b1', 3)];
  const out = outdentOutlineNode(nodes, 'b');
  assert.deepEqual(levels(out), [1, 1, 2]);
  assert.equal(outdentOutlineNode(nodes, 'a'), null);
});

// ── 删除 ──
test('删除节点会连带删掉整棵子树', () => {
  const {nodes: out, removed} = removeOutlineSubtree(tree(), 'a');
  assert.equal(removed, 3);
  assert.deepEqual(ids(out), ['b', 'b1']);
});

test('删除叶子只删自己', () => {
  const {nodes: out, removed} = removeOutlineSubtree(tree(), 'b1');
  assert.equal(removed, 1);
  assert.deepEqual(ids(out), ['a', 'a1', 'a1x', 'b']);
});

test('删除未知 id 原样返回', () => {
  const before = tree();
  const {nodes: out, removed} = removeOutlineSubtree(before, 'zzz');
  assert.equal(removed, 0);
  assert.deepEqual(out, before);
});

// ── 新增 ──
test('新增同级：插在节点子树之后，层级与参照节点一致', () => {
  const out = insertSiblingAfter(tree(), 'a1', N('new', 1, '新章节'));
  assert.deepEqual(ids(out), ['a', 'a1', 'a1x', 'new', 'b', 'b1']);
  assert.equal(out.find(n => n.id === 'new').level, 2);
});

test('新增子级：插在子树末尾，层级 +1', () => {
  const out = insertChildOf(tree(), 'a', N('new', 1, '新章节'));
  assert.deepEqual(ids(out), ['a', 'a1', 'a1x', 'new', 'b', 'b1']);
  assert.equal(out.find(n => n.id === 'new').level, 2);
  assert.deepEqual(levels(out), [1, 2, 3, 2, 1, 2]);
});

test('4 级节点不能再加子级', () => {
  const nodes = [N('a', 1), N('b', 4)];
  assert.equal(insertChildOf(nodes, 'b', N('new', 1)), null);
});

test('新增子级到已有子级之后（不会插到别的章节里）', () => {
  const nodes = [N('a', 1), N('a1', 2), N('a1x', 3), N('a2', 2), N('b', 1)];
  const out = insertChildOf(nodes, 'a1', N('new', 1));
  assert.deepEqual(ids(out), ['a', 'a1', 'a1x', 'new', 'a2', 'b']);
  assert.equal(out.find(n => n.id === 'new').level, 3);
});

// ── 批量操作（目录调整：批量上移/下移/批量缩进/减少缩进）──
// 语义与单节点一致（逐个复用单节点函数），这里守三件事：
//   ① 一批同级节点整体挪一格（不互相插队）；② 子树跟着走；③ 动不了的项如实计入 skipped。
test('批量上移：一批同级节点整体上移一格', () => {
  const nodes = flat(['s1', 's2', 's3', 's4']);
  const r = moveManyOutlineNodes(nodes, ['s2', 's3'], -1);
  // s2/s3 整体挪到 s1 前面
  assert.deepEqual(ids(r.nodes), ['s2', 's3', 's1', 's4']);
  assert.equal(r.moved, 2);
  assert.equal(r.skipped, 0);
});

test('批量下移：一批同级节点整体下移一格', () => {
  const nodes = flat(['s1', 's2', 's3', 's4']);
  const r = moveManyOutlineNodes(nodes, ['s2', 's3'], +1);
  assert.deepEqual(ids(r.nodes), ['s1', 's4', 's2', 's3']);
  assert.equal(r.moved, 2);
});

test('批量上移：已是第一个同级的项计入 skipped（如实说明，不假装成功）', () => {
  const nodes = flat(['s1', 's2', 's3']);
  const r = moveManyOutlineNodes(nodes, ['s1', 's3'], -1);
  assert.equal(r.moved, 1);      // 只有 s3 能动
  assert.equal(r.skipped, 1);    // s1 已是第一个同级
  assert.deepEqual(ids(r.nodes), ['s1', 's3', 's2']);
});

test('批量移动：子树跟着父节点一起走', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's2', level: 1, title: 'B' },
    { id: 's2.1', level: 2, title: 'B-1' },
    { id: 's3', level: 1, title: 'C' },
  ];
  const r = moveManyOutlineNodes(nodes, ['s2'], -1);
  assert.deepEqual(ids(r.nodes), ['s2', 's2.1', 's1', 's3']);
});

test('批量缩进：一批同级节点**整批**降一级（关键：不能只降第一个）', () => {
  const nodes = flat(['s1', 's2', 's3', 's4']);
  const r = indentManyOutlineNodes(nodes, ['s2', 's3', 's4']);
  assert.deepEqual(levels(r.nodes), [1, 2, 2, 2]);   // 三个都成 s1 的子级
  assert.equal(r.moved, 3);
  assert.equal(r.skipped, 0);
});

test('批量缩进：这批的头没有同级前驱 → 整批都降不了（如实计 skipped）', () => {
  const nodes = flat(['s1', 's2', 's3']);
  const r = indentManyOutlineNodes(nodes, ['s1', 's2', 's3']);
  assert.equal(r.moved, 0);
  assert.equal(r.skipped, 3);
  assert.deepEqual(levels(r.nodes), [1, 1, 1]);
});

test('批量减少缩进：一起升一级', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's2', level: 2, title: 'B' },
    { id: 's3', level: 2, title: 'C' },
  ];
  const r = outdentManyOutlineNodes(nodes, ['s2', 's3']);
  assert.deepEqual(levels(r.nodes), [1, 1, 1]);
  assert.equal(r.moved, 2);
});

test('批量操作：空选择不改变任何东西', () => {
  const nodes = flat(['s1', 's2']);
  const r = moveManyOutlineNodes(nodes, [], -1);
  assert.deepEqual(ids(r.nodes), ['s1', 's2']);
  assert.equal(r.moved, 0);
});

// ── 拖动排序（上下拖动）──
// 规则：拖到谁旁边就成为它的同级（落点层级 = 插入点后一个节点的层级）；连带整棵子树。
test('拖动：把一节拖到另一节之前（成为它的同级）', () => {
  const nodes = flat(['s1', 's2', 's3']);
  const r = moveOutlineNodeTo(nodes, 's3', 0);
  assert.deepEqual(ids(r), ['s3', 's1', 's2']);
});

test('拖动：连带子树一起走', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's1.1', level: 2, title: 'A-1' },
    { id: 's2', level: 1, title: 'B' },
  ];
  const r = moveOutlineNodeTo(nodes, 's1', 3);      // 拖到最后
  assert.deepEqual(ids(r), ['s2', 's1', 's1.1']);
});

test('拖动：落点层级跟随后面的节点（拖到二级章节前 → 也变二级）', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's1.1', level: 2, title: 'A-1' },
    { id: 's1.2', level: 2, title: 'A-2' },
    { id: 's2', level: 1, title: 'B' },
  ];
  const r = moveOutlineNodeTo(nodes, 's2', 1);      // 插到 s1.1 之前 → 同级 2
  assert.deepEqual(ids(r), ['s1', 's2', 's1.1', 's1.2']);
  assert.equal(r[1].level, 2);
});

test('拖动：不能把自己塞进自己的子树（返回 null）', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's1.1', level: 2, title: 'A-1' },
    { id: 's2', level: 1, title: 'B' },
  ];
  assert.equal(moveOutlineNodeTo(nodes, 's1', 2), null);   // 2 落在 s1 子树内
});

test('拖动：原位不动返回 null（不写库）', () => {
  const nodes = flat(['s1', 's2']);
  assert.equal(moveOutlineNodeTo(nodes, 's1', 0), null);
});

test('拖动（整组）：一批节点作为一块移动，组内顺序不变', () => {
  const nodes = flat(['s1', 's2', 's3', 's4']);
  const r = moveOutlineNodesTo(nodes, ['s2', 's3'], 4);   // 拖到末尾
  assert.deepEqual(ids(r), ['s1', 's4', 's2', 's3']);
});

test('拖动（整组）：组内含父子也不重复搬（各子树只搬一次）', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's2', level: 1, title: 'B' },
    { id: 's2.1', level: 2, title: 'B-1' },
    { id: 's3', level: 1, title: 'C' },
  ];
  const r = moveOutlineNodesTo(nodes, ['s2', 's2.1'], 0);   // 父子都选中
  assert.deepEqual(ids(r), ['s2', 's2.1', 's1', 's3']);
});

// ── Notion 式拖动：水平位置决定层级 ──
test('落点层级范围：插到最顶只能是 1 级', () => {
  const nodes = flat(['s1', 's2']);
  assert.deepEqual(levelRangeAt(nodes, 0), { min: 1, max: 1, natural: 1 });
});

test('落点层级范围：最深是"前一个节点的层级 + 1"', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's1.1', level: 2, title: 'A-1' },
    { id: 's2', level: 1, title: 'B' },
  ];
  // 插到 s2 之前：前一个是 s1.1(2 级) → 最深 3 级；后一个是 s2(1 级) → 默认 1 级
  assert.deepEqual(levelRangeAt(nodes, 2), { min: 1, max: 3, natural: 1 });
});

test('Notion 式拖动：往右拖一格 → 成为子级', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's2', level: 1, title: 'B' },
    { id: 's3', level: 1, title: 'C' },
  ];
  const r = moveOutlineNodeToLevel(nodes, 's3', 1, 2);   // 拖到 s2 之前 + 右移一格
  assert.deepEqual(ids(r), ['s1', 's3', 's2']);
  assert.equal(r[1].level, 2);                            // s3 成为 s1 的子级
});

test('Notion 式拖动：往左拖 → 变成上级（与目标同级）', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's1.1', level: 2, title: 'A-1' },
    { id: 's1.2', level: 2, title: 'A-2' },
  ];
  const r = moveOutlineNodeToLevel(nodes, 's1.2', 1, 1);  // 往左拖到 1 级
  assert.equal(r[1].level, 1);
  assert.deepEqual(ids(r), ['s1', 's1.2', 's1.1']);
});

test('Notion 式拖动：连带子树一起改层级', () => {
  const nodes = [
    { id: 's1', level: 1, title: 'A' },
    { id: 's2', level: 1, title: 'B' },
    { id: 's2.1', level: 2, title: 'B-1' },
  ];
  const r = moveOutlineNodeToLevel(nodes, 's2', 0, 2);    // 拖到最顶 + 降一级
  assert.deepEqual(ids(r), ['s2', 's2.1', 's1']);
  assert.deepEqual(r.map(n => n.level), [2, 3, 1]);       // 子树跟着降
});

test('Notion 式拖动：原位 + 同层级 → null（不写库）', () => {
  const nodes = flat(['s1', 's2']);
  assert.equal(moveOutlineNodeToLevel(nodes, 's1', 0, 1), null);
});

// 契约：这几个函数返回 {nodes, …} **对象**，不是数组。
// 踩过一次：面板把 removeOutlineSubtree 的返回值当数组传给保存函数，
// Array.isArray 判 false 后静默返回 —— 界面上"点了确认但什么都没发生"。
test('返回 {nodes, …} 的函数：形状固定（调用方必须解构 .nodes）', () => {
  const rm = removeOutlineSubtree(flat(['s1', 's2']), 's1');
  assert.ok(Array.isArray(rm.nodes), 'removeOutlineSubtree.nodes 必须是数组');
  assert.equal(typeof rm.removed, 'number');
  assert.deepEqual(ids(rm.nodes), ['s2']);

  const mv = moveManyOutlineNodes(flat(['s1', 's2']), ['s2'], -1);
  assert.ok(Array.isArray(mv.nodes), 'moveManyOutlineNodes.nodes 必须是数组');
  assert.equal(typeof mv.moved, 'number');

  const ind = indentManyOutlineNodes(flat(['s1', 's2']), ['s2']);
  assert.ok(Array.isArray(ind.nodes));
  const out = outdentManyOutlineNodes(flat(['s1', 's2']), ['s1']);
  assert.ok(Array.isArray(out.nodes));
});
