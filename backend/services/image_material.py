"""R3 图片素材：从上传文件里抽出图片（DOCX 直接取媒体；PDF 按图片区域渲染），
配图注（邻近上文）存为素材，可插入正文。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent.parent / "data" / "media"
MAX_IMAGES = 40
MIN_SIZE = 90          # 小于 90px 的图片（图标/线条）跳过


def _caption_of(texts: list[str]) -> str:
    """取图片前一行的短文本当图注（多半是"图 3-1 xxx"）。"""
    for text in reversed(texts[-3:]):
        line = str(text or "").strip()
        if 3 <= len(line) <= 60:
            return line
    return ""


def extract_docx_images(path: str, out_dir: Path | None = None) -> list[dict]:
    """DOCX：从 zip 的 word/media 里取图片（按出现顺序），用文档段落文本猜图注。"""
    import docx
    import zipfile

    out_dir = out_dir or MEDIA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    try:
        with zipfile.ZipFile(path) as archive:
            media = [name for name in archive.namelist()
                     if name.startswith("word/media/") and re.search(r"\.(png|jpe?g|gif|bmp)$", name, re.I)]
            paragraphs = [p.text for p in docx.Document(path).paragraphs if str(p.text or "").strip()]
    except Exception:
        return results
    for index, name in enumerate(media[:MAX_IMAGES]):
        try:
            data = archive.read(name) if False else None
        except Exception:
            data = None
        try:
            import zipfile as _zip
            with _zip.ZipFile(path) as archive:
                data = archive.read(name)
        except Exception:
            continue
        suffix = Path(name).suffix.lower()
        target = out_dir / f"docx_{Path(path).stem}_{index}{suffix}"
        try:
            target.write_bytes(data)
        except Exception:
            continue
        results.append({"page": 0, "path": str(target), "caption": _caption_of(paragraphs),
                        "source": "docx", "size": len(data)})
    return results


def extract_pdf_images(path: str, out_dir: Path | None = None) -> list[dict]:
    """PDF：用 pdfplumber 找出含图片的页面，按图片区域渲染成 PNG（限 40 张）。"""
    out_dir = out_dir or MEDIA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    try:
        import pdfplumber
    except Exception:
        return results
    try:
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                if len(results) >= MAX_IMAGES:
                    break
                try:
                    images = page.images or []
                except Exception:
                    continue
                if not images:
                    continue
                page_text = (page.extract_text() or "").splitlines()
                page_text = [line for line in page_text if line.strip()]
                for index, image in enumerate(images[:6]):
                    if len(results) >= MAX_IMAGES:
                        break
                    try:
                        bbox = (max(0, image["x0"] - 2), max(0, image["top"] - 2),
                                min(page.width, image["x1"] + 2), min(page.height, image["bottom"] + 2))
                        width = bbox[2] - bbox[0]
                        height = bbox[3] - bbox[1]
                        if width < MIN_SIZE or height < MIN_SIZE:
                            continue
                        rendered = page.crop(bbox).to_image(resolution=140)
                        target = out_dir / f"pdf_{Path(path).stem}_p{page_no}_{index}.png"
                        rendered.save(str(target))
                    except Exception:
                        continue
                    results.append({"page": page_no, "path": str(target),
                                    "caption": _caption_of(page_text), "source": "pdf",
                                    "size": target.stat().st_size if target.exists() else 0})
    except Exception:
        return results
    return results


def extract(path: str, suffix: str = "") -> list[dict]:
    suffix = (suffix or Path(path).suffix or "").lower()
    if suffix in (".docx", ".doc"):
        return extract_docx_images(path)
    if suffix == ".pdf":
        return extract_pdf_images(path)
    return []
