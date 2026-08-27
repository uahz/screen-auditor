"""把演示数据拷到独立目录并生成报告页,避免污染项目内真实 reports/。

用法:python scripts/build_demo_report.py [days]
输出:F:\\temp_demo\\reports\\report.html(路径打印在最后一行)
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DST = Path("F:/temp_demo")


def main(days: int) -> None:
    shutil.rmtree(DST, ignore_errors=True)
    DST.mkdir(parents=True)
    for f in ("db.py", "tracker.py", "report.py", "screentime.py"):
        shutil.copy2(ROOT / f, DST / f)
    shutil.copytree(ROOT / "data", DST / "data")
    subprocess.run([sys.executable, str(DST / "screentime.py"),
                    "report", "--days", str(days), "--no-open"], check=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
