#!/usr/bin/env bash
# 一条命令跑完"代码 + 界面"的自检流水线。
# 用法：bash scripts/check.sh [--thread <threadId>] [--block <blockId>]
set -uo pipefail
cd "$(dirname "$0")/.."
THREAD="${THREAD:-24840930-be03-4745-99b3-8626200cf5ec}"
BLOCK="${BLOCK:-s1.2.1.1}"
FAIL=0
line() { printf '\n\033[1m%s\033[0m\n' "$1"; }
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=1; }

line "1/6 后端语法"
python3 -c "import compileall,sys; sys.exit(0 if compileall.compile_dir('backend', quiet=2) else 1)" && pass "backend 全部 .py 编译通过" || fail "backend 编译失败"

line "2/6 前端语法"
ok=1
for f in frontend/assets/*.mjs frontend/assets/*.js; do node --check "$f" >/dev/null 2>&1 || ok=0; done
[ $ok -eq 1 ] && pass "前端脚本语法通过" || fail "前端脚本语法错误"

line "3/6 资源版本一致性"
V=$(grep -rho "20261016-v[0-9]*" frontend/*.html frontend/assets/*.js frontend/assets/*.mjs 2>/dev/null | sort -u | tr '\n' ' ')
N=$(echo "$V" | wc -w | tr -d ' ')
[ "$N" -eq 1 ] && pass "版本号统一：$V" || fail "版本号不一致：$V"

line "4/6 后端单测"
( cd backend && LLM_MODE=mock ../.venv/bin/python -m pytest ../tests/ -q --no-header 2>&1 | tail -1 | tee /tmp/pytest.out ) >/dev/null
grep -q "failed" /tmp/pytest.out && fail "$(cat /tmp/pytest.out)" || pass "$(cat /tmp/pytest.out)"

line "5/6 前端单测"
FT=$(node --test tests/frontend/*.test.mjs 2>&1 | grep -E "ℹ (pass|fail)" | tr '\n' ' ')
echo "  $FT"
echo "$FT" | grep -q "fail 0" && pass "前端单测通过" || fail "前端单测失败"

line "6/6 界面自动审查（真实浏览器，按设计稿规则）"
if curl -s -o /dev/null --max-time 3 http://127.0.0.1:8015/api/writing-specs; then
  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:9333/json/version; then
    .venv/bin/python scripts/ui-selfcheck.py --thread "$THREAD" --block "$BLOCK" --shot docs/screenshots/ui-selfcheck.png \
      && pass "界面规则全部通过" || fail "界面规则未通过（见上）"
  else
    printf '  \033[33mSKIP\033[0m  未检测到 Chrome 调试端口 9333（按 README 启动后重跑）\n'
  fi
else
  printf '  \033[33mSKIP\033[0m  后端未运行（127.0.0.1:8015），跳过界面审查\n'
fi

line "结论"
[ $FAIL -eq 0 ] && printf '  \033[32m全部通过\033[0m\n' || printf '  \033[31m有失败项\033[0m\n'
exit $FAIL
