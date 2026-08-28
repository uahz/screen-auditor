"""生成一份逼真的合成演示数据到 data/screen_time.db。

用途:为 README 截图准备素材,或供开发调试。
用法:python scripts/seed_demo.py [days]        # 默认生成最近 7 天
注意:会写入脚本所在项目目录下的 data/,跑完可用 git 已忽略、无需清理;
      若想覆盖正式数据请先备份。
"""
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

# (进程名, 标题池, 权重, 典型时段小时集合)
APPS = [
    ("code.exe", ["screentime.py - screen-auditor", "main.rs - rust-playground",
                  "report.py - screen-auditor", "AGENTS.md - workbench"], 0.30, set(range(9, 24))),
    ("chrome.exe", ["(126) Rust 使用者社区 - YouTube", "GitHub Pull Requests",
                    "Hacker News", "知乎 - 有问题,就会有答案",
                    "Bilibili - 一网在线学习", "Stack Overflow"], 0.28, set(range(8, 24))),
    ("wechat.exe", ["微信", "文件传输助手", "研发三人组"], 0.12, set(range(8, 23))),
    ("windowsterminal.exe", ["pyinstaller --onefile", "pytest -q", "git rebase -i main"], 0.08,
     set(range(10, 24))),
    ("mumunxdevice.exe", ["MuMu模拟器12 - 原神"], 0.07, {21, 22, 23}),
    ("wps.exe", ["2026年度预算终稿v7_真的不改了.docx"], 0.05, set(range(9, 19))),
    ("spotify.exe", ["Deep Focus 歌单"], 0.04, set(range(9, 18))),
    ("explorer.exe", ["Downloads", "代码"], 0.03, set(range(9, 23))),
    ("msedge.exe", ["文档协作平台 - 登录页"], 0.03, set(range(9, 18))),
]

SWITCH_EVERY = range(300, 2701, 60)   # 每个窗口停留 5~45 分钟,期间按分钟心跳落库


def seed(days: int) -> None:
    conn = db.connect()
    conn.execute("DELETE FROM samples")
    random.seed(2026)
    start = int(datetime.combine(
        datetime.now().date() - timedelta(days=days - 1), datetime.min.time()).timestamp())
    end = int(time.time())
    ts = start

    while ts < end:
        hour = datetime.fromtimestamp(ts).hour
        if 1 <= hour < 8:                       # 深夜:睡觉
            ts += 1800
            continue
        if random.random() < 0.06:              # 随手离开
            gap = random.randrange(300, 2700)
            conn.execute("INSERT OR REPLACE INTO samples VALUES (?,?,?,0)",
                         (ts, "away", "", ))
            ts += gap
            continue
        pool = [(a[0], a[1], a[2]) for a in APPS if hour in a[3]] \
            or [(a[0], a[1], a[2]) for a in APPS]
        exe, titles, w = random.choices(pool, weights=[p[2] for p in pool])[0]
        stay = random.choice(list(SWITCH_EVERY))
        chosen = f"{random.choice(titles)} - {guess_host(exe)}"
        step = 60                                    # 模拟采集器的分钟级心跳
        inner = ts
        while inner < ts + stay and inner < end:
            hour2 = datetime.fromtimestamp(inner).hour
            if hour2 < 8:                            # 深夜截断(0-7 点不记录)
                break
            conn.execute("INSERT OR REPLACE INTO samples VALUES (?,?,?,1)",
                         (inner, exe, chosen))
            inner += step
        ts = inner
    conn.commit()
    print(f"已写入 {conn.execute('SELECT COUNT(*) FROM samples').fetchone()[0]} 条演示样本"
          f" -> {db.DB_PATH}")


def guess_host(exe: str) -> str:
    hosts = {
        "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge",
        "code.exe": "Visual Studio Code", "wechat.exe": "微信",
        "windowsterminal.exe": "Windows Terminal", "wps.exe": "WPS Office",
    }
    return hosts.get(exe, exe)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    seed(days)
