"""托盘图标 + 置顶浮窗(零依赖,纯 ctypes Win32)。

    python screentime.py tray

- 托盘图标:左键显示/隐藏浮窗;右键菜单(打开今日报告 / 数据文件夹 / 退出)
- 置顶浮窗:每 30 秒刷新"今日专注总时长 + 当前最常用应用",点击直接生成并打开报告
"""
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

import db
import report

WM_APP_TRAY = 0x8000  # WM_APP
WM_APP_TIMER = 1

CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001
WM_CREATE, WM_PAINT, WM_DESTROY, WM_TIMER = 0x0001, 0x000F, 0x0002, 0x0113
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
WM_COMMAND = 0x0111
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
SPI_GETWORKAREA = 0x0030
SW_SHOWNOACTIVATE, SW_HIDE = 4, 0
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10

BG_COLOR = 0x231A17      # COLORREF 是 0x00BBGGRR,对应 #171A23
BORDER_COLOR = 0x382B26
TXT_COLOR = 0xF2E9E6
MUT_COLOR = 0xA8938A

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
sh32 = ctypes.WinDLL("shell32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi")

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    class _GUID(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", _GUID), ("hBalloonIcon", wintypes.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", WPARAM), ("lParam", LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_ubyte * 32)]


state = {"widget": None, "tray": None, "lines": ["今日专注 0 分钟", "启动中…"]}
MENU_REPORT, MENU_FOLDER, MENU_QUIT = 1001, 1002, 1003


def _setup_types():
    """64 位下句柄是 64 位宽,凡返回句柄/接收句柄的 API 都要显式声明,否则被截断。"""
    H = wintypes.HANDLE
    k32.GetModuleHandleW.restype = H
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                    wintypes.HMENU, H, wintypes.LPVOID]
    u32.LoadImageW.restype = H
    u32.LoadImageW.argtypes = [H, wintypes.LPCWSTR, wintypes.UINT,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u32.LoadIconW.restype = H
    u32.LoadIconW.argtypes = [H, wintypes.LPCWSTR]
    u32.LoadCursorW.restype = H
    u32.LoadCursorW.argtypes = [H, wintypes.LPCWSTR]
    u32.BeginPaint.restype = wintypes.HDC
    u32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                              ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    u32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
    u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    g32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    g32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    g32.CreateSolidBrush.restype = wintypes.HBRUSH
    g32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    g32.CreatePen.restype = wintypes.HPEN
    g32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
    g32.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int]
    g32.SelectObject.restype = H
    g32.SelectObject.argtypes = [wintypes.HDC, H]
    g32.CreateFontW.restype = H
    g32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wintypes.LPCWSTR]
    g32.DeleteObject.argtypes = [H]
    u32.CreatePopupMenu.restype = wintypes.HMENU
    u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT,
                                wintypes.LPCWSTR]
    u32.TrackPopupMenu.restype = ctypes.c_int
    u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.LPVOID]
    u32.DestroyMenu.argtypes = [wintypes.HMENU]
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.LPVOID]
    u32.GetMessageW.restype = ctypes.c_int
    u32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT]
    u32.DefWindowProcW.restype = LRESULT
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    sh32.Shell_NotifyIconW.restype = wintypes.BOOL
    sh32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD]


def _icon_handle():
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "assets" / "icon.ico")  # noqa: SLF001
    candidates.append(Path(__file__).resolve().parent / "assets" / "icon.ico")
    for path in candidates:
        if path.exists():
            h = u32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            if h:
                return h
    return u32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))


def refresh_lines():
    total, apps = report.today_totals()
    if apps:
        top_exe, top_sec = apps.most_common(1)[0]
        second = f"{report.app_name(top_exe)} · {report.fmt_seconds(top_sec)}"
    else:
        second = "还没有记录,开始用吧"
    state["lines"] = [f"今日专注 {report.fmt_seconds(total)}", second]


def _paint(hwnd):
    ps = PAINTSTRUCT()
    hdc = u32.BeginPaint(hwnd, ctypes.byref(ps))
    rect = wintypes.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(rect))

    bg = g32.CreateSolidBrush(BG_COLOR)
    u32.FillRect(hdc, ctypes.byref(rect), bg)
    g32.DeleteObject(bg)

    pen = g32.CreatePen(0, 1, BORDER_COLOR)  # PS_SOLID
    old = g32.SelectObject(hdc, pen)
    g32.RoundRect(hdc, 0, 0, rect.right, rect.bottom, 14, 14)
    g32.SelectObject(hdc, old)
    g32.DeleteObject(pen)

    g32.SetBkMode(hdc, 1)  # TRANSPARENT
    font_big = g32.CreateFontW(-20, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 0, 0,
                               "Microsoft YaHei UI")
    font_small = g32.CreateFontW(-14, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0,
                                 "Microsoft YaHei UI")
    box = wintypes.RECT(18, 10, rect.right - 12, rect.bottom)
    old_font = g32.SelectObject(hdc, font_big)
    g32.SetTextColor(hdc, TXT_COLOR)
    line1 = state["lines"][0]
    u32.DrawTextW(hdc, line1, -1, ctypes.byref(box), 0x24)  # DT_SINGLELINE|DT_LEFT
    box2 = wintypes.RECT(18, 36, rect.right - 12, rect.bottom)
    g32.SelectObject(hdc, font_small)
    g32.SetTextColor(hdc, MUT_COLOR)
    u32.DrawTextW(hdc, state["lines"][1], -1, ctypes.byref(box2), 0x24)
    g32.SelectObject(hdc, old_font)
    g32.DeleteObject(font_big)
    g32.DeleteObject(font_small)
    u32.EndPaint(hwnd, ctypes.byref(ps))


def _open_report():
    path = report.generate(days=1, open_browser=True)
    os.startfile(path)


def _popup_menu(hwnd):
    menu = u32.CreatePopupMenu()
    u32.AppendMenuW(menu, 0, MENU_REPORT, "打开今日报告")
    u32.AppendMenuW(menu, 0, MENU_FOLDER, "打开数据文件夹")
    u32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
    u32.AppendMenuW(menu, 0, MENU_QUIT, "退出")
    pt = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    u32.SetForegroundWindow(hwnd)  # 让菜单在失焦时能正常关闭
    chosen = u32.TrackPopupMenu(menu, 0x0002 | 0x0080 | 0x0100,  # RIGHTBUTTON|NONOTIFY|RETURNCMD
                                pt.x, pt.y, 0, hwnd, None)
    u32.DestroyMenu(menu)
    if chosen == MENU_REPORT:
        _open_report()
    elif chosen == MENU_FOLDER:
        os.startfile(db.DATA_DIR)
    elif chosen == MENU_QUIT:
        sh32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(state["tray"]))
        u32.PostQuitMessage(0)


def make_wndproc():
    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_APP_TRAY:
            lp = lparam & 0xFFFFFFFF
            if lp == WM_LBUTTONUP and state["widget"]:
                style = u32.IsWindowVisible(state["widget"])
                u32.ShowWindow(state["widget"], SW_HIDE if style else SW_SHOWNOACTIVATE)
            elif lp == WM_RBUTTONUP:
                _popup_menu(hwnd)
            return 0
        if msg == WM_TIMER:
            refresh_lines()
            if state["widget"]:
                u32.InvalidateRect(state["widget"], None, True)
            return 0
        if msg == WM_PAINT:
            _paint(hwnd)
            return 0
        if msg == WM_LBUTTONUP and hwnd == state["widget"]:
            _open_report()
            return 0
        if msg == WM_DESTROY:
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)
    return WNDPROC(wndproc)


def create_window(class_name, title, style, exstyle, x, y, w, h, wndproc, instance):
    wc = WNDCLASSW()
    wc.style = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = wndproc
    wc.hInstance = instance
    wc.hCursor = u32.LoadCursorW(None, wintypes.LPCWSTR(32512))  # IDC_ARROW
    wc.lpszClassName = class_name
    atom = u32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = u32.CreateWindowExW(exstyle, class_name, title, style,
                               x, y, w, h, None, None, instance, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    return hwnd


def main():
    _setup_types()
    # 高 DPI 下文字才清晰;老系统逐级降级
    try:
        u32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4))
    except Exception:
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except Exception:
            u32.SetProcessDPIAware()

    hinstance = k32.GetModuleHandleW(None)
    wndproc = make_wndproc()

    # 隐藏的消息窗口:承载托盘回调
    tray_hwnd = create_window("ScreenTimeTrayWnd", "ScreenTime", 0, 0, 0, 0, 0, 0,
                              wndproc, hinstance)
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd = tray_hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_APP_TRAY
    nid.hIcon = _icon_handle()
    nid.szTip = "屏幕时间审计器 - 左键浮窗 / 右键菜单"
    if not sh32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        print("托盘图标创建失败( explorer 崩溃或受限会话?)", file=sys.stderr)
        return 1
    state["tray"] = nid

    # 置顶浮窗:贴着任务栏右下角(坐标在创建时给定,避免 NOMOVE 语义混淆)
    wa = wintypes.RECT()
    u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
    wx, wy = wa.right - 248 - 14, wa.bottom - 72 - 14
    widget = create_window(
        "ScreenTimeWidgetW", "ScreenTime 浮窗",
        0x80000000,  # WS_POPUP
        0x08000000 | 0x00000080 | 0x00000008,  # NOACTIVATE | TOOLWINDOW | TOPMOST
        wx, wy, 248, 72, wndproc, hinstance)
    state["widget"] = widget
    u32.SetWindowPos(widget, wintypes.HWND(-1), 0, 0, 0, 0, 0x0013)  # 仅提权置顶
    try:
        dwm.DwmSetWindowAttribute(wintypes.HWND(widget), DWMWA_WINDOW_CORNER_PREFERENCE,
                                  ctypes.byref(ctypes.c_int(DWMWCP_ROUND)), 4)
    except Exception:
        pass
    u32.SetTimer(widget, WM_APP_TIMER, 30000, None)
    refresh_lines()
    u32.ShowWindow(widget, SW_SHOWNOACTIVATE)
    u32.InvalidateRect(widget, None, True)

    msg = MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))
    return int(msg.wParam)


if __name__ == "__main__":
    sys.exit(main())
