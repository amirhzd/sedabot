"""The Speak tab editor has to round-trip Persian and keep it right to left."""

from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SHOT = Path(tempfile.gettempdir()) / "sedabot-editor.png"

WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_A = 0x41
EM_CANUNDO = 0x00C6
EM_UNDO = 0x00C7


def _widget_texts(widget) -> set[str]:
    found = set()
    try:
        found.add(str(widget.cget("text")))
    except Exception:
        pass
    for child in widget.winfo_children():
        found.update(_widget_texts(child))
    return found


def _send(editor, msg: int, wparam: int = 0, lparam: int = 0) -> int:
    from editor import _send as send

    return send(editor._hwnd, msg, wparam, lparam)


def _capture(hwnd):
    """Paint the editor into a bitmap. Screen coordinates stay out of it."""
    from PIL import Image

    from editor import _user32

    user32 = _user32
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.UpdateWindow.argtypes = [ctypes.c_void_p]
    user32.UpdateWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetClientRect failed")
    width, height = int(rect.right), int(rect.bottom)
    if width < 2 or height < 2:
        raise OSError(f"editor client is {width}x{height}")
    user32.UpdateWindow(hwnd)
    hdc = user32.GetDC(hwnd)
    if not hdc:
        raise OSError("GetDC failed")
    memory = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(memory, bitmap)
    try:
        painted = bool(user32.PrintWindow(hwnd, memory, 2))
        if not painted:
            gdi32.BitBlt(memory, 0, 0, width, height, hdc, 0, 0, 0x00CC0020)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        raw = ctypes.create_string_buffer(width * height * 4)
        gdi32.SelectObject(memory, old)
        old = None
        rows = gdi32.GetDIBits(memory, bitmap, 0, height, raw, ctypes.byref(info), 0)
        if rows <= 0:
            raise OSError(f"GetDIBits failed (winerror {ctypes.get_last_error()})")
        image = Image.frombuffer("RGB", (width, height), raw, "raw", "BGRX", 0, 1).copy()
        return image
    finally:
        if old:
            gdi32.SelectObject(memory, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, hdc)


def _ink_sides(image) -> tuple[int, int]:
    width, height = image.size
    pixels = image.load()
    left = right = 0
    split_left = int(width * 0.4)
    split_right = int(width * 0.6)
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels[x, y][:3]
            if (red * 3 + green * 6 + blue) // 10 >= 170:
                continue
            if x < split_left:
                left += 1
            elif x >= split_right:
                right += 1
    return left, right


@unittest.skipUnless(sys.platform == "win32", "the editor is a Windows rich edit control")
class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk

        cls.root = tk.Tk()
        cls.root.geometry("720x380+60+60")
        cls.root.title("Sedabot editor test")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from editor import SpeechEditor

        self.editor = SpeechEditor(self.root)
        self.editor.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
        if self.editor.winfo_exists():
            self.editor.destroy()
        self.root.update_idletasks()

    def test_persian_round_trip_keeps_joins_and_newlines(self):
        text = "سلام، این یک آزمون است.\nنیم\u200cفاصله و سال 2024."
        self.editor.insert("1.0", text)
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), text)
        self.editor.delete("1.0", "end")
        self.assertEqual(self.editor.get("1.0", "end"), "\n")

    def test_typed_characters_append_instead_of_jumping_home(self):
        self.editor.insert("1.0", "کتاب")
        for char in "ها":
            _send(self.editor, WM_CHAR, ord(char))
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), "کتابها")
        self.assertEqual(self.editor._selection(), (6, 6))

    def test_long_paste_is_not_cut_at_the_old_32k_limit(self):
        text = "کتاب " * 8000
        self.editor.insert("1.0", text)
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), text)

    def test_ctrl_enter_speaks_without_inserting_a_newline(self):
        calls = []
        self.editor._on_submit = lambda: calls.append("speak")
        self.editor.insert("1.0", "سلام")
        with patch("editor._ctrl_down", return_value=True):
            self.editor._on_message(self.editor._hwnd, WM_KEYDOWN, VK_RETURN, 0)
            self.editor._on_message(self.editor._hwnd, WM_KEYDOWN, VK_RETURN, 1 << 30)
        self.root.update()
        self.assertEqual(calls, ["speak"])
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), "سلام")

    def test_native_ctrl_enter_does_not_reenter_tk(self):
        calls = []
        self.editor._on_submit = lambda: calls.append("speak")
        self.editor.insert("1.0", "سلام")

        def reject_after(*_args, **_kwargs):
            raise AssertionError("Ctrl+Enter called Tk from the window procedure")

        original = self.editor.after
        self.editor.after = reject_after
        try:
            with patch("editor._ctrl_down", return_value=True):
                self.editor._on_native(self.editor._hwnd, WM_KEYDOWN, VK_RETURN, 0)
        finally:
            self.editor.after = original
        self.assertEqual(calls, [])
        self.editor._flush_shortcuts()
        self.assertEqual(calls, ["speak"])
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), "سلام")

    def test_ctrl_enter_posted_during_mainloop_speaks(self):
        from editor import _user32

        calls = []
        self.editor._on_submit = lambda: calls.append("speak")
        self.editor.insert("1.0", "سلام")
        with patch("editor._ctrl_down", return_value=True):
            self.assertNotEqual(_user32.PostMessageW(self.editor._hwnd, WM_KEYDOWN, VK_RETURN, 0), 0)
            self.root.after(200, self.root.quit)
            self.root.mainloop()
        self.assertEqual(calls, ["speak"])
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), "سلام")

    def test_escape_stops_once(self):
        calls = []
        self.editor._on_cancel = lambda: calls.append("stop")
        self.editor.insert("1.0", "سلام")
        self.editor._on_message(self.editor._hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
        self.editor._on_message(self.editor._hwnd, WM_KEYDOWN, VK_ESCAPE, 1 << 30)
        self.root.update()
        self.assertEqual(calls, ["stop"])
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), "سلام")

    def test_ctrl_a_selects_the_whole_text(self):
        self.editor.insert("1.0", "سلام")
        with patch("editor._ctrl_down", return_value=True):
            self.editor._on_message(self.editor._hwnd, WM_KEYDOWN, VK_A, 0)
        self.assertEqual(self.editor._selection(), (0, 4))

    def test_a_typed_character_can_be_undone(self):
        self.editor.insert("1.0", "سلام")
        _send(self.editor, WM_CHAR, ord("!"))
        self.assertNotEqual(_send(self.editor, EM_CANUNDO), 0)
        _send(self.editor, EM_UNDO)
        self.assertEqual(self.editor.get("1.0", "end").rstrip("\n"), "سلام")

    def test_persian_paragraph_is_rtl_and_english_is_ltr(self):
        from editor import PFA_LEFT, PFA_RIGHT, PFE_RTLPARA

        self.editor.insert("1.0", "کتاب سلام")
        align, effects = self.editor._paragraph()
        self.assertEqual(align, PFA_RIGHT)
        self.assertTrue(effects & PFE_RTLPARA)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", "Hello")
        align, effects = self.editor._paragraph()
        self.assertEqual(align, PFA_LEFT)
        self.assertFalse(effects & PFE_RTLPARA)

    def test_font_stays_tahoma(self):
        from editor import FONT_TWIPS

        self.editor.insert("1.0", "سلام")
        face, height = self.editor._face()
        self.assertEqual(face, "Tahoma")
        self.assertEqual(height, FONT_TWIPS)

    def test_persian_is_drawn_on_the_right_of_a_light_page(self):
        self.editor.insert("1.0", "کتاب سلام")
        self.root.attributes("-topmost", True)
        self.root.update()
        image = _capture(self.editor._hwnd)
        image.save(SHOT)
        self.assertGreater(image.size[0], 200)
        corner = image.getpixel((20, 20))[:3]
        self.assertGreater(min(corner), 220, f"page color {corner} saved {SHOT}")
        left, right = _ink_sides(image)
        self.assertGreater(right, 30, f"no text ink on the right (left={left} right={right}) saved {SHOT}")
        self.assertGreater(right, left * 2, f"text is not right-aligned (left={left} right={right}) saved {SHOT}")


@unittest.skipUnless(sys.platform == "win32", "the editor is a Windows rich edit control")
class SpeakTabTests(unittest.TestCase):
    def test_speak_tab_uses_the_editor_and_leaves_config_alone(self):
        from voices import resolve_paths

        paths = resolve_paths()
        before_cfg = paths.config.read_bytes() if paths.config.is_file() else None
        before_hist = paths.history.read_bytes() if paths.history.is_file() else None
        from sayit import SAMPLE_TEXT, SayItApp
        from editor import SpeechEditor

        app = None
        try:
            app = SayItApp(autoload=False, hotkeys=False, tray=False)
            app.update()
            self.assertIsInstance(app.text_box, SpeechEditor)
            self.assertEqual(app.text_box.get("1.0", "end").rstrip("\n"), SAMPLE_TEXT)
            loaded = "این متن از تاریخچه آمده است.\nخط دوم"
            app._load_history_text(loaded)
            self.assertEqual(app.text_box.get("1.0", "end").rstrip("\n"), loaded)
            app._set_text("متن کلیپ‌بورد")
            self.assertEqual(app.text_box.get("1.0", "end").rstrip("\n"), "متن کلیپ‌بورد")
            labels = _widget_texts(app._shortcut_cards)
            self.assertIn("Speak clipboard", labels)
            self.assertIn("Speak selection", labels)
            self.assertIn("Ctrl", labels)
            self.assertIn("Alt", labels)
            self.assertIn("V", labels)
            self.assertIn("S", labels)
            app.config["hotkey_clipboard"] = "ctrl+shift+f9"
            app._render_global_shortcuts()
            labels = _widget_texts(app._shortcut_cards)
            self.assertIn("F9", labels)
            self.assertIn("Shift", labels)
            self.assertNotIn("V", labels)
        finally:
            if app is not None:
                app._quitting = True
                app.destroy()
            if before_cfg is not None and paths.config.read_bytes() != before_cfg:
                paths.config.write_bytes(before_cfg)
                self.fail("opening the editor wrote config.json")
            if before_hist is not None and paths.history.read_bytes() != before_hist:
                paths.history.write_bytes(before_hist)
                self.fail("opening the editor wrote history.json")

    def test_escape_stops_playback_outside_the_editor(self):
        from voices import resolve_paths

        paths = resolve_paths()
        before_cfg = paths.config.read_bytes() if paths.config.is_file() else None
        before_hist = paths.history.read_bytes() if paths.history.is_file() else None
        from sayit import SayItApp

        app = None
        try:
            app = SayItApp(autoload=False, hotkeys=False, tray=False)
            app.update()
            calls = []
            app.stop_playback = lambda: calls.append("stop")
            app.status.focus_set()
            app.event_generate("<Escape>")
            app.update()
            self.assertEqual(calls, ["stop"])
        finally:
            if app is not None:
                app._quitting = True
                app.destroy()
            if before_cfg is not None and paths.config.read_bytes() != before_cfg:
                paths.config.write_bytes(before_cfg)
                self.fail("the Escape test wrote config.json")
            if before_hist is not None and paths.history.read_bytes() != before_hist:
                paths.history.write_bytes(before_hist)
                self.fail("the Escape test wrote history.json")

    def test_the_keystroke_retag_is_gone(self):
        source = (ROOT / "sayit.py").read_text(encoding="utf-8")
        self.assertNotIn("_apply_rtl", source)
        self.assertNotIn("sayit-rtl", source)
        self.assertIn("SpeechEditor", source)
