---
name: bid-setup
description: "应标项目快速初始化 - 使用全局工具模式,无需 uv sync。触发：创建项目、环境检查、依赖修复。  Use when: 用户需要"初始化环境"、"安装依赖"时使用。
dependencies: [bid-session-manager]
version: 2.0
---

# Bid Setup (应标环境快速初始化)

> **首次使用必读**：本 Skill 负责环境检查和项目初始化，是所有应标工作的起点。

## 核心规则（强制执行）

### 全新项目直接触发
如果是通过 其他skills 出发的本脚本，全局优先使用下面的命令：

```python
python -c "
from pathlib import Path
import os

if not Path('pyproject.toml').exists():
    print('[INFO] 新项目检测，正在初始化...')
    os.system('python .chatcode/skills/bid-setup/scripts/check_env.py "<项目路径>" --init  --fix')
else:
    print('[OK] 项目已初始化')
"
```

### 规则 0：工具模式优先（最高优先级）

```python
# 正确的工作流程，目录根据
python bid-setup/scripts/check_env.py "<项目路径>" --init --fix  # 一键初始化

# 禁止的操作
uv sync                    # 不需要!
source .venv/bin/activate  # 不需要!
```


### 规则 1：生产环境 Shell 规范（所有 Skill 必须遵守）

> **关键背景**: 生产环境 Agent 在 Windows 上默认使用 **CMD** (不是 bash/PowerShell)

| 操作 | 禁止 | 正确 |
|------|---------|---------|
| 创建目录 | `mkdir -p` | `python -c "import os; os.makedirs('path', exist_ok=True)"` |
| 安装工具 | `pip install` | `uv tool install` |
| 运行脚本 | `python` | `uv run scripts/xxx.py` |
| 检查工具 | `which markitdown` | `python -c "import shutil; exit(0 if shutil.which('markitdown') else 1)"` |

### 规则 2：路径规范

含路径必须用双引号包裹,避免中文/空格错误:
```bash
# 正确
uv run scripts/convert.py "发售稿--广东省机场.docx" -o "10_解析结果/标书全文.md"

# 错误
python scripts/convert.py 发售稿--广东省机场.docx -o 10_解析结果/标书全文.md
```


## Workflow

### 场景 1：创建新项目（推荐一键命令）
check_env.py路径自行替换
```bash
# 一键初始化 
python scripts/check_env.py "<项目路径>" --init --fix

# 等效于:
# 1. 创建目录结构
# 2. 生成 pyproject.toml / project.json / BID.md
# 3. 自动安装 uv tool install markitdown
```


### 场景 2：检查已有项目

```bash
# 仅检查（不修复）
python <skill路径>/scripts/check_env.py

# 检查 + 自动修复
python <skill路径>/scripts/check_env.py --fix
```

### 场景 3：接手他人项目

```bash
# 1. 检查环境
python <skill路径>/scripts/check_env.py

# 2. 如果缺失依赖,自动修复
python <skill路径>/scripts/check_env.py --fix

# 3. 查看进度
python -c "import json; print(json.dumps(json.load(open('project.json', encoding='utf-8')), ensure_ascii=False, indent=2))"
```

## Troubleshooting

### Q: markitdown 命令找不到

**解决**:
```bash
# 自动修复
python <skill路径>/ scripts/check_env.py --fix

# 或手动安装
uv tool install markitdown[docx,pdf,pptx]
```

### Q: 旧项目有 .venv/ 目录怎么办?

**解决**:
```bash
# 1. 删除虚拟环境 (可选)
python -c "import shutil; shutil.rmtree('.venv', ignore_errors=True)"

# 2. 重新初始化
python scripts/check_env.py --fix
```

---

## Red Flags（强制遵守）

### Always
1. **推荐使用 `uv run`**
2. **禁止使用 `uv sync`**
2. **降级使用 `python scripts/xxx.py`**
3. **工具用 `uv tool install` 全局安装**
4. 新项目用一键命令: `python scripts/check_env.py <path> --init --fix`
5. 使用跨平台 Python 命令 (避免 mkdir -p / which 等)

### Never
1. 不要创建虚拟环境
2. 不要运行 `uv sync`
3. 不要使用 Unix-only 命令 (生产环境是 Windows CMD)

---

## Decision Tree

```
用户发起应标任务
    │
    ├─ 确定项目路径
    │   ├─ 用户明确指定? → 使用指定路径
    │   └─ 未指定? → 询问或使用 .
    │
    ├─ 检测 markitdown 工具
    │   ├─ 不存在 → python scripts/check_env.py <路径> --init --fix
    │   └─ 存在   → 继续后续任务
    │
    └─ 执行任务 (用 python 运行脚本,用 markitdown 转换文件)
```

## 最佳实践

**一键命令（强烈推荐）**:
```bash
# 新项目
python scripts/check_env.py "<项目路径>" --init --fix

# 已有项目
python scripts/check_env.py --fix
```
