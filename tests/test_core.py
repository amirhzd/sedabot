"""Offline tests for voice paths, hotkeys, and the piper-tts 1.8 API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import hotkeys
from hotkeys import INPUT, WinHotkeys, hotkey_labels, parse_hotkey, probe_register
from voices import (
    CATALOG,
    DEFAULT_VOICE,
    DownloadError,
    menu_voice_names,
    migrate_nested_models,
    model_paths,
    normalize_speech_text,
    piper_voice_url,
    voice_download_plan,
    voice_title,
    windows_startup_vbs,
)


ROOT = Path(__file__).resolve().parents[1]


class VoiceTests(unittest.TestCase):
    def test_persian_catalog_includes_requested_voices(self):
        for name in (
            "fa_IR-gyro-medium",
            "fa_IR-amir-medium",
            "fa_IR-mana-medium",
            "fa_IR-ganji-medium",
            "fa_IR-ganji_adabi-medium",
            "fa_IR-reza_ibrahim-medium",
        ):
            self.assertIn(name, CATALOG)

    def test_default_voice_is_ganji(self):
        self.assertEqual(DEFAULT_VOICE, "fa_IR-ganji-medium")

    def test_menu_voices_are_alphabetical(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = menu_voice_names(Path(tmp))
        titles = [voice_title(name) for name in names]
        self.assertEqual(titles, sorted(titles, key=str.casefold))
        self.assertEqual(titles[0], "Amir")
        self.assertLess(titles.index("Ganji"), titles.index("Ganji Adabi"))

    def test_gyro_url_matches_piper_1_8_layout(self):
        url = piper_voice_url("fa_IR-gyro-medium", ".onnx")
        self.assertEqual(
            url,
            "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            "fa/fa_IR/gyro/medium/fa_IR-gyro-medium.onnx?download=true",
        )
        config = piper_voice_url("fa_IR-gyro-medium", ".onnx.json")
        self.assertIn("fa_IR-gyro-medium.onnx.json", config)

    def test_ganji_adabi_keeps_the_underscore(self):
        url = piper_voice_url("fa_IR-ganji_adabi-medium", ".onnx")
        self.assertIn("/fa/fa_IR/ganji_adabi/medium/fa_IR-ganji_adabi-medium.onnx", url)

    def test_mana_is_not_fetched_from_rhasspy(self):
        plan = voice_download_plan("fa_IR-mana-medium")
        urls = " ".join(url for _name, url, _kind in plan)
        self.assertIn("MahtaFetrat/Mana-Persian-Piper", urls)
        self.assertNotIn("rhasspy/piper-voices", urls)
        names = [name for name, _url, _kind in plan]
        self.assertEqual(names, ["fa_IR-mana-medium.onnx", "fa_IR-mana-medium.onnx.json"])

    def test_unknown_voice_is_rejected(self):
        with self.assertRaises(DownloadError):
            piper_voice_url("not a voice", ".onnx")

    def test_config_path_is_onnx_json(self):
        onnx, config = model_paths(Path("models"), "fa_IR-gyro-medium")
        self.assertEqual(onnx.name, "fa_IR-gyro-medium.onnx")
        self.assertEqual(config.name, "fa_IR-gyro-medium.onnx.json")

    def test_nested_models_are_flattened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "fa" / "fa_IR" / "gyro" / "medium"
            nested.mkdir(parents=True)
            onnx = nested / "fa_IR-gyro-medium.onnx"
            config = Path(str(onnx) + ".json")
            onnx.write_bytes(b"x" * 32)
            config.write_text('{"audio": {}, "phoneme_id_map": {}}', encoding="utf-8")
            migrate_nested_models(root)
            self.assertTrue((root / "fa_IR-gyro-medium.onnx").is_file())
            self.assertTrue((root / "fa_IR-gyro-medium.onnx.json").is_file())

    def test_normalize_persian_letters_and_lines(self):
        text = normalize_speech_text("سلام\u064a \u0643تاب\nخط دوم")
        self.assertEqual(text, "سلامی کتاب. خط دوم")
        self.assertIn("\u200c", normalize_speech_text("نیم\u200cفاصله"))

    def test_startup_script_quotes_paths(self):
        script = windows_startup_vbs(r"C:\app\SayIt.exe")
        self.assertIn(r'sh.Run """C:\app\SayIt.exe""", 0, False', script)
        both = windows_startup_vbs(r"C:\py\pythonw.exe", r"C:\app\sayit.py")
        self.assertIn(r'""C:\py\pythonw.exe"" ""C:\app\sayit.py""', both)

    def test_app_does_not_import_removed_piper_helpers(self):
        source = (ROOT / "sayit.py").read_text(encoding="utf-8")
        self.assertNotIn("ensure_voice_exists", source)
        self.assertNotIn("from piper.download import", source)
        voices_source = (ROOT / "voices.py").read_text(encoding="utf-8")
        self.assertIn("synthesize_wav", voices_source)
        self.assertIn("SynthesisConfig", voices_source)

    def test_app_does_not_import_pocket(self):
        sayit = (ROOT / "sayit.py").read_text(encoding="utf-8")
        cli = (ROOT / "cli.py").read_text(encoding="utf-8")
        for source in (sayit, cli):
            self.assertNotIn("pocket_engine", source)
            self.assertNotIn("audio_util", source)
            self.assertNotIn("fa_text", source)
        self.assertFalse((ROOT / "pocket_engine.py").exists())
        self.assertFalse((ROOT / "tests" / "test_pocket.py").exists())


class PiperApiTests(unittest.TestCase):
    def test_installed_piper_has_synthesize_wav(self):
        from piper import PiperVoice, SynthesisConfig

        self.assertTrue(hasattr(PiperVoice, "synthesize_wav"))
        self.assertTrue(hasattr(PiperVoice, "load"))
        self.assertEqual(SynthesisConfig(length_scale=1.25).length_scale, 1.25)

    def test_espeak_data_includes_persian(self):
        from piper.phonemize_espeak import ESPEAK_DATA_DIR

        root = Path(ESPEAK_DATA_DIR)
        self.assertTrue((root / "phontab").is_file(), "espeak-ng-data is missing")
        persian = (root / "fa_dict").is_file() or (root / "lang" / "ira" / "fa").is_file()
        self.assertTrue(persian, "Persian espeak data is missing")

    def test_download_voices_module_has_no_legacy_helpers(self):
        import piper.download_voices as module

        self.assertTrue(hasattr(module, "download_voice"))
        self.assertFalse(hasattr(module, "ensure_voice_exists"))


class HotkeyTests(unittest.TestCase):
    def test_labels_for_on_screen_keycaps(self):
        self.assertEqual(hotkey_labels("ctrl+alt+v"), ["Ctrl", "Alt", "V"])
        self.assertEqual(hotkey_labels("ctrl+shift+f9"), ["Ctrl", "Shift", "F9"])

    def test_parse_ctrl_alt_v(self):
        modifiers, vk = parse_hotkey("ctrl+alt+v")
        self.assertEqual(vk, ord("V"))
        self.assertTrue(modifiers & hotkeys.MOD_CONTROL)
        self.assertTrue(modifiers & hotkeys.MOD_ALT)
        self.assertTrue(modifiers & hotkeys.MOD_NOREPEAT)

    def test_reject_modifiers_only(self):
        with self.assertRaises(hotkeys.HotkeyError):
            parse_hotkey("ctrl+alt")

    def test_input_struct_matches_windows_size(self):
        size = __import__("ctypes").sizeof(INPUT)
        self.assertIn(size, (28, 40))

    def test_register_and_release(self):
        error = probe_register("ctrl+alt+shift+f24")
        self.assertIsNone(error, error)

    def test_hotkey_thread_starts_and_stops(self):
        keys = WinHotkeys()
        errors = keys.start({"ctrl+alt+shift+f23": lambda: None})
        keys.stop()
        self.assertEqual(errors, [])


class PayloadTests(unittest.TestCase):
    def test_html_and_lfs_pointer_are_rejected(self):
        from voices import _check_payload

        with self.assertRaises(DownloadError):
            _check_payload("onnx", b"<html>nope</html>")
        pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n"
        with self.assertRaises(DownloadError):
            _check_payload("onnx", pointer)

    def test_json_config_must_parse(self):
        from voices import _check_payload

        good = json.dumps({"audio": {"sample_rate": 22050}, "phoneme_id_map": {"^": [1]}}).encode()
        _check_payload("json", good)
        with self.assertRaises(DownloadError):
            _check_payload("json", b"{}")


if __name__ == "__main__":
    unittest.main()
