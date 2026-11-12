"""
SQLite 连接管理与 DDL 初始化。

用法（在路由中）：
    async with get_db() as db:
        rows = await db.execute("SELECT * FROM projects")

DB_PATH 默认为 backend/ 同级目录下的 ge_solution.db，
测试可通过修改 db.DB_PATH = ":memory:" 切换为内存库。
"""

import logging
import os
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger(__name__)

_this_dir = os.path.dirname(os.path.abspath(__file__))


def _default_db_path() -> str:
    """库文件位置：优先环境变量，其次容器持久盘，最后仓库内默认目录。

    线上（Render）必须落在持久盘上，否则**每次重新部署都会清空数据**：
      GE_DB_PATH        —— 显式指定（最高优先）
      CONFIG_PATH=/data —— 容器持久盘（render.yaml 已挂载），库放 /data/ge_solution.db
      否则              —— backend/data/ge_solution.db（本地开发）
    """
    explicit = os.environ.get("GE_DB_PATH", "").strip()
    if explicit:
        return os.path.normpath(explicit)
    data_dir = os.environ.get("CONFIG_PATH", "").strip()
    if data_dir and os.path.isdir(data_dir):
        return os.path.normpath(os.path.join(data_dir, "ge_solution.db"))
    return os.path.normpath(os.path.join(_this_dir, "data", "ge_solution.db"))


DB_PATH: str = _default_db_path()

# 结构版本：每次有 schema 变更时递增，并把迁移写进 schema_migrations。
# 目前只做版本记录（历史迁移仍是幂等 ALTER）；后续迁移可按版本条件执行。
SCHEMA_VERSION = 2

_LEADS_DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    company TEXT NOT NULL,
    contact TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    utm_source TEXT NOT NULL DEFAULT '',
    utm_medium TEXT NOT NULL DEFAULT '',
    utm_campaign TEXT NOT NULL DEFAULT '',
    utm_content TEXT NOT NULL DEFAULT '',
    referer TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_DDL = """
CREATE TABLE IF NOT EXISTS chapter_writing_jobs (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    block_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT '待编制',
    snapshot TEXT NOT NULL,
    artifacts TEXT NOT NULL DEFAULT '{}',
    content TEXT NOT NULL DEFAULT '',
    saved INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- 引用条目（P2）：外部官方来源的溯源记录（不进素材库 —— 素材库只放我方材料）。
-- 每条必须带：机构 · 文号/日期 · 标题 · URL · 摘录；status: pending|accepted|rejected
CREATE TABLE IF NOT EXISTS citations (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    -- trust: official（官方原文）/ unverified（非官方，需用户确认）/ confirmed（非官方但用户已确认可靠）
    trust TEXT NOT NULL DEFAULT 'official',
    -- gap: 该来源是为了补哪个素材缺口（按缺口逐条检索时记录，便于回看"还差什么"）
    gap TEXT NOT NULL DEFAULT '',
    -- segments: 抓到的可用片段（段落/表格/图片）JSON；selected: 用户选中的片段下标 JSON
    segments TEXT NOT NULL DEFAULT '[]',
    selected TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_citations_project ON citations(project_id, status);

-- 质检发现（落库用于待办/趋势/已处理标记；实时计算仍保留为"当前状态"）
-- 生成时的事实缺口（"依据核对"待办）——与质检发现分开存，避免被 sync_findings 清掉
CREATE TABLE IF NOT EXISTS todo_states (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    block_id   TEXT NOT NULL DEFAULT '',      -- 章级任务填章节编码；项目级留空
    task_id    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',  -- open | ignored | snoozed | resolved
    reason     TEXT NOT NULL DEFAULT '',
    until_at   TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, block_id, task_id)
);

CREATE TABLE IF NOT EXISTS writing_model_settings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL CHECK(scope IN ('project','block')),
    scope_id   TEXT NOT NULL,
    settings   TEXT NOT NULL DEFAULT '{}',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope, scope_id)
);

-- 设计版图表（Diagram Design 技能）：HTML 源 + PNG 导出 + 内容/校验留痕。
CREATE TABLE IF NOT EXISTS diagram_assets (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    block_id    INTEGER,
    section     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    skill_type  TEXT NOT NULL DEFAULT 'architecture',
    size_preset TEXT NOT NULL DEFAULT 'doc-wide',
    html        TEXT NOT NULL DEFAULT '',
    svg         TEXT NOT NULL DEFAULT '',
    png_path    TEXT NOT NULL DEFAULT '',
    png_width   INTEGER,
    png_height  INTEGER,
    brief_json  TEXT NOT NULL DEFAULT '{}',
    checks_json TEXT NOT NULL DEFAULT '{}',
    model       TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'done',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_diagram_assets_project ON diagram_assets(project_id, created_at);

CREATE TABLE IF NOT EXISTS diagram_jobs (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    block_id    INTEGER,
    asset_id    TEXT,
    status      TEXT NOT NULL DEFAULT 'queued',
    stage       TEXT NOT NULL DEFAULT '排队中',
    error       TEXT NOT NULL DEFAULT '',
    brief_json  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_diagram_jobs_project ON diagram_jobs(project_id, created_at);

-- 设计版图表质量档：fast（默认快速结构图）/ design_architecture / design_all
CREATE TABLE IF NOT EXISTS diagram_settings (
    project_id  INTEGER PRIMARY KEY,
    quality     TEXT NOT NULL DEFAULT 'fast',
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_gaps (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    thread_id   TEXT NOT NULL DEFAULT '',
    block_id    TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thread_id, block_id, message)
);
CREATE INDEX IF NOT EXISTS idx_chapter_gaps_open ON chapter_gaps(project_id, block_id, status);

CREATE TABLE IF NOT EXISTS quality_findings (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    check_code TEXT NOT NULL DEFAULT '',
    "group" TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'medium',
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thread_id, block_id, check_code, message)
);
CREATE INDEX IF NOT EXISTS idx_quality_findings_open ON quality_findings(project_id, status);

-- 片段 → 需求 的绑定（P6）：只有绑定的片段才算"这条要求有素材了"
CREATE TABLE IF NOT EXISTS need_snippets (
    plan_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    citation_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (plan_id, block_id, requirement_id, citation_id, segment_index)
);

-- 检索历史（P5）：同一章下次打开时可看"上次找到的候选"，避免重复搜
CREATE TABLE IF NOT EXISTS citation_searches (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    results TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_citation_searches_block ON citation_searches(project_id, block_id, created_at);

-- 引用 → 章节 的多对多关联（一条官方依据常被多章引用；citations.block_id 保留为"首个关联"）
CREATE TABLE IF NOT EXISTS citation_chunks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    citation_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    heading    TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending | confirmed | rejected
    confirmed_at TEXT,
    UNIQUE(citation_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS citation_links (
    citation_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (citation_id, block_id)
);

-- 编写规范（P0.5）：L0 全局纪律 + L1 模块配方，多版本、可回滚、生成留痕。
-- key：'global' 或 layout_key（understanding/solution/implementation/evidence/point_to_point）
CREATE TABLE IF NOT EXISTS writing_specs (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'recipe',
    layout_key TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'published',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_writing_specs_key ON writing_specs(key, version DESC);

-- 技术评分点一级目录的编写计划：先规划、确认、再并发生成（P0）。
-- 单位 = 「技术评分项应答」评分根下的一级子目录；plan 里存整棵子树的叶子计划。
CREATE TABLE IF NOT EXISTS section_practices (
    project_id INTEGER NOT NULL,
    block_id   TEXT NOT NULL,          -- 章节编码（s1.2.1.1）
    section    TEXT NOT NULL,          -- 节标题
    practice   TEXT NOT NULL DEFAULT '',  -- 该节"写法"（= 生成时用的提示词）
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, block_id, section)
);

CREATE TABLE IF NOT EXISTS writing_plans (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    root_block_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    plan TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS chapter_studio (
    block_id INTEGER PRIMARY KEY REFERENCES blocks(id),
    layout TEXT NOT NULL DEFAULT 'solution',
    writing_status TEXT NOT NULL DEFAULT 'draft',
    confirmed_hash TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chapter_material_chunks (
    block_id   INTEGER NOT NULL,
    chunk_id   INTEGER NOT NULL,
    section    TEXT NOT NULL DEFAULT '',
    mode       TEXT NOT NULL DEFAULT 'cite',
    requirement_id TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (block_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS section_write_results (
    block_id    INTEGER NOT NULL,
    section     TEXT NOT NULL,
    result_json TEXT DEFAULT '{}',
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (block_id, section)
);

CREATE TABLE IF NOT EXISTS evidence_need_actions (
    block_id    INTEGER NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    need_id     TEXT NOT NULL,
    action      TEXT NOT NULL DEFAULT 'ignored',
    reason      TEXT DEFAULT '',
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (block_id, section, need_id)
);

CREATE TABLE IF NOT EXISTS review_jobs (
    id             TEXT PRIMARY KEY,
    project_id     INTEGER NOT NULL,
    thread_id      TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | partial | failed
    only_unreviewed INTEGER NOT NULL DEFAULT 1,
    limit_n        INTEGER NOT NULL DEFAULT 0,
    total          INTEGER NOT NULL DEFAULT 0,
    done           INTEGER NOT NULL DEFAULT 0,
    failed         INTEGER NOT NULL DEFAULT 0,
    current_block  TEXT NOT NULL DEFAULT '',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    company       TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    plan          TEXT NOT NULL DEFAULT 'trial',      -- trial | pro | paused
    trial_ends_at DATETIME,
    status        TEXT NOT NULL DEFAULT 'active',     -- active | disabled
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);

CREATE TABLE IF NOT EXISTS account_sessions (
    token      TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_account_sessions_account ON account_sessions(account_id);

CREATE TABLE IF NOT EXISTS block_purged_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER NOT NULL,
    placeholder TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'drop',    -- search | upload | drop
    reason      TEXT NOT NULL DEFAULT '',
    section     TEXT NOT NULL DEFAULT '',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_purged_block ON block_purged_items(block_id);

CREATE TABLE IF NOT EXISTS review_issue_states (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id      INTEGER NOT NULL,
    fingerprint   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',   -- open | fixed | verified
    severity      TEXT NOT NULL DEFAULT 'medium',
    point         TEXT NOT NULL DEFAULT '',
    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    fixed_at      DATETIME,
    verified_at   DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_issue_unique ON review_issue_states(block_id, fingerprint);
CREATE INDEX IF NOT EXISTS idx_review_issue_status ON review_issue_states(status);

CREATE TABLE IF NOT EXISTS review_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id       INTEGER NOT NULL,
    project_id     INTEGER NOT NULL DEFAULT 0,
    score          INTEGER DEFAULT 0,
    tech_score     INTEGER DEFAULT 0,
    comp_score     INTEGER DEFAULT 0,
    satisfied      TEXT DEFAULT '',
    issues_count   INTEGER DEFAULT 0,
    needs_material_count INTEGER DEFAULT 0,
    stage          TEXT DEFAULT 'writing',   -- writing | final
    thread_id      TEXT DEFAULT '',
    tier           TEXT DEFAULT '',
    content_version TEXT DEFAULT '',
    evidence_version TEXT DEFAULT '',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_reviews (
    block_id      INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL,
    tech_score    INTEGER DEFAULT 0,
    comp_score    INTEGER DEFAULT 0,
    score         INTEGER DEFAULT 0,
    satisfied     TEXT DEFAULT '',
    criterion_text TEXT DEFAULT '',
    covered_points TEXT DEFAULT '[]',
    missing_points TEXT DEFAULT '[]',
    issues         TEXT DEFAULT '[]',
    needs_material TEXT DEFAULT '[]',
    tier           TEXT DEFAULT '',
    stage          TEXT DEFAULT 'writing',   -- writing | final（终评权威）
    thread_id      TEXT DEFAULT '',
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_materials (
    block_id INTEGER NOT NULL REFERENCES blocks(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    confirmed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(block_id, material_id)
);
CREATE TABLE IF NOT EXISTS response_document_settings (
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    template TEXT NOT NULL DEFAULT 'standard',
    PRIMARY KEY(project_id, thread_id)
);
CREATE TABLE IF NOT EXISTS document_export_jobs (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    snapshot TEXT NOT NULL,
    result BLOB,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    status      TEXT DEFAULT 'init',
    deadline    TEXT,
    summary     TEXT,
    base_snapshot_id INTEGER,
    target_pages INTEGER,
    -- 投标范围（投标人视角）：我们投的是哪个标的/标包。
    -- 这是"一次设定、贯穿全程"的属性，不该每次靠提炼要求里的文字去推 ——
    -- 实测多标包文件里推错范围=整份技术应答目录错。
    scope_target        TEXT DEFAULT '',
    scope_package_no    TEXT DEFAULT '',
    scope_package_name  TEXT DEFAULT '',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 待确认项的作答：拆分时把"识别到了什么"摆给用户，用户的回答落库，
-- 下一次拆分（以及报告）按回答执行 —— 不再是"看得到但改不了"。
CREATE TABLE IF NOT EXISTS split_answers (
    project_id  INTEGER NOT NULL,
    thread_id   TEXT DEFAULT '',
    key         TEXT NOT NULL,
    value       TEXT DEFAULT '{}',
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, thread_id, key)
);

CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    filename    TEXT,
    type        TEXT,
    file_path   TEXT,
    role        TEXT,
    file_size   INTEGER,
    parsed_at   DATETIME,
    parse_status TEXT DEFAULT 'pending',
    deleted_at  DATETIME          -- 逻辑删除（材料条"✕"用它；新库也要有，不能只靠 ALTER）
);

-- 标包分析的结果（**唯一产物**）：从招标文件正文里抽出来的"标的/标包/标段"事实。
-- 只存事实 + 出处（evidence），名字允许为空（留给用户确认，不编造）。
-- 可重复生成（重新分析就覆盖），所以它是缓存 + 事实快照，不是用户输入。
CREATE TABLE IF NOT EXISTS project_packages (
    project_id          INTEGER NOT NULL REFERENCES projects(id),
    target              TEXT NOT NULL DEFAULT '',
    package_no          TEXT NOT NULL DEFAULT '',
    package_name        TEXT NOT NULL DEFAULT '',
    confidence          TEXT NOT NULL DEFAULT 'low',
    mentions            INTEGER NOT NULL DEFAULT 0,
    source_material_ids TEXT NOT NULL DEFAULT '[]',
    evidence            TEXT NOT NULL DEFAULT '[]',
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, target, package_no)
);

-- 标包工作空间的稳定聚合根。project_packages 保留原始分析快照；packages 提供不会因
-- 重新识别而变化的 package_id，并承载候选/已确认/已归档状态。
CREATE TABLE IF NOT EXISTS packages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          INTEGER NOT NULL REFERENCES projects(id),
    kind                TEXT NOT NULL DEFAULT 'package',
    target_code         TEXT NOT NULL DEFAULT '',
    package_code        TEXT NOT NULL DEFAULT '',
    display_name        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'candidate',
    confidence          TEXT NOT NULL DEFAULT 'low',
    source_material_ids TEXT NOT NULL DEFAULT '[]',
    evidence            TEXT NOT NULL DEFAULT '[]',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, target_code, package_code)
);

-- 文件 ↔ 标包：**多对多**（用户口径：「一个文件可能会属于多个标包，这块没有严格的对应关系」）。
-- 所以不能用 materials.package_no 单列。来源是分析命中的事实，也可由用户手改。
CREATE TABLE IF NOT EXISTS material_packages (
    material_id INTEGER NOT NULL REFERENCES materials(id),
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    package_id  INTEGER REFERENCES packages(id),
    target      TEXT NOT NULL DEFAULT '',
    package_no  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (material_id, target, package_no)
);

CREATE TABLE IF NOT EXISTS package_spec_selections (
    package_id INTEGER PRIMARY KEY REFERENCES packages(id),
    material_ids TEXT NOT NULL DEFAULT '[]',
    no_spec INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    block_id    TEXT,
    kind        TEXT,
    level       INTEGER,
    title       TEXT,
    content     TEXT,
    domain      TEXT,
    parent_title TEXT,
    requirement TEXT,
    key_points  TEXT,
    veto_items  TEXT,
    bonus_items TEXT,
    score_items TEXT,
    evidence_required TEXT,
    constraint_level TEXT,
    indicators  TEXT,
    score       TEXT,
    source      TEXT,
    order_idx   INTEGER,
    run_thread_id TEXT,
    status      TEXT DEFAULT 'empty',
    -- 逐节提炼的结果状态（'' 正常 / 'no_source' 无原文 / 'failed' 需要重试）+ 原因
    error       TEXT DEFAULT '',
    error_kind  TEXT DEFAULT '',
    context_version INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS section_coverage_reviews (
    block_id      INTEGER NOT NULL,
    section_title TEXT NOT NULL,
    text_hash     TEXT NOT NULL,
    context_version INTEGER DEFAULT 0,
    deterministic_json TEXT DEFAULT '{}',
    llm_json      TEXT DEFAULT '{}',
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (block_id, section_title, text_hash)
);

CREATE TABLE IF NOT EXISTS review_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    run_thread_id TEXT,
    -- point_id：**内容派生的稳定 id**。重出目录时同一条评分点必须还是同一个 id，
    -- 否则章节上的 review_point_ids 会对不上，"这章为哪个评分点得分"就断了。
    point_id    TEXT,
    package_no  TEXT,
    category    TEXT,
    clause      TEXT,
    seq         TEXT,
    factor      TEXT,
    item        TEXT,
    criterion   TEXT,
    points      REAL,
    bands       TEXT,
    objective   INTEGER DEFAULT 0,
    source_file TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_points_run ON review_points(run_thread_id);

CREATE TABLE IF NOT EXISTS spec_requirements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    run_thread_id TEXT,
    -- requirement_id：稳定 id，同上；章节靠它反查"要响应什么"
    requirement_id TEXT,
    topic       TEXT,
    title       TEXT,
    text        TEXT,
    level       INTEGER,
    parent_id   TEXT,
    mandatory_level TEXT,
    verification_method TEXT,
    source_file TEXT,
    order_idx   INTEGER,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_spec_requirements_run ON spec_requirements(run_thread_id);

CREATE TABLE IF NOT EXISTS block_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER REFERENCES blocks(id),
    revision_no INTEGER,
    content     TEXT,
    summary     TEXT,
    source      TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    package_id  INTEGER REFERENCES packages(id),
    run_thread_id TEXT,
    trigger     TEXT,
    snapshot    TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    page        INTEGER DEFAULT 0,
    file_path   TEXT NOT NULL,
    caption     TEXT DEFAULT '',
    source      TEXT DEFAULT 'docx',
    size_bytes  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    thread_id   TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    stage       TEXT NOT NULL DEFAULT 'idle',
    -- 这一条运行属于哪个标包（标的 + 标包号）。用户口径：**每个标包是单独的内容**
    -- ——评分点/目录/编写/评审都各自独立，所以运行必须能按标包归属与检索。
    target      TEXT NOT NULL DEFAULT '',
    package_no  TEXT NOT NULL DEFAULT '',
    package_id  INTEGER REFERENCES packages(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL REFERENCES workflow_runs(thread_id),
    block_id        TEXT NOT NULL,
    agent           TEXT NOT NULL,
    score           INTEGER,
    issues          TEXT,
    strengths       TEXT,
    error           TEXT DEFAULT '',
    stage           TEXT DEFAULT 'writing',   -- writing | final（明细审计）
    tier            TEXT DEFAULT '',
    content_version TEXT DEFAULT '',
    evidence_version TEXT DEFAULT '',
    project_id      INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reviews_thread_block ON reviews(thread_id, block_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_project ON workflow_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_packages_project_status ON packages(project_id, status);
CREATE INDEX IF NOT EXISTS idx_blocks_project ON blocks(project_id);
CREATE INDEX IF NOT EXISTS idx_block_revisions_block ON block_revisions(block_id);

CREATE TABLE IF NOT EXISTS llm_usage (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    call_site              TEXT NOT NULL DEFAULT '',
    provider               TEXT NOT NULL DEFAULT '',
    model                  TEXT NOT NULL DEFAULT '',
    streaming              INTEGER NOT NULL DEFAULT 0,
    input_tokens           INTEGER,
    output_tokens          INTEGER,
    cache_read_tokens      INTEGER,
    cache_creation_tokens  INTEGER,
    reasoning_tokens       INTEGER,
    total_tokens           INTEGER,
    project_id             INTEGER,
    thread_id              TEXT,
    ok                     INTEGER NOT NULL DEFAULT 1,
    error                  TEXT DEFAULT '',
    latency_ms             INTEGER,
    created_at             DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_site ON llm_usage(call_site, model);
"""


async def init_db(conn: aiosqlite.Connection) -> None:
    """执行建表 DDL（CREATE TABLE IF NOT EXISTS，幂等）。"""
    await conn.executescript(_DDL)
    try:
        # 老库补列（评审 v3）：新库 DDL 已含 tier，这里只会对老库生效
        await conn.execute("ALTER TABLE chapter_reviews ADD COLUMN tier TEXT DEFAULT ''")
        await conn.commit()
    except Exception:
        pass
    await conn.execute("CREATE TABLE IF NOT EXISTS project_writing_facts(project_id INTEGER PRIMARY KEY REFERENCES projects(id), facts TEXT NOT NULL DEFAULT '{}')")
    issue_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(review_issue_states)")).fetchall()}
    for column in ('verified_content_version', 'verified_evidence_version'):
        if column not in issue_cols:
            await conn.execute(f"ALTER TABLE review_issue_states ADD COLUMN {column} TEXT DEFAULT ''")
    review_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(chapter_reviews)")).fetchall()}
    for column in ('content_version', 'evidence_version', 'stage', 'thread_id'):
        if column not in review_cols:
            # stage 默认 writing：老数据都来自编写时评审；终评会逐章覆盖为 final
            await conn.execute(f"ALTER TABLE chapter_reviews ADD COLUMN {column} TEXT DEFAULT 'writing'" if column == 'stage'
                               else f"ALTER TABLE chapter_reviews ADD COLUMN {column} TEXT DEFAULT ''")
    history_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(review_history)")).fetchall()}
    for column in ('stage', 'thread_id', 'tier', 'content_version', 'evidence_version'):
        if column not in history_cols:
            await conn.execute(f"ALTER TABLE review_history ADD COLUMN {column} TEXT DEFAULT 'writing'" if column == 'stage'
                               else f"ALTER TABLE review_history ADD COLUMN {column} TEXT DEFAULT ''")
    reviews_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(reviews)")).fetchall()}
    for column in ('stage', 'tier', 'content_version', 'evidence_version'):
        if column not in reviews_cols:
            await conn.execute(f"ALTER TABLE reviews ADD COLUMN {column} TEXT DEFAULT 'writing'" if column == 'stage'
                               else f"ALTER TABLE reviews ADD COLUMN {column} TEXT DEFAULT ''")
    if 'project_id' not in reviews_cols:
        await conn.execute("ALTER TABLE reviews ADD COLUMN project_id INTEGER DEFAULT 0")
    await conn.commit()
    await conn.executescript(_LEADS_DDL)
    await conn.executescript('''
        CREATE TRIGGER IF NOT EXISTS cleanup_chapter_studio AFTER DELETE ON blocks BEGIN
            DELETE FROM chapter_studio WHERE block_id=OLD.id;
            DELETE FROM chapter_materials WHERE block_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS cleanup_chapter_material AFTER DELETE ON materials BEGIN
            DELETE FROM chapter_materials WHERE material_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS review_chapter_material_delete BEFORE DELETE ON materials BEGIN
            UPDATE chapter_studio SET writing_status='review' WHERE writing_status='complete'
                AND block_id IN (SELECT block_id FROM chapter_materials WHERE material_id=OLD.id);
        END;
        CREATE TRIGGER IF NOT EXISTS review_chapter_material_change AFTER UPDATE OF parse_status ON materials
            WHEN NEW.parse_status != OLD.parse_status AND NEW.parse_status != 'done' BEGIN
            UPDATE chapter_materials SET confirmed=0 WHERE material_id=NEW.id;
            UPDATE chapter_studio SET writing_status='review' WHERE writing_status='complete'
                AND block_id IN (SELECT block_id FROM chapter_materials WHERE material_id=NEW.id);
        END;
    ''')
    await conn.commit()
    # 旧 reviews 明细 → 章级当前态（仅补完全没有章评审的行；幂等）
    try:
        from services.review_migration import backfill_review_tables
        await backfill_review_tables(conn)
    except Exception:
        pass
    # 迁移已有数据库：添加新列（如果不存在）
    export_columns = {r[1] for r in await (await conn.execute('PRAGMA table_info(document_export_jobs)')).fetchall()}
    for name, kind in [('preview_pdf', 'BLOB'), ('preview_status', "TEXT DEFAULT 'pending'"),
                       ('preview_error', "TEXT DEFAULT ''")]:
        if name not in export_columns:
            await conn.execute(f'ALTER TABLE document_export_jobs ADD COLUMN {name} {kind}')
    # 素材库 M1：解析片段的状态（待确认/已确认），章节绑定加 chunk 与方式
    chunk_cols = {r[1] for r in await (await conn.execute('PRAGMA table_info(material_chunks)')).fetchall()}
    if chunk_cols:
        if 'status' not in chunk_cols:
            await conn.execute("ALTER TABLE material_chunks ADD COLUMN status TEXT DEFAULT 'confirmed'")
        if 'confirmed_at' not in chunk_cols:
            await conn.execute("ALTER TABLE material_chunks ADD COLUMN confirmed_at TEXT")
    # 素材语义：片段状态 = candidate(候选) / selected(素材) / rejected(丢弃) / invalid(出处已删)
    try:
        await conn.execute("UPDATE material_chunks SET status='candidate' WHERE status IN ('confirmed','pending') OR status IS NULL")
        await conn.execute("UPDATE citation_chunks SET status='candidate' WHERE status IN ('confirmed','pending') OR status IS NULL")
        await conn.commit()
    except Exception:
        pass
    # C2：片段类型标签（JSON 数组文本）
    chunk_cols2 = {r[1] for r in await (await conn.execute('PRAGMA table_info(material_chunks)')).fetchall()}
    if chunk_cols2 and 'tags' not in chunk_cols2:
        await conn.execute("ALTER TABLE material_chunks ADD COLUMN tags TEXT DEFAULT ''")
    cite_cols = {r[1] for r in await (await conn.execute('PRAGMA table_info(citation_chunks)')).fetchall()}
    if cite_cols and 'tags' not in cite_cols:
        await conn.execute("ALTER TABLE citation_chunks ADD COLUMN tags TEXT DEFAULT ''")
    await conn.commit()
    # C3：片段所属章节路径（第三章 > 3.2 > 3.2.1）
    chunk_cols3 = {r[1] for r in await (await conn.execute('PRAGMA table_info(material_chunks)')).fetchall()}
    if chunk_cols3 and 'path' not in chunk_cols3:
        await conn.execute("ALTER TABLE material_chunks ADD COLUMN path TEXT DEFAULT ''")
    cite_cols3 = {r[1] for r in await (await conn.execute('PRAGMA table_info(citation_chunks)')).fetchall()}
    if cite_cols3 and 'path' not in cite_cols3:
        await conn.execute("ALTER TABLE citation_chunks ADD COLUMN path TEXT DEFAULT ''")
    await conn.commit()
    material_cols = {r[1] for r in await (await conn.execute('PRAGMA table_info(chapter_materials)')).fetchall()}
    if material_cols and 'section' not in material_cols:
        await conn.execute("ALTER TABLE chapter_materials ADD COLUMN section TEXT DEFAULT ''")
    if material_cols and 'chunk_id' not in material_cols:
        await conn.execute("ALTER TABLE chapter_materials ADD COLUMN chunk_id INTEGER")
    if material_cols and 'mode' not in material_cols:
        await conn.execute("ALTER TABLE chapter_materials ADD COLUMN mode TEXT DEFAULT 'cite'")
    cs_cols = {r[1] for r in await (await conn.execute('PRAGMA table_info(chapter_studio)')).fetchall()}
    try:
        cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(projects)")).fetchall()}
        if cols and 'owner_account_id' not in cols:
            await conn.execute("ALTER TABLE projects ADD COLUMN owner_account_id INTEGER")
            await conn.commit()
    except Exception:
        pass

    if cs_cols and 'chapter_requirement' not in cs_cols:
        await conn.execute("ALTER TABLE chapter_studio ADD COLUMN chapter_requirement TEXT DEFAULT ''")
    if cs_cols and 'layout_prompt' not in cs_cols:
        await conn.execute("ALTER TABLE chapter_studio ADD COLUMN layout_prompt TEXT DEFAULT ''")
    cmc_cols = {r[1] for r in await (await conn.execute('PRAGMA table_info(chapter_material_chunks)')).fetchall()}
    if cmc_cols and 'chunk_kind' not in cmc_cols:
        await conn.execute("ALTER TABLE chapter_material_chunks ADD COLUMN chunk_kind TEXT DEFAULT 'file'")
    if cmc_cols and 'requirement_id' not in cmc_cols:
        await conn.execute("ALTER TABLE chapter_material_chunks ADD COLUMN requirement_id TEXT DEFAULT ''")
    await conn.commit()
    usage_cols = {r[1] for r in await (await conn.execute('PRAGMA table_info(llm_usage)')).fetchall()}
    for name, kind in [('block_id', 'INTEGER'), ('section', "TEXT DEFAULT ''"), ('role', "TEXT DEFAULT ''")]:
        if name not in usage_cols:
            await conn.execute(f'ALTER TABLE llm_usage ADD COLUMN {name} {kind}')
    await conn.commit()
    cursor = await conn.execute("PRAGMA table_info(blocks)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    # run_thread_id：该行属于哪一次 workflow run。老库的行留 NULL（不回填），
    # 读路径在「该 run 一行都没有」时退回显示全部行，见 block_store.list_blocks。
    for col in ("key_points", "veto_items", "bonus_items",
                "score_items", "evidence_required", "constraint_level", "indicators",
                "run_thread_id",
                # error/error_kind：逐节提炼的结果状态。以前只在 SSE 帧里带一次，
                # 刷新就没了，而且"无原文"（正常）与"全部 API 失败"（要处理）在页面上
                # 完全同形 —— 于是失败章节被当成"已完成"、页面照样显示「提炼完成」。
                # error_kind: '' 正常 | 'no_source' 无原文（正常，不必重试）
                #             | 'failed' 需要重试
                "error", "error_kind"):
        if col not in existing_cols:
            await conn.execute(f"ALTER TABLE blocks ADD COLUMN {col} TEXT")
    # 来源链（2026-10-13，用户确定的核心数据关系）：
    #   招标评分点 ↔ 技术规范要求 ↔ 应标章节 ↔ 企业素材 ↔ 生成内容 ↔ 审查结果
    # 编写计划版本与更新时间（"计划已变更，建议重新生成"的判断依据）
    plan_columns = {r[1] for r in await (await conn.execute("PRAGMA table_info(writing_plans)")).fetchall()}
    if plan_columns:
        for name, kind in (("version", "INTEGER NOT NULL DEFAULT 1"),
                           ("updated_at", "TEXT NOT NULL DEFAULT ''")):
            if name not in plan_columns:
                await conn.execute(f"ALTER TABLE writing_plans ADD COLUMN {name} {kind}")
        await conn.execute("UPDATE writing_plans SET updated_at=created_at WHERE updated_at=''")

    # citations 的 trust / gap 是后加的列：老库里的 citations 表早已建好，
    # 光改 CREATE TABLE 不会补列（实测：接受引用时报 500 no such column: gap）。
    citation_columns = {r[1] for r in await (await conn.execute("PRAGMA table_info(citations)")).fetchall()}
    if citation_columns:
        for name, kind in (("trust", "TEXT NOT NULL DEFAULT 'official'"),
                           ("gap", "TEXT NOT NULL DEFAULT ''"),
                           ("segments", "TEXT NOT NULL DEFAULT '[]'"),
                           ("selected", "TEXT NOT NULL DEFAULT '[]'")):
            if name not in citation_columns:
                await conn.execute(f"ALTER TABLE citations ADD COLUMN {name} {kind}")

    # 在此之前评分点/规范要求只在本轮派生中内存存活，章节只有 8 段散文，
    # 于是"这章为哪个评分点得分、响应了哪条规范要求"无法反查、覆盖度算不出来。
    for col in ("review_point_ids", "spec_requirement_ids", "criterion_text", "spec_topic",
                # stable_key：章节的**内容派生稳定键**。block_id 是位置派生的，
                # 重拆目录换位置就换 id；而应答条目 id 嵌着 block_id，素材挂接也会失效。
                # 重拆时靠这个键把正文/8字段/素材挂接迁移到新章节。
                "stable_key"):
        if col not in existing_cols:
            await conn.execute(f"ALTER TABLE blocks ADD COLUMN {col} TEXT")
    if "score_weight" not in existing_cols:
        await conn.execute("ALTER TABLE blocks ADD COLUMN score_weight REAL")
    # 篇幅（2026-10-16，用户要求：填一个总页数，由系统分配到各章，不能让用户逐章手工填）：
    # 页数直接落在目录节点上（与目录同一个对象），pinned 记录"人工改过、不再被自动分配覆盖"。
    # 中途中断标记（2026-10-16）：进程在"两个节点之间"被杀/重启时，
    # workflow_runs.stage 会停在 points_review，与"正等用户确认"完全同形。
    # 用 checkpoints 对账（见 services/run_recovery.py），这里只加字段存事实。
    for _col, _kind in (("interrupted_at", "DATETIME"), ("interrupted_node", "TEXT")):
        try:
            _cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(workflow_runs)")).fetchall()}
            if _col not in _cols:
                await conn.execute(f"ALTER TABLE workflow_runs ADD COLUMN {_col} {_kind}")
        except Exception as exc:                  # 加列失败不影响启动
            logger.warning("workflow_runs.%s 迁移跳过：%s", _col, exc)

    # 名称清洗（2026-10-17）：早期名称提取把 Markdown 表格分隔符一起吃进来，
    # 界面上显示成「高可靠技术专题研究与验证标包||||||||---|---|---|」（实测 project 29）。
    # 按第一个 "|" 截断；幂等，已在库里的脏名一次修好。
    for _tbl, _col in (("packages", "display_name"), ("project_packages", "package_name")):
        try:
            await conn.execute(
                f"UPDATE {_tbl} SET {_col} = TRIM(SUBSTR({_col}, 1, INSTR({_col}, '|') - 1)) "
                f"WHERE {_col} LIKE '%|%'"
            )
        except Exception as exc:
            logger.warning("%s.%s 名称清洗跳过：%s", _tbl, _col, exc)
    await conn.commit()

    # packages.display_name 回填（2026-10-16，用户反馈"标的名称没有展示"）：
    # 抽取阶段刻意留空名称（不编造），确认标包时只写了编号标签（如「标的 1」）；
    # 而名称其实就在 evidence 引文里（「标的1：2026年…采购项目」）。
    # 这里从**已有引文**回填，不重跑解析、不编造（引文即原文）。
    # 只回填"为空 或 只是编号标签"的行，人工/已确认的名称一律不动。
    try:
        import json as _json_local
        import re as _re

        from services.package_analysis import name_from_quotes as _name_from_quotes

        _bare = _re.compile(r"^(标的|标包|标段)\s*[0-9一二三四五六七八九十]*$")
        _cur = await conn.execute("SELECT id, display_name, evidence FROM packages")
        _rows = await _cur.fetchall()
        _filled = 0
        for _row in _rows:
            _get = (lambda k: _row[k]) if hasattr(_row, "keys") else (lambda k, r=_row: r[{"id": 0, "display_name": 1, "evidence": 2}[k]])
            _disp = str(_get("display_name") or "").strip()
            if _disp and not _bare.match(_disp):
                continue                      # 已有真实名称：不动
            try:
                _ev = _json_local.loads(_get("evidence") or "[]")
            except Exception:
                _ev = []
            _name = _name_from_quotes([e.get("quote") for e in _ev if isinstance(e, dict)])
            if not _name:
                continue
            await conn.execute("UPDATE packages SET display_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                               (_name, _get("id")))
            _filled += 1
        if _filled:
            await conn.commit()
            logger.info("packages.display_name 回填：%s 个标包从引文取到名称", _filled)
    except Exception as exc:                  # 回填失败不能影响启动
        logger.warning("packages.display_name 回填跳过：%s", exc)

    if "page_budget" not in existing_cols:
        await conn.execute("ALTER TABLE blocks ADD COLUMN page_budget INTEGER")
    if "page_budget_pinned" not in existing_cols:
        await conn.execute("ALTER TABLE blocks ADD COLUMN page_budget_pinned INTEGER NOT NULL DEFAULT 0")
    _block_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(blocks)")).fetchall()}
    _mat_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(materials)")).fetchall()}
    if _mat_cols and "parse_error" not in _mat_cols:
        await conn.execute("ALTER TABLE materials ADD COLUMN parse_error TEXT DEFAULT ''")
    if "context_version" not in _block_cols:
        await conn.execute("ALTER TABLE blocks ADD COLUMN context_version INTEGER DEFAULT 0")

    # spec_requirements.package_no（2026-10-13）：技术要求按标包/标的归属
    # （用户口径：不存在"全项目共享"）。旧库需补列，否则落库写入直接报错。
    _sr_cols = {row[1] for row in await (await conn.execute(
        "PRAGMA table_info(spec_requirements)")).fetchall()}
    if _sr_cols and "package_no" not in _sr_cols:
        await conn.execute("ALTER TABLE spec_requirements ADD COLUMN package_no TEXT")

    # workflow_runs.target / package_no（用户口径：**每个标包是单独的内容**）：
    # 一条运行归属一个标包，前端按标包找/建运行，五阶段都只看该标包的内容。
    _wr_cols = {row[1] for row in await (await conn.execute(
        "PRAGMA table_info(workflow_runs)")).fetchall()}
    if _wr_cols:
        for _col in ("target", "package_no"):
            if _col not in _wr_cols:
                await conn.execute(
                    f"ALTER TABLE workflow_runs ADD COLUMN {_col} TEXT NOT NULL DEFAULT ''")
        if "package_id" not in _wr_cols:
            await conn.execute("ALTER TABLE workflow_runs ADD COLUMN package_id INTEGER")

    _mp_cols = {row[1] for row in await (await conn.execute(
        "PRAGMA table_info(material_packages)")).fetchall()}
    if _mp_cols and "package_id" not in _mp_cols:
        await conn.execute("ALTER TABLE material_packages ADD COLUMN package_id INTEGER")

    # 旧项目已有 project_packages 时，首次启动自动生成稳定 package_id；重复执行幂等。
    await conn.execute(
        """INSERT OR IGNORE INTO packages
           (project_id, target_code, package_code, display_name, confidence,
            source_material_ids, evidence, status)
           SELECT project_id, target, package_no, package_name, confidence,
                  source_material_ids, evidence, 'candidate'
             FROM project_packages"""
    )
    await conn.execute(
        """UPDATE material_packages
              SET package_id = (SELECT p.id FROM packages p
                                 WHERE p.project_id = material_packages.project_id
                                   AND p.target_code = material_packages.target
                                   AND p.package_code = material_packages.package_no)
            WHERE package_id IS NULL"""
    )
    await conn.execute(
        """UPDATE workflow_runs
              SET package_id = (SELECT p.id FROM packages p
                                 WHERE p.project_id = workflow_runs.project_id
                                   AND p.target_code = workflow_runs.target
                                   AND p.package_code = workflow_runs.package_no)
            WHERE package_id IS NULL"""
    )

    # reviews：对齐评分口径的判断（审查是否真正满足要求）。
    #   criterion_text 评审依据的评分口径；satisfied 满足度；
    #   covered_points / missing_points 命中的与缺失的得分要点（JSON 数组）
    cursor = await conn.execute("PRAGMA table_info(projects)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    for col in ("scope_target", "scope_package_no", "scope_package_name"):
        if col not in existing_cols:
            await conn.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT DEFAULT ''")

    cursor = await conn.execute("PRAGMA table_info(reviews)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    for col in ("criterion_text", "satisfied", "covered_points", "missing_points"):
        if col not in existing_cols:
            await conn.execute(f"ALTER TABLE reviews ADD COLUMN {col} TEXT")

    # material_chunks.page：出处页码（PDF 有；Word 无页码概念，留 0）
    cursor = await conn.execute("PRAGMA table_info(material_chunks)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if "page" not in existing_cols:
        await conn.execute("ALTER TABLE material_chunks ADD COLUMN page INTEGER")
    cursor = await conn.execute("PRAGMA table_info(materials)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if "file_size" not in existing_cols:
        await conn.execute("ALTER TABLE materials ADD COLUMN file_size INTEGER")
    if "parse_status" not in existing_cols:
        await conn.execute("ALTER TABLE materials ADD COLUMN parse_status TEXT DEFAULT 'pending'")
    # 逻辑删除列：老库补列（新库已在上面的 CREATE TABLE 里）
    _mat_cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(materials)")).fetchall()}
    if "deleted_at" not in _mat_cols:
        await conn.execute("ALTER TABLE materials ADD COLUMN deleted_at DATETIME")
    cursor = await conn.execute("PRAGMA table_info(projects)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if "target_pages" not in existing_cols:
        await conn.execute("ALTER TABLE projects ADD COLUMN target_pages INTEGER")
    # llm_usage 的归集列：老库的行留 NULL（语义是「不属于任何 run」），不回填。
    cursor = await conn.execute("PRAGMA table_info(llm_usage)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if "reasoning_tokens" not in existing_cols:
        await conn.execute("ALTER TABLE llm_usage ADD COLUMN reasoning_tokens INTEGER")
    if "project_id" not in existing_cols:
        await conn.execute("ALTER TABLE llm_usage ADD COLUMN project_id INTEGER")
    if "thread_id" not in existing_cols:
        await conn.execute("ALTER TABLE llm_usage ADD COLUMN thread_id TEXT")

    # 设计版图表 job：老库若已建过表，补 brief_json（新库由 _DDL 直接带）。
    _diagram_job_cols = {row[1] for row in await (await conn.execute(
        "PRAGMA table_info(diagram_jobs)")).fetchall()}
    if _diagram_job_cols and "brief_json" not in _diagram_job_cols:
        await conn.execute("ALTER TABLE diagram_jobs ADD COLUMN brief_json TEXT NOT NULL DEFAULT '{}'")

    _snapshot_cols = {row[1] for row in await (await conn.execute(
        "PRAGMA table_info(project_snapshots)")).fetchall()}
    if _snapshot_cols:
        if "package_id" not in _snapshot_cols:
            await conn.execute("ALTER TABLE project_snapshots ADD COLUMN package_id INTEGER")
        if "run_thread_id" not in _snapshot_cols:
            await conn.execute("ALTER TABLE project_snapshots ADD COLUMN run_thread_id TEXT")
    # 索引建在加列之后：老库的 blocks 表先执行上面的 ALTER 才有 run_thread_id，
    # 放进 _DDL 会在老库上引用了尚不存在的列而直接报错。
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blocks_project_run ON blocks(project_id, run_thread_id)"
    )
    # 同上：thread_id 要先 ALTER 出来才能建索引。
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_run ON llm_usage(thread_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_package ON project_snapshots(project_id, package_id, id)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    await conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,)
    )
    await conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
    # PRAGMA foreign_keys 在事务内是 no-op：先提交，再打开/校验。
    await conn.commit()
    # 外键默认关闭：历史库仍有孤儿行（实测 block_revisions/reviews 各有若干）。
    # 清理完成后用 APP_ENFORCE_FK=1 开启；开启前先做 foreign_key_check，避免带病运行。
    if os.environ.get("APP_ENFORCE_FK", "1").strip().lower() in {"1", "true", "yes"}:
        await conn.execute("PRAGMA foreign_keys = ON")
        violations = await (await conn.execute("PRAGMA foreign_key_check")).fetchall()
        if violations:
            raise RuntimeError(f"schema 外键校验失败：{len(violations)} 条孤儿行，先清理再开启 APP_ENFORCE_FK")
    await conn.commit()


@asynccontextmanager
async def get_db():
    """
    返回配置了 row_factory 的 aiosqlite 连接，用于 async with 上下文。
    row_factory = aiosqlite.Row 使 fetchone/fetchall 返回可按列名索引的 Row 对象。
    """
    if DB_PATH != ":memory:":
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        if os.environ.get("APP_ENFORCE_FK", "1").strip().lower() in {"1", "true", "yes"}:
            await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
