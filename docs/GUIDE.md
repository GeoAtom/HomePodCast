# HomePod Cast — Guide · 使用须知

[← Back to README](../README.md) · [English](#english) · [中文](#中文) · [Build from source](#build-from-source)

---

## English

![How to use HomePod Cast](usage-en.png)

- **First run** — the exe is not code-signed, so Windows SmartScreen may ask: *More info → Run anyway*.
- **HomePod access** — Home app → Home Settings → Speakers & TV → *Anyone on the Same Network*, no password.
- **Firewall** — allow the app on private networks when Windows asks. A red dot means a HomePod cannot reach your PC.
- **Which speaker is which** — **♪** next to a channel plays a short test sound on that HomePod, before or while streaming.
- **Latency** — AirPlay needs a buffer. *Auto* keeps it as small as your Wi‑Fi allows, about 150 ms in total: fine for music and video, too slow for games.
- **Quad** — separate rear channels need a 5.1 or 7.1 Windows output device. With a stereo source the rear speakers repeat the front ones.
- **PC speakers** keep playing. Turn them down on the device itself: the Windows volume also changes what is streamed.
- **Settings** — source device, buffer (Auto or fixed) and language (中文 / English). The window follows the Windows light or dark mode.
- Tested with HomePod mini on Windows 11.

---

## 中文

![HomePod Cast 使用方法](usage-zh.png)

- **首次运行** — exe 没有代码签名，Windows SmartScreen 可能会拦截：*更多信息 → 仍要运行*。
- **HomePod 权限** — “家庭”App → 家庭设置 → 扬声器与电视 → *同一网络上的任何人*，不设密码。
- **防火墙** — Windows 询问时允许本程序使用专用网络。圆点变红表示 HomePod 连不回电脑。
- **分清每台音箱** — 声道旁的 **♪** 会让那台 HomePod 响一声测试音，串流前、串流中都能用。
- **延迟** — AirPlay 必须有缓冲。*自动*档会按 Wi‑Fi 状况把它压到最小，总延迟约 150 ms：听音乐、看视频没问题，不适合游戏。
- **四声道** — 独立的后置声道需要把 Windows 输出设备配置成 5.1 或 7.1。音源是立体声时，后置会重复前置。
- **电脑音箱**会同时出声。请在设备本身上调小：Windows 音量也会改变串流的音量。
- **设置** — 音源设备、缓冲（自动或固定）和语言（中文 / English）。窗口跟随 Windows 的浅色或深色模式。
- 已在 Windows 11 上用 HomePod mini 测试。

---

## Build from source

Windows 10/11, Python 3.12.

```powershell
pip install -r requirements.txt
python app.py     # run
.\build.ps1       # build dist\HomePodCast.exe
```

`engine.py` captures, routes and streams; `app.py` is the window. `--autostart` starts streaming with the last setup.

## Credits

Built on [pyatv](https://github.com/postlund/pyatv), [SoundCard](https://github.com/bastibe/SoundCard) and [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter). Not affiliated with Apple; HomePod and AirPlay are trademarks of Apple Inc.
