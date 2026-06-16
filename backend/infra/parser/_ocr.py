"""infra/parser/_ocr.py — RapidOCR 引擎单例与图片 OCR。

被 docx.py（段内嵌入图片）与 pdf.py（扫描件整页）共享。
进程内只初始化一次。
"""

import io
import logging

logger = logging.getLogger(__name__)

_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_image(image_bytes: bytes) -> str:
    """对单张图片字节做 OCR，返回按行拼接的纯文本。失败返回空串。
    使用 RapidOCR：result = [[box, text, score], ...]，第二项即识别文本。
    长边超 2000px 先缩放，缓解大图内存压力。
    """
    try:
        import numpy as np
        from PIL import Image

        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        max_side = 2000
        w, h = pil.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            pil = pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        img = np.array(pil)
        result, _elapse = _get_ocr()(img)
        if not result:
            return ""
        return "\n".join(item[1] for item in result if len(item) >= 2 and item[1])
    except Exception as e:
        logger.warning(f"OCR 失败：{e}")
        return ""
