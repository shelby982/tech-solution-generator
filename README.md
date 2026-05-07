# 📄 技术方案生成助手

> 上传技术规范书（PDF / DOCX）→ AI 逐章扩写 → 一键下载完整技术方案（Markdown / Word）

---

## 功能特性

- **智能目录解析**：自动识别 PDF / DOCX 中的标题层级（最多 4 级），构建结构化章节目录
- **逐章 AI 扩写**：按目录顺序逐章调用大语言模型，生成专业、可落地的技术方案内容
- **字数目标设置**：可配置每章节目标字数（100～10000），生成后实时统计字数
- **实时流式显示**：通过 SSE 技术实时展示 AI 生成过程，无需等待
- **多格式下载**：支持 Markdown（`.md`）和 Word（`.docx`）格式下载
- **多模型支持**：兼容 OpenAI、Anthropic Claude、豆包（ByteDance）、Kimi（月之暗面）及自定义 Base URL
- **配置持久化**：API 配置自动保存到本地文件，服务重启后无需重新配置

---

## 环境要求

| 依赖 | 版本要求 |
|------|---------|
| Python | **3.10 或以上** |
| 操作系统 | macOS / Linux / Windows（WSL2） |
| 网络 | 可访问所选 LLM API 服务 |

---

## 快速启动

```bash
# 1. 进入项目目录
cd tech-solution-generator

# 2. 添加执行权限（首次运行）
chmod +x start.sh

# 3. 一键启动（自动创建虚拟环境、安装依赖、打开浏览器）
./start.sh
```

启动成功后浏览器将自动打开 `http://localhost:8000`。

> **Windows 用户**：请在 Git Bash 或 WSL2 中运行 `./start.sh`。

---

## 🐳 Docker 部署

### 本地 Docker 运行

```bash
# 构建镜像
docker build -t tech-solution-generator .

# 运行容器（挂载持久化卷，API 配置不会随容器删除而丢失）
docker run -d \
  --name tech-solution \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  tech-solution-generator

# 访问应用
open http://localhost:8000
```

> **持久化说明**：`-v $(pwd)/data:/data` 将 API 配置文件映射到宿主机 `./data/` 目录，重建容器后配置仍然保留。

停止和删除容器：
```bash
docker stop tech-solution
docker rm tech-solution
```

---

## ☁️ 一键部署到 Railway

[Railway](https://railway.app) 是一个便捷的云平台，支持直接从 GitHub 部署。

### 步骤

1. **Fork 本仓库**到你的 GitHub 账户

2. **登录 Railway** → New Project → Deploy from GitHub Repo → 选择 Fork 的仓库

3. Railway 自动识别 `Dockerfile`，点击 **Deploy** 开始构建

4. 构建完成后，在 **Settings → Networking** 中生成公网域名

5. （可选）在 **Variables** 中添加环境变量：
   ```
   PORT=8000
   ```

6. 在 **Volumes** 中挂载持久化磁盘到 `/data`，确保 API 配置在重部署后不丢失

> **Railway 免费套餐**：每月 $5 额度，足够轻量使用。

---

## ☁️ 部署到 Render

[Render](https://render.com) 同样支持 Docker，且有永久免费套餐（有休眠机制）。

### 步骤

1. **Fork 本仓库**到 GitHub

2. 登录 Render → New → **Web Service** → Connect Repository

3. 设置：
   - **Environment**: Docker
   - **Dockerfile Path**: `./Dockerfile`
   - **Health Check Path**: `/health`

4. Render 将自动使用项目根目录的 `render.yaml` 配置，包含持久化磁盘挂载

5. 点击 **Create Web Service** 开始部署

> **注意**：Render 免费套餐在无流量时会休眠，首次访问需要约 30 秒唤醒。

---

## 🔧 环境变量说明

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PORT` | `8000` | 服务监听端口 |
| `CONFIG_PATH` | 项目根目录 | API 配置文件（`.api_config.json`）存放目录，建议指向持久化卷 |

---

## 使用流程

### 第一步：上传规范书

将技术规范书（PDF 或 DOCX 格式）拖拽到上传区，或点击选择文件。系统将自动解析文档目录结构，列出所有识别到的章节。

> **提示**：DOCX 文件建议使用标准 Heading 1~4 样式设置标题；PDF 文件建议标题使用较大字号或带编号格式（如 `1.`、`1.1`、`1.1.1`、`第一章`）。

### 第二步：配置 API Key

点击右上角「⚙️ 配置 API」按钮，选择提供商并填入 API Key：

| 提供商 | API Key 格式 | 默认模型 |
|--------|-------------|---------|
| OpenAI | `sk-...` | `gpt-4o` |
| Claude | `sk-ant-...` | `claude-3-5-sonnet-20241022` |
| 豆包 | ARK API Key | `doubao-pro-32k` |
| Kimi | `sk-...` | `moonshot-v1-32k` |

配置成功后会自动保存，服务重启后无需重新输入。

### 第三步：开始生成

设置目标字数后，点击「🚀 开始生成方案」，系统将按章节顺序逐个调用 AI 生成内容。

### 第四步：下载方案

所有章节生成完成后，可下载：
- **Word (.docx)**：适合进一步编辑和打印
- **Markdown (.md)**：适合在 Markdown 编辑器中查看

---

## 项目结构

```
tech-solution-generator/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── routes/
│   │   ├── upload.py            # 文件上传与解析
│   │   ├── config.py            # API Key 配置管理
│   │   ├── generate.py          # SSE 流式生成引擎
│   │   └── download.py          # 结果下载（MD / DOCX）
│   ├── services/
│   │   ├── parser.py            # PDF / DOCX 文档解析（支持4级标题）
│   │   ├── llm.py               # LLM 调用封装（OpenAI / Claude / 豆包 / Kimi）
│   │   ├── config_store.py      # API Key 内存存储 + 文件持久化
│   │   ├── task_store.py        # 生成任务状态管理
│   │   └── docx_generator.py    # Markdown → Word 文档转换
│   └── utils/
│       └── sse.py               # SSE 事件格式化工具
├── frontend/
│   └── index.html               # 单文件前端（Tailwind CSS + marked.js）
├── tests/
│   ├── conftest.py              # pytest 配置
│   └── test_api.py              # 集成测试
├── Dockerfile                   # Docker 镜像构建
├── .dockerignore                # Docker 构建排除列表
├── railway.toml                 # Railway 部署配置
├── render.yaml                  # Render 部署配置
├── requirements.txt             # 生产依赖
├── requirements-dev.txt         # 开发 / 测试依赖
├── start.sh                     # 一键本地启动脚本
└── README.md
```

---

## 开发模式

```bash
source .venv/bin/activate
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

---

## 常见问题

**Q：上传文档后章节识别为空？**
- DOCX：确认标题使用了「标题 1~4」等 Heading 样式
- PDF：确认标题字号明显大于正文，或使用标准编号格式（`1.`、`1.1`、`1.1.1`）

**Q：API Key 验证失败？**
- 确认 Key 格式正确，并检查账户余额是否充足
- 豆包需在火山引擎控制台获取 ARK API Key，并在 Base URL 填写对应端点

**Q：Docker 重建容器后配置丢失？**
- 确保启动时挂载了 `-v` 卷到 `/data`，并设置 `CONFIG_PATH=/data`

**Q：Railway / Render 部署后访问速度慢？**
- 首次构建约需 2～3 分钟；Render 免费套餐无流量时会休眠，首次访问约 30 秒唤醒

---

## 许可证

MIT License
