"""前台窗口采样器(仅 Windows)。

每 POLL_INTERVAL 秒读取一次当前焦点窗口的进程名与标题;
只有内容变化或到达 HEARTBEAT 间隔时才落库,空闲超过阈值后样本标记为未专注。
零第三方依赖:全部通过 ctypes 调用系统 API。
"""
import ctypes
import signal
import time

try:
    from ctypes import wintypes
except ImportError:  # 非 Windows 平台,screentime.py 会先行拦截
    wintypes = None

POLL_INTERVAL = 3      # 采样间隔(秒)
HEARTBEAT = 60         # 同一窗口状态的心跳写入间隔(秒)
IDLE_THRESHOLD = 120   # 无鼠标键盘输入超过该秒数视为"离开"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]


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

    stop = False

    def bye(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    conn = db.connect()
    last, last_write = None, 0
    log(f"开始记录:每 {POLL_INTERVAL}s 采样一次,心跳 {HEARTBEAT}s,空闲阈值 {IDLE_THRESHOLD}s")
    while not stop:
        time.sleep(POLL_INTERVAL)
        try:
            exe, title = foreground()
        except Exception:
            continue
        now = int(time.time())
        sample = (exe, title, 0 if idle_seconds() >= IDLE_THRESHOLD else 1)
        if sample != last or now - last_write >= HEARTBEAT:
            db.insert_sample(conn, now, *sample)
            last, last_write = sample, now
    conn.close()
    log("已停止记录")


if __name__ == "__main__":
    _init_api()
    run_forever()
