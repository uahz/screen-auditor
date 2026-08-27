"""屏幕时间被动审计器 —— 命令行入口。

用法:
    python screentime.py start               后台开始记录
    python screentime.py run                 前台运行(调试用,Ctrl+C 结束)
    python screentime.py stop                停止后台记录
    python screentime.py status              查看运行状态与最后一条记录
    python screentime.py today [--top N]     控制台看今天的应用耗时排行
    python screentime.py report [--days N]   生成 HTML 报告并自动打开浏览器
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

import db

PID_FILE = db.DATA_DIR / "tracker.pid"
LOG_FILE = db.DATA_DIR / "tracker.log"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _require_windows():
    if os.name != "nt":
        print("采样器目前仅支持 Windows(报告查看不受影响)。")
        sys.exit(1)


def _pid_alive(pid: int) -> bool:
    import ctypes
    k32 = ctypes.WinDLL("kernel32")
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    k32.CloseHandle(handle)
    return True


def _running_pid():
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return None
    return pid if _pid_alive(pid) else None


def cmd_start(_):
    _require_windows()
    pid = _running_pid()
    if pid:
        print(f"已经在运行了(PID {pid}),无需重复启动。")
        return
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        daemon_cmd = [sys.executable, "_daemon"]          # exe 重启自身
    else:
        daemon_cmd = [sys.executable, os.path.abspath(__file__), "_daemon"]
    with open(LOG_FILE, "a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            daemon_cmd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
        )
    time.sleep(1.2)
    if proc.poll() is None:
        PID_FILE.write_text(str(proc.pid))
        print(f"✅ 开始记录(PID {proc.pid})。数据保存在 {db.DB_PATH}")
        print("   随时可用 `python screentime.py today` 查看,`stop` 停止。")
    else:
        print("❌ 启动失败,详见日志:", LOG_FILE)


def _daemon(_):
    import tracker
    tracker._init_api()
    PID_FILE.write_text(str(os.getpid()))
    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
        def log(m):
            fh.write(f"[{datetime.now():%H:%M:%S}] {m}\n")
            fh.flush()
        tracker.run_forever(log=log)
    PID_FILE.unlink(missing_ok=True)


def cmd_run(_):
    _require_windows()
    import tracker
    tracker._init_api()
    try:
        tracker.run_forever()
    except KeyboardInterrupt:
        pass


def cmd_stop(_):
    pid = _running_pid()
    if not pid:
        print("当前没有在运行。")
        PID_FILE.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, check=False)
    time.sleep(0.8)
    ok = not _pid_alive(pid)
    PID_FILE.unlink(missing_ok=True)
    print("🛑 已停止。" if ok else f"⚠️ 进程可能仍在运行,可手动执行 taskkill /F /PID {pid}")


def cmd_status(_):
    pid = _running_pid()
    if pid:
        print(f"● 正在记录(PID {pid})")
    else:
        print("○ 未在运行")
    conn = db.connect()
    row = db.latest_sample(conn)
    conn.close()
    if row:
        ts, exe, title, focused = row
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        state = "专注" if focused else "离开"
        print(f"最后一条记录:{when} [{state}] {exe} - {title[:50]}")
    else:
        print("数据库中还没有任何记录。")


def cmd_today(args):
    rows = [r for r in db.load_samples(1)
            if datetime.fromtimestamp(r[0]).date() == datetime.today().date()]
    if not rows:
        print("今天还没有记录。用 `python screentime.py start` 启动采集器吧。")
        return
    apps, total = Counter(), 0
    prev_key, switch_count = None, 0
    for i, (ts, exe, title, focused) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else ts + 60
        span = min(nxt - ts, 180)
        if not focused:
            continue
        apps[exe] += span
        total += span
        key = (exe, title)
        if prev_key is not None and key != prev_key:
            switch_count += 1
        prev_key = key

    from report import app_name, fmt_seconds
    print(f"\n今天累计专注:{fmt_seconds(total)},窗口切换 {switch_count} 次\n")
    for exe, sec in apps.most_common(args.top):
        bar = "▇" * max(1, int(sec / max(apps.values()) * 28))
        print(f"  {app_name(exe):<18}{fmt_seconds(sec):>12}  {bar}")
    print()


def cmd_report(args):
    path = _generate(args.days, open_browser=not args.no_open)
    print(("已生成报告:" if args.no_open else "已在浏览器打开报告:"), path)


def _generate(days, open_browser):
    import report
    return report.generate(days=days, open_browser=open_browser)


def main():
    parser = argparse.ArgumentParser(
        prog="screentime",
        description="屏幕时间被动审计器 —— 记录你的一天,数据只留在本机。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="后台开始记录").set_defaults(fn=cmd_start)
    sub.add_parser("run", help="前台运行(调试)").set_defaults(fn=cmd_run)
    sub.add_parser("stop", help="停止后台记录").set_defaults(fn=cmd_stop)
    sub.add_parser("status", help="查看运行状态").set_defaults(fn=cmd_status)
    p_today = sub.add_parser("today", help="控制台查看今日排行")
    p_today.add_argument("--top", type=int, default=10, help="显示前 N 名")
    p_today.set_defaults(fn=cmd_today)
    p_rep = sub.add_parser("report", help="生成 HTML 报告并打开")
    p_rep.add_argument("--days", type=int, default=7, help="统计最近 N 天(默认 7)")
    p_rep.add_argument("--no-open", action="store_true", help="只生成不打开浏览器")
    p_rep.set_defaults(fn=cmd_report)
    sub.add_parser("_daemon", help=argparse.SUPPRESS).set_defaults(fn=_daemon)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    main()
