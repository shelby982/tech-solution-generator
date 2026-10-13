import test from 'node:test';
import assert from 'node:assert/strict';
import {
  REQUIREMENT_ROLES, SOURCE_ROLES, ALL_MATERIAL_ROLES,
  ROLE_LABEL, ROLE_HINT, isRequirementRole,
  inferRoleFromFilename, roleMismatchWarning,
} from '../../frontend/assets/material-roles.mjs';

// 背景：角色决定这份文件进哪条链路 ——
//   要求类（spec/main_rfp/scoring/evaluation/requirement）→ 解析→提炼→目录→评分覆盖
//   素材类（source）→ 只被检索匹配
// 实测踩到：技术规范书（招标人随招标文件发出的附件「技术招标文件」）被传成 source，
// 于是**没进要求链路**，既没结构化也没参与目录规划。下面锁住这个语义。

test('spec 归入要求类，语义是招标人给的技术规范书', () => {
  assert.ok(REQUIREMENT_ROLES.includes('spec'));
  assert.ok(!SOURCE_ROLES.includes('spec'));
  assert.equal(isRequirementRole('spec'), true);
  assert.equal(isRequirementRole('source'), false);
  // 标签写清是什么文件，不用含糊的"规范书（投标文件）"（那是反的）
  assert.equal(ROLE_LABEL.spec, '技术规范书');
  assert.match(ROLE_HINT.spec, /技术规范书|技术招标文件/);
  assert.match(ROLE_HINT.source, /不参与要求提炼/);
});

test('角色清单自洽：两类互斥且合起来覆盖白名单', () => {
  const overlap = REQUIREMENT_ROLES.filter(r => SOURCE_ROLES.includes(r));
  assert.deepEqual(overlap, []);
  assert.deepEqual([...ALL_MATERIAL_ROLES].sort(),
    ['evaluation', 'main_rfp', 'requirement', 'scoring', 'source', 'spec']);
  for (const r of ALL_MATERIAL_ROLES) {
    assert.ok(ROLE_LABEL[r], `缺标签：${r}`);
    assert.ok(ROLE_HINT[r], `缺说明：${r}`);
  }
});

test('按文件名识别技术规范书', () => {
  for (const name of [
    '4_高可靠技术专题研究与验证-技术招标文件.docx',
    '技术规范书.pdf',
    'XX项目技术需求书.docx',
    '工作说明书.docx',
  ]) {
    const got = inferRoleFromFilename(name);
    assert.ok(got, `未识别：${name}`);
    assert.equal(got.role, 'spec');
  }
  // 我方素材文件名不该被误判
  for (const name of ['产品规格书.pdf', '架构方案.pptx', '客户案例清单.docx', '']) {
    assert.equal(inferRoleFromFilename(name), null, `误判：${name}`);
  }
});

test('技术规范书被归为素材时给出纠错提示', () => {
  const warn = roleMismatchWarning('source', '4_高可靠技术专题研究与验证-技术招标文件.docx');
  assert.ok(warn, '应给出提示');
  assert.match(warn, /技术规范书/);
  assert.match(warn, /不会参与要求提炼/);   // 说清后果
  assert.match(warn, /建议改选/);

  // 已经选对 → 不打扰
  assert.equal(roleMismatchWarning('spec', '4_可靠-技术招标文件.docx'), null);
  // 我方素材文件名 → 不提示
  assert.equal(roleMismatchWarning('source', '产品规格书.pdf'), null);
  // 归到其他要求类（不是 source）→ 当前只提示 source 这一种真实坑
  assert.equal(roleMismatchWarning('scoring', '技术规范书.pdf'), null);
});

test('提示文案本身不含 HTML（文件名由页面另行转义后拼接）', () => {
  // 提示框在页面上用 innerHTML 渲染，文件名可能带尖括号。
  // 本模块的职责是**只产出纯文本提示**（不回显文件名），
  // 文件名由 project-init.html 的 escapeHtmlForRole 转义后拼接。
  const warn = roleMismatchWarning('source', '<img>.技术规范书.docx');
  assert.ok(warn, '应给出提示');
  assert.ok(!warn.includes('<img>'), '模块不应把文件名回显进提示');
  assert.ok(!/[<>]/.test(warn), '提示文案里不应出现尖括号');
});

// ── 界面只给三类可选（用户要求：招标文件 / 技术规范书 / 其他）──────────
// 语义全集 REQUIREMENT_ROLES 仍保留 scoring/evaluation：那是**历史数据里已存在**的
// 分类，从语义集合里删掉会让那些材料掉出要求链路（不再参与解析/目录）。
test('界面可选的要求类只有三类，且语义全集仍覆盖历史分类', async () => {
  const { SELECTABLE_REQUIREMENT_ROLES, REQUIREMENT_ROLES, ROLE_LABEL } = await import(
    '../../frontend/assets/material-roles.mjs');
  assert.deepEqual(SELECTABLE_REQUIREMENT_ROLES, ['main_rfp', 'spec', 'requirement']);
  assert.deepEqual(SELECTABLE_REQUIREMENT_ROLES.map(r => ROLE_LABEL[r]),
    ['招标文件', '技术规范书', '其他']);
  // 可选项必须是语义全集的子集；历史分类仍在全集里
  for (const r of SELECTABLE_REQUIREMENT_ROLES) assert.ok(REQUIREMENT_ROLES.includes(r));
  for (const r of ['scoring', 'evaluation']) assert.ok(REQUIREMENT_ROLES.includes(r));
  assert.equal(ROLE_LABEL.requirement, '其他');
  assert.equal(ROLE_LABEL.main_rfp, '招标文件');
});
