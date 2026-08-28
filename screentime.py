"""屏幕时间被动审计器 —— 命令行入口。

用法:
    python screentime.py start                后台开始记录
    python screentime.py run                  前台运行(调试用,Ctrl+C 结束)
    python screentime.py stop                 停止后台记录
    python screentime.py status               查看运行状态与最后一条记录
    python screentime.py today [--top N]      控制台看今天的应用耗时排行
    python screentime.py report [--days N]    生成 HTML 报告并自动打开浏览器
    python screentime.py tray                 托盘图标 + 置顶浮窗(今日专注实时可见)
    python screentime.py export [--days N]    导出会话明细 CSV
    python screentime.py install|uninstall    开机自启(计划任务)
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import db
import report

PID_FILE = db.DATA_DIR / "tracker.pid"
LOG_FILE = db.DATA_DIR / "tracker.log"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
TASK_NAME = "ScreenTimeAuditor"


def _require_windows():
    if os.name != "nt":
        print("该命令目前仅支持 Windows(报告查看不受影响)。")
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


def _launch_args(extra):
    """返回 (可执行文件, 参数列表),兼容源码与 exe 双形态。"""
    if getattr(sys, "frozen", False):
        return [sys.executable] + extra
    return [sys.executable, os.path.abspath(__file__)] + extra


def cmd_start(_):
    _require_windows()
    pid = _running_pid()
    if pid:
        print(f"已经在运行了(PID {pid}),无需重复启动。")
        return
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            _launch_args(["_daemon"]),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
        )
    time.sleep(1.2)
    if proc.poll() is None:
        PID_FILE.write_text(str(proc.pid))
        print(f"✅ 开始记录(PID {proc.pid})。数据保存在 {db.DB_PATH}")
        print("   随时可用 `screentime today` 查看,`stop` 停止;`tray` 可挂托盘浮窗。")
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
    print(f"● 正在记录(PID {pid})" if pid else "○ 未在运行")
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
    total, apps = report.today_totals()
    if not apps:
        print("今天还没有记录。用 `screentime start` 启动采集器吧。")
        return
    print(f"\n今天累计专注:{report.fmt_seconds(total)}\n")
    top = apps.most_common(args.top)
    for exe, sec in top:
        bar = "▇" * max(1, int(sec / top[0][1] * 28))
        print(f"  {report.app_name(exe):<18}{report.fmt_seconds(sec):>12}  {bar}")
    print()


def cmd_report(args):
    path = report.generate(days=args.days, open_browser=not args.no_open)
    print(("已生成报告:" if args.no_open else "已在浏览器打开报告:"), path)


def cmd_export(args):
    out = Path(args.out) if args.out else Path.cwd() / \
        f"screentime_export_{datetime.now():%Y%m%d}.csv"
    path = report.export_csv(args.days, out)
    print(f"已导出最近 {args.days} 天会话 -> {path}")


def cmd_install(_):
    _require_windows()
    # schtasks /TR 的引号很挑剔:整体再包一层转义引号,兼容源码与 exe 双形态
    if getattr(sys, "frozen", False):
        tr = f"\\\"{sys.executable}\\\" start"
    else:
        tr = f"\\\"{sys.executable}\\\" \\\"{os.path.abspath(__file__)}\\\" start"
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON",
                        "/F", "/TR", f'"{tr}"'], capture_output=True, text=True)
    if r.returncode == 0:
        print("✅ 已注册开机自启(计划任务 ScreenTimeAuditor)。`screentime uninstall` 可撤销。")
    else:
        print("❌ 注册失败:", (r.stderr or r.stdout).strip())


def cmd_uninstall(_):
    _require_windows()
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, text=True)
    print("✅ 已移除开机自启。" if r.returncode == 0 else "❌ 移除失败:", (r.stderr or r.stdout).strip())


def cmd_tray(_):
    _require_windows()
    import tray
    tray.main()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="screentime",
        description="屏幕时间被动审计器 —— 记录你的一天,数据只留在本机。")
    parser.add_argument("--version", action="version",
                        version=f"screentime {report.VERSION}")
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
    p_exp = sub.add_parser("export", help="导出会话明细 CSV")
    p_exp.add_argument("--days", type=int, default=30, help="导出最近 N 天(默认 30)")
    p_exp.add_argument("--out", default=None, help="输出文件路径")
    p_exp.set_defaults(fn=cmd_export)
    sub.add_parser("install", help="注册开机自启(计划任务)").set_defaults(fn=cmd_install)
    sub.add_parser("uninstall", help="移除开机自启").set_defaults(fn=cmd_uninstall)
    sub.add_parser("tray", help="托盘图标 + 今日专注浮窗").set_defaults(fn=cmd_tray)
    sub.add_parser("_daemon", help=argparse.SUPPRESS).set_defaults(fn=_daemon)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
