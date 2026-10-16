"""在隔离的 LibreOffice 用户目录中生成实际排版 PDF，不发起网络请求。"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys


def preview_environment(root):
    env=os.environ.copy()
    if sys.platform=='darwin' and not env.get('FONTCONFIG_FILE'):
        # Headless LibreOffice 的默认 fontconfig 不一定扫描 macOS 中文字体。
        # 只调整 PDF 预览的字体回退，不修改 DOCX 所指定的字体。
        config=root/'fonts.conf'
        config.write_text(f'''<?xml version="1.0"?>
<fontconfig><dir>/System/Library/Fonts</dir><dir>/Library/Fonts</dir>
<cachedir>{root/'font-cache'}</cachedir>
<alias><family>宋体</family><prefer><family>Songti SC</family></prefer></alias>
<alias><family>仿宋</family><prefer><family>STFangsong</family><family>Songti SC</family></prefer></alias>
<alias><family>sans-serif</family><prefer><family>Heiti SC</family><family>Arial</family></prefer></alias>
<alias><family>serif</family><prefer><family>Songti SC</family><family>Times New Roman</family></prefer></alias>
</fontconfig>''')
        env['FONTCONFIG_FILE']=str(config)
    return env


def render_pdf(data):
    executable = os.environ.get('DOCX_PREVIEW_SOFFICE') or shutil.which('soffice')
    if not executable:
        candidate=Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
        executable=str(candidate) if candidate.is_file() else None
    if not executable:
        raise RuntimeError('未安装 LibreOffice，Word 文件仍可下载；请安装后重试排版预览')
    with tempfile.TemporaryDirectory(prefix='response-preview-') as folder:
        root=Path(folder);source=root/'response.docx';source.write_bytes(data)
        output=root/'pdf';output.mkdir()
        try:
            subprocess.run([executable, f'-env:UserInstallation={(root/"profile").as_uri()}',
                            '--headless','--convert-to','pdf','--outdir',str(output),str(source)],
                           env=preview_environment(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('排版预览超过两分钟，请分组导出或重试') from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('排版引擎转换失败，Word 文件仍可下载，请重试预览') from exc
        pdf=output/'response.pdf'
        if not pdf.is_file() or not pdf.stat().st_size: raise RuntimeError('排版引擎未生成 PDF，请重试预览')
        return pdf.read_bytes()
