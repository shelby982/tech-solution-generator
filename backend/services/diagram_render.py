"""把自包含的图表 HTML 渲染成 PNG。

使用 Playwright/Chromium，与上游 skill 的 export_png.py 同一条路：
file:// 打开 → 断网 → 等字体 → 截取第一个顶层 SVG。
没有安装 Playwright 时抛清晰错误，由上层把 job 标失败，不伪造图片。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

# 本地开发：浏览器可能装在项目内（沙箱不允许写 ~/Library/Caches）。
_LOCAL_BROWSERS = Path(__file__).resolve().parent.parent.parent / ".playwright-browsers"
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and _LOCAL_BROWSERS.is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_LOCAL_BROWSERS)

_VIEWBOX_RE = re.compile(r'viewBox=["\']\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)')


def viewbox_size(html: str) -> tuple[int, int]:
    match = _VIEWBOX_RE.search(str(html or ""))
    if not match:
        return 1280, 720
    width, height = float(match.group(3)), float(match.group(4))
    if width <= 0 or height <= 0:
        return 1280, 720
    return int(round(width)), int(round(height))


def render_png_sync(html_path: Path, out_path: Path, scale: int = 2) -> tuple[int, int, dict]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "设计版图表渲染需要 Playwright：pip install playwright && playwright install chromium"
        ) from exc

    html_path = Path(html_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(device_scale_factor=max(1, min(3, int(scale))))
            # 只允许本地文件；任何远程字体/图片/脚本请求都断掉。
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort())
            page.goto(html_path.resolve().as_uri() + "?motion=static", wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                page.wait_for_timeout(800)
            page.evaluate("document.fonts && document.fonts.ready")
            svg = page.locator("svg").first
            if svg.count() == 0:
                raise RuntimeError("生成的 HTML 里没有 SVG")
            svg.evaluate(
                """el => {
                    const box = el.viewBox && el.viewBox.baseVal;
                    if (!box || !Number.isFinite(box.width) || !Number.isFinite(box.height) ||
                        box.width <= 0 || box.height <= 0) return;
                    for (const axis of ['width', 'height']) {
                        el.style.setProperty(axis, box[axis] + 'px', 'important');
                        el.style.setProperty('min-' + axis, box[axis] + 'px', 'important');
                        el.style.setProperty('max-' + axis, box[axis] + 'px', 'important');
                    }
                }"""
            )
            page.wait_for_timeout(150)
            geometry = _audit_geometry(page)
            svg.screenshot(path=str(out_path), omit_background=False)
        finally:
            browser.close()
    try:
        from PIL import Image
        with Image.open(out_path) as image:
            return image.width, image.height, geometry
    except Exception:  # noqa: BLE001
        width, height = viewbox_size(html_path.read_text(encoding="utf-8", errors="ignore"))
        return width * max(1, min(3, int(scale))), height * max(1, min(3, int(scale))), geometry


def _audit_geometry(page) -> dict:
    """轻量几何审计：文本越界 + 文本标签互相遮挡（只报告，不阻断）。"""
    try:
        return page.evaluate(
            """() => {
                const svg = document.querySelector('svg');
                if (!svg) return {texts: 0, overflow: [], overlaps: []};
                const vb = svg.viewBox && svg.viewBox.baseVal;
                const boxes = [...svg.querySelectorAll('text')]
                    .filter((el) => (el.textContent || '').trim())
                    .map((el) => {
                        let b;
                        try { b = el.getBBox(); } catch (e) { return null; }
                        return {text: (el.textContent || '').trim().slice(0, 40),
                                x: b.x, y: b.y, w: b.width, h: b.height};
                    }).filter(Boolean);
                const overflow = vb ? boxes.filter((b) => b.x < -2 || b.y < -2 ||
                    b.x + b.w > vb.width + 2 || b.y + b.h > vb.height + 2).slice(0, 8) : [];
                const overlaps = [];
                const area = (b) => Math.max(0, b.w) * Math.max(0, b.h);
                for (let i = 0; i < boxes.length && overlaps.length < 8; i += 1) {
                    for (let j = i + 1; j < boxes.length && overlaps.length < 8; j += 1) {
                        const a = boxes[i], b = boxes[j];
                        const ox = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
                        const oy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
                        const inter = ox * oy;
                        if (inter > 0 && inter > Math.min(area(a), area(b)) * 0.3) {
                            overlaps.push({a: a.text, b: b.text});
                        }
                    }
                }
                return {texts: boxes.length, overflow, overlaps};
            }"""
        )
    except Exception:  # noqa: BLE001
        return {"texts": 0, "overflow": [], "overlaps": [], "error": "geometry audit failed"}


async def render_png(html_path: Path, out_path: Path, scale: int = 2) -> tuple[int, int, dict]:
    return await asyncio.to_thread(render_png_sync, html_path, out_path, scale)
