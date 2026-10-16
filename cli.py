#!/usr/bin/env python3
"""Speak from the command line. Audio is synthesized on this machine."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from voices import (
    CATALOG,
    DEFAULT_VOICE,
    configure_logging,
    ensure_voice,
    find_piper_exe,
    installed_voice_names,
    load_piper_voice,
    menu_voice_names,
    model_paths,
    resolve_paths,
    short_error,
    synthesize_best,
    voice_is_installed,
    voice_title,
)


def _print_voices(models: Path) -> None:
    installed = set(installed_voice_names(models))
    names = menu_voice_names(models)
    for name in names:
        spec = CATALOG.get(name)
        title = spec.title if spec else "Custom"
        voice_state = "installed" if name in installed else "not downloaded"
        print(f"{name}\t{voice_state}\t{title}")


def _wav_rate(path: Path) -> int:
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            return int(handle.getframerate() or 22050)
    except Exception:
        return 22050


def _play(path: Path) -> None:
    import pygame

    pygame.mixer.init(frequency=_wav_rate(path), size=-16, channels=1)
    try:
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        pygame.mixer.music.stop()
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
    finally:
        pygame.mixer.quit()


def _read_text(args) -> str:
    if args.text_file:
        return Path(args.text_file).read_text(encoding="utf-8")
    if args.clipboard:
        import pyperclip

        return pyperclip.paste() or ""
    return " ".join(args.text)


def _byte_progress(filename: str, got: int, total: int) -> None:
    if total:
        print(f"\r{filename}: {got / 1048576:.1f} / {total / 1048576:.1f} MB", end="", flush=True)
    else:
        print(f"\r{filename}: {got / 1048576:.1f} MB", end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sedabot command line")
    parser.add_argument("text", nargs="*", help="Text to speak")
    parser.add_argument("--clipboard", action="store_true", help="Speak the clipboard")
    parser.add_argument("--text-file", help="Read a chapter from a UTF-8 text file")
    parser.add_argument("--voice", default=None, help=f"Voice id (default: {DEFAULT_VOICE})")
    parser.add_argument("--speed", type=float, default=1.0, help="1.0 is normal. Lower is slower.")
    parser.add_argument("--list-voices", action="store_true", help="List Persian voices")
    parser.add_argument("--download", metavar="VOICE", help="Download a Piper voice and exit")
    parser.add_argument("--out", help="Write a WAV file instead of only playing it")
    parser.add_argument("--no-play", action="store_true", help="Do not play audio")
    args = parser.parse_args(argv)

    paths = resolve_paths()
    configure_logging(paths.log)

    if args.list_voices:
        _print_voices(paths.models)
        return 0

    voice_name = (args.download or args.voice or DEFAULT_VOICE).strip()
    if voice_name == "pocket-farsi-v2":
        print(
            "Pocket Farsi v2 is not available. It only speaks short phrases, "
            "so Sedabot uses Piper until a long-form version exists.",
            file=sys.stderr,
        )
        return 1

    try:
        if not voice_is_installed(paths.models, voice_name) or args.download:
            print(f"Downloading {voice_title(voice_name)}…")
            ensure_voice(voice_name, paths.models, progress=_byte_progress, force=bool(args.download))
            print()
        if args.download and not args.text and not args.clipboard and not args.text_file:
            print(f"Installed {voice_name}")
            return 0
    except Exception as exc:
        print(f"Error: {short_error(exc)}", file=sys.stderr)
        return 1

    try:
        text = _read_text(args)
    except OSError as exc:
        print(f"Error: could not read the text file ({exc}).", file=sys.stderr)
        return 1
    if not text.strip():
        print("No text provided. Pass words, --text-file, or --clipboard.", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "sayit" / f"cli-{time.time_ns()}.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    voice = None
    try:
        model, config = model_paths(paths.models, voice_name)
        try:
            voice = load_piper_voice(model, config)
        except Exception as exc:
            print(f"piper-tts could not load the voice ({short_error(exc)}). Trying piper.exe if it is installed.")
        engine = synthesize_best(
            text=text,
            wav_path=out_path,
            speed=args.speed,
            voice=voice,
            piper_exe=find_piper_exe(paths),
            model_path=model,
            config_path=config,
        )
    except Exception as exc:
        print(f"Error: {short_error(exc)}", file=sys.stderr)
        return 1

    print(f"Wrote {out_path} ({engine})")
    if not args.no_play:
        try:
            _play(out_path)
        except Exception as exc:
            print(f"Error: could not play audio ({short_error(exc)})", file=sys.stderr)
            return 1
    if not args.out:
        try:
            out_path.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
