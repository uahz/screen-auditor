"""从生成的报告里抽出指定 section,做成独立小页面便于截图。

用法:python scripts/extract_section.py [关键字] [源文件]
"""
import re
import sys
from pathlib import Path

keyword = sys.argv[1] if len(sys.argv) > 1 else "热力图"
src = Path(sys.argv[2] if len(sys.argv) > 2 else "F:/temp_demo/reports/report.html")
html = src.read_text(encoding="utf-8")
head = html.split("<body>")[0] + "<body><div class=wrap>"
sections = re.findall(r"<section[^>]*>.*?</section>", html, re.S)
part = next(s for s in sections if keyword in s)
out = head + part + """
<script>
var q=new URLSearchParams(location.search).get('theme');
if(q==='light'||q==='dark')document.documentElement.dataset.theme=q;
window.addEventListener('load',function(){
 document.querySelectorAll('.reveal,.cell').forEach(function(el){el.classList.add('in');
  el.style.opacity=1;el.style.transform='none';});
 document.querySelectorAll('.track b,.stack b').forEach(function(b){b.style.animation='none';});
 var p=document.querySelector('.ring .prog');if(p)p.style.strokeDashoffset=getComputedStyle(p).getPropertyValue('--off');
});
</script>
</div></body></html>"""
dst = src.parent / f"{keyword}.html"
dst.write_text(out, encoding="utf-8")
print("ok:", dst)
