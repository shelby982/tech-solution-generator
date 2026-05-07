# 技术方案生成助手

> 上传技术规范书 → AI 逐章节智能扩写 → 下载完整技术方案文档

一款专为投标、方案编写场景设计的 AI 辅助工具。只需上传采购方的技术规范书（招标文件），系统自动解析文档目录结构，调用大语言模型逐章节生成专业的技术方案内容，最终支持导出为 Word 或 Markdown 格式。

---

## 目录

- [整体流程](#整体流程)
- [上传文件要求](#上传文件要求)
- [模型配置说明](#模型配置说明)
- [生成参数设置](#生成参数设置)
- [下载格式说明](#下载格式说明)
- [本地启动](#本地启动)
- [Docker 部署](#docker-部署)
- [云平台部署](#云平台部署)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 整体流程

```
上传规范书 (PDF/DOCX)
        ↓
系统自动解析文档目录结构
  · 识别章节标题（支持 1~4 级）
  · 提取各章节原始内容作为生成上下文
        ↓
配置 API Key（OpenAI / Claude / 豆包 / Kimi）
        ↓
设置每章节目标字数（默认 500 字）
        ↓
点击「开始生成方案」
  · 按目录顺序逐章节调用 LLM
  · 实时流式展示生成内容
  · 左侧目录显示各章节进度
  · 顶部显示总字数统计
        ↓
全部章节完成后下载
  · Word (.docx) — 适合编辑和提交
  · Markdown (.md) — 适合二次加工
```

### 界面区域说明

| 区域 | 功能 |
|------|------|
| 左侧目录栏 | 显示解析出的章节目录，标注各章节生成状态和字数 |
| 右侧内容区 | 实时流式展示当前正在生成的章节内容（Markdown 渲染） |
| 顶部进度条 | 显示整体完成进度百分比和已生成总字数 |
| 右上角配置按钮 | 管理 API Key 和模型参数 |
| 操作栏 | 目标字数设置、开始生成、下载按钮 |

---

## 上传文件要求

### 支持格式

| 格式 | 说明 |
|------|------|
| `.docx` | Microsoft Word 文档，推荐格式，识别准确率最高 |
| `.pdf` | PDF 文档，支持文字型 PDF（不支持纯扫描图片） |

> **注意**：扫描件 PDF（即整页为图片的 PDF）无法提取文字，系统将无法识别章节。如需处理扫描件，请先用 OCR 工具转换为可检索 PDF 或 DOCX。

### DOCX 文件要求

系统通过 Word 的**标题样式**识别章节层级，建议：

- 一级标题使用「**标题 1**」（Heading 1）样式
- 二级标题使用「**标题 2**」（Heading 2）样式
- 三级标题使用「**标题 3**」（Heading 3）样式
- 四级标题使用「**标题 4**」（Heading 4）样式

若文档未使用 Heading 样式，系统会自动根据编号格式识别标题：

| 示例 | 识别层级 |
|------|---------|
| `1. 总体架构` | 一级（Level 1） |
| `1.1 系统设计` | 二级（Level 2） |
| `1.1.1 数据库设计` | 三级（Level 3） |
| `1.1.1.1 表结构说明` | 四级（Level 4） |
| `第一章 项目概述` | 一级（Level 1） |
| `一、项目背景` | 一级（Level 1） |

### PDF 文件要求

系统通过以下策略识别 PDF 中的标题：

1. **字体大小**：字号明显大于正文（超过正文字号 15%）的文字行，识别为一级标题
2. **编号格式**：与上表相同的编号规则，优先级高于字体大小
3. **行长度**：字体偏大且长度较短（不超过 60 字符），识别为二级标题

**PDF 最佳实践：**
- 标题字号比正文大至少 2pt
- 使用标准数字编号（`1.`、`1.1`、`1.1.1`）
- 避免将标题做成图片或艺术字
- 确保 PDF 是"可检索"（可复制文字）而非扫描件

### 文件大小建议

建议单份文件不超过 **20 MB**，章节数量不超过 **50 个**。章节数量过多会导致生成时间过长（每章节约需 20～60 秒，取决于目标字数和模型速度）。

---

## 模型配置说明

点击右上角「⚙️ 配置 API」按钮进入配置面板。**配置一次后自动保存，重启服务无需重新输入。**

### 支持的模型提供商

#### OpenAI

适合追求最高质量输出，或已有 OpenAI API 账户的用户。

| 字段 | 填写内容 |
|------|---------|
| 提供商 | OpenAI |
| API Key | 以 `sk-` 开头，在 [platform.openai.com](https://platform.openai.com/api-keys) 获取 |
| Base URL | `https://api.openai.com/v1`（默认，可留空） |
| 模型 | `gpt-4o`（默认）、`gpt-4o-mini`、`gpt-4-turbo` 等 |

> **兼容中转服务**：如使用 API 中转站（如 ChatANY、API2D 等），将 Base URL 替换为中转地址，其余不变。

#### Anthropic Claude

适合需要长文本、逻辑严密输出的场景。

| 字段 | 填写内容 |
|------|---------|
| 提供商 | Claude |
| API Key | 以 `sk-ant-` 开头，在 [console.anthropic.com](https://console.anthropic.com) 获取 |
| Base URL | `https://api.anthropic.com`（默认，可留空） |
| 模型 | `claude-3-5-sonnet-20241022`（默认）、`claude-3-haiku-20240307` 等 |

#### 豆包（ByteDance / 火山引擎）

国内可直连，无需代理，适合对数据合规有要求的场景。

| 字段 | 填写内容 |
|------|---------|
| 提供商 | 豆包 |
| API Key | 在[火山引擎控制台](https://console.volcengine.com/ark) → API Key 管理中获取 |
| Base URL | `https://ark.cn-beijing.volces.com/api/v3`（默认，可留空） |
| 模型 | `doubao-pro-32k`（默认）；需在控制台先开通对应模型 |

> **豆包注意事项**：需在火山引擎控制台创建推理接入点，并确认模型 ID 与控制台一致。

#### Kimi（月之暗面）

国内可直连，长文本能力强，适合规范书较长的场景。

| 字段 | 填写内容 |
|------|---------|
| 提供商 | Kimi |
| API Key | 以 `sk-` 开头，在 [platform.moonshot.cn](https://platform.moonshot.cn/console/api-keys) 获取 |
| Base URL | `https://api.moonshot.cn/v1`（默认，可留空） |
| 模型 | `moonshot-v1-32k`（默认）、`moonshot-v1-8k`、`moonshot-v1-128k` |

### 配置操作步骤

1. 点击右上角「⚙️ 配置 API」
2. 选择提供商（点击对应按钮，Base URL 和模型自动填入）
3. 在「API Key」输入框中粘贴密钥
4. 点击「✓ 验证连接」——系统将发送一次测试请求确认 Key 有效
5. 验证通过后点击「保存配置」

**修改配置时无需重新输入 Key**：如只想更换模型或 Base URL，API Key 输入框保留原值即可，系统会自动沿用已保存的密钥。

### 安全说明

- API Key 存储于服务器本地文件（`.api_config.json`），不会上传至任何第三方
- 页面显示时 Key 自动脱敏（仅显示首尾各 4 位）
- 如需清除配置，删除项目根目录下的 `.api_config.json` 文件即可

---

## 生成参数设置

### 目标字数

在操作栏的「目标字数」输入框中设置每个章节的扩写目标，范围 100～10000 字，默认 500 字。

| 建议字数 | 适用场景 |
|---------|---------|
| 200～500 字 | 概述类章节、目录项较多时快速生成 |
| 500～1000 字 | 常规技术章节，内容较完整 |
| 1000～2000 字 | 核心功能章节，需要详细展开 |
| 2000 字以上 | 重点技术方案章节，但生成时间较长 |

> **说明**：目标字数为 AI 的生成指导，实际输出字数可能略有浮动（±20%）。统计字数以中文字符为准。

### 字数统计

- 每个章节完成后，目录中对应条目右侧显示该章节字数
- 顶部进度条右侧实时显示**已生成总字数**
- 全部完成后，汇总卡片展示所有章节的字数分布

---

## 下载格式说明

所有章节生成完成后，页面下方出现两个下载按钮。

### Word 格式（.docx）

点击「⬇ 下载 Word」下载 `.docx` 文件，适合提交给客户或进一步编辑排版。

**文档格式规范：**

| 元素 | 样式 |
|------|------|
| 页面 | A4 纸张，上下左右各 2.5 cm 边距 |
| 正文字体 | 宋体，11pt |
| 一级标题 | 16pt，深蓝色（#1F3864），段前 12pt |
| 二级标题 | 14pt，蓝色（#2E5496），段前 10pt |
| 三级标题 | 12pt，浅蓝色（#2E75B6），段前 8pt |
| 四级标题 | 11pt，深灰色（#404040） |
| 代码块 | Courier New 9pt，灰色背景（#F3F4F6） |
| 表格 | 网格边框，表头行浅蓝背景（#DBEAFE） |

**Markdown 转换支持的元素：**
- 标题（`#`～`####`）
- 加粗（`**文字**`）、斜体（`*文字*`）、行内代码（`` `代码` ``）
- 无序列表（`-`）、有序列表（`1.`）
- 代码块（` ``` `）
- 表格（`| 列1 | 列2 |`）

### Markdown 格式（.md）

点击「⬇ 下载 Markdown」下载 `.md` 文件，适合在 Markdown 编辑器（Typora、Obsidian、语雀等）中查看和二次加工。

文件内容为各章节标题 + 生成内容的纯文本拼接，可直接粘贴到其他系统中使用。

### 下载文件命名

两种格式的文件名均为：`技术方案_任务ID前8位.docx / .md`，例如：`技术方案_3f8a1b2c.docx`

---

## 本地启动

### 环境要求

- Python 3.10 或以上
- macOS / Linux / Windows（WSL2）

### 一键启动

```bash
cd tech-solution-generator
chmod +x start.sh
./start.sh
```

启动成功后浏览器自动打开 `http://localhost:8000`。

### 手动启动

```bash
cd tech-solution-generator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Docker 部署

```bash
# 构建镜像
docker build -t tech-solution-generator .

# 运行容器（-v 挂载持久化卷，API 配置重建容器后不丢失）
docker run -d \
  --name tech-solution \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  tech-solution-generator

# 访问
open http://localhost:8000
```

**环境变量：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `8000` | 服务监听端口 |
| `CONFIG_PATH` | 项目根目录 | API 配置文件存放路径，建议指向挂载卷 |

---

## 云平台部署

### Railway（推荐）

1. Fork 本仓库到 GitHub
2. 登录 [railway.app](https://railway.app) → New Project → Deploy from GitHub Repo
3. 选择仓库，Railway 自动识别 `Dockerfile` 开始构建
4. 在 **Volumes** 中挂载持久化磁盘到 `/data`
5. 在 **Variables** 中添加 `CONFIG_PATH=/data`
6. Settings → Networking → Generate Domain 获取公网地址

### Render

1. Fork 仓库到 GitHub
2. 登录 [render.com](https://render.com) → New → Web Service → Connect Repository
3. Runtime 选择 Docker，Render 自动读取 `render.yaml`（含磁盘挂载配置）
4. 在 Environment 中添加 `CONFIG_PATH=/data`
5. 点击 Create Web Service

> Render 免费套餐无流量时会休眠，首次访问需约 30 秒唤醒。

---

## 项目结构

```
tech-solution-generator/
├── backend/
│   ├── main.py                  # FastAPI 入口，静态文件服务
│   ├── routes/
│   │   ├── upload.py            # 文件上传与解析（返回原始文件名）
│   │   ├── config.py            # API Key 配置（保存 / 验证 / 清除）
│   │   ├── generate.py          # SSE 流式生成引擎
│   │   └── download.py          # 结果下载（MD / DOCX / JSON）
│   ├── services/
│   │   ├── parser.py            # PDF / DOCX 解析（4级标题识别）
│   │   ├── llm.py               # LLM 调用（OpenAI / Claude / 豆包 / Kimi）
│   │   ├── config_store.py      # 配置内存存储 + JSON 文件持久化
│   │   ├── task_store.py        # 生成任务状态管理
│   │   └── docx_generator.py    # Markdown → Word 文档转换
│   └── utils/
│       └── sse.py               # SSE 事件格式化
├── frontend/
│   └── index.html               # 单文件前端（Tailwind CSS + marked.js）
├── tests/
│   ├── conftest.py
│   └── test_api.py
├── Dockerfile
├── .dockerignore
├── railway.toml                 # Railway 部署配置
├── render.yaml                  # Render 部署配置
├── requirements.txt
├── requirements-dev.txt
├── start.sh                     # 一键本地启动
└── README.md
```

---

## 常见问题

**Q：上传后章节识别为空，怎么处理？**

DOCX：检查文档是否使用了「标题 1/2/3/4」样式。在 Word 中选中标题文字，查看右侧「样式」面板中显示的样式名。

PDF：确认是可检索 PDF（用鼠标能选中文字），且标题字号明显大于正文，或使用了标准数字编号格式。

---

**Q：生成内容质量不理想，怎么提升？**

- 选用能力更强的模型（GPT-4o、Claude 3.5 Sonnet、豆包 Pro）
- 适当增加目标字数，让模型有更大的发挥空间
- 上传的规范书章节原文内容越详细，AI 生成的参考上下文越丰富，输出质量越高

---

**Q：生成中途断开，已完成的内容会丢失吗？**

不会。已完成的章节内容保存在服务器内存中，可直接点击「下载」导出已完成部分。刷新页面或重新生成才会清空。

---

**Q：端口 8000 被占用怎么办？**

```bash
# 查找占用进程
lsof -i :8000
# 修改 start.sh 中的端口，或启动时指定其他端口
uvicorn main:app --port 8001
```

---

**Q：豆包 API 调用失败，提示模型不存在？**

豆包（火山引擎）的模型 ID 需要在控制台创建推理接入点后才能使用，且模型 ID 格式为 `ep-xxxxxxxx-xxxxx`（接入点 ID）而非 `doubao-pro-32k`。请在配置面板中将「模型」字段修改为实际的接入点 ID。

---

**Q：如何清除已保存的 API Key？**

删除项目根目录（或 `CONFIG_PATH` 指向的目录）下的 `.api_config.json` 文件，重启服务后配置清空。

---

## 许可证

MIT License
