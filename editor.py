"""Speak-tab editor.

Tk's text widget does not shape or reorder Persian, and retagging the whole
document on each key moved the caret. This hosts Windows RICHEDIT50W instead,
so letters join, undo works, and the usual clipboard keys behave.
"""

from __future__ import annotations

import ctypes
import logging
import unicodedata
from collections import deque
from ctypes import wintypes

import tkinter as tk

log = logging.getLogger("sayit.editor")

# The system caret is black. A light page keeps it visible in either app theme.
PAPER = "#fffdf8"
INK = "#1c1916"
EDGE = "#d4cdc0"
FONT_NAME = "Tahoma"
FONT_TWIPS = 16 * 20
TEXT_LIMIT = 1_000_000
_MARGIN = 16

WM_USER = 0x0400
WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
WM_SETTEXT = 0x000C
EM_SETSEL = 0x00B1
EM_CANUNDO = 0x00C6
EM_UNDO = 0x00C7
EM_EXGETSEL = WM_USER + 52
EM_EXLIMITTEXT = WM_USER + 53
EM_EXSETSEL = WM_USER + 55
EM_GETCHARFORMAT = WM_USER + 58
EM_GETPARAFORMAT = WM_USER + 61
EM_SETBKGNDCOLOR = WM_USER + 67
EM_SETCHARFORMAT = WM_USER + 68
EM_SETPARAFORMAT = WM_USER + 71
EM_SETTEXTMODE = WM_USER + 89
EM_GETTEXTEX = WM_USER + 94
EM_GETTEXTLENGTHEX = WM_USER + 95
EM_SETTEXTEX = WM_USER + 97
EM_SETLANGOPTIONS = WM_USER + 120
EM_GETLANGOPTIONS = WM_USER + 121
EM_SETBIDIOPTIONS = WM_USER + 200
EM_GETBIDIOPTIONS = WM_USER + 201
EM_SETTYPOGRAPHYOPTIONS = WM_USER + 202
EM_SETEDITSTYLE = WM_USER + 204
EM_SCROLLCARET = WM_USER + 49
EM_SETMARGINS = 0x00D3

TM_PLAINTEXT = 1
TM_MULTILEVELUNDO = 8
TM_MULTICODEPAGE = 32
ST_KEEPUNDO = 1
ST_UNICODE = 8
GT_USECRLF = 1
GTL_USECRLF = 1
GTL_PRECISE = 2
GTL_NUMCHARS = 8
CP_UNICODE = 1200

SCF_DEFAULT = 0x0000
SCF_ALL = 0x0004
CFM_COLOR = 0x40000000
CFM_CHARSET = 0x08000000
CFM_FACE = 0x20000000
CFM_SIZE = 0x80000000
ARABIC_CHARSET = 178

PFM_ALIGNMENT = 0x00000008
PFM_RTLPARA = 0x00010000
PFE_RTLPARA = 0x0001
PFA_LEFT = 1
PFA_RIGHT = 2

SES_EXTENDBACKCOLOR = 0x00000004
SES_BIDI = 0x00001000
TO_ADVANCEDTYPOGRAPHY = 1

IMF_AUTOFONT = 0x0002
IMF_AUTOFONTSIZEADJUST = 0x0010
IMF_DUALFONT = 0x0080

BOM_DEFPARADIR = 0x0001
BOM_CONTEXTREADING = 0x0008
BOM_CONTEXTALIGNMENT = 0x0010
BOM_UNICODEBIDI = 0x0080
BOE_RTLDIR = 0x0001
BOE_CONTEXTREADING = 0x0008
BOE_CONTEXTALIGNMENT = 0x0010
BOE_UNICODEBIDI = 0x0080

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
WS_TABSTOP = 0x00010000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040
ES_WANTRETURN = 0x1000
ES_NOHIDESEL = 0x0100
EC_LEFTMARGIN = 0x0001
EC_RIGHTMARGIN = 0x0002

VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_A = 0x41
GWLP_WNDPROC = -4
GWL_STYLE = -16

_HWND = ctypes.c_void_p
_LRESULT = ctypes.c_ssize_t
_WPARAM = ctypes.c_size_t
_LPARAM = ctypes.c_ssize_t
_UINT = ctypes.c_uint
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, _HWND, _UINT, _WPARAM, _LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32_style = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
_kernel32.LoadLibraryW.restype = ctypes.c_void_p
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = ctypes.c_void_p

_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    _HWND,
    _HWND,
    _HWND,
    ctypes.c_void_p,
]
_user32.CreateWindowExW.restype = _HWND
_user32.DestroyWindow.argtypes = [_HWND]
_user32.DestroyWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [_HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.MoveWindow.argtypes = [
    _HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.BOOL,
]
_user32.MoveWindow.restype = wintypes.BOOL
_user32.SendMessageW.argtypes = [_HWND, _UINT, _WPARAM, _LPARAM]
_user32.SendMessageW.restype = _LRESULT
_user32.CallWindowProcW.argtypes = [_HWND, _HWND, _UINT, _WPARAM, _LPARAM]
_user32.CallWindowProcW.restype = _LRESULT
_user32.DefWindowProcW.argtypes = [_HWND, _UINT, _WPARAM, _LPARAM]
_user32.DefWindowProcW.restype = _LRESULT
_user32.GetKeyState.argtypes = [ctypes.c_int]
_user32.GetKeyState.restype = ctypes.c_short
_user32.SetFocus.argtypes = [_HWND]
_user32.SetFocus.restype = _HWND
_user32.InvalidateRect.argtypes = [_HWND, ctypes.c_void_p, wintypes.BOOL]
_user32.InvalidateRect.restype = wintypes.BOOL
_user32.UpdateWindow.argtypes = [_HWND]
_user32.UpdateWindow.restype = wintypes.BOOL

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _set_wndproc = _user32.SetWindowLongPtrW
else:
    _set_wndproc = _user32.SetWindowLongW
_set_wndproc.argtypes = [_HWND, ctypes.c_int, ctypes.c_void_p]
_set_wndproc.restype = ctypes.c_void_p

_get_style = _user32_style.GetWindowLongW
_set_style = _user32_style.SetWindowLongW
_get_style.argtypes = [_HWND, ctypes.c_int]
_get_style.restype = ctypes.c_uint
_set_style.argtypes = [_HWND, ctypes.c_int, ctypes.c_uint]
_set_style.restype = ctypes.c_uint

_MSFTEDIT = None


class SETTEXTEX(ctypes.Structure):
    _fields_ = [("flags", wintypes.DWORD), ("codepage", wintypes.UINT)]


class GETTEXTEX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("codepage", wintypes.UINT),
        ("lpDefaultChar", ctypes.c_char_p),
        ("lpUsedDefChar", ctypes.c_void_p),
    ]


class GETTEXTLENGTHEX(ctypes.Structure):
    _fields_ = [("flags", wintypes.DWORD), ("codepage", wintypes.UINT)]


class BIDIOPTIONS(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("wMask", wintypes.WORD),
        ("wEffects", wintypes.WORD),
    ]


class CHARFORMATW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwMask", wintypes.DWORD),
        ("dwEffects", wintypes.DWORD),
        ("yHeight", wintypes.LONG),
        ("yOffset", wintypes.LONG),
        ("crTextColor", wintypes.DWORD),
        ("bCharSet", wintypes.BYTE),
        ("bPitchAndFamily", wintypes.BYTE),
        ("szFaceName", wintypes.WCHAR * 32),
    ]


class PARAFORMAT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwMask", wintypes.DWORD),
        ("wNumbering", wintypes.WORD),
        ("wEffects", wintypes.WORD),
        ("dxStartIndent", wintypes.LONG),
        ("dxRightIndent", wintypes.LONG),
        ("dxOffset", wintypes.LONG),
        ("wAlignment", wintypes.WORD),
        ("cTabCount", wintypes.SHORT),
        ("rgxTabs", wintypes.LONG * 32),
    ]


class CHARRANGE(ctypes.Structure):
    _fields_ = [("cpMin", wintypes.LONG), ("cpMax", wintypes.LONG)]


def _check_layouts() -> None:
    pointer = ctypes.sizeof(ctypes.c_void_p)
    expected_get = 32 if pointer == 8 else 20
    sizes = {
        "PARAFORMAT": (PARAFORMAT, 156),
        "BIDIOPTIONS": (BIDIOPTIONS, 8),
        "SETTEXTEX": (SETTEXTEX, 8),
        "GETTEXTLENGTHEX": (GETTEXTLENGTHEX, 8),
        "GETTEXTEX": (GETTEXTEX, expected_get),
        "CHARRANGE": (CHARRANGE, 8),
    }
    for name, (struct, size) in sizes.items():
        actual = ctypes.sizeof(struct)
        if actual != size:
            raise RuntimeError(f"{name} is {actual} bytes, expected {size}")


_check_layouts()


def _colorref(value: str) -> int:
    rgb = int(value[1:], 16)
    red, green, blue = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return (blue << 16) | (green << 8) | red


_PAPER_REF = _colorref(PAPER)
_INK_REF = _colorref(INK)


def _send(hwnd, msg: int, wparam: int = 0, lparam: int = 0) -> int:
    return int(_user32.SendMessageW(hwnd, msg, wparam, lparam))


def _ctrl_down() -> bool:
    return _user32.GetKeyState(VK_CONTROL) < 0


def _load_library() -> None:
    global _MSFTEDIT
    if _MSFTEDIT:
        return
    _MSFTEDIT = _kernel32.LoadLibraryW("msftedit.dll")
    if not _MSFTEDIT:
        raise OSError(f"Could not load msftedit.dll (winerror {ctypes.get_last_error()})")


def _to_richedit(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def _from_richedit(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _prefers_rtl(text: str) -> bool:
    """Paragraph direction follows the first strong letter. Persian is the default."""
    for char in text:
        kind = unicodedata.bidirectional(char)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return True


def _clip_children(parent) -> None:
    style = int(_get_style(parent, GWL_STYLE))
    if style & WS_CLIPCHILDREN:
        return
    _set_style(parent, GWL_STYLE, style | WS_CLIPCHILDREN)


class SpeechEditor(tk.Frame):
    """Plain text editor with Windows bidi, shaping, undo, and clipboard keys.

    get(\"1.0\", \"end\"), delete(\"1.0\", \"end\"), and insert(\"1.0\", text)
    match the calls the Speak tab already makes. get includes the trailing
    newline a Tk text widget returns.
    """

    def __init__(self, master, *, on_submit=None, on_cancel=None, **kwargs):
        kwargs.setdefault("bg", PAPER)
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", EDGE)
        kwargs.setdefault("highlightcolor", EDGE)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("width", 400)
        kwargs.setdefault("height", 180)
        super().__init__(master, **kwargs)
        self.configure(cursor="xterm", takefocus=1)
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._pending = ""
        self._hwnd = None
        self._proc = None
        self._old_proc = None
        self._creating = False
        self._placing = False
        self._destroying = False
        self._focusing = False
        self._shortcuts: deque = deque()
        self._pump_id = None
        self._native_depth = 0
        self._arm_shortcut_pump(20)
        self.bind("<Map>", self._on_map, add="+")
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<FocusIn>", self._on_focus_in, add="+")

    def insert(self, index: str, text: str, *_tags) -> None:
        text = "" if text is None else str(text)
        current = self._value()
        if index == "1.0":
            self._set(text + current)
        elif index == "end":
            self._set(current + text)
        else:
            raise ValueError(f'SpeechEditor.insert supports "1.0" and "end" (got {index!r})')

    def delete(self, start: str, end: str | None = None) -> None:
        if start == "1.0" and end == "end":
            self._set("")
            return
        raise ValueError('SpeechEditor.delete supports "1.0", "end"')

    def get(self, start: str = "1.0", end: str = "end") -> str:
        if (start, end) != ("1.0", "end"):
            raise ValueError('SpeechEditor.get supports "1.0", "end"')
        return self._value() + "\n"

    def focus_set(self):
        if self._ensure() and self._hwnd:
            _user32.SetFocus(self._hwnd)
            return None
        return super().focus_set()

    def _value(self) -> str:
        if not self._alive():
            return self._pending
        return self._read()

    def _set(self, text: str) -> None:
        self._pending = text
        if self._alive():
            self._write(text)
            return
        # Creating the window writes _pending. A second write would be a duplicate undo step.
        self._ensure()

    def _on_map(self, event) -> None:
        if event.widget is not self:
            return
        self._ensure()
        self._place()

    def _on_configure(self, event) -> None:
        if event.widget is not self:
            return
        if self._ensure():
            self._place()

    def _on_focus_in(self, event) -> None:
        if event.widget is not self or self._focusing or not self._hwnd:
            return
        self._focusing = True
        try:
            _user32.SetFocus(self._hwnd)
        finally:
            self._focusing = False

    def _on_destroy(self, event) -> None:
        if event.widget is not self or self._destroying:
            return
        self._destroying = True
        pump = self._pump_id
        self._pump_id = None
        if pump is not None:
            try:
                self.after_cancel(pump)
            except tk.TclError:
                pass
        self._shortcuts.clear()
        self._destroy_native()

    def _ensure(self) -> bool:
        if self._alive():
            return True
        if self._creating or not self.winfo_exists():
            return False
        try:
            parent = int(self.winfo_id())
        except (tk.TclError, TypeError, ValueError):
            return False
        if not parent:
            return False
        self._creating = True
        try:
            self._create(parent)
        finally:
            self._creating = False
        return self._alive()

    def _create(self, parent: int) -> None:
        _load_library()
        _clip_children(parent)
        style = (
            WS_CHILD
            | WS_VISIBLE
            | WS_VSCROLL
            | WS_TABSTOP
            | WS_CLIPSIBLINGS
            | ES_MULTILINE
            | ES_AUTOVSCROLL
            | ES_WANTRETURN
            | ES_NOHIDESEL
        )
        # WS_EX_LAYOUTRTL and WS_EX_RTLREADING mirror the window and reverse
        # Persian a second time. Direction is set on the paragraph instead.
        hwnd = _user32.CreateWindowExW(
            0,
            "RICHEDIT50W",
            "",
            style,
            0,
            0,
            10,
            10,
            parent,
            None,
            _kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise OSError(f"Could not create the text editor (winerror {ctypes.get_last_error()})")
        self._hwnd = hwnd
        try:
            self._subclass(hwnd)
            self._set_mode()
            self._write(self._pending)
            self._place()
        except Exception:
            self._destroy_native()
            raise

    def _subclass(self, hwnd) -> None:
        # Pointer-sized window procedures. A 32-bit callback truncates the
        # return address on 64-bit Windows and crashes the process.
        self._proc = _WNDPROC(self._on_native)
        previous = _set_wndproc(hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
        if not previous:
            raise OSError(f"Could not attach the editor keys (winerror {ctypes.get_last_error()})")
        self._old_proc = previous

    def _set_mode(self) -> None:
        empty = ctypes.create_unicode_buffer("")
        _send(self._hwnd, WM_SETTEXT, 0, ctypes.addressof(empty))
        # Bidirectional options do nothing unless the control is plaintext.
        result = _send(
            self._hwnd,
            EM_SETTEXTMODE,
            TM_PLAINTEXT | TM_MULTILEVELUNDO | TM_MULTICODEPAGE,
            0,
        )
        if result != 0:
            log.warning("Rich edit plaintext mode was rejected (%s)", result)
        _send(self._hwnd, EM_EXLIMITTEXT, 0, TEXT_LIMIT)
        features = SES_BIDI | SES_EXTENDBACKCOLOR
        _send(self._hwnd, EM_SETEDITSTYLE, features, features)
        _send(self._hwnd, EM_SETTYPOGRAPHYOPTIONS, TO_ADVANCEDTYPOGRAPHY, TO_ADVANCEDTYPOGRAPHY)
        options = _send(self._hwnd, EM_GETLANGOPTIONS, 0, 0)
        options &= ~(IMF_AUTOFONT | IMF_DUALFONT | IMF_AUTOFONTSIZEADJUST)
        _send(self._hwnd, EM_SETLANGOPTIONS, 0, options)
        _send(
            self._hwnd,
            EM_SETMARGINS,
            EC_LEFTMARGIN | EC_RIGHTMARGIN,
            _MARGIN | (_MARGIN << 16),
        )

    def _write(self, text: str) -> None:
        self._pending = text
        payload = _to_richedit(text)
        info = SETTEXTEX(ST_KEEPUNDO | ST_UNICODE, CP_UNICODE)
        buf = ctypes.create_unicode_buffer(payload)
        _send(self._hwnd, EM_SETTEXTEX, ctypes.addressof(info), ctypes.addressof(buf))
        self._apply_presentation(text)
        self._move_caret_to_end()

    def _apply_presentation(self, text: str) -> None:
        _send(self._hwnd, EM_SETBKGNDCOLOR, 0, _PAPER_REF)
        self._apply_font(SCF_DEFAULT)
        self._apply_font(SCF_ALL)
        self._apply_bidi()
        self._apply_paragraph(_prefers_rtl(text))
        _user32.InvalidateRect(self._hwnd, None, True)

    def _apply_font(self, which: int) -> None:
        fmt = CHARFORMATW()
        fmt.cbSize = ctypes.sizeof(fmt)
        fmt.dwMask = CFM_FACE | CFM_SIZE | CFM_COLOR | CFM_CHARSET
        fmt.yHeight = FONT_TWIPS
        fmt.crTextColor = _INK_REF
        fmt.bCharSet = ARABIC_CHARSET
        fmt.szFaceName = FONT_NAME
        _send(self._hwnd, EM_SETCHARFORMAT, which, ctypes.addressof(fmt))

    def _apply_bidi(self) -> None:
        options = BIDIOPTIONS()
        options.cbSize = ctypes.sizeof(options)
        options.wMask = BOM_DEFPARADIR | BOM_CONTEXTREADING | BOM_CONTEXTALIGNMENT | BOM_UNICODEBIDI
        options.wEffects = BOE_RTLDIR | BOE_CONTEXTREADING | BOE_CONTEXTALIGNMENT | BOE_UNICODEBIDI
        _send(self._hwnd, EM_SETBIDIOPTIONS, 0, ctypes.addressof(options))

    def _apply_paragraph(self, rtl: bool) -> None:
        fmt = PARAFORMAT()
        fmt.cbSize = ctypes.sizeof(fmt)
        fmt.dwMask = PFM_ALIGNMENT | PFM_RTLPARA
        fmt.wAlignment = PFA_RIGHT if rtl else PFA_LEFT
        fmt.wEffects = PFE_RTLPARA if rtl else 0
        _send(self._hwnd, EM_SETPARAFORMAT, 0, ctypes.addressof(fmt))

    def _move_caret_to_end(self) -> None:
        count = self._char_count()
        if count < 0:
            return
        self._set_sel(count, count)
        _send(self._hwnd, EM_SCROLLCARET, 0, 0)

    def _select_all(self) -> None:
        count = self._char_count()
        if count < 0:
            return
        self._set_sel(0, max(count, 0))

    def _set_sel(self, start: int, end: int) -> None:
        span = CHARRANGE(int(start), int(end))
        _send(self._hwnd, EM_EXSETSEL, 0, ctypes.addressof(span))

    def _selection(self) -> tuple[int, int]:
        span = CHARRANGE()
        _send(self._hwnd, EM_EXGETSEL, 0, ctypes.addressof(span))
        return int(span.cpMin), int(span.cpMax)

    def _paragraph(self) -> tuple[int, int]:
        fmt = PARAFORMAT()
        fmt.cbSize = ctypes.sizeof(fmt)
        fmt.dwMask = PFM_ALIGNMENT | PFM_RTLPARA
        _send(self._hwnd, EM_GETPARAFORMAT, 0, ctypes.addressof(fmt))
        return int(fmt.wAlignment), int(fmt.wEffects)

    def _face(self) -> tuple[str, int]:
        fmt = CHARFORMATW()
        fmt.cbSize = ctypes.sizeof(fmt)
        _send(self._hwnd, EM_GETCHARFORMAT, SCF_DEFAULT, ctypes.addressof(fmt))
        return str(fmt.szFaceName).split("\x00", 1)[0], int(fmt.yHeight)

    def _char_count(self) -> int:
        info = GETTEXTLENGTHEX(GTL_USECRLF | GTL_PRECISE | GTL_NUMCHARS, CP_UNICODE)
        count = _send(self._hwnd, EM_GETTEXTLENGTHEX, ctypes.addressof(info), 0)
        if count < 0 or count > TEXT_LIMIT:
            return -1
        return count

    def _read(self) -> str:
        count = self._char_count()
        if count < 0:
            return self._pending
        buf = ctypes.create_unicode_buffer(count + 1)
        info = GETTEXTEX()
        info.cb = ctypes.sizeof(buf)
        info.flags = GT_USECRLF
        info.codepage = CP_UNICODE
        _send(self._hwnd, EM_GETTEXTEX, ctypes.addressof(info), ctypes.addressof(buf))
        return _from_richedit(buf.value)

    def _place(self) -> None:
        if self._placing or not self._alive():
            return
        self._placing = True
        try:
            pad = int(self.cget("highlightthickness") or 0)
            top = pad + 8
            width = max(int(self.winfo_width()) - 2 * pad, 1)
            height = max(int(self.winfo_height()) - top - pad, 1)
            _user32.MoveWindow(self._hwnd, pad, top, width, height, True)
        finally:
            self._placing = False

    def _defer(self, callback) -> None:
        # Tk's mainloop releases the GIL around its Win32 get-message call.
        # after() from this window procedure then restores a thread state that
        # no longer exists and kills the process. A Tk timer drains the queue.
        if callback is None:
            return
        self._shortcuts.append(callback)
        if self._native_depth:
            return
        self._arm_shortcut_pump(0)

    def _arm_shortcut_pump(self, delay_ms: int) -> None:
        if self._destroying:
            return
        if self._pump_id is not None:
            if delay_ms > 0:
                return
            try:
                self.after_cancel(self._pump_id)
            except tk.TclError:
                pass
            self._pump_id = None
        try:
            self._pump_id = self.after(delay_ms, self._pump_shortcuts)
        except tk.TclError:
            self._pump_id = None

    def _pump_shortcuts(self) -> None:
        self._pump_id = None
        self._flush_shortcuts()
        if self._destroying:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._arm_shortcut_pump(20)

    def _flush_shortcuts(self) -> None:
        pending = []
        while self._shortcuts:
            pending.append(self._shortcuts.popleft())
        for callback in pending:
            try:
                callback()
            except Exception:
                log.exception("Text editor shortcut failed")

    def _on_native(self, hwnd, msg, wparam, lparam):
        self._native_depth += 1
        try:
            return self._on_message(hwnd, msg, wparam, lparam)
        finally:
            self._native_depth -= 1

    def _shortcut(self, msg: int, wparam: int, lparam: int) -> bool:
        ctrl = _ctrl_down()
        if msg == WM_CHAR:
            # TranslateMessage has already queued this character for Ctrl+Enter and Ctrl+A.
            return ctrl and (wparam & 0xFF) in (1, 10, 13)
        if msg != WM_KEYDOWN:
            return False
        vk = wparam & 0xFF
        repeat = bool(lparam & (1 << 30))
        if ctrl and vk == VK_RETURN:
            if not repeat:
                self._defer(self._on_submit)
            return True
        if vk == VK_ESCAPE:
            if not repeat:
                self._defer(self._on_cancel)
            return True
        if ctrl and vk == VK_A:
            if not repeat:
                self._select_all()
            return True
        return False

    def _on_message(self, hwnd, msg, wparam, lparam):
        swallow = False
        try:
            swallow = self._shortcut(int(msg), int(wparam), int(lparam))
        except Exception:
            log.exception("Text editor shortcut failed")
        if swallow:
            return 0
        old = self._old_proc
        if not old:
            return int(_user32.DefWindowProcW(hwnd, msg, wparam, lparam))
        return int(_user32.CallWindowProcW(old, hwnd, msg, wparam, lparam))

    def _alive(self) -> bool:
        return bool(self._hwnd) and bool(_user32.IsWindow(self._hwnd))

    def _destroy_native(self) -> None:
        hwnd = self._hwnd
        self._hwnd = None
        proc = self._old_proc
        self._old_proc = None
        if hwnd and _user32.IsWindow(hwnd):
            if proc:
                _set_wndproc(hwnd, GWLP_WNDPROC, ctypes.c_void_p(proc))
            _user32.DestroyWindow(hwnd)
        self._proc = None
