Windows setup for ffmpeg + webrtcvad

This project optionally uses `ffmpeg` (system binary) and `webrtcvad` (Python) on the server to decode browser WebM/OPUS audio and perform server-side VAD confirmation. If you want the full hybrid endpointing (client RMS + server VAD), follow these steps.

1) Install ffmpeg (recommended method: Chocolatey)
- Open an elevated PowerShell (Run as Administrator)
- Install Chocolatey (if you don't have it):
  Set-ExecutionPolicy Bypass -Scope Process -Force; `iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))`
- Install ffmpeg:
  `choco install ffmpeg -y`
- Verify:
  `ffmpeg -version`

Alternative: download static build from https://ffmpeg.org/download.html and add the `bin` folder to your PATH.

2) Install `webrtcvad` in your Python environment
- Activate your virtualenv/venv used by the project (Windows PowerShell example):
  `& .\.venv\Scripts\Activate.ps1`
- Install build tools (if pip wheel not available):
  - You may need "Visual C++ Build Tools" (Visual Studio). Install via https://visualstudio.microsoft.com/visual-cpp-build-tools/ if pip build fails.
- Install package:
  `pip install webrtcvad`

3) Verify in Python REPL
```python
import webrtcvad
print(webrtcvad)
```

Notes
- If you cannot install ffmpeg or webrtcvad, the server will gracefully fall back to simpler behavior: it will attempt to process raw audio buffer (if present) or ask client to re-speak. This will work but server-side VAD confirmation won't be available.
- If you plan to run this on a production Linux host, use your system package manager (apt, yum) or static ffmpeg builds.

If you want, I can try to add an automated helper script to attempt ffmpeg install (via choco) but that requires Administrator privileges and may fail in restricted environments.