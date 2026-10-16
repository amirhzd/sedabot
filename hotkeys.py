"""Global hotkeys via the Windows RegisterHotKey API.

The ``keyboard`` package installs a low-level hook and often needs an
Administrator process. RegisterHotKey does not. The selection shortcut waits
until Ctrl/Alt are released before it sends Ctrl+C, because sending Ctrl+C
while those modifiers are still down becomes Ctrl+Alt+C and copies nothing.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

log = logging.getLogger("sayit.hotkeys")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}
for _i in range(1, 25):
    _NAMED_KEYS[f"f{_i}"] = 0x6F + _i


class HotkeyError(Exception):
    """A hotkey string is invalid or Windows rejected it."""


class SelectionError(Exception):
    """The current selection could not be copied."""


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:
    user32 = None
    kernel32 = None


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    # The union has to be as large as MOUSEINPUT or SendInput rejects the packet.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


if user32 is not None:
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse ``ctrl+alt+v`` into ``(modifiers, virtual_key)``."""
    if not spec or not spec.strip():
        raise HotkeyError("Enter a hotkey such as ctrl+alt+v.")
    parts = [part.strip().lower() for part in spec.replace("-", "+").split("+") if part.strip()]
    modifiers = MOD_NOREPEAT
    key: Optional[int] = None
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
            continue
        if key is not None:
            raise HotkeyError(f"Hotkey '{spec}' has more than one key.")
        key = _virtual_key(part)
    if key is None:
        raise HotkeyError(f"Hotkey '{spec}' needs a key, not only modifiers.")
    return modifiers, key


def _virtual_key(part: str) -> int:
    if part in _NAMED_KEYS:
        return _NAMED_KEYS[part]
    if len(part) == 1 and part.isascii() and part.isalnum():
        return ord(part.upper())
    raise HotkeyError(f"Unknown key '{part}'. Use a letter, digit, or name like f9.")


def format_hotkey(spec: str) -> str:
    return "+".join(hotkey_labels(spec))


def hotkey_labels(spec: str) -> list[str]:
    """``ctrl+alt+v`` becomes ``["Ctrl", "Alt", "V"]`` for on-screen keycaps."""
    return [part.strip().capitalize() for part in spec.replace("-", "+").split("+") if part.strip()]


def probe_register(spec: str = "ctrl+alt+shift+f24") -> Optional[str]:
    """Register and immediately release a hotkey. Return an error string, or None."""
    if user32 is None:
        return "Global hotkeys are only available on Windows."
    modifiers, vk = parse_hotkey(spec)
    hotkey_id = 0x5A17
    if not user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
        err = ctypes.get_last_error()
        return f"RegisterHotKey failed (Windows error {err})."
    try:
        return None
    finally:
        user32.UnregisterHotKey(None, hotkey_id)


def _key_is_down(vk: int) -> bool:
    if user32 is None:
        return False
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def wait_modifiers_released(timeout: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    watched = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)
    while time.monotonic() < deadline:
        if not any(_key_is_down(vk) for vk in watched):
            return True
        time.sleep(0.02)
    return not any(_key_is_down(vk) for vk in watched)


def send_ctrl_c() -> None:
    if user32 is None:
        raise SelectionError("Selection copy is only available on Windows.")
    keys = (VK_CONTROL, ord("C"))
    inputs = (INPUT * 4)()
    index = 0
    for vk in keys:
        inputs[index].type = INPUT_KEYBOARD
        inputs[index].ki = KEYBDINPUT(wVk=vk)
        index += 1
    for vk in reversed(keys):
        inputs[index].type = INPUT_KEYBOARD
        inputs[index].ki = KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)
        index += 1
    sent = user32.SendInput(4, inputs, ctypes.sizeof(INPUT))
    if sent != 4:
        err = ctypes.get_last_error()
        raise SelectionError(f"Windows did not accept the copy keystroke (error {err}).")


def _read_clipboard() -> str:
    import pyperclip

    last = "clipboard unavailable"
    for _ in range(12):
        try:
            value = pyperclip.paste()
            return "" if value is None else str(value)
        except Exception as exc:
            last = str(exc)
            time.sleep(0.04)
    raise SelectionError(f"Could not read the clipboard ({last}).")


def _write_clipboard(value: str) -> None:
    import pyperclip

    last = "clipboard unavailable"
    for _ in range(12):
        try:
            pyperclip.copy(value)
            return
        except Exception as exc:
            last = str(exc)
            time.sleep(0.04)
    raise SelectionError(f"Could not restore the clipboard ({last}).")


def capture_selection(timeout: float = 0.8) -> str:
    """Copy the active selection and put the previous clipboard contents back.

    Returns an empty string when no selection was copied.
    """
    previous = _read_clipboard()
    token = f"__sayit_{time.time_ns()}__"
    _write_clipboard(token)
    try:
        if not wait_modifiers_released(1.5):
            raise SelectionError("Release Ctrl and Alt, then press the selection hotkey again.")
        time.sleep(0.05)
        send_ctrl_c()
        found = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = _read_clipboard()
            if current != token:
                found = current
                break
            time.sleep(0.03)
        time.sleep(0.04)
        current = _read_clipboard()
        if current != token:
            found = current
        return found.strip()
    finally:
        try:
            _write_clipboard(previous)
        except Exception:
            log.exception("Could not restore the clipboard")


class WinHotkeys:
    """Register hotkeys on a dedicated thread and invoke callbacks there."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._errors: list[str] = []
        self._busy = threading.Lock()
        self._last_fire: dict[int, float] = {}

    def start(self, bindings: dict[str, Callable[[], None]]) -> list[str]:
        """Replace the current bindings. Return human-readable registration errors."""
        self.stop()
        if not bindings:
            return []
        if sys.platform != "win32" or user32 is None:
            return ["Global hotkeys are only available on Windows."]
        parsed: list[tuple[str, int, int, Callable[[], None]]] = []
        errors: list[str] = []
        for spec, callback in bindings.items():
            try:
                modifiers, vk = parse_hotkey(spec)
            except HotkeyError as exc:
                errors.append(str(exc))
                continue
            parsed.append((spec, modifiers, vk, callback))
        if not parsed:
            return errors
        self._stop.clear()
        self._ready.clear()
        self._errors = list(errors)
        self._thread = threading.Thread(
            target=self._run,
            args=(parsed,),
            name="sayit-hotkeys",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return list(self._errors)

    def stop(self) -> None:
        self._stop.set()
        thread_id = self._thread_id
        if thread_id and user32 is not None:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None
        self._thread_id = None

    def _run(self, parsed: list[tuple[str, int, int, Callable[[], None]]]) -> None:
        assert user32 is not None and kernel32 is not None
        self._thread_id = int(kernel32.GetCurrentThreadId())
        callbacks: dict[int, Callable[[], None]] = {}
        ids: list[int] = []
        try:
            for index, (spec, modifiers, vk, callback) in enumerate(parsed, start=1):
                if not user32.RegisterHotKey(None, index, modifiers, vk):
                    err = ctypes.get_last_error()
                    self._errors.append(
                        f"Hotkey {format_hotkey(spec)} is already in use or was rejected "
                        f"(Windows error {err})."
                    )
                    log.warning("RegisterHotKey failed for %s (%s)", spec, err)
                    continue
                callbacks[index] = callback
                ids.append(index)
            self._ready.set()
            message = wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0 or result == -1:
                    break
                if message.message == WM_HOTKEY:
                    self._dispatch(int(message.wParam), callbacks)
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            for hotkey_id in ids:
                user32.UnregisterHotKey(None, hotkey_id)
            self._ready.set()

    def _dispatch(self, hotkey_id: int, callbacks: dict[int, Callable[[], None]]) -> None:
        callback = callbacks.get(hotkey_id)
        if callback is None:
            return
        now = time.monotonic()
        if now - self._last_fire.get(hotkey_id, 0.0) < 0.35:
            return
        if not self._busy.acquire(blocking=False):
            return
        self._last_fire[hotkey_id] = now
        try:
            callback()
        except Exception:
            log.exception("Hotkey callback failed")
        finally:
            self._busy.release()
