#!/bin/bash
# =====================================================
#  技术方案生成助手 v1.0 — 一键启动脚本
# =====================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$SCRIPT_DIR/.venv"
PORT=8000

# ── 彩色输出 ──────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║      技术方案生成助手  v1.0          ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── 1. 检查 Python 版本（需要 3.10+）──────────────
PYTHON_BIN=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info[:2])")
        MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)")
        MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON_BIN="$cmd"
            info "Python 版本：$("$cmd" --version)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    error "未找到 Python 3.10 或以上版本。\n  请从 https://www.python.org 下载安装后重试。"
fi

# ── 2. 检查端口是否被占用 ─────────────────────────
if lsof -iTCP:$PORT -sTCP:LISTEN -t &>/dev/null 2>&1; then
    warn "端口 $PORT 已被占用！"
    echo "     请先关闭占用端口的进程，或修改 start.sh 中的 PORT 变量后重试。"
    echo "     查看占用进程：lsof -i :$PORT"
    exit 1
fi

# ── 3. 创建 / 激活虚拟环境 ───────────────────────
if [ ! -d "$VENV_DIR" ]; then
    info "创建虚拟环境..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "虚拟环境已激活：$VENV_DIR"

# ── 4. 安装依赖（仅在需要时更新）────────────────
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
STAMP="$VENV_DIR/.install_stamp"

# 若 requirements.txt 比安装时间戳新，则重新安装
if [ ! -f "$STAMP" ] || [ "$REQUIREMENTS" -nt "$STAMP" ]; then
    info "安装 / 更新依赖包..."
    pip install -r "$REQUIREMENTS" -q --disable-pip-version-check
    touch "$STAMP"
else
    info "依赖包已是最新，跳过安装"
fi

# ── 5. 启动服务 ───────────────────────────────────
echo ""
info "服务启动中..."
echo ""
echo "  ┌──────────────────────────────────────────┐"
echo "  │  访问地址：http://localhost:$PORT          │"
echo "  │  API 文档：http://localhost:$PORT/docs     │"
echo "  │  按 Ctrl+C 停止服务                       │"
echo "  └──────────────────────────────────────────┘"
echo ""

# ── 6. 自动打开浏览器（延迟 1.5 秒等待服务就绪）──
(
    sleep 1.5
    URL="http://localhost:$PORT"
    if command -v open &>/dev/null; then          # macOS
        open "$URL"
    elif command -v xdg-open &>/dev/null; then    # Linux
        xdg-open "$URL"
    elif command -v start &>/dev/null; then        # Windows Git Bash
        start "$URL"
    fi
) &

# ── 7. 启动 uvicorn（生产模式，无热重载）─────────
#   开发模式（代码改动自动重启）请加 --reload 参数：
#   uvicorn main:app --host 0.0.0.0 --port $PORT --reload
cd "$BACKEND_DIR"
exec uvicorn main:app --host 0.0.0.0 --port "$PORT" --log-level info
