#!/usr/bin/env python3
"""UI 自动审查：按设计稿规则检查真实界面（还原度 + 结构 + 视觉主次）。

依赖：本机 Chrome（需以 --remote-debugging-port=9333 启动）、websockets、运行中的后端。
用法：python scripts/ui-selfcheck.py --thread <threadId> [--block s1.2.1.1] [--shot out.png]
输出：每条规则的 PASS/FAIL + 截图；退出码非 0 表示有 FAIL。
"""
import argparse, asyncio, base64, json, pathlib, sys, urllib.request
import websockets

PORT = 9333
APP = "http://127.0.0.1:8015/three-mode?projectId=29&packageId=559&view=outline&threadId={thread}&planning=1"

HARNESS = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const out = { steps: [] };
  const mark = (name, ok, extra) => out.steps.push({ name, ok, ...(extra || {}) });

  // 0) 编写阶段头部布局：页签在标题行右侧、无「Word 导出格式」
  for (let i = 0; i < 40 && !document.querySelector('.sr-rail-item[data-stage="4"]'); i++) await sleep(400);
  document.querySelector('.sr-rail-item[data-stage="4"]')?.click();
  for (let i = 0; i < 60 && !document.querySelector('.sr-stage[data-stage="4"]'); i++) await sleep(400);
  const head = document.querySelector('.sr-stage[data-stage="4"] .sr-stage-head');
  const tabs = head?.querySelector('.sr-subtabs');
  if (head && tabs) {
    const h = head.getBoundingClientRect();
    const t = tabs.getBoundingClientRect();
    const sameRow = Math.abs((h.top + h.height / 2) - (t.top + t.height / 2)) <= 12;
    const rightAligned = t.right > h.right - 40;
    mark('页签在「应答编写」同一行且靠右', sameRow && rightAligned,
         { sameRow, rightAligned, headRight: Math.round(h.right), tabsRight: Math.round(t.right) });
  } else {
    mark('页签在「应答编写」同一行且靠右', false, { note: '未找到头部或页签' });
  }
  mark('已去掉「Word 导出格式」', !document.body.textContent.includes('Word 导出格式'), {});

  // 1) 进入编写阶段
  document.querySelector('.sr-rail-item[data-stage="4"]')?.click();
  for (let i = 0; i < 60 && !document.querySelector('.cs-node'); i++) await sleep(500);
  mark('进入编写阶段', !!document.querySelector('.cs-node'), { nodes: document.querySelectorAll('.cs-node').length });

  // 2) 选中目标章节（按 blockId 或标题）
  const targetBlock = __BLOCK__;
  const targetTitle = __TITLE__;
  const nodes = [...document.querySelectorAll('.cs-node')];
  let node = targetBlock ? nodes.find((n) => (n.dataset.block || '').includes(targetBlock)) : null;
  if (!node && targetTitle) node = nodes.find((n) => (n.textContent || '').replace(/\s+/g, '').includes(targetTitle));
  if (!node) node = nodes.find((n) => !/目录/.test(n.textContent || ''));
  node?.click();
  await sleep(2600);

  // 3) 打开「本章编写计划」
  document.querySelector('.cs-plan-btn')?.click();
  for (let i = 0; i < 40 && !document.querySelector('[data-cp-sec]'); i++) await sleep(400);
  const dialog = [...document.querySelectorAll('dialog')].find((d) => d.open && (d.classList.contains('cs-cp-dialog') || d.textContent.includes('本章计划')));
  mark('打开本章编写计划', !!dialog);
  if (!dialog) return JSON.stringify(out);
  await sleep(600);

  const measure = (el, props) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const o = { width: Math.round(rect.width), height: Math.round(rect.height) };
    props.forEach((p) => { o[p] = cs[p]; });
    return o;
  };

  // A 结构：五个区块
  const hasTodo = !!dialog.querySelector('[data-cp-sec="todo"]');
  const hasDeliv = !!dialog.querySelector('[data-cp-sec="deliverables"]') || !!dialog.querySelector('[data-cp-sec="children"]');
  const hasFolds = dialog.querySelectorAll('details.cs-cp-block').length;
  const footer = dialog.querySelector('.cs-cp-footer');
  mark('计划面板区块齐备（交付物/折叠/底部操作）', hasDeliv && hasFolds >= 2 && !!footer,
       { todoBlockRemoved: !hasTodo, deliverables: hasDeliv, folds: hasFolds, footer: !!footer });

  // B 主操作唯一且是主色
  const primary = [...dialog.querySelectorAll('.cs-cp-primary')];
  const headerPrimary = primary.filter((b) => !b.closest('.cs-cp-footer'));
  const primaryBg = headerPrimary[0] ? getComputedStyle(headerPrimary[0]).backgroundColor : '';
  mark('主操作唯一且蓝底', headerPrimary.length === 1 && /29, 78, 216|30, 64, 175/.test(primaryBg),
       { count: headerPrimary.length, bg: primaryBg, label: headerPrimary[0]?.textContent?.trim() });

  // C 交付物：只展开一项
  const rows = [...dialog.querySelectorAll('.cs-cp-del')];
  const openRows = rows.filter((r) => { const b = r.querySelector('.cs-cp-del-body'); return b && !b.hidden; });
  mark('交付物只展开一项', rows.length === 0 || openRows.length === 1, { rows: rows.length, open: openRows.length });

  // D 状态只出现一次（每行）
  const dupStatus = rows.filter((r) => (r.textContent.match(/不足|未写|达标/g) || []).length > 2).length;
  mark('状态词不重复', dupStatus === 0, { rows: rows.length, dupRows: dupStatus });

  // E 折叠摘要带数字
  const foldSummaries = [...dialog.querySelectorAll('details.cs-cp-block > summary')].map((s) => s.textContent.trim());
  const withNumber = foldSummaries.filter((t) => /\d/.test(t)).length;
  mark('折叠摘要带数字', foldSummaries.length === 0 || withNumber === foldSummaries.length,
       { total: foldSummaries.length, withNumber });

  // F ⓘ 只出现一次
  const infoCount = dialog.querySelectorAll('.cs-cp-info').length;
  mark('口径提示 ⓘ 只有一个', infoCount <= 1, { count: infoCount });

  // G 弹窗宽度接近设计（680 ± 60）
  const dw = Math.round(dialog.getBoundingClientRect().width);
  mark('弹窗宽度符合设计（680±60）', Math.abs(dw - 680) <= 60, { width: dw });

  // H 底部操作 sticky
  const footerPos = measure(footer, ['position']);
  mark('底部操作固定可见', footerPos?.position === 'sticky' || footerPos?.position === 'fixed', footerPos || {});

  // I 无横向溢出
  const bodyEl = dialog.querySelector('.cs-plan-body');
  const overflowX = bodyEl ? bodyEl.scrollWidth - bodyEl.clientWidth : 0;
  mark('无横向溢出', overflowX <= 2, { overflowX });

  // J 待办条数与主操作文案一致
  const todoCount = dialog.querySelectorAll('[data-cp-todo]').length;
  const mainLabel = headerPrimary[0]?.textContent || '';
  const consistent = /AI 编制本章/.test(mainLabel);
  mark('计划面板主操作为「AI 编制本章」', consistent, { label: mainLabel.trim() });

  // K 交付物展开区在标题下方（不是右侧）
  const openRow = openRows[0];
  if (openRow) {
    const headEl = openRow.querySelector('.cs-cp-del-head');
    const bodyEl2 = openRow.querySelector('.cs-cp-del-body');
    const below = headEl && bodyEl2 && bodyEl2.getBoundingClientRect().top >= headEl.getBoundingClientRect().bottom - 2;
    mark('交付物展开区在标题下方', !!below, { headBottom: Math.round(headEl?.getBoundingClientRect().bottom || 0),
      bodyTop: Math.round(bodyEl2?.getBoundingClientRect().top || 0) });
  } else {
    mark('交付物展开区在标题下方', true, { note: '无展开项' });
  }

  // L 序号样式统一：区块用 .cs-cp-idx，待办用 .cs-cp-no，且两者尺寸一致
  const idxEls = [...dialog.querySelectorAll('.cs-cp-idx')];
  const noEls = [...dialog.querySelectorAll('.cs-cp-no')];
  const sz = (el) => { const r = el.getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); };
  const idxSizes = new Set(idxEls.map(sz));
  const noSizes = new Set(noEls.map(sz));
  const sameSize = idxSizes.size <= 1 && noSizes.size <= 1 && [...idxSizes][0] === [...noSizes][0];
  mark('序号样式统一（区块/待办同款同尺寸）', idxEls.length >= 2 && noEls.length >= 0 && (noEls.length === 0 || sameSize),
       { blocks: idxEls.length, todos: noEls.length, sizes: [...idxSizes, ...noSizes] });

  // M 顶部主按钮与数字行同一行（垂直中心差 ≤ 14px）
  const factsEl = dialog.querySelector('.cs-cp-facts');
  const mainBtn = dialog.querySelector('[data-cp-main]');
  let sameRow = false, rowDetail = {};
  if (factsEl && mainBtn) {
    const f = factsEl.getBoundingClientRect(), b = mainBtn.getBoundingClientRect();
    const centerDiff = Math.abs((f.top + f.height / 2) - (b.top + b.height / 2));
    sameRow = centerDiff <= 14;
    rowDetail = { centerDiff: Math.round(centerDiff) };
  }
  mark('顶部主按钮与数字行同一行', sameRow, rowDetail);

  // N 所有区块都可展开收起（结构一致）
  const sections = [...dialog.querySelectorAll('[data-cp-sec]')];
  const allDetails = sections.length > 0 && sections.every((el) => el.tagName === 'DETAILS');
  mark('计划面板不含待办块（待办在右栏）', !dialog.querySelector('[data-cp-sec="todo"]'), {});
  // 区块序号必须从 1 连续（删块后容易跳号）
  const idxNums = [...dialog.querySelectorAll('[data-cp-sec] .cs-cp-idx')].map((el) => Number(el.textContent.trim()));
  const idxOk = idxNums.length > 0 && idxNums.every((n, i) => n === i + 1);
  mark('计划面板区块序号从 1 连续', idxOk, { numbers: idxNums });
  // 弹窗标题与章头入口一致（都叫「本章计划」）
  const dlgTitle = dialog.querySelector('header h3')?.textContent.trim() || '';
  mark('弹窗标题为「本章计划」', dlgTitle === '本章计划', { title: dlgTitle });
  mark('所有区块可展开收起（结构一致）', allDetails,
       { total: sections.length, details: sections.filter((el) => el.tagName === 'DETAILS').length });

  // O 块内按钮字号与行内链接一致（≤13px，防止被弹窗头部样式污染）
  const innerBtns = [...dialog.querySelectorAll('.cs-cp-block button')].filter((b) => b.offsetParent !== null);
  const bigBtns = innerBtns.filter((b) => parseFloat(getComputedStyle(b).fontSize) > 13);
  mark('块内按钮字号一致（≤13px）', bigBtns.length === 0,
       { buttons: innerBtns.length, tooBig: bigBtns.slice(0, 3).map((b) => b.textContent.trim()) });

  // P 序号行与内容区有区分（序号行有浅底 + 边框，内容区无）
  const summaries = [...dialog.querySelectorAll('[data-cp-sec] > summary')];
  const badSummary = summaries.filter((el) => {
    const cs = getComputedStyle(el);
    return cs.backgroundColor === 'rgba(0, 0, 0, 0)' || cs.borderBottomWidth === '0px';
  });
  mark('序号行与内容区有视觉区分', summaries.length >= 3 && badSummary.length === 0,
       { summaries: summaries.length, noTint: badSummary.length });

  // S 右栏信息架构：页签 = 待办 / 素材 / 进度（依据已并入计划面板）
  const tabEls = [...document.querySelectorAll('.cs-inspector-tabs [data-panel]')];
  const tabKeys = tabEls.map((b) => b.dataset.panel);
  // 文案可能带计数徽标（如「待办4」），匹配用前缀
  const tabTexts = tabEls.map((b) => b.textContent.replace(/\d+/g, '').trim());
  mark('右栏页签为 待办/素材/进度',
       tabKeys.length === 3 && tabKeys[0] === 'todo' && tabKeys[1] === 'materials' && tabKeys[2] === 'ai'
       && tabTexts[0] === '待办' && tabTexts[1] === '素材' && tabTexts[2] === '进度',
       { keys: tabKeys, texts: tabTexts });
  // 待办页签：按模块分组，每条含动作按钮
  const todoPane = document.querySelector('[data-pane="todo"]');
  const todoRows = todoPane ? [...todoPane.querySelectorAll('.cs-todo-row')] : [];
  const todoActs = todoRows.filter((r) => r.querySelector('.cs-todo-act')).length;
  const hasGroups = todoPane ? !!todoPane.querySelector('.cs-todo-glabel') : false;
  mark('待办页签按模块分组且每条有动作', !!todoPane && (todoRows.length === 0 || (hasGroups && todoActs === todoRows.length)),
       { rows: todoRows.length, groups: hasGroups, acts: todoActs });
  // 待办页签不得再出现"素材模块"（素材问题归素材页签）
  const todoHasMaterialGroup = todoPane ? /素材模块/.test(todoPane.textContent) : false;
  mark('待办页签不含素材模块', !todoHasMaterialGroup, { hasMaterialGroup: todoHasMaterialGroup });
  // 页签互斥：切到素材时，两个 pane 只能有一个可见
  const matTabEl = [...document.querySelectorAll('.cs-inspector-tabs [data-panel]')].find((b) => b.dataset.panel === 'materials');
  matTabEl?.click();
  await sleep(400);
  const visiblePanes = [...document.querySelectorAll('[data-pane]')].filter((el) => el.offsetParent !== null).length;
  const todoHidden = todoPane ? todoPane.offsetParent === null : true;
  mark('页签切换互斥（切素材时待办隐藏）', visiblePanes <= 1 && todoHidden, { visiblePanes, todoHidden });
  [...document.querySelectorAll('.cs-inspector-tabs [data-panel]')].find((b) => b.dataset.panel === 'todo')?.click();
  await sleep(300);
  // 素材页签：三分法
  const matPane2 = document.querySelector('[data-pane="materials"]');
  const gapGroups = matPane2 ? [...matPane2.querySelectorAll('.cs-gap-glabel')].map((g) => g.textContent.trim()) : [];
  const matHasGapTitle = matPane2 ? /素材缺口/.test(matPane2.textContent) : false;
  const matHasManageFold = matPane2 ? !!matPane2.querySelector('.cs-mat-manage') : false;
  mark('素材页签有缺口三分法分组', gapGroups.length === 0 || gapGroups.some((g) => g.includes('公开资料可补')) || gapGroups.some((g) => g.includes('必须你上传')),
       { groups: gapGroups });
  mark('素材页签有「素材缺口」总述且已关联内容折叠', matHasGapTitle && matHasManageFold,
       { gapTitle: matHasGapTitle, manageFold: matHasManageFold });

  // T 章头「本章计划」按钮（带待办角标）
  const planBtn = document.querySelector('.cs-plan-btn');
  const badge = planBtn?.querySelector('.cs-plan-badge');
  // 章头按钮只写「本章计划」；待办数量显示在右栏「待办」页签标题上
  mark('章头入口为纯「本章计划」（无角标）',
       !!planBtn && /^本章计划$/.test(planBtn.textContent.replace(/\s+/g, ' ').trim()) && !planBtn.querySelector('.cs-plan-badge'),
       { label: planBtn?.textContent?.replace(/\s+/g, ' ').trim() });
  const tabBadge = document.querySelector('[data-todo-badge]');
  const tabBadgeCount = tabBadge && !tabBadge.hidden ? tabBadge.textContent.trim() : '';
  mark('待办页签标题显示待办数量', !!tabBadge, { badge: tabBadgeCount });

  // U 章节类型已移入计划面板「写法」
  const kindRow = dialog.querySelector('[data-cp-layout]');
  mark('章节类型在计划面板内', !!kindRow, { found: !!kindRow });

  // V 头部到工作台的垂直间距（章选中后再测）
  const stageHead = document.querySelector('.sr-stage[data-stage="4"] .sr-stage-head');
  const wsEl = document.querySelector('#sr-write-body .cs-workspace');
  const gapPx = (stageHead && wsEl) ? Math.round(wsEl.getBoundingClientRect().top - stageHead.getBoundingClientRect().bottom) : null;
  mark('头部到工作台间距 ≤ 18px', gapPx !== null && gapPx <= 18, { gap: gapPx });

  // W 素材池：卡片式、无重复状态条、空态有上传入口
  const matTab = [...document.querySelectorAll('.cs-inspector-tabs [data-panel]')].find((b) => b.textContent.trim() === '素材池');
  matTab?.click();
  await sleep(500);
  const matPane = document.querySelector('[data-pane="materials"]');
  if (matPane) {
    const cards = matPane.querySelectorAll('.cs-mat-card').length;
    const dupSummary = !!matPane.querySelector('.cs-material-summary');
    const emptyState = matPane.querySelector('.cs-mat-empty');
    const uploadable = !!matPane.querySelector('[data-cs="upload"]');
    mark('素材池为卡片式且无重复状态条', !dupSummary && (cards > 0 || !emptyState) && uploadable,
         { cards, dupSummary, hasEmptyHint: !!emptyState, uploadable });
  } else {
    mark('素材池为卡片式且无重复状态条', false, { note: '未找到素材面板' });
  }
  // 切回依据页签，避免影响后续规则
  [...document.querySelectorAll('.cs-inspector-tabs [data-panel]')].find((b) => b.textContent.trim() === '依据')?.click();
  await sleep(300);

  // X 工具条：主动作 + 视图 + 插入▾ + 更多▾；无重复"文字生成图"；状态是浮动 toast
  const toolbar = document.querySelector('.cs-toolbar');
  if (toolbar) {
    const labels = [...toolbar.querySelectorAll(':scope > button, :scope > .cs-toolbar-right > button, :scope > .cs-toolbar-right > details > summary')]
      .map((b) => b.textContent.replace(/\s+/g, ' ').trim()).filter(Boolean);
    const dupDiagram = toolbar.textContent.includes('文字生成图');
    const moreItems = [...toolbar.querySelectorAll('.cs-more-menu button')].map((b) => b.textContent.trim());
    const insertItems = [...toolbar.querySelectorAll('.cs-insert-menu button')].map((b) => b.textContent.trim());
    mark('工具条分组清晰（主操作/视图/插入/更多）',
         labels.includes('AI 编制本章') && labels.some((l) => l.startsWith('插入')) && labels.some((l) => l.startsWith('更多')) && !dupDiagram,
         { labels, dupDiagram });
    mark('更多菜单只剩确认完成与导出（无质检）', moreItems.length <= 3 && !moreItems.includes('质检清单') && insertItems.length <= 3,
         { more: moreItems, insert: insertItems });
  } else {
    mark('工具条分组清晰（主操作/视图/插入/更多）', false, { note: '未找到工具条' });
    mark('更多菜单按性质分组（≤4 项）', false, { note: '未找到工具条' });
  }
  const statusEl = document.querySelector('[data-cs="status"]');
  const statusStyle = statusEl ? getComputedStyle(statusEl).position : '';
  mark('状态为浮动 toast（不占正文行）', statusStyle === 'fixed', { position: statusStyle });

  // Y 插入面板：图片 / 表格 两类 ×（AI 生成 / 从素材插入）
  document.querySelector('[data-open-insert]')?.click();
  await sleep(700);
  const insDlg = [...document.querySelectorAll('dialog')].find((d) => d.open && d.classList.contains('cs-insert-dialog'));
  mark('「插入…」打开插入面板', !!insDlg, {});
  if (insDlg) {
    const insTabs = [...insDlg.querySelectorAll('[data-ins-tab]')].map((b) => b.textContent.trim());
    mark('插入面板只有 图片/表格 两个页签', insTabs.length === 2 && insTabs[0] === '图片' && insTabs[1] === '表格', { tabs: insTabs });
    const hasInfoGen = !!insDlg.querySelector('[data-ins-generate="info"]');
    const hasArchGen = !!insDlg.querySelector('[data-ins-generate="arch"]');
    const hasImgMat = !!insDlg.querySelector('[data-ins-from-material="image"]');
    mark('图片页签：AI 生成信息图 / 架构图 / 从素材插入', hasInfoGen && hasArchGen && hasImgMat, {});
    const removedOld = !/从官方来源|结构模板|摘录/.test(insDlg.textContent);
    mark('面板已去掉 官方来源/结构模板/摘录', removedOld, { removedOld });
    insDlg.querySelector('[data-ins-generate="info"]')?.click();
    await sleep(400);
    const genForm = insDlg.querySelector('[data-ins-text]');
    const runBtn = insDlg.querySelector('[data-ins-run]');
    mark('信息图生成表单有预填内容与生成按钮',
         !!genForm && !!runBtn && (genForm.value || '').length >= 4, { prefill: (genForm?.value || '').slice(0, 24) });
    [...insDlg.querySelectorAll('[data-ins-tab]')].find((b) => b.dataset.insTab === 'table')?.click();
    await sleep(300);
    const tGen = !!insDlg.querySelector('[data-ins-generate="table"]');
    const tMat = !!insDlg.querySelector('[data-ins-from-material="table"]');
    mark('表格页签：AI 生成表格 / 从素材插入表格', tGen && tMat, {});
    insDlg.close();
  } else {
    for (const name of ['插入面板只有 图片/表格 两个页签', '图片页签：AI 生成信息图 / 架构图 / 从素材插入',
                        '面板已去掉 官方来源/结构模板/摘录', '信息图生成表单有预填内容与生成按钮',
                        '表格页签：AI 生成表格 / 从素材插入表格']) mark(name, false, {});
  }

  out.metrics = {
    dialog: measure(dialog, ['backgroundColor', 'borderRadius']),
    title: measure(dialog.querySelector('.cs-cp-head h4'), ['fontSize', 'fontWeight']),
    facts: measure(dialog.querySelector('.cs-cp-facts'), ['fontSize', 'color']),
  };
  out.todoCount = todoCount;
  return JSON.stringify(out);
})()
"""


async def run(args) -> int:
    info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=5).read())
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        seq, console = 0, []

        async def call(method, params=None, sid=None, await_promise=False):
            nonlocal seq
            seq += 1
            msg = {"id": seq, "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            while True:
                data = json.loads(await ws.recv())
                if data.get("method") == "Runtime.consoleAPICalled" and data["params"].get("type") == "error":
                    console.append(" ".join(str(a.get("value", "")) for a in data["params"].get("args", [])))
                if data.get("method") == "Runtime.exceptionThrown":
                    console.append("exception: " + str(data["params"].get("exceptionDetails", {}).get("text")))
                if data.get("id") == seq:
                    if await_promise and data.get("result", {}).get("exceptionDetails"):
                        console.append("eval-exc: " + str(data["result"]["exceptionDetails"].get("text")))
                    return data.get("result", {})

        target = await call("Target.createTarget", {"url": "about:blank"})
        sid = (await call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}))["sessionId"]
        await call("Runtime.enable", sid=sid)
        await call("Page.enable", sid=sid)
        await call("Emulation.setDeviceMetricsOverride",
                   {"width": 1440, "height": 960, "deviceScaleFactor": 2, "mobile": False}, sid=sid)
        await call("Page.navigate", {"url": APP.format(thread=args.thread)}, sid=sid)
        await asyncio.sleep(args.wait)
        js = HARNESS.replace("__BLOCK__", json.dumps(args.block)).replace("__TITLE__", json.dumps(args.title))
        result = await call("Runtime.evaluate",
                            {"expression": js, "awaitPromise": True, "returnByValue": True},
                            sid=sid, await_promise=True)
        if args.shot:
            data = await call("Page.captureScreenshot", {"format": "png"}, sid=sid)
            pathlib.Path(args.shot).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(args.shot).write_bytes(base64.b64decode(data.get("data", "")))
        await call("Target.closeTarget", {"targetId": target["targetId"]})

    raw = result.get("result", {}).get("value")
    try:
        report = json.loads(raw) if raw else {"steps": [], "error": "no result"}
    except json.JSONDecodeError:
        report = {"steps": [], "error": str(raw)[:300]}

    steps = report.get("steps", [])
    # 控制台错误也算一条规则（页面 JS 崩了，界面看着对也不能过）
    if console:
        steps = steps + [{"name": f"页面无控制台错误（发现 {len(console)} 条）", "ok": False,
                          "detail": console[:3]}]
    else:
        steps = steps + [{"name": "页面无控制台错误", "ok": True}]
    failed = [s for s in steps if not s.get("ok")]
    print(f"  UI 自动审查 · {args.block or args.title or '本章'}")
    for step in steps:
        print(f"   {'PASS' if step.get('ok') else 'FAIL'}  {step.get('name')}")
    if console:
        print(f"   控制台错误 {len(console)} 条：{console[:3]}")
    metrics = report.get("metrics") or {}
    if metrics.get("title"):
        print(f"   度量：标题 {metrics['title'].get('fontSize')}/{metrics['title'].get('fontWeight')} · "
              f"数字行 {metrics['facts'].get('fontSize')} · 弹窗宽 {metrics['dialog'].get('width')}px")
    print(f"   结论：{len(steps) - len(failed)}/{len(steps)} 通过" + (f"，截图 {args.shot}" if args.shot else ""))
    return 0 if steps and not failed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", required=True)
    ap.add_argument("--block", default="")
    ap.add_argument("--title", default="项目背景现状目标")
    ap.add_argument("--wait", type=float, default=8)
    ap.add_argument("--shot", default="")
    sys.exit(asyncio.run(run(ap.parse_args())))
