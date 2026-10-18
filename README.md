# Sedabot for Windows

Sedabot reads Persian aloud on your own computer. It is built for Persian speakers, and for anyone learning the language: paste a sentence, hear it spoken, and say it back. Everything runs locally — the voice stays on your PC, and nothing you type is ever uploaded.

## Why this exists

This project is inspired by [callebtc/sayit](https://github.com/callebtc/sayit), Caleb's Mac app for private, local text-to-speech. Sedabot is a separate Windows app with its own code, focused on Persian voices.

It is free to use, free to fork, and open for anyone to build on under the [MIT license](LICENSE).

## What it does

- Speaks Persian text aloud with natural voices, entirely offline after setup
- Global hotkeys to speak your clipboard or any selected text, from any app
- A simple editor with playback controls, speed adjustment, and voice switching
- Command-line tool for scripting and batch conversion to WAV
- Sits in the system tray so it's always one hotkey away

Speech synthesis uses [Piper](https://github.com/OHF-Voice/piper1-gpl). Gyro is the default voice. The network is used only to download a voice, or the optional `piper.exe` fallback.

## Run

```powershell
cd sayit-windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python sayit.py
```

Or double-click `run.bat` after that install. `setup.py` installs the same requirements.

The first launch downloads Gyro into `models\` (about 63 MB). Later launches of an installed voice work offline. Do not install `onnxruntime-gpu` next to `onnxruntime`.

## Voices

| Voice | Id | Source |
| --- | --- | --- |
| Amir | `fa_IR-amir-medium` | rhasspy/piper-voices |
| Ganji | `fa_IR-ganji-medium` | rhasspy/piper-voices |
| Ganji Adabi | `fa_IR-ganji_adabi-medium` | rhasspy/piper-voices |
| Gyro (default) | `fa_IR-gyro-medium` | rhasspy/piper-voices |
| Mana | `fa_IR-mana-medium` | MahtaFetrat/Mana-Persian-Piper |
| Reza / Ibrahim | `fa_IR-reza_ibrahim-medium` | rhasspy/piper-voices |

Pick a voice in the app and it downloads once. Any other official Piper id (for example `en_US-lessac-medium`) can be typed on the Voices tab.

You can also drop a pair of files into `models\`:

- `name.onnx`
- `name.onnx.json` (the config name ends in `.onnx.json`, not `.json`)

## Hotkeys

- `Ctrl+Alt+V` speaks the clipboard
- `Ctrl+Alt+S` speaks the current selection

These use the Windows `RegisterHotKey` API and do not need Administrator. The selection shortcut waits until you release the keys, sends Ctrl+C, then puts your previous clipboard contents back.

`Ctrl+Enter` speaks the text box. `Esc` stops playback.

Closing the window leaves Sedabot in the system tray. Quit from the tray icon. That behavior is a checkbox under Settings.

## Command line

```powershell
python cli.py "سلام، حال شما چطور است؟"
python cli.py --text-file chapter.txt --out chapter.wav
python cli.py --clipboard
python cli.py --voice fa_IR-gyro-medium "یک جمله با پایپر"
python cli.py --download fa_IR-ganji-medium
python cli.py --list-voices
```

The default voice on the command line is Gyro. Playback uses the wav's sample rate, 22.05 kHz for Piper.

## Package a single exe

```powershell
build.bat
```

`dist\Sedabot.exe` is a windowed Piper app. It does not embed voice models. The first run downloads Ganji next to the exe when that folder is writable, otherwise into `%LOCALAPPDATA%\Sedabot`.

`build.bat onedir` builds a folder instead. It starts faster than the single-file exe.

Settings can also download the official Windows `piper.exe` (rhasspy piper 2023.11.14-2). Sedabot uses the `piper-tts` Python package first and that binary only if loading or synthesis fails.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
```

## Privacy

Synthesis, history, and settings stay on this computer. Downloads come from Hugging Face (`rhasspy/piper-voices` and Mana's own repository) and, only if you ask, GitHub for `piper.exe`. Nothing you type is uploaded.

## License

Sedabot is open source under the [MIT license](LICENSE). You can use it, change it, and fork it, including for your own projects. Keep the copyright notice and this permission with the copies you share.
