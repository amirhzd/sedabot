Local Persian text-to-speech for Windows. Everything runs on your own PC — the voice stays local and nothing you type is ever uploaded.

### Download

Grab **Sedabot.exe** from the assets below and run it — no installer needed. On first launch it downloads the default Gyro voice (about 63 MB) into a `models` folder next to the exe; after that it works fully offline.

### What it does

- Speaks Persian text aloud with natural voices, entirely offline after setup
- Global hotkeys: `Ctrl+Alt+V` speaks your clipboard, `Ctrl+Alt+S` speaks any selected text, from any app — no Administrator needed
- Simple editor with playback controls, speed adjustment, and voice switching (`Ctrl+Enter` speaks, `Esc` stops)
- Command-line tool for scripting and batch conversion to WAV
- Sits in the system tray, always one hotkey away

### Voices

Gyro is the default. Amir, Ganji, Ganji Adabi, Mana, and Reza/Ibrahim are built in and download once per voice. Any other official [Piper](https://github.com/OHF-Voice/piper1-gpl) voice id can be typed on the Voices tab, or you can drop your own `.onnx` + `.onnx.json` pair into the `models` folder.

### Notes

- Windows 10/11, 64-bit
- Speech synthesis runs on [Piper](https://github.com/OHF-Voice/piper1-gpl) with onnxruntime
- Windows SmartScreen may warn on first run since the exe is not code-signed — click "More info" → "Run anyway"
- Built by GitHub Actions from this tag; the build log is public under the Actions tab
