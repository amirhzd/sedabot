"""Local Piper voices for Sedabot.

Downloads stay on disk under the models folder. Official Persian voices use the
same URLs as ``python -m piper.download_voices`` in piper-tts 1.8.0. Mana is not
in that catalog, so it is fetched from its own Hugging Face repository.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

log = logging.getLogger("sayit.voices")

DEFAULT_VOICE = "fa_IR-gyro-medium"
MIN_ONNX_BYTES = 100_000

# Matches piper.download_voices in piper-tts 1.8.0. Imported from that module
# when it is available so a future patch release can change the host in one place.
_DEFAULT_URL_FORMAT = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "{lang_family}/{lang_code}/{voice_name}/{voice_quality}/"
    "{lang_code}-{voice_name}-{voice_quality}{extension}?download=true"
)
_DEFAULT_VOICE_PATTERN = re.compile(
    r"^(?P<lang_family>[^-]+)_(?P<lang_region>[^-]+)-"
    r"(?P<voice_name>[^-]+)-(?P<voice_quality>.+)$"
)

PIPER_EXE_URL = (
    "https://github.com/rhasspy/piper/releases/download/"
    "2023.11.14-2/piper_windows_amd64.zip"
)

ProgressCb = Callable[[str, int, int], None]


class DownloadError(Exception):
    """A voice or engine file could not be downloaded."""


class DownloadCancelled(Exception):
    """The user switched voices or closed the app during a download."""


class SynthesisError(Exception):
    """Text could not be turned into a WAV file."""


class EngineMissing(Exception):
    """Neither piper-tts nor piper.exe is usable."""


@dataclass(frozen=True)
class VoiceSpec:
    """A voice Sedabot knows how to fetch."""

    name: str
    title: str
    description: str
    source: str  # "piper" uses the official rhasspy layout; "url" uses files.
    files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AppPaths:
    root: Path
    models: Path
    config: Path
    history: Path
    log: Path
    bin_dir: Path
    icon: Path


CATALOG: dict[str, VoiceSpec] = {
    "fa_IR-gyro-medium": VoiceSpec(
        name="fa_IR-gyro-medium",
        title="Gyro",
        description="Natural male Persian voice. About 63 MB, downloaded once.",
        source="piper",
    ),
    "fa_IR-amir-medium": VoiceSpec(
        name="fa_IR-amir-medium",
        title="Amir",
        description="Clear male Persian voice from rhasspy/piper-voices. About 63 MB.",
        source="piper",
    ),
    "fa_IR-mana-medium": VoiceSpec(
        name="fa_IR-mana-medium",
        title="Mana",
        description="Mana-TTS Persian voice (MahtaFetrat/Mana-Persian-Piper). About 63 MB.",
        source="url",
        files=(
            (
                "fa_IR-mana-medium.onnx",
                "https://huggingface.co/MahtaFetrat/Mana-Persian-Piper/resolve/main/"
                "fa_IR-mana-medium.onnx?download=true",
            ),
            (
                "fa_IR-mana-medium.onnx.json",
                "https://huggingface.co/MahtaFetrat/Mana-Persian-Piper/resolve/main/"
                "fa_IR-mana-medium.onnx.json?download=true",
            ),
        ),
    ),
    "fa_IR-ganji-medium": VoiceSpec(
        name="fa_IR-ganji-medium",
        title="Ganji",
        description="Male Persian voice trained on the Ganji dataset. About 63 MB.",
        source="piper",
    ),
    "fa_IR-ganji_adabi-medium": VoiceSpec(
        name="fa_IR-ganji_adabi-medium",
        title="Ganji Adabi",
        description="Literary male Persian voice, fine-tuned from Ganji. About 63 MB.",
        source="piper",
    ),
    "fa_IR-reza_ibrahim-medium": VoiceSpec(
        name="fa_IR-reza_ibrahim-medium",
        title="Reza / Ibrahim",
        description="Male Persian voice (RezaHedayatfar and IbrahimWalk). About 63 MB.",
        source="piper",
    ),
}


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if log_path.exists() and log_path.stat().st_size > 1_000_000:
            log_path.write_text("", encoding="utf-8")
    except OSError:
        pass
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        handlers.append(console)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def resolve_paths() -> AppPaths:
    """Project folder when running from source, otherwise the folder of the .exe.

    If that folder is not writable (Program Files), use %LOCALAPPDATA%\\Sedabot.
    An older install may still keep its files in %LOCALAPPDATA%\\Begobot,
    or in %LOCALAPPDATA%\\SayIt when Begobot was never created.
    """
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent
    models = root / "models"
    if not _dir_is_writable(models):
        local = os.environ.get("LOCALAPPDATA") or str(Path.home())
        sedabot = Path(local) / "Sedabot"
        begobot = Path(local) / "Begobot"
        sayit = Path(local) / "SayIt"
        if sedabot.is_dir():
            root = sedabot
        elif begobot.is_dir():
            root = begobot
        elif sayit.is_dir():
            root = sayit
        else:
            root = sedabot
        models = root / "models"
        models.mkdir(parents=True, exist_ok=True)
    else:
        models.mkdir(parents=True, exist_ok=True)
    cleanup_partial_downloads(models)
    migrate_nested_models(models)
    return AppPaths(
        root=root,
        models=models,
        config=root / "config.json",
        history=root / "history.json",
        log=root / "sayit.log",
        bin_dir=root / "bin",
        icon=root / "assets" / "sayit.ico",
    )


def _dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def cleanup_partial_downloads(models: Path) -> None:
    for partial in models.glob("*.part"):
        try:
            partial.unlink()
        except OSError:
            log.warning("Could not remove partial download %s", partial)


def migrate_nested_models(models: Path) -> None:
    """Move models saved by an older build under models/fa/fa_IR/... up to models/."""
    for onnx in list(models.rglob("*.onnx")):
        if onnx.parent == models or onnx.name.endswith(".part"):
            continue
        target = models / onnx.name
        config = Path(str(onnx) + ".json")
        try:
            if not target.exists():
                shutil.move(str(onnx), str(target))
            if config.exists():
                dest_config = models / config.name
                if not dest_config.exists():
                    shutil.move(str(config), str(dest_config))
        except OSError:
            log.exception("Could not move %s", onnx)
    for dirpath, _dirnames, _filenames in os.walk(models, topdown=False):
        folder = Path(dirpath)
        if folder == models:
            continue
        try:
            if not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass


def voice_title(name: str) -> str:
    spec = CATALOG.get(name)
    return spec.title if spec else name


def model_paths(models: Path, name: str) -> tuple[Path, Path]:
    """Return ``(model.onnx, model.onnx.json)``.

    Piper's config is ``<file>.onnx.json``, not ``<file>.json``.
    """
    onnx = models / f"{name}.onnx"
    return onnx, Path(str(onnx) + ".json")


def onnx_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < MIN_ONNX_BYTES:
            return False
        with path.open("rb") as handle:
            head = handle.read(128)
    except OSError:
        return False
    return not _bad_payload(head)


def json_config_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < 20:
            return False
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and ("phoneme_id_map" in data or "audio" in data)


def voice_is_installed(models: Path, name: str) -> bool:
    onnx, config = model_paths(models, name)
    return onnx_is_valid(onnx) and json_config_is_valid(config)


def installed_voice_names(models: Path) -> list[str]:
    names: list[str] = []
    if not models.is_dir():
        return names
    for onnx in sorted(models.glob("*.onnx")):
        name = onnx.name[: -len(".onnx")]
        if voice_is_installed(models, name):
            names.append(name)
    return names


def menu_voice_names(models: Path, extra: Iterable[str] = ()) -> list[str]:
    names = list(CATALOG)
    seen = set(names)
    for name in list(extra) + installed_voice_names(models):
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return sorted(names, key=lambda name: voice_title(name).casefold())


def _piper_url_tools() -> tuple[str, re.Pattern[str]]:
    try:
        from piper.download_voices import URL_FORMAT, VOICE_PATTERN

        return URL_FORMAT, VOICE_PATTERN
    except Exception:
        return _DEFAULT_URL_FORMAT, _DEFAULT_VOICE_PATTERN


def is_piper_voice_name(name: str) -> bool:
    _url_format, pattern = _piper_url_tools()
    return pattern.match(name.strip()) is not None


def piper_voice_url(voice: str, extension: str) -> str:
    """Build the Hugging Face URL used by piper-tts 1.8.0."""
    url_format, pattern = _piper_url_tools()
    voice = voice.strip()
    match = pattern.match(voice)
    if not match:
        raise DownloadError(
            f"Voice '{voice}' is not a Piper id. Expected a name like fa_IR-gyro-medium."
        )
    lang_family = match.group("lang_family")
    lang_code = lang_family + "_" + match.group("lang_region")
    return url_format.format(
        lang_family=lang_family,
        lang_code=lang_code,
        voice_name=match.group("voice_name"),
        voice_quality=match.group("voice_quality"),
        extension=extension,
    )


def _bad_payload(data: bytes) -> bool:
    head = data.lstrip().lower()
    if head.startswith(b"<") or head.startswith(b"<!doctype"):
        return True
    if head.startswith(b"version https://git-lfs.github.com"):
        return True
    if head.startswith(b"{") and b'"error"' in head[:200]:
        return True
    return False


def _check_payload(kind: str, data: bytes) -> None:
    if _bad_payload(data):
        raise DownloadError(
            "The download was a web page or a Git LFS pointer, not the voice file. "
            "Nothing was saved. Check the connection and try again."
        )
    if kind == "onnx" and len(data) < MIN_ONNX_BYTES:
        raise DownloadError(
            f"The model file is only {len(data)} bytes, which is too small to be a Piper voice."
        )
    if kind == "json":
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DownloadError(f"The voice config is not valid JSON ({exc}).") from exc
        if not isinstance(parsed, dict) or (
            "phoneme_id_map" not in parsed and "audio" not in parsed
        ):
            raise DownloadError("The voice config is missing Piper fields.")


def _session():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "Sedabot-Windows/1.1 (local text-to-speech)"
    return session


def download_file(
    url: str,
    dest: Path,
    *,
    kind: str,
    progress: Optional[ProgressCb] = None,
    cancel: Optional["object"] = None,
    force: bool = False,
) -> None:
    """Stream ``url`` to ``dest``. A failed download does not replace a good file."""
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not force:
        if kind == "onnx" and onnx_is_valid(dest):
            return
        if kind == "json" and json_config_is_valid(dest):
            return
    partial = dest.with_name(dest.name + ".part")
    delays = (0, 1.5, 4.0)
    last_error: Exception | None = None
    for attempt, delay in enumerate(delays):
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            raise DownloadCancelled()
        if delay:
            _sleep_cancel(delay, cancel)
        try:
            _stream_to_partial(url, partial, kind, progress, cancel)
            os.replace(partial, dest)
            return
        except DownloadCancelled:
            _remove(partial)
            raise
        except DownloadError as exc:
            last_error = exc
            _remove(partial)
            # HTML, LFS pointers, and HTTP 404 will not succeed on retry.
            message = str(exc).lower()
            if "web page" in message or "not found" in message or "json" in message:
                break
            log.warning("Download attempt %s failed: %s", attempt + 1, exc)
        except requests.RequestException as exc:
            last_error = exc
            _remove(partial)
            log.warning("Download attempt %s failed: %s", attempt + 1, exc)
    detail = str(last_error).strip() if last_error else "unknown error"
    host = urlparse(url).netloc or url
    raise DownloadError(f"Could not download {dest.name} from {host}. {detail}")


def _sleep_cancel(seconds: float, cancel: Optional[object]) -> None:
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            raise DownloadCancelled()
        time.sleep(0.1)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _stream_to_partial(url, partial, kind, progress, cancel) -> None:
    import requests

    try:
        response = _session().get(url, stream=True, timeout=(15, 60), allow_redirects=True)
    except requests.RequestException:
        raise
    with response:
        if response.status_code == 404:
            raise DownloadError(f"File was not found ({response.status_code}).")
        if response.status_code in (401, 403):
            raise DownloadError(f"Download was refused ({response.status_code}).")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DownloadError(f"Download failed ({response.status_code}).") from exc
        total = int(response.headers.get("Content-Length") or 0)
        got = 0
        head = b""
        last_report = 0.0
        label = partial.name.removesuffix(".part")
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                    raise DownloadCancelled()
                if not chunk:
                    continue
                if len(head) < 256:
                    head += chunk[: 256 - len(head)]
                    if _bad_payload(head):
                        raise DownloadError(
                            "The download was a web page or a Git LFS pointer, not the voice file."
                        )
                handle.write(chunk)
                got += len(chunk)
                now = time.monotonic()
                if progress is not None and (now - last_report >= 0.25 or (total and got >= total)):
                    last_report = now
                    progress(label, got, total)
        if progress is not None:
            progress(label, got, total)
        if kind == "json":
            _check_payload("json", partial.read_bytes())
        elif got < MIN_ONNX_BYTES:
            raise DownloadError(
                f"The model file is only {got} bytes, which is too small to be a Piper voice."
            )


def voice_download_plan(name: str) -> list[tuple[str, str, str]]:
    """Return ``(filename, url, kind)`` for each file a voice needs."""
    spec = CATALOG.get(name)
    if spec is not None and spec.source == "url":
        plan = []
        for filename, url in spec.files:
            kind = "json" if filename.endswith(".json") else "onnx"
            plan.append((filename, url, kind))
        return plan
    if spec is not None or is_piper_voice_name(name):
        return [
            (f"{name}.onnx.json", piper_voice_url(name, ".onnx.json"), "json"),
            (f"{name}.onnx", piper_voice_url(name, ".onnx"), "onnx"),
        ]
    raise DownloadError(
        f"Voice '{name}' is not installed and is not a known Piper id. "
        "Add the .onnx and .onnx.json files from the Voices tab."
    )


def ensure_voice(
    name: str,
    models: Path,
    *,
    progress: Optional[ProgressCb] = None,
    cancel: Optional[object] = None,
    force: bool = False,
) -> tuple[Path, Path]:
    """Download ``name`` when the local files are missing or invalid."""
    name = name.strip()
    onnx, config = model_paths(models, name)
    if not force and voice_is_installed(models, name):
        return onnx, config
    if voice_is_installed(models, name) and name not in CATALOG and not force:
        return onnx, config
    plan = voice_download_plan(name)
    for filename, url, kind in plan:
        dest = models / filename
        log.info("Fetching %s", filename)
        download_file(url, dest, kind=kind, progress=progress, cancel=cancel, force=force)
    if not voice_is_installed(models, name):
        raise DownloadError(
            f"Downloaded files for {name}, but the model or its config did not validate."
        )
    return onnx, config


def import_voice_file(onnx_source: Path, models: Path) -> str:
    """Copy a user-supplied ``.onnx`` plus the sibling ``.onnx.json`` into models/."""
    onnx_source = Path(onnx_source)
    if onnx_source.suffix.lower() != ".onnx":
        raise DownloadError("Choose a Piper .onnx file.")
    config_source = Path(str(onnx_source) + ".json")
    if not config_source.is_file():
        raise DownloadError(
            f"Missing config next to the model: {config_source.name}. "
            "Piper voices need both the .onnx file and the .onnx.json file."
        )
    name = onnx_source.name[: -len(".onnx")]
    dest_onnx, dest_config = model_paths(models, name)
    shutil.copy2(onnx_source, dest_onnx)
    shutil.copy2(config_source, dest_config)
    if not voice_is_installed(models, name):
        raise DownloadError(
            f"{name} was copied, but the files do not look like a Piper voice. "
            "Check that the JSON config matches the model."
        )
    return name


def import_piper():
    try:
        from piper import PiperVoice
    except Exception as exc:
        raise EngineMissing(
            "piper-tts is not installed or failed to import. "
            "From the project folder run: .venv\\Scripts\\pip install -r requirements.txt. "
            f"Details: {exc}"
        ) from exc
    try:
        from piper import SynthesisConfig
    except Exception:
        SynthesisConfig = None  # type: ignore[assignment]
    return PiperVoice, SynthesisConfig


def espeak_data_dir() -> Optional[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "piper" / "espeak-ng-data")
    try:
        from piper.phonemize_espeak import ESPEAK_DATA_DIR

        candidates.append(Path(ESPEAK_DATA_DIR))
    except Exception:
        pass
    for candidate in candidates:
        if (candidate / "phontab").is_file():
            return candidate
    return None


def load_piper_voice(onnx: Path, config: Path, *, use_cuda: bool = False):
    PiperVoice, _synthesis = import_piper()
    kwargs = {
        "config_path": str(config),
        "use_cuda": use_cuda,
    }
    data_dir = espeak_data_dir()
    if data_dir is not None:
        kwargs["espeak_data_dir"] = str(data_dir)
    try:
        return PiperVoice.load(str(onnx), **kwargs)
    except TypeError:
        return PiperVoice.load(str(onnx), config_path=str(config))


def normalize_speech_text(text: str) -> str:
    """Light Persian cleanup. Keeps ZWNJ. Does not send text anywhere."""
    if not text:
        return ""
    text = text.replace("\u0640", "")  # tatweel
    text = text.replace("\u064a", "\u06cc")  # Arabic yeh -> Persian yeh
    text = text.replace("\u0643", "\u06a9")  # Arabic kaf -> Persian keheh
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    if not lines:
        return ""
    merged = lines[0]
    for line in lines[1:]:
        if merged[-1] in ".!?؟؛;:،,":
            merged = f"{merged} {line}"
        else:
            merged = f"{merged}. {line}"
    return merged.strip()


def length_scale_for_speed(speed: float) -> float:
    """Piper's length_scale is the inverse of the UI speed (1 is normal, 2 is faster)."""
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = 1.0
    if value <= 0:
        value = 1.0
    value = min(2.0, max(0.5, value))
    return 1.0 / value


def synthesize_to_wav(voice, text: str, wav_path: Path, speed: float = 1.0) -> None:
    text = normalize_speech_text(text)
    if not text:
        raise SynthesisError("Nothing to speak.")
    _piper, SynthesisConfig = import_piper()
    scale = length_scale_for_speed(speed)
    syn = None
    if SynthesisConfig is not None:
        try:
            syn = SynthesisConfig(length_scale=scale)
        except Exception:
            log.exception("SynthesisConfig failed; continuing without length_scale")
            syn = None
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    _remove(wav_path)
    with wave.open(str(wav_path), "wb") as wav_file:
        _write_with_python(voice, text, wav_file, syn)
    _assert_wav(wav_path)


def _write_with_python(voice, text: str, wav_file, syn) -> None:
    if hasattr(voice, "synthesize_wav"):
        try:
            voice.synthesize_wav(text, wav_file, syn_config=syn)
            return
        except TypeError:
            # piper builds that predate the syn_config argument
            voice.synthesize_wav(text, wav_file)
            return
    if syn is not None:
        try:
            chunks = voice.synthesize(text, syn_config=syn)
        except TypeError:
            chunks = voice.synthesize(text)
    else:
        chunks = voice.synthesize(text)
    first = True
    wrote = False
    for chunk in chunks:
        if first:
            wav_file.setframerate(chunk.sample_rate)
            wav_file.setsampwidth(chunk.sample_width)
            wav_file.setnchannels(chunk.sample_channels)
            first = False
        wav_file.writeframes(chunk.audio_int16_bytes)
        wrote = True
    if not wrote:
        raise SynthesisError("The voice returned no audio for that text.")


def _assert_wav(wav_path: Path) -> None:
    if not wav_path.is_file() or wav_path.stat().st_size < 44:
        raise SynthesisError("Synthesis produced an empty audio file.")
    with wave.open(str(wav_path), "rb") as wav_file:
        if wav_file.getnframes() <= 0:
            raise SynthesisError("Synthesis produced a WAV file with no audio frames.")


def find_piper_exe(paths: AppPaths) -> Optional[Path]:
    candidates = [
        paths.bin_dir / "piper" / "piper.exe",
        paths.bin_dir / "piper.exe",
        paths.root / "piper" / "piper.exe",
        paths.root / "piper.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("piper")
    if found:
        return Path(found)
    return None


def download_piper_exe(
    paths: AppPaths,
    *,
    progress: Optional[ProgressCb] = None,
    cancel: Optional[object] = None,
) -> Path:
    """Download the official Windows piper.exe build used as a fallback engine."""
    import io
    import zipfile

    paths.bin_dir.mkdir(parents=True, exist_ok=True)
    archive = paths.bin_dir / "piper_windows_amd64.zip"
    try:
        download_file(
            PIPER_EXE_URL,
            archive,
            kind="onnx",  # size floor rejects HTML error pages
            progress=progress,
            cancel=cancel,
            force=True,
        )
    except DownloadError as exc:
        # The zip is smaller than a neural model is not the issue; MIN_ONNX still
        # applies and the official zip is tens of megabytes. Re-raise with context.
        raise DownloadError(f"Could not download piper.exe. {exc}") from exc
    try:
        payload = archive.read_bytes()
        if _bad_payload(payload[:256]):
            raise DownloadError("The piper.exe download was not a zip archive.")
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            bundle.extractall(paths.bin_dir)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DownloadError(f"Could not extract piper.exe ({exc}).") from exc
    finally:
        _remove(archive)
    exe = find_piper_exe(paths)
    if exe is None:
        raise DownloadError(
            "The archive extracted, but piper.exe was not inside it. "
            f"Look in {paths.bin_dir}."
        )
    return exe


def synthesize_with_exe(
    exe: Path,
    model: Path,
    config: Path,
    text: str,
    wav_path: Path,
    speed: float = 1.0,
) -> None:
    text = normalize_speech_text(text)
    if not text:
        raise SynthesisError("Nothing to speak.")
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    _remove(wav_path)
    scale = f"{length_scale_for_speed(speed):.3f}"
    base = [
        str(exe),
        "--model",
        str(model),
        "--config",
        str(config),
        "--output_file",
        str(wav_path),
    ]
    attempts = [base + ["--length-scale", scale], base + ["--length_scale", scale], base]
    last = "piper.exe failed"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    for args in attempts:
        try:
            proc = subprocess.run(
                args,
                input=text.encode("utf-8"),
                cwd=str(exe.parent),
                capture_output=True,
                timeout=180,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SynthesisError(f"piper.exe could not run ({exc}).") from exc
        if proc.returncode == 0 and wav_path.is_file():
            _assert_wav(wav_path)
            return
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        last = (err or out or f"piper.exe exited with code {proc.returncode}")[-500:]
        lowered = last.lower()
        if "length" not in lowered and "unrecognized" not in lowered and "unknown" not in lowered:
            break
    raise SynthesisError(last)


def synthesize_best(
    *,
    text: str,
    wav_path: Path,
    speed: float,
    voice=None,
    piper_exe: Optional[Path] = None,
    model_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> str:
    """Synthesize with piper-tts, then piper.exe. Return which engine was used."""
    errors: list[str] = []
    if voice is not None:
        try:
            synthesize_to_wav(voice, text, wav_path, speed=speed)
            return "piper-tts"
        except Exception as exc:
            log.exception("piper-tts synthesis failed")
            errors.append(f"piper-tts: {exc}")
    if piper_exe is not None and model_path is not None and config_path is not None:
        try:
            synthesize_with_exe(piper_exe, model_path, config_path, text, wav_path, speed=speed)
            return "piper.exe"
        except Exception as exc:
            log.exception("piper.exe synthesis failed")
            errors.append(f"piper.exe: {exc}")
    if not errors:
        raise SynthesisError(
            "No speech engine is ready. Download a Persian voice first. "
            "Sedabot uses piper-tts, and can use piper.exe from Settings if that fails."
        )
    hint = ""
    if voice is not None and piper_exe is None:
        hint = " You can download the piper.exe fallback from Settings and try again."
    raise SynthesisError(" ".join(errors) + hint)


def windows_startup_vbs(executable: str, script: Optional[str] = None) -> str:
    """VBScript that starts the app without a console flash."""
    pieces = [f'""{executable}""']
    if script:
        pieces.append(f'""{script}""')
    command = " ".join(pieces)
    return (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.Run "{command}", 0, False\r\n'
    )


def startup_launcher_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("APPDATA is not set, so Sedabot cannot change Windows startup.")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "Sedabot.vbs"
    )


def set_windows_startup(enabled: bool, executable: str, script: Optional[str] = None) -> None:
    path = startup_launcher_path()
    for old_name in ("SayIt.vbs", "SayIt.cmd", "Begobot.vbs", "Begobot.cmd"):
        old = path.with_name(old_name)
        if old.exists():
            old.unlink()
    if not enabled:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(windows_startup_vbs(executable, script), encoding="utf-8")


def short_error(exc: BaseException, limit: int = 240) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    message = " ".join(message.split())
    if len(message) > limit:
        return message[: limit - 3] + "..."
    return message
