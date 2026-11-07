"""生成「自然」的数字图（抗锯齿字体，像真实页面截图）：python make-natural.py <输出目录> <码1> <码2> ..."""
import sys
from pathlib import Path

import fitz  # PyMuPDF


def render_code(code):
    with fitz.open() as doc:
        page = doc.new_page(width=520, height=200)
        page.insert_text((40, 130), code, fontsize=110, fontname="helv")
        return page.get_pixmap(dpi=96).tobytes("png")


if sys.argv[1] == "--stdout":
    sys.stdout.buffer.write(render_code(sys.argv[2]))
else:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for code in sys.argv[2:]:
        (out / f"natural-{code}.png").write_bytes(render_code(code))
        print(f"natural-{code}.png")
