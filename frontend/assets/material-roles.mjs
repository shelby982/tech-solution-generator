/**
 * 材料角色体系（共享一份，页面与 node --test 都从这里取）。
 *
 * 为什么单独抽出来：角色决定了「这份文件进哪条链路」——
 *   要求类（spec / main_rfp / requirement / scoring / evaluation）→ 解析→提炼→目录→评分覆盖
 *   素材类（source）→ 只作为"我方技术素材"被检索匹配
 * 而 `spec` 的标签此前写作「规范书（投标文件）」——语义指向反了：技术规范书是**招标人**
 * 随招标文件发出的文件（招标文件原文：「二、技术规范书 详见招标文件附件"技术招标文件"」），
 * 不是投标文件。实测因此被传成 `source`（我方素材），既没有结构化，也没参与目录规划。
 *
 * 所以这里做三件事：
 *   1. 标签写清楚「谁给的、干什么用的」；
 *   2. 把「技术规范书」在要求类里**单列**（与"主招标文件"并列），而不是含糊地叫"规范书"；
 *   3. 提供 `inferRoleFromFilename()` —— 文件名明显是技术规范书/技术招标文件却传成 source 时，
 *      在界面上给出纠正提示（不静默改写用户的选择）。
 */

/** 要求类角色（决定评分与目录，走解析/提炼链路）。**语义全集**，勿删项：
 *  `scoring` / `evaluation` 是历史数据里已存在的分类，删掉会让那些材料掉出要求链路。 */
export const REQUIREMENT_ROLES = ['main_rfp', 'spec', 'scoring', 'evaluation', 'requirement'];

/** 界面上**可选的**要求类角色（用户要求只保留三类：招标文件 / 技术规范书 / 其他）。
 *  与上面的语义全集分开：老材料仍能正确归类显示，但新上传时不再让人面对五种选择。 */
export const SELECTABLE_REQUIREMENT_ROLES = ['main_rfp', 'spec', 'requirement'];

/** 我方素材角色（只参与检索匹配，不参与要求提炼）。 */
export const SOURCE_ROLES = ['source'];

export const ALL_MATERIAL_ROLES = [...REQUIREMENT_ROLES, ...SOURCE_ROLES];

/**
 * 角色 → 界面标签。文案原则：**写明这份文件是谁给的**，
 * 避免用户按"看起来像什么"来选。
 */
export const ROLE_LABEL = {
  main_rfp: '招标文件',
  spec: '技术规范书',
  scoring: '评分表',
  evaluation: '评审要素',
  requirement: '其他',
  source: '我方技术素材',
};

/** 角色 → 一句话说明（上传弹窗/选择器下的提示用）。 */
export const ROLE_HINT = {
  main_rfp: '招标公告、投标人须知、评分办法——定义章节骨架与评分点',
  spec: '招标文件附件「技术规范书 / 技术招标文件」——定义每章要写的技术内容',
  scoring: '评分表 / 评标办法（历史分类，新上传请并入「招标文件」）',
  evaluation: '评审要素材料（历史分类，新上传请并入「其他」）',
  requirement: '澄清、补充通知等其他要求文件',
  source: '我方产品资料、方案、案例——只用于素材匹配，不参与要求提炼',
};

/** 该角色是否属要求类链路。 */
export function isRequirementRole(role) {
  return REQUIREMENT_ROLES.includes(role);
}

// 文件名里出现这些词，基本可断定是**招标人给的**技术规范书，而不是我方素材。
const SPEC_BOOK_HINTS = [
  '技术规范书', '技术规范', '技术招标文件', '技术需求书', '技术要求书',
  '需求规格', '工作说明书', 'task specification',
];

/**
 * 按文件名推断"这份文件更可能是哪一类"。
 *
 * 返回 ``{ role, reason }``；推断不出返回 ``null``。
 *
 * **不自动改写用户的选择** —— 只在两者明显冲突时提示，由用户点确认。
 * 自动改写会在用户故意把旧规范书当素材引用时帮倒忙。
 */
export function inferRoleFromFilename(filename) {
  const name = String(filename || '').toLowerCase();
  if (!name) return null;
  for (const hint of SPEC_BOOK_HINTS) {
    if (name.includes(hint.toLowerCase())) {
      return {
        role: 'spec',
        reason: `文件名含「${hint}」—— 这是招标人随招标文件发出的技术规范书，属要求类文件`,
      };
    }
  }
  return null;
}

/**
 * 校验"文件名推断出的角色"与"用户选的分类"是否冲突。
 *
 * 只报**该提示的**一种冲突：文件像技术规范书，却被归到我方素材（source）——
 * 这是实测踩到的坑（技术规范书当素材传，结果没进要求链路）。
 */
export function roleMismatchWarning(role, filename) {
  const guess = inferRoleFromFilename(filename);
  if (!guess) return null;
  if (role === guess.role) return null;
  if (SOURCE_ROLES.includes(role)) {
    return `${guess.reason}，当前归为「${ROLE_LABEL[role] || role}」将不会参与要求提炼与目录生成。` +
           `建议改选「${ROLE_LABEL[guess.role]}」。`;
  }
  return null;
}
