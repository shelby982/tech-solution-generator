#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境检查脚本 v2.3 - 增加项目初始化功能
用法: 
  python scripts/check_env.py                      # 检查环境
  python scripts/check_env.py --fix                # 尝试修复 markitdown
  python scripts/check_env.py <path> --init --fix  # 初始化项目并修复环境
"""

import sys
import os
import subprocess
import shutil
import argparse
from pathlib import Path

# ============ Windows 编码修复 ============
def fix_windows_encoding():
    if sys.platform == "win32":
        import io
        if hasattr(sys.stdout, 'buffer'):
            try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            except: pass
        try: os.system('chcp 65001 >nul 2>&1')
        except: pass

fix_windows_encoding()

# ============ 检查工具集 ============
def check_command(cmd, name):
    """检测外部命令是否可用"""
    try:
        # 使用 subprocess 运行 --version 检查
        result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version = result.stdout.split('\n')[0].strip()
            return True, f"{name} 已就绪 ({version[:40]})"
        return False, f"{name} 返回异常"
    except Exception:
        return False, f"{name} 未安装或不在 PATH 中"

def check_markitdown():
    """专门针对全局 markitdown 的检测"""
    if shutil.which("markitdown"):
        return True, "markitdown (全局工具已就绪)"
    return False, "markitdown 未安装 (建议运行 --fix)"

# ============ 修复逻辑 ============
def fix_dependencies():
    print("\n" + "-"*20 + "\n正在执行修复...")
    # 优先安装 uv
    if not shutil.which("uv"):
        print("  正在尝试安装 uv...")
        subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=False)
    
    # 使用 uv tool 安装 markitdown (全局模式)
    if shutil.which("uv"):
        print("  正在通过 uv tool 安装 markitdown...")
        try:
            subprocess.run(["uv", "tool", "install", "markitdown[all]"], check=True)
            print("  [OK] markitdown 安装成功")
        except Exception as e:
            print(f"  [FAIL] 安装失败: {e}")
    else:
        print("  [FAIL] 缺少 uv，无法安装工具")

# ============ 项目初始化逻辑 ============
def init_project(target_path: str, project_name: str = None):
    """初始化项目结构 - 调用 init_project.py 或内嵌逻辑"""
    
    # 方案 1: 调用同级目录下的 init_project.py
    script_dir = Path(__file__).parent
    init_script = script_dir / "init_project.py"
    
    if init_script.exists():
        print(f"\n正在调用 init_project.py 初始化项目...")
        cmd = [sys.executable, str(init_script), target_path]
        if project_name:
            cmd.extend(["--name", project_name])
        
        try:
            subprocess.run(cmd, check=True)
            print("[OK] 项目初始化完成")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] 初始化失败: {e}")
            return False
    else:
        # 方案 2: 内嵌简化版初始化逻辑 (fallback)
        print(f"\n未找到 init_project.py，使用内嵌逻辑初始化...")
        print(f"[WARN] init_project.py 应位于: {init_script}")
        
        base_path = Path(target_path).resolve()
        project_dirs = [
            "00_原始文件/01_标书正文",
            "00_原始文件/02_应标模板",
            "10_解析结果",
            "20_应标草稿",
            "30_最终输出",
            "_state",
        ]
        
        try:
            for d in project_dirs:
                (base_path / d).mkdir(parents=True, exist_ok=True)
            print(f"[OK] 基础目录创建完成: {base_path}")
            return True
        except Exception as e:
            print(f"[FAIL] 目录创建失败: {e}")
            return False

# ============ 主逻辑 ============
def main():
    parser = argparse.ArgumentParser(
        description="应标环境检查与项目初始化工具"
    )
    parser.add_argument("path", nargs="?", default=".", 
                        help="项目路径 (配合 --init 使用)")
    parser.add_argument("--init", action="store_true", 
                        help="初始化项目结构")
    parser.add_argument("--fix", action="store_true", 
                        help="自动修复环境依赖")
    parser.add_argument("--name", type=str, 
                        help="项目名称 (配合 --init 使用)")
    args = parser.parse_args()

    # 模式 1: 项目初始化
    if args.init:
        print("="*40 + "\n  项目初始化模式\n" + "="*40)
        init_success = init_project(args.path, args.name)
        
        # 初始化后自动检查环境
        if init_success and args.fix:
            print("\n" + "="*40 + "\n  环境修复\n" + "="*40)
            fix_dependencies()
        
        return 0 if init_success else 1

    # 模式 2: 环境检查
    print("="*40 + "\n  系统环境诊断\n" + "="*40)

    # 1. 核心工具检测
    uv_ok, uv_msg = check_command("uv", "uv")
    print(f"  [{'OK' if uv_ok else '!!'}] {uv_msg}")

    mk_ok, mk_msg = check_markitdown()
    print(f"  [{'OK' if mk_ok else '!!'}] {mk_msg}")

    # 2. 可选工具检测
    pd_ok, pd_msg = check_command("pandoc", "pandoc")
    print(f"  [{'OK' if pd_ok else '..'}] {pd_msg}")

    rg_ok, rg_msg = check_command("rg", "ripgrep")
    print(f"  [{'OK' if rg_ok else '..'}] {rg_msg}")

    # 执行修复
    if args.fix and not mk_ok:
        fix_dependencies()
        # 修复后再检查一次
        print("\n" + "="*40 + "\n  修复后二次检查\n" + "="*40)
        final_ok, final_msg = check_markitdown()
        print(f"  [{'OK' if final_ok else 'FAIL'}] {final_msg}")

    print("\n" + "="*40)
    return 0

if __name__ == "__main__":
    sys.exit(main())
