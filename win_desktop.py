from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional

from config import APP_NAME

IS_WINDOWS = sys.platform == "win32"

GWL_EXSTYLE = -20

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SW_RESTORE = 9


if IS_WINDOWS:
    user32 = ctypes.windll.user32
    _IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8

    if _IS_64BIT:
        _GetWindowLongPtr = user32.GetWindowLongPtrW
        _SetWindowLongPtr = user32.SetWindowLongPtrW
        _GetWindowLongPtr.restype = ctypes.c_ssize_t
        _GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
        _SetWindowLongPtr.restype = ctypes.c_ssize_t
        _SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    else:
        _GetWindowLongPtr = user32.GetWindowLongW
        _SetWindowLongPtr = user32.SetWindowLongW
        _GetWindowLongPtr.restype = ctypes.c_long
        _GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
        _SetWindowLongPtr.restype = ctypes.c_long
        _SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    _SetWindowPos = user32.SetWindowPos
    _SetWindowPos.restype = wintypes.BOOL
    _SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]

    _ShowWindow = user32.ShowWindow
    _ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _ShowWindow.restype = wintypes.BOOL

    _SetForegroundWindow = user32.SetForegroundWindow
    _SetForegroundWindow.argtypes = [wintypes.HWND]
    _SetForegroundWindow.restype = wintypes.BOOL


def bring_window_to_front(hwnd: int) -> None:
    """Reliably surface a dialog launched from the background system tray on Windows."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        _ShowWindow(hwnd, SW_RESTORE)
        # Brief topmost toggle lifts the dialog above maximized apps without locking it on top
        _SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        _SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        _SetForegroundWindow(hwnd)
    except Exception:
        pass


def apply_window_layer(hwnd: int, layer_mode: str) -> None:
    """Hide from taskbar/Alt+Tab and set safe same-process Z-order (never cross-process owner)."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        ex_style = _GetWindowLongPtr(hwnd, GWL_EXSTYLE)
        ex_style = (ex_style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        _SetWindowLongPtr(hwnd, GWL_EXSTYLE, ex_style)

        if layer_mode == "desktop":
            insert_after = HWND_BOTTOM
        elif layer_mode == "floating":
            insert_after = HWND_TOPMOST
        else:
            insert_after = HWND_NOTOPMOST

        _SetWindowPos(
            hwnd,
            insert_after,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception:
        pass


def send_to_bottom(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        _SetWindowPos(
            hwnd,
            HWND_BOTTOM,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


def _get_pythonw_executable() -> Path:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return candidate
    return exe


def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enabled: bool, main_script: Optional[Path] = None) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "仅支持 Windows 系统"
    try:
        import winreg

        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            reg_path,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                script = (main_script or (Path(__file__).resolve().parent / "main.py")).resolve()
                pythonw = _get_pythonw_executable()
                cmd = f'"{pythonw}" "{script}"'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                return True, "已开启开机自动启动"
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
                return True, "已关闭开机自动启动"
    except Exception as exc:
        return False, f"修改开机自启失败：{exc}"
