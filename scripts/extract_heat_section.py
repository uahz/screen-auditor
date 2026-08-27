"""从生成的报告里抽出热力图 section,做成独立小页面便于截图。"""
import re
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "F:/temp_demo/reports/report.html")
html = src.read_text(encoding="utf-8")
head = html.split("<body>")[0] + "<body><div class=wrap>"
sections = re.findall(r"<section>.*?</section>", html, re.S)
heat = next(s for s in sections if "热力图" in s)
out = head + heat + "</div></body></html>"
dst = src.parent / "heat.html"
dst.write_text(out, encoding="utf-8")
print("ok:", dst, len(out))
