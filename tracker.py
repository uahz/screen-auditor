"""前台窗口采样器(仅 Windows)。

每 poll_interval 秒读取一次当前焦点窗口的进程名与标题;
只有内容变化或到达 heartbeat 间隔时才落库,空闲超过阈值后样本标记为未专注。
命中忽略名单(进程名 / 标题正则)的窗口完全不记录。
零第三方依赖:全部通过 ctypes 调用系统 API。
"""
import configparser
import ctypes
import json
import signal
import time

try:
    from ctypes import wintypes
except ImportError:  # 非 Windows 平台,screentime.py 会先行拦截
    wintypes = None

import db

POLL_INTERVAL = 3      # 采样间隔(秒)
HEARTBEAT = 60         # 同一窗口状态的心跳写入间隔(秒)
IDLE_THRESHOLD = 120   # 无鼠标键盘输入超过该秒数视为"离开"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]


def load_config() -> dict:
    """从 data/config.ini 读取采样配置;文件不存在则生成带注释的默认配置。"""
    path = db.DATA_DIR / "config.ini"
    if not path.exists():
        path.write_text(
            "[tracker]\n"
            "# 采样间隔(秒),越小越精确,建议 2~10\n"
            "poll_interval = 3\n"
            "# 同一窗口状态的心跳写入间隔(秒),用于证明窗口仍在焦点\n"
            "heartbeat = 60\n"
            "# 无键鼠输入超过该秒数记为“离开”(注:纯阅读也会被算作离开)\n"
            "idle_threshold = 120\n",
            encoding="utf-8",
        )
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    t = cp["tracker"] if cp.has_section("tracker") else configparser.SectionProxy(cp, "tracker")

    def _int(name, default, lo, hi):
        try:
            return max(lo, min(hi, int(t.get(name, str(default)))))
        except (ValueError, TypeError):
            return default

    return {
        "poll_interval": _int("poll_interval", POLL_INTERVAL, 1, 60),
        "heartbeat": _int("heartbeat", HEARTBEAT, 10, 600),
        "idle_threshold": _int("idle_threshold", IDLE_THRESHOLD, 30, 3600),
    }


def load_ignore() -> dict:
    """忽略名单:命中进程名或标题正则的窗口完全不记录。

    data/ignore.json 结构:{"exe": ["snipaste.exe"], "title_regex": ["任务视图"]}
    """
    path = db.DATA_DIR / "ignore.json"
    if not path.exists():
        path.write_text(json.dumps(
            {"exe": [], "title_regex": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exe": set(), "regex": []}
    import re
    regexes = []
    for pattern in raw.get("title_regex", []):
        try:
            regexes.append(re.compile(pattern))
        except re.error:
            continue
    return {"exe": {e.lower() for e in raw.get("exe", [])}, "regex": regexes}


def is_ignored(ignore: dict, exe: str, title: str) -> bool:
    if exe in ignore["exe"]:
        return True
    return any(r.search(title) for r in ignore["regex"])


def _init_api():
    global _u32, _k32, _title_buf, _path_buf
    _u32 = ctypes.WinDLL("user32")
    _k32 = ctypes.WinDLL("kernel32")

    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]

    _k32.GetTickCount64.restype = ctypes.c_uint64
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _title_buf = ctypes.create_unicode_buffer(1024)
    _path_buf = ctypes.create_unicode_buffer(1024)


def idle_seconds() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not _u32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now_low = _k32.GetTickCount64() & 0xFFFFFFFF
    return ((now_low - info.dwTime) & 0xFFFFFFFF) / 1000.0


def process_name(pid: int) -> str:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "unknown"
    try:
        size = wintypes.DWORD(len(_path_buf))
        if _k32.QueryFullProcessImageNameW(handle, 0, _path_buf, ctypes.byref(size)):
            return _path_buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        return "unknown"
    finally:
        _k32.CloseHandle(handle)


def foreground():
    """返回 (exe 名, 窗口标题);锁屏/安全桌面时抛异常由调用方跳过。"""
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("no foreground window")
    pid = wintypes.DWORD(0)
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    n = _u32.GetWindowTextW(hwnd, _title_buf, len(_title_buf))
    return process_name(pid.value), (_title_buf.value[:300] if n > 0 else "")


def run_forever(log=print) -> None:
    import db

    cfg = load_config()
    ignore = load_ignore()
    stop = False

    def bye(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    conn = db.connect()
    last, last_write = None, 0
    log(f"开始记录:每 {cfg['poll_interval']}s 采样,心跳 {cfg['heartbeat']}s,"
        f"空闲阈值 {cfg['idle_threshold']}s,忽略名单 {len(ignore['exe'])} 进程/"
        f"{len(ignore['regex'])} 正则")
    while not stop:
        time.sleep(cfg["poll_interval"])
        try:
            exe, title = foreground()
        except Exception:
            continue
        if is_ignored(ignore, exe, title):
            last, last_write = None, 0  # 忽略窗口:断开心跳链,不产生任何记录
            continue
        now = int(time.time())
        focused = 0 if idle_seconds() >= cfg["idle_threshold"] else 1
        sample = (exe, title, focused)
        if sample != last or now - last_write >= cfg["heartbeat"]:
            db.insert_sample(conn, now, *sample)
            last, last_write = sample, now
    conn.close()
    log("已停止记录")


if __name__ == "__main__":
    _init_api()
    run_forever()
