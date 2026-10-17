#!/usr/bin/env python3
"""Sedabot for Windows — local, private text-to-speech with Persian voices.

Synthesis stays on this machine. The network is used only when a Piper voice
or the optional piper.exe fallback is missing.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import customtkinter as ctk
import pygame
import pyperclip
from tkinter import filedialog

from app_icon import build_image, ensure_icon
from editor import SpeechEditor
from hotkeys import SelectionError, WinHotkeys, capture_selection, hotkey_labels
from voices import (
    CATALOG,
    DEFAULT_VOICE,
    DownloadCancelled,
    AppPaths,
    configure_logging,
    download_piper_exe,
    ensure_voice,
    find_piper_exe,
    import_voice_file,
    installed_voice_names,
    is_piper_voice_name,
    load_piper_voice,
    menu_voice_names,
    model_paths,
    resolve_paths,
    set_windows_startup,
    short_error,
    synthesize_best,
    voice_is_installed,
    voice_title,
)

log = logging.getLogger("sayit")

APP_TITLE = "Sedabot"
SAMPLE_TEXT = (
    "سلام! این برنامه صدا بات برای ویندوز است. "
    "متن فارسی را اینجا بنویسید و دکمه Speak را بزنید."
)
MAX_CHARS = 8000
HISTORY_CHARS = 100_000
HISTORY_KEEP = 40

DEFAULT_CONFIG = {
    "current_voice": DEFAULT_VOICE,
    "speed": 1.0,
    "hotkey_clipboard": "ctrl+alt+v",
    "hotkey_selection": "ctrl+alt+s",
    "minimize_to_tray": True,
    "run_at_startup": False,
    "theme": "dark",
    "geometry": "880x740",
    "extra_voices": [],
}


class SayItApp(ctk.CTk):
    def __init__(self, *, autoload: bool = True, hotkeys: bool = True, tray: bool = True):
        super().__init__()
        self.paths: AppPaths = resolve_paths()
        if not logging.getLogger().handlers:
            configure_logging(self.paths.log)

        self.config = self._load_config()
        self.history: list[dict] = self._load_history()
        self.current_voice = None
        self.model_path: Path | None = None
        self.config_path: Path | None = None
        self.piper_exe: Path | None = find_piper_exe(self.paths)
        self.engine_name = ""
        self._mixer_rate = 22050
        self._voice_lock = threading.Lock()
        self._speak_lock = threading.Lock()
        self._play_gen = 0
        self.stop_flag = threading.Event()
        self._download_cancel = threading.Event()
        self._voice_token = 0
        self._loading = False
        self._quitting = False
        self._updating_menu = False
        self._tray_hint_shown = False
        self._last_speed_save = 0.0
        self.audio_ok = False
        self.tray = None
        self._hotkeys_enabled = hotkeys
        self.hotkeys = WinHotkeys()
        self._want_tray = tray

        self._apply_theme(self.config.get("theme", "dark"))
        self.title(APP_TITLE)
        self.minsize(720, 560)
        geometry = str(self.config.get("geometry") or "880x740")
        if re.match(r"^\d+x\d+", geometry):
            self.geometry(geometry)

        icon = ensure_icon(self.paths.icon)
        if icon is not None:
            try:
                self.iconbitmap(str(icon))
            except Exception:
                log.exception("Could not set the window icon")

        self._init_audio()
        self._cleanup_temp_wavs()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.config.get("run_at_startup"):
            self._write_startup(True, quiet=True)
        if hotkeys:
            self._install_hotkeys(announce=False)
        if tray:
            self._start_tray()
        if autoload:
            self.load_voice(self.config.get("current_voice") or DEFAULT_VOICE)
        else:
            self.set_status("Ready — voice loads when you pick one.")

    # ------------------------------------------------------------------ config
    def _load_config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        path = self.paths.config
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg.update(loaded)
            except Exception:
                log.exception("Could not read %s", path)
        if not isinstance(cfg.get("extra_voices"), list):
            cfg["extra_voices"] = []
        cfg.pop("reference_voice", None)
        cfg.pop("reference_path", None)
        if cfg.get("current_voice") == "pocket-farsi-v2":
            cfg["current_voice"] = DEFAULT_VOICE
        return cfg

    def save_config(self) -> None:
        try:
            self.paths.config.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            log.exception("Could not save config")

    def _load_history(self) -> list:
        path = self.paths.history
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            log.exception("Could not read history")
            return []

    def _save_history(self) -> None:
        try:
            self.paths.history.write_text(
                json.dumps(self.history[-HISTORY_KEEP:], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            log.exception("Could not save history")

    def _save_geometry(self) -> None:
        try:
            if self.state() == "normal":
                self.config["geometry"] = self.geometry()
        except Exception:
            pass

    # ---------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(header, text="Sedabot", font=ctk.CTkFont(size=22, weight="bold")).pack(
            side="left", padx=8, pady=8
        )
        self.voice_var = ctk.StringVar(value=self.config.get("current_voice") or DEFAULT_VOICE)
        self.voice_menu = ctk.CTkOptionMenu(
            header,
            values=self._voice_names(),
            variable=self.voice_var,
            command=self._on_voice_change,
            width=260,
        )
        self.voice_menu.pack(side="right", padx=8, pady=8)
        ctk.CTkLabel(header, text="Voice").pack(side="right")

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self.tabs.add("Speak")
        self.tabs.add("Voices")
        self.tabs.add("Settings")
        self._build_speak_tab(self.tabs.tab("Speak"))
        self._build_voices_tab(self.tabs.tab("Voices"))
        self._build_settings_tab(self.tabs.tab("Settings"))

        # Esc stops playback wherever focus is inside the window. The text
        # editor handles Esc itself when it has the keyboard focus.
        self.bind_all("<Escape>", self._on_escape, add="+")

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.progress.grid_remove()

        self.status = ctk.CTkLabel(self, text="Starting…", anchor="w", justify="left", wraplength=820)
        self.status.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 10))

    def _build_speak_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            parent,
            text="Type or paste Persian text. Ctrl+Enter speaks. Esc stops.",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))

        self.text_box = SpeechEditor(
            parent,
            on_submit=self.speak_current,
            on_cancel=self.stop_playback,
        )
        self.text_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)
        self.text_box.insert("1.0", SAMPLE_TEXT)
        self.after_idle(self.text_box.focus_set)

        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        ctk.CTkButton(controls, text="Speak", width=100, command=self.speak_current).pack(side="left", padx=(0, 6))
        ctk.CTkButton(controls, text="Clipboard", width=110, command=self.speak_clipboard).pack(side="left", padx=6)
        ctk.CTkButton(
            controls, text="Stop", width=80, fg_color="#c0392b", hover_color="#922b21", command=self.stop_playback
        ).pack(side="left", padx=6)

        ctk.CTkLabel(controls, text="Speed").pack(side="left", padx=(18, 4))
        self.speed_slider = ctk.CTkSlider(controls, from_=0.5, to=2.0, number_of_steps=15, width=150)
        self.speed_slider.set(float(self.config.get("speed") or 1.0))
        self.speed_slider.pack(side="left", padx=4)
        self.speed_label = ctk.CTkLabel(controls, text=self._speed_text(), width=48)
        self.speed_label.pack(side="left")
        self.speed_slider.configure(command=self._on_speed_change)

        self._build_global_shortcuts(parent)

        history_wrap = ctk.CTkFrame(parent)
        history_wrap.grid(row=4, column=0, sticky="nsew", padx=8, pady=(4, 8))
        history_wrap.grid_columnconfigure(0, weight=1)
        history_wrap.grid_rowconfigure(1, weight=1)
        title_row = ctk.CTkFrame(history_wrap, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(title_row, text="History", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4)
        ctk.CTkButton(title_row, text="Clear", width=70, command=self.clear_history).pack(side="right", padx=4, pady=4)
        self.history_frame = ctk.CTkScrollableFrame(history_wrap, height=140)
        self.history_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.refresh_history()

    def _build_global_shortcuts(self, parent) -> None:
        strip = ctk.CTkFrame(parent, corner_radius=12)
        strip.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 4))
        head = ctk.CTkFrame(strip, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="From any window", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(head, text="These keys work in other apps", text_color=("gray40", "gray70")).pack(side="right")
        self._shortcut_cards = ctk.CTkFrame(strip, fg_color="transparent")
        self._shortcut_cards.pack(fill="x", padx=6, pady=(0, 8))
        self._render_global_shortcuts()

    def _render_global_shortcuts(self) -> None:
        cards = getattr(self, "_shortcut_cards", None)
        if cards is None:
            return
        for child in cards.winfo_children():
            child.destroy()
        specs = (
            (
                self.config.get("hotkey_clipboard") or "ctrl+alt+v",
                "Speak clipboard",
                "Reads the last thing you copied.",
            ),
            (
                self.config.get("hotkey_selection") or "ctrl+alt+s",
                "Speak selection",
                "Highlight text, then press. Your clipboard is put back.",
            ),
        )
        for spec, title, detail in specs:
            self._shortcut_card(cards, spec, title, detail)

    def _shortcut_card(self, parent, spec: str, title: str, detail: str) -> None:
        card = ctk.CTkFrame(
            parent,
            corner_radius=10,
            border_width=1,
            border_color=("gray70", "gray28"),
            fg_color=("gray95", "gray14"),
        )
        card.pack(side="left", fill="both", expand=True, padx=4)
        card.grid_columnconfigure(1, weight=1)
        keys = ctk.CTkFrame(card, fg_color="transparent")
        keys.grid(row=0, column=0, rowspan=2, sticky="w", padx=(10, 8), pady=10)
        labels = hotkey_labels(str(spec)) or ["?"]
        for part in labels:
            self._keycap(keys, part)
        ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=1, sticky="sw", padx=(0, 10), pady=(10, 0)
        )
        ctk.CTkLabel(
            card,
            text=detail,
            anchor="w",
            justify="left",
            wraplength=250,
            text_color=("gray40", "gray70"),
        ).grid(row=1, column=1, sticky="nw", padx=(0, 10), pady=(0, 10))

    def _keycap(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            width=max(28, 9 * len(text) + 14),
            height=26,
            corner_radius=6,
            border_width=1,
            border_color=("#c5ceda", "#4a5568"),
            fg_color=("#ffffff", "#2c3544"),
            text_color=("gray10", "#DCE4EE"),
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(0, 4))

    def _build_voices_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            parent,
            text=(
                "Piper voices download once, about 63 MB each, and stay in the models folder. "
                "Nothing you type is uploaded."
            ),
            anchor="w",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self.voice_list = ctk.CTkScrollableFrame(parent)
        self.voice_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self.refresh_voice_rows()

        tools = ctk.CTkFrame(parent, fg_color="transparent")
        tools.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        ctk.CTkButton(tools, text="Add voice files…", command=self.add_voice_files).pack(side="left", padx=(0, 6))
        ctk.CTkButton(tools, text="Open models folder", command=self.open_models_folder).pack(side="left", padx=6)
        ctk.CTkButton(tools, text="Rescan", width=80, command=self.rescan_voices).pack(side="left", padx=6)

        other = ctk.CTkFrame(parent)
        other.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        ctk.CTkLabel(other, text="Other Piper voice").pack(side="left", padx=8)
        self.other_var = ctk.StringVar()
        entry = ctk.CTkEntry(other, textvariable=self.other_var, width=280, placeholder_text="en_US-lessac-medium")
        entry.pack(side="left", padx=4, pady=8)
        ctk.CTkButton(other, text="Download", width=110, command=self.download_other_voice).pack(side="left", padx=8)

    def _build_settings_tab(self, parent) -> None:
        parent.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            parent,
            text="Hotkeys use Windows itself and do not need Administrator. The selection hotkey copies the highlighted text after you release the keys, then restores your clipboard.",
            anchor="w",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 8))

        ctk.CTkLabel(parent, text="Speak clipboard").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.clip_hotkey_var = ctk.StringVar(value=self.config.get("hotkey_clipboard") or "ctrl+alt+v")
        ctk.CTkEntry(parent, textvariable=self.clip_hotkey_var, width=220).grid(row=1, column=1, sticky="w", padx=8)

        ctk.CTkLabel(parent, text="Speak selection").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.sel_hotkey_var = ctk.StringVar(value=self.config.get("hotkey_selection") or "ctrl+alt+s")
        ctk.CTkEntry(parent, textvariable=self.sel_hotkey_var, width=220).grid(row=2, column=1, sticky="w", padx=8)
        ctk.CTkButton(parent, text="Apply hotkeys", width=140, command=self.apply_hotkeys_from_ui).grid(
            row=3, column=1, sticky="w", padx=8, pady=4
        )

        self.footer_var = ctk.StringVar(value=self._hotkey_footer())
        ctk.CTkLabel(parent, textvariable=self.footer_var, text_color="gray", anchor="w").grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8)
        )

        self.tray_var = ctk.BooleanVar(value=bool(self.config.get("minimize_to_tray", True)))
        ctk.CTkCheckBox(
            parent,
            text="Closing the window keeps Sedabot in the system tray",
            variable=self.tray_var,
            command=self._on_tray_toggle,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        self.startup_var = ctk.BooleanVar(value=bool(self.config.get("run_at_startup", False)))
        ctk.CTkCheckBox(
            parent,
            text="Start Sedabot when I sign in to Windows",
            variable=self.startup_var,
            command=self._on_startup_toggle,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(parent, text="Theme").grid(row=7, column=0, sticky="w", padx=8, pady=4)
        theme = str(self.config.get("theme") or "dark").capitalize()
        if theme not in ("Dark", "Light", "System"):
            theme = "Dark"
        self.theme_menu = ctk.CTkOptionMenu(
            parent, values=["Dark", "Light", "System"], command=self._on_theme_change, width=140
        )
        self.theme_menu.set(theme)
        self.theme_menu.grid(row=7, column=1, sticky="w", padx=8, pady=4)

        exe = self.piper_exe
        self.exe_label = ctk.CTkLabel(
            parent,
            text=self._exe_text(exe),
            anchor="w",
            justify="left",
            wraplength=760,
        )
        self.exe_label.grid(row=8, column=0, columnspan=2, sticky="ew", padx=8, pady=(12, 4))
        ctk.CTkButton(parent, text="Download piper.exe fallback", command=self.download_exe).grid(
            row=9, column=0, columnspan=2, sticky="w", padx=8, pady=4
        )
        ctk.CTkLabel(
            parent,
            text="piper.exe is optional. Sedabot uses the piper-tts Python package first, and this official binary only if that fails. Speech still stays on this PC.",
            anchor="w",
            wraplength=760,
            justify="left",
            text_color="gray",
        ).grid(row=10, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        ctk.CTkButton(parent, text="Open log", width=100, command=self.open_log).grid(
            row=11, column=0, sticky="w", padx=8, pady=(8, 8)
        )
        self.narration_label = ctk.CTkLabel(
            parent,
            text=self._narration_text(),
            anchor="w",
            justify="left",
            wraplength=760,
        )
        self.narration_label.grid(row=12, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

    def _on_escape(self, _event=None):
        if self._quitting:
            return
        try:
            focused = self.focus_get()
        except Exception:
            focused = None
        if focused is not None and str(focused).startswith(str(self.text_box)):
            # The editor's own Esc handler fires too; stopping twice is harmless.
            return
        self.stop_playback()

    def _voice_names(self) -> list[str]:
        extras = self.config.get("extra_voices") or []
        return menu_voice_names(self.paths.models, extras)

    def _voice_label(self, name: str) -> str:
        return voice_title(name)

    def _narration_text(self) -> str:
        if self.engine_name:
            return f"Active voice engine: {self.engine_name}."
        return "Piper voices stay on this PC. The first download of a voice is about 63 MB."

    def _refresh_narration_label(self) -> None:
        if hasattr(self, "narration_label"):
            self.narration_label.configure(text=self._narration_text())

    def reload_voice_menu(self) -> None:
        names = self._voice_names()
        current = self.voice_var.get()
        if current and current not in names:
            names.append(current)
            names.sort(key=lambda name: voice_title(name).casefold())
        self._updating_menu = True
        try:
            self.voice_menu.configure(values=names)
            if current:
                self.voice_var.set(current)
        finally:
            self._updating_menu = False

    def refresh_voice_rows(self) -> None:
        if not hasattr(self, "voice_list"):
            return
        for child in self.voice_list.winfo_children():
            child.destroy()
        for name in self._voice_names():
            self._add_voice_row(name)

    def _add_voice_row(self, name: str) -> None:
        spec = CATALOG.get(name)
        description = spec.description if spec else "Custom voice in the models folder."
        installed = voice_is_installed(self.paths.models, name)
        if installed:
            onnx, _config = model_paths(self.paths.models, name)
            description = f"{description} On disk: {onnx.stat().st_size / (1024 * 1024):.0f} MB."
        row = ctk.CTkFrame(self.voice_list)
        row.pack(fill="x", pady=4, padx=2)
        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        ctk.CTkLabel(
            text_col,
            text=f"{self._voice_label(name)}   {name}",
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).pack(fill="x")
        ctk.CTkLabel(
            text_col,
            text=description,
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(fill="x")
        state = "Installed" if installed else "Not downloaded"
        ctk.CTkLabel(row, text=state, width=120).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Use" if installed else "Download",
            width=110,
            command=lambda n=name: self.load_voice(n),
        ).pack(side="left", padx=4, pady=8)
        if name in CATALOG or is_piper_voice_name(name):
            ctk.CTkButton(
                row,
                text="Redownload",
                width=110,
                fg_color="transparent",
                border_width=1,
                command=lambda n=name: self.load_voice(n, force=True),
            ).pack(side="right", padx=6)

    def refresh_history(self) -> None:
        if not hasattr(self, "history_frame"):
            return
        for child in self.history_frame.winfo_children():
            child.destroy()
        items = list(reversed(self.history[-15:]))
        if not items:
            ctk.CTkLabel(self.history_frame, text="Nothing spoken yet.", text_color="gray").pack(anchor="w", padx=4, pady=4)
            return
        for item in items:
            text = str(item.get("text") or "")
            shown = text.replace("\n", " ")
            if len(shown) > 96:
                shown = shown[:93] + "..."
            when = item.get("time") or ""
            voice = item.get("voice") or ""
            ctk.CTkButton(
                self.history_frame,
                text=f"[{when}] {voice}: {shown}",
                anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray85", "gray25"),
                command=lambda t=text: self._load_history_text(t),
            ).pack(fill="x", pady=1)

    def _load_history_text(self, text: str) -> None:
        self._set_text(text)

    def clear_history(self) -> None:
        self.history.clear()
        self._save_history()
        self.refresh_history()

    def _speed_text(self) -> str:
        return f"{float(self.config.get('speed') or 1.0):.1f}x"

    def _on_speed_change(self, value) -> None:
        self.config["speed"] = round(float(value), 2)
        self.speed_label.configure(text=f"{float(value):.1f}x")
        now = time.time()
        if now - self._last_speed_save > 0.4:
            self._last_speed_save = now
            self.save_config()

    def _on_voice_change(self, choice: str) -> None:
        if self._updating_menu:
            return
        self.load_voice(choice)

    def _on_theme_change(self, choice: str) -> None:
        self.config["theme"] = choice.lower()
        self._apply_theme(choice)
        self.save_config()

    def _apply_theme(self, theme: str) -> None:
        try:
            ctk.set_appearance_mode(str(theme))
            ctk.set_default_color_theme("blue")
        except Exception:
            ctk.set_appearance_mode("dark")

    def _hotkey_footer(self) -> str:
        clip = self.config.get("hotkey_clipboard") or "ctrl+alt+v"
        sel = self.config.get("hotkey_selection") or "ctrl+alt+s"
        return f"Hotkeys: {clip} clipboard, {sel} selection. Local and private."

    def set_status(self, message: str, *, error: bool = False) -> None:
        def apply() -> None:
            if not self.winfo_exists():
                return
            self.status.configure(text=message)
            self.status.configure(text_color="#e74c3c" if error else ("gray10", "gray90"))

        self._ui(apply)

    def set_progress(self, frac: float | None) -> None:
        def apply() -> None:
            if not self.winfo_exists():
                return
            if frac is None:
                self.progress.grid_remove()
                return
            if not self.progress.winfo_ismapped():
                self.progress.grid()
            self.progress.set(max(0.0, min(1.0, float(frac))))

        self._ui(apply)

    def _ui(self, fn) -> None:
        def wrapped() -> None:
            if self._quitting:
                return
            try:
                fn()
            except Exception:
                log.exception("UI update failed")

        try:
            self.after(0, wrapped)
        except Exception:
            pass

    def _on_download_progress(self, filename: str, got: int, total: int) -> None:
        got_mb = got / (1024 * 1024)
        if total > 0:
            total_mb = total / (1024 * 1024)
            self.set_status(f"Downloading {filename} — {got_mb:.1f} / {total_mb:.1f} MB")
            self.set_progress(got / total)
        else:
            self.set_status(f"Downloading {filename} — {got_mb:.1f} MB")
            self.set_progress(0)

    # -------------------------------------------------------------------- voices
    def load_voice(self, name: str, *, force: bool = False) -> None:
        name = (name or "").strip()
        if not name:
            self.set_status("Choose a voice first.", error=True)
            return
        if name == "pocket-farsi-v2":
            self.set_status(
                "Pocket Farsi v2 is not available. It only speaks short phrases, so Sedabot uses Piper until a long-form version exists.",
                error=True,
            )
            self._updating_menu = True
            try:
                self.voice_var.set(self.config.get("current_voice") or DEFAULT_VOICE)
            finally:
                self._updating_menu = False
            return
        self._voice_token += 1
        token = self._voice_token
        self._download_cancel.set()
        cancel = threading.Event()
        self._download_cancel = cancel
        self._loading = True
        self.config["current_voice"] = name
        self.save_config()
        self._updating_menu = True
        try:
            names = self._voice_names()
            if name not in names:
                names.append(name)
                self.voice_menu.configure(values=names)
            self.voice_var.set(name)
        finally:
            self._updating_menu = False

        def work() -> None:
            try:
                installed = voice_is_installed(self.paths.models, name)
                if force or not installed:
                    self.set_status(
                        f"Downloading {voice_title(name)} ({name}). "
                        "About 63 MB. This stays in the models folder."
                    )
                else:
                    self.set_status(f"Loading {voice_title(name)}…")
                onnx, config = ensure_voice(
                    name,
                    self.paths.models,
                    progress=self._on_download_progress,
                    cancel=cancel,
                    force=force,
                )
                if token != self._voice_token:
                    return
                self.set_progress(None)
                self.set_status(f"Loading {voice_title(name)}…")
                voice = None
                load_error = None
                try:
                    voice = load_piper_voice(onnx, config)
                    engine = "piper-tts"
                except Exception as exc:
                    log.exception("Could not load %s with piper-tts", name)
                    load_error = exc
                    engine = ""
                    exe = find_piper_exe(self.paths)
                    if exe is None:
                        raise RuntimeError(
                            f"Could not load {name} with piper-tts ({short_error(exc)}). "
                            "The model files are in the models folder. "
                            "Download the piper.exe fallback from Settings, or reinstall piper-tts."
                        ) from exc
                    engine = "piper.exe"
                if token != self._voice_token:
                    return
                with self._voice_lock:
                    self.current_voice = voice
                    self.model_path = onnx
                    self.config_path = config
                    self.piper_exe = find_piper_exe(self.paths)
                    self.engine_name = engine
                if load_error is None:
                    log.info("Voice ready: %s via piper-tts", name)
                    self.set_status(f"Ready — {voice_title(name)}")
                else:
                    log.info("Voice ready: %s via piper.exe", name)
                    self.set_status(
                        f"Ready — {voice_title(name)} via piper.exe. "
                        f"piper-tts could not load it ({short_error(load_error)}).",
                        error=True,
                    )
                self._ui(self.refresh_voice_rows)
                self._ui(self.reload_voice_menu)
                self._ui(self._refresh_narration_label)
            except DownloadCancelled:
                return
            except Exception as exc:
                if token != self._voice_token:
                    return
                log.exception("Voice %s failed", name)
                with self._voice_lock:
                    self.current_voice = None
                    self.model_path = None
                    self.config_path = None
                self.set_progress(None)
                self.set_status(f"Error: {short_error(exc)} Details are in sayit.log.", error=True)
                self._ui(self.refresh_voice_rows)
                self._ui(self._refresh_narration_label)
            finally:
                if token == self._voice_token:
                    self._loading = False

        threading.Thread(target=work, name="sayit-voice", daemon=True).start()

    def add_voice_files(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose a Piper .onnx model",
            filetypes=[("Piper ONNX", "*.onnx"), ("All files", "*.*")],
        )
        if not selected:
            return
        try:
            name = import_voice_file(Path(selected), self.paths.models)
        except Exception as exc:
            log.exception("Import failed")
            self.set_status(f"Error: {short_error(exc)}", error=True)
            return
        self._remember_extra(name)
        self.load_voice(name)

    def download_other_voice(self) -> None:
        name = self.other_var.get().strip()
        if name in CATALOG or is_piper_voice_name(name):
            self._remember_extra(name)
            self.load_voice(name)
            return
        self.set_status("Enter a Piper voice id such as en_US-lessac-medium or fa_IR-gyro-medium.", error=True)

    def _remember_extra(self, name: str) -> None:
        if name not in CATALOG:
            extras = [item for item in (self.config.get("extra_voices") or []) if isinstance(item, str)]
            if name not in extras:
                extras.append(name)
                self.config["extra_voices"] = extras
                self.save_config()
        self.reload_voice_menu()
        self.refresh_voice_rows()

    def rescan_voices(self) -> None:
        found = installed_voice_names(self.paths.models)
        for name in found:
            self._remember_extra(name)
        self.set_status(f"Found {len(found)} voice(s) in {self.paths.models}.")

    def open_models_folder(self) -> None:
        self._open_path(self.paths.models)

    def open_log(self) -> None:
        if not self.paths.log.exists():
            self.paths.log.write_text("", encoding="utf-8")
        self._open_path(self.paths.log)

    def _open_path(self, path: Path) -> None:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            self.set_status(f"Error: could not open {path} ({exc}).", error=True)

    def download_exe(self) -> None:
        cancel = self._download_cancel

        def work() -> None:
            try:
                self.set_status("Downloading the official Windows piper.exe…")
                exe = download_piper_exe(self.paths, progress=self._on_download_progress, cancel=cancel)
                with self._voice_lock:
                    self.piper_exe = exe
                self.set_progress(None)
                self.set_status(f"piper.exe is ready ({exe}).")
                self._ui(lambda: self.exe_label.configure(text=self._exe_text(exe)))
            except DownloadCancelled:
                self.set_progress(None)
            except Exception as exc:
                log.exception("piper.exe download failed")
                self.set_progress(None)
                self.set_status(f"Error: {short_error(exc)}", error=True)

        threading.Thread(target=work, name="sayit-piper-exe", daemon=True).start()

    def _exe_text(self, exe: Path | None) -> str:
        if exe is None:
            return "piper.exe fallback: not installed. piper-tts is the main engine."
        return f"piper.exe fallback: {exe}"

    # ------------------------------------------------------------------- speech
    def speak_current(self) -> None:
        self.speak_text(self.text_box.get("1.0", "end"))

    def speak_clipboard(self) -> None:
        try:
            text = pyperclip.paste() or ""
        except Exception as exc:
            self.set_status(f"Error: could not read the clipboard ({exc}).", error=True)
            return
        if not str(text).strip():
            self.set_status("The clipboard is empty.", error=True)
            return
        self._set_text(str(text))
        self.speak_text(str(text))

    def speak_external_text(self, text: str) -> None:
        self._set_text(text)
        self.speak_text(text)

    def _set_text(self, text: str) -> None:
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", text)

    def speak_text(self, text: str) -> None:
        raw = (text or "").strip()
        if not raw:
            self.set_status("Nothing to speak.", error=True)
            return
        voice_ready = self.current_voice is not None or self.model_path is not None
        if self._loading or not voice_ready:
            if self._loading:
                self.set_status("The voice is still loading. Wait until the status says Ready.")
                return
            self.set_status(
                "No voice is loaded. Open the Voices tab and download Ganji (or another Persian voice).",
                error=True,
            )
            return
        if len(raw) > MAX_CHARS:
            raw = raw[:MAX_CHARS]
            self.set_status(f"Speaking the first {MAX_CHARS:,} characters.")
        if not self._ensure_audio():
            return

        generation = self._interrupt_speech()
        speed = float(self.config.get("speed") or 1.0)

        def work() -> None:
            acquired = self._speak_lock.acquire(timeout=180)
            if not acquired:
                self.set_status("Error: audio is still busy. Press Stop and try again.", error=True)
                return
            wav_path = Path(tempfile.gettempdir()) / "sayit" / f"say-{time.time_ns()}.wav"
            try:
                if generation != self._play_gen:
                    return
                self.stop_flag.clear()
                with self._voice_lock:
                    voice = self.current_voice
                    exe = self.piper_exe
                    model = self.model_path
                    config = self.config_path
                    voice_name = self.config.get("current_voice") or "voice"
                self.set_status("Synthesizing…")
                engine = synthesize_best(
                    text=raw,
                    wav_path=wav_path,
                    speed=speed,
                    voice=voice,
                    piper_exe=exe,
                    model_path=model,
                    config_path=config,
                )
                if generation != self._play_gen or self.stop_flag.is_set():
                    return
                self._set_mixer_rate(22050)
                self.set_status("Playing…")
                self._play_wav(wav_path, generation)
                if generation != self._play_gen:
                    return
                self._ui(lambda: self._add_history(raw, str(voice_name)))
                self.set_status(f"Ready — {voice_title(str(voice_name))} ({engine})")
                log.info("Spoke %s characters with %s via %s", len(raw), voice_name, engine)
            except Exception as exc:
                if generation != self._play_gen:
                    return
                log.exception("Speak failed")
                self.set_status(f"Error: {short_error(exc)} Details are in sayit.log.", error=True)
            finally:
                self._delete_later(wav_path)
                self._speak_lock.release()

        threading.Thread(target=work, name="sayit-speak", daemon=True).start()

    def _play_wav(self, wav_path: Path, generation: int) -> None:
        pygame.mixer.music.load(str(wav_path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if self.stop_flag.is_set() or generation != self._play_gen:
                pygame.mixer.music.stop()
                break
            time.sleep(0.05)
        pygame.mixer.music.stop()
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass

    def _add_history(self, text: str, voice: str) -> None:
        self.history.append(
            {
                "text": text[:HISTORY_CHARS],
                "voice": voice,
                "time": time.strftime("%Y-%m-%d %H:%M"),
            }
        )
        self._save_history()
        self.refresh_history()

    def _interrupt_speech(self) -> int:
        self.stop_flag.set()
        self._play_gen += 1
        try:
            if self.audio_ok and pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
        return self._play_gen

    def stop_playback(self) -> None:
        self._interrupt_speech()
        self.set_status("Stopped.")

    def _ensure_audio(self) -> bool:
        if self.audio_ok:
            return True
        self._init_audio()
        if self.audio_ok:
            return True
        self.set_status("Error: Windows did not provide an audio device, so playback cannot start.", error=True)
        return False

    def _init_audio(self) -> None:
        try:
            if pygame.mixer.get_init():
                self.audio_ok = True
                self._mixer_rate = int(pygame.mixer.get_init()[0])
                return
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
            self._mixer_rate = 22050
            self.audio_ok = True
        except Exception:
            log.exception("Audio init failed")
            self.audio_ok = False

    def _set_mixer_rate(self, rate: int) -> None:
        rate = int(rate)
        current = pygame.mixer.get_init()
        if current and int(current[0]) == rate and self.audio_ok:
            self._mixer_rate = rate
            return
        if current:
            pygame.mixer.quit()
        buffer = 1024 if rate >= 24000 else 512
        pygame.mixer.init(frequency=rate, size=-16, channels=1, buffer=buffer)
        self._mixer_rate = rate
        self.audio_ok = True

    def _cleanup_temp_wavs(self) -> None:
        folder = Path(tempfile.gettempdir()) / "sayit"
        if not folder.is_dir():
            return
        for wav in folder.glob("*.wav"):
            self._delete_later(wav)

    def _delete_later(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------ hotkeys
    def apply_hotkeys_from_ui(self) -> None:
        clip = self.clip_hotkey_var.get().strip().lower()
        sel = self.sel_hotkey_var.get().strip().lower()
        if clip == sel:
            self.set_status("Choose two different hotkeys.", error=True)
            return
        self.config["hotkey_clipboard"] = clip
        self.config["hotkey_selection"] = sel
        self.save_config()
        self.footer_var.set(self._hotkey_footer())
        self._render_global_shortcuts()
        self._hotkeys_enabled = True
        self._install_hotkeys(announce=True)

    def _install_hotkeys(self, *, announce: bool) -> None:
        bindings = {
            self.config.get("hotkey_clipboard") or "ctrl+alt+v": self._hotkey_clipboard,
            self.config.get("hotkey_selection") or "ctrl+alt+s": self._hotkey_selection,
        }
        errors = self.hotkeys.start(bindings)
        if errors:
            self.set_status("Error: " + " ".join(errors), error=True)
        elif announce:
            self.set_status("Hotkeys updated. " + self._hotkey_footer())

    def _hotkey_clipboard(self) -> None:
        self.after(0, self.speak_clipboard)

    def _hotkey_selection(self) -> None:
        try:
            text = capture_selection()
        except SelectionError as exc:
            self.set_status(str(exc), error=True)
            return
        except Exception as exc:
            log.exception("Selection hotkey failed")
            self.set_status(f"Error: could not copy the selection ({short_error(exc)}).", error=True)
            return
        if not text:
            self.set_status("No selected text. Highlight text, then use the selection hotkey.", error=True)
            return
        self.after(0, lambda t=text: self.speak_external_text(t))

    # --------------------------------------------------------------------- tray
    def _start_tray(self) -> None:
        try:
            import pystray
        except ImportError:
            log.info("pystray is not installed; system tray is off")
            self.tray = None
            return
        try:
            image = build_image(64)
        except Exception:
            log.exception("Could not draw the tray icon")
            return
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Speak clipboard", self._tray_clipboard),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        try:
            self.tray = pystray.Icon("sedabot", image, "Sedabot", menu)
            threading.Thread(target=self.tray.run, name="sayit-tray", daemon=True).start()
        except Exception:
            log.exception("System tray failed")
            self.tray = None

    def _tray_show(self, _icon, _item) -> None:
        self.after(0, self.show_window)

    def _tray_clipboard(self, _icon, _item) -> None:
        self.after(0, self.speak_clipboard)

    def _tray_quit(self, _icon, _item) -> None:
        self.after(0, self.quit_app)

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass

    def _on_tray_toggle(self) -> None:
        enabled = bool(self.tray_var.get())
        if enabled and self.tray is None:
            self._start_tray()
            if self.tray is None:
                self.tray_var.set(False)
                self.set_status(
                    "System tray is unavailable. Install pystray and pillow, then restart Sedabot.",
                    error=True,
                )
                return
        self.config["minimize_to_tray"] = enabled
        self.save_config()

    def _on_startup_toggle(self) -> None:
        enabled = bool(self.startup_var.get())
        if not self._write_startup(enabled, quiet=False):
            self.startup_var.set(not enabled)
            return
        self.config["run_at_startup"] = enabled
        self.save_config()
        if enabled:
            self.set_status("Sedabot will start when you sign in to Windows.")
        else:
            self.set_status("Sedabot will not start with Windows.")

    def launch_target(self) -> tuple[str, str | None]:
        if getattr(sys, "frozen", False):
            return sys.executable, None
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = pythonw if pythonw.is_file() else Path(sys.executable)
        return str(executable), str(Path(__file__).resolve())

    def _write_startup(self, enabled: bool, *, quiet: bool) -> bool:
        try:
            executable, script = self.launch_target()
            set_windows_startup(enabled, executable, script)
            return True
        except Exception as exc:
            log.exception("Startup shortcut failed")
            if not quiet:
                self.set_status(f"Error: {short_error(exc)}", error=True)
            return False

    def on_close(self) -> None:
        self._save_geometry()
        self.save_config()
        if self.config.get("minimize_to_tray", True) and self.tray is not None and not self._quitting:
            self.withdraw()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                try:
                    self.tray.notify(
                        "Sedabot is still running. Right-click the tray icon to quit.",
                        "Sedabot",
                    )
                except Exception:
                    log.debug("Tray notify failed", exc_info=True)
            return
        self.quit_app()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._download_cancel.set()
        self.stop_playback()
        self._save_geometry()
        self.save_config()
        try:
            self.hotkeys.stop()
        except Exception:
            log.exception("Hotkey shutdown failed")
        tray = self.tray
        self.tray = None
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                log.exception("Tray shutdown failed")
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
        except Exception:
            pass
        self.destroy()


def _windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sedabot.LocalTTS")
    except Exception:
        pass


def _emit(message: str) -> None:
    """Print when a console is attached. A windowed exe has no stdout."""
    if sys.stdout is not None:
        print(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sedabot for Windows")
    parser.add_argument("--smoke-ui", action="store_true", help="Build the window and exit")
    parser.add_argument("--smoke-load", action="store_true", help="Load the default voice and exit")
    args = parser.parse_args(argv)

    paths = resolve_paths()
    configure_logging(paths.log)
    _windows_app_id()

    smoke = args.smoke_ui or args.smoke_load
    app = SayItApp(autoload=not args.smoke_ui, hotkeys=not smoke, tray=not smoke)
    if args.smoke_ui:
        app.update()
        _emit("UI_OK")
        app._quitting = True
        app.destroy()
        return 0
    if args.smoke_load:
        started = time.time()
        outcome = {"ok": False}

        def check() -> None:
            text = app.status.cget("text")
            failed = text.startswith("Error") or text.startswith("Missing")
            timed_out = (time.time() - started) > 180
            if text.startswith("Ready") or failed or timed_out:
                _emit(f"STATUS={text}")
                outcome["ok"] = text.startswith("Ready")
                app._quitting = True
                app.destroy()
                return
            app.after(300, check)

        app.after(300, check)
        app.mainloop()
        return 0 if outcome["ok"] else 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
