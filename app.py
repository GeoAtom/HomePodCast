"""HomePod Cast: stream Windows system audio to 1, 2 or 4 HomePods."""

from concurrent.futures import Future
import ctypes
import json
import math
import os
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
import winreg

import customtkinter as ctk

import engine as eng

APP_NAME = "HomePod Cast"
VERSION = "1.1.0"
CONFIG_PATH = os.path.join(os.environ.get("APPDATA", "."), "HomePodCast", "config.json")
RELEASES = "https://github.com/GeoAtom/HomePodCast/releases/latest"
LATEST_API = "https://api.github.com/repos/GeoAtom/HomePodCast/releases/latest"

# Where Windows keeps the programs it starts at sign-in
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HomePodCast"
# At sign-in the network may not be up yet: scan this often before giving up
AUTOSTART_SCANS = 20
AUTOSTART_RETRY_MS = 5000

# Network buffer choices in ms; None is automatic
BUFFERS = [None, 20, 40, 80, 150, 300]
LANGUAGES = {"zh": "中文", "en": "English"}
FONTS = {"zh": "Microsoft YaHei UI", "en": "Segoe UI"}

# Every widget has a fixed size, so the window is the same in both languages
WIDTH = 340
PAD = 20
LABEL = 56

# (light, dark)
SURFACE = ("#f5f5f7", "#1c1c1e")
FIELD = ("#e8e8ed", "#2c2c2e")
FIELD_HOVER = ("#dcdce1", "#3a3a3c")
SELECTED = ("#ffffff", "#636366")
TEXT = ("#1d1d1f", "#f5f5f7")
MUTED = ("#6e6e73", "#98989d")
FAINT = ("#c7c7cc", "#48484a")
ACCENT, ACCENT_HOVER, INK = "#ff9f0a", "#f59000", "#1d1d1f"
LINK = ("#b25000", "#ff9f0a")
KNOB = "#ffffff"
AMBER, GREEN, RED = "#ff9f0a", "#34c759", "#ff453a"

STRINGS = {
    "zh": {
        "mode_mono": "单声道",
        "mode_stereo": "立体声",
        "mode_quad": "四声道",
        "role_mono": "扬声器",
        "role_L": "左",
        "role_R": "右",
        "role_FL": "左前",
        "role_FR": "右前",
        "role_RL": "左后",
        "role_RR": "右后",
        "choose": "选择 HomePod",
        "volume": "音量",
        "start": "开始串流",
        "stop": "停止",
        "settings": "设置",
        "source": "音源",
        "source_item": "{name}（{channels} 声道）",
        "buffer": "缓冲",
        "auto": "自动",
        "language": "语言",
        "startup": "自启",
        "startup_on": "登录 Windows 后自动串流",
        "version": "版本",
        "update": "{current}  →  下载 {latest}",
        "update_hint": "有新版本 {latest}",
        "scanning": "正在扫描局域网…",
        "found": "找到 {count} 台 AirPlay 音箱",
        "none_found": "没有找到 AirPlay 音箱",
        "scan_failed": "扫描失败：{error}",
        "pick_all": "请为每个声道选择一台音箱",
        "no_duplicates": "同一台音箱不能分配给两个声道",
        "connecting": "正在连接…",
        "connect_failed": "连接失败：{error}",
        "stopped": "已停止",
        "interrupted": "已中断：{error}",
        "streaming": "串流中 · 延迟约 {latency} ms · 缓冲 {buffer} ms",
        "streaming_auto": "串流中 · 延迟约 {latency} ms · 自动缓冲 {buffer} ms",
        "testing": "正在 {name} 上播放测试音…",
        "test_failed": "测试音播放失败：{error}",
        "reconnecting": "{names} 断开了连接，正在重连…",
        "waiting": "{names} 正被其他设备使用，空闲后会自动重连。",
        "err_capture": "音频采集失败：{detail}",
        "err_error": "{detail}",
        "access_hint": "请在“家庭”App → 家庭设置 → 扬声器与电视中允许“同一网络上的任何人”。",
        "firewall_hint": "{names} 连不回本机，不会出声。请在 Windows 防火墙中允许本程序使用专用网络。",
        "quad_hint": "音源只有 {channels} 声道，后置会重复前置。",
    },
    "en": {
        "mode_mono": "Mono",
        "mode_stereo": "Stereo",
        "mode_quad": "Quad",
        "role_mono": "Speaker",
        "role_L": "Left",
        "role_R": "Right",
        "role_FL": "Front L",
        "role_FR": "Front R",
        "role_RL": "Rear L",
        "role_RR": "Rear R",
        "choose": "Choose a HomePod",
        "volume": "Volume",
        "start": "Start streaming",
        "stop": "Stop",
        "settings": "Settings",
        "source": "Source",
        "source_item": "{name} ({channels} ch)",
        "buffer": "Buffer",
        "auto": "Auto",
        "language": "Language",
        "startup": "Startup",
        "startup_on": "Stream when I sign in to Windows",
        "version": "Version",
        "update": "{current}  →  get {latest}",
        "update_hint": "Version {latest} is out",
        "scanning": "Scanning the network…",
        "found": "Found {count} AirPlay speaker(s)",
        "none_found": "No AirPlay speakers found",
        "scan_failed": "Scan failed: {error}",
        "pick_all": "Pick a speaker for every channel",
        "no_duplicates": "A speaker cannot serve two channels",
        "connecting": "Connecting…",
        "connect_failed": "Could not connect: {error}",
        "stopped": "Stopped",
        "interrupted": "Interrupted: {error}",
        "streaming": "Streaming · about {latency} ms latency · buffer {buffer} ms",
        "streaming_auto": "Streaming · about {latency} ms latency · auto buffer {buffer} ms",
        "testing": "Playing a test sound on {name}…",
        "test_failed": "Test sound failed: {error}",
        "reconnecting": "{names} disconnected. Reconnecting…",
        "waiting": "{names} is in use by another device. It will reconnect once free.",
        "err_capture": "Audio capture failed: {detail}",
        "err_error": "{detail}",
        "access_hint": "In the Home app → Home Settings → Speakers & TV, allow "
        "“Anyone on the Same Network”.",
        "firewall_hint": "{names} cannot reach this PC, so it stays silent. Allow this "
        "app on private networks in Windows Firewall.",
        "quad_hint": "The source has only {channels} channels: rear repeats front.",
    },
}


def system_language() -> str:
    try:
        chinese = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04
    except (AttributeError, OSError):
        chinese = False
    return "zh" if chinese else "en"


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def save_config(config: dict):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(config, file, ensure_ascii=False, indent=1)
    except OSError:
        pass


def launch_command() -> str:
    """Command line that starts this program streaming, out of the way."""
    if getattr(sys, "frozen", False):
        program = f'"{sys.executable}"'
    else:
        windowed = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        python = windowed if os.path.exists(windowed) else sys.executable
        program = f'"{python}" "{os.path.abspath(__file__)}"'
    return program + " --autostart --minimized"


def startup_command():
    """What Windows runs for us at sign-in, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except OSError:
        return None


def set_startup(enabled: bool):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                winreg.DeleteValue(key, RUN_NAME)
    except OSError:
        pass


def newer_version():
    """Version of the latest release on GitHub if it is newer than this one."""

    def numbers(version):
        return tuple(int(part) for part in version.split("."))

    request = urllib.request.Request(
        LATEST_API, headers={"User-Agent": f"HomePodCast/{VERSION}"}
    )
    with urllib.request.urlopen(request, timeout=5) as reply:
        latest = json.load(reply)["tag_name"].lstrip("v")
    return latest if numbers(latest) > numbers(VERSION) else None


def in_background(function) -> Future:
    """Run a blocking function in its own thread."""
    future = Future()

    def work():
        try:
            future.set_result(function())
        except Exception as ex:  # pylint: disable=broad-except
            future.set_exception(ex)

    threading.Thread(target=work, daemon=True).start()
    return future


class App:
    def __init__(self):
        self.config = load_config()
        self.lang = self.config.get("language")
        if self.lang not in STRINGS:
            self.lang = system_language()
        self.engine = eng.Engine()
        self.sources = eng.list_sources()
        self.confs = {}  # menu label -> device configuration
        self.pending = []  # (future, callback) resolved on the GUI thread
        self.running = False  # start requested
        self.streaming = False  # start succeeded
        self.testing = 0  # test sounds being played while not streaming
        self.volume_synced = False
        self.show_settings = False
        self.update = None  # version of a newer release
        # --autostart: begin streaming with the saved setup once devices are found.
        # Counts the scans that may still be spent on finding them.
        self.autostart = AUTOSTART_SCANS if "--autostart" in sys.argv else 0
        if getattr(sys, "frozen", False) and startup_command() not in (None, launch_command()):
            set_startup(True)  # the exe has been moved since

        ctk.set_appearance_mode("system")
        self.root = root = ctk.CTk(fg_color=SURFACE)
        root.title(APP_NAME)
        try:
            # The built exe carries the icon itself; from source it sits next to us
            frozen = getattr(sys, "frozen", False)
            root.iconbitmap(
                sys.executable if frozen
                else os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
            )
        except tk.TclError:
            pass
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self.mode = self.config.get("mode", "mono")
        if self.mode not in eng.MODES:
            self.mode = "mono"
        self.source_index = next(
            (i for i, s in enumerate(self.sources) if s[0] == self.config.get("source")), 0
        )
        saved_buffer = self.config.get("buffer")
        self.buffer = saved_buffer if saved_buffer in BUFFERS else None
        self.volume = tk.DoubleVar(value=30)
        self.status_text = ("scanning", {}, "")
        self.body = None
        self.build()
        if "--minimized" in sys.argv:
            # Once the main loop runs: customtkinter leaves a window hidden for good
            # that is minimised before it has been shown
            root.after(0, root.iconify)
        self.scan()
        self.pending.append((in_background(newer_version), self.checked))
        self.tick()

    def tr(self, key: str, **values) -> str:
        return STRINGS[self.lang][key].format(**values)

    # --- layout -----------------------------------------------------------------

    def build(self):
        """Create all widgets in the current language."""
        if self.body:
            self.body.destroy()
        family = FONTS[self.lang]
        self.font = ctk.CTkFont(family, 13)
        self.small = ctk.CTkFont(family, 12)
        self.bold = ctk.CTkFont(family, 14, "bold")
        self.symbol = ctk.CTkFont("Segoe UI Symbol", 15)

        self.body = body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(padx=PAD, pady=(PAD, 12))

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack()
        self.mode_labels = {self.tr(f"mode_{key}"): key for key in eng.MODES}
        self.mode_box = ctk.CTkSegmentedButton(
            top, values=list(self.mode_labels), command=self.set_mode, font=self.font,
            width=WIDTH - 40, height=34, dynamic_resizing=False, corner_radius=9,
            fg_color=FIELD, unselected_color=FIELD, unselected_hover_color=FIELD_HOVER,
            selected_color=SELECTED, selected_hover_color=SELECTED, text_color=TEXT,
        )
        self.mode_box.set(self.tr(f"mode_{self.mode}"))
        self.mode_box.pack(side="left")
        self.scan_button = self.icon_button(top, "↻", self.scan)
        self.scan_button.pack(side="left", padx=(6, 0))

        self.roles_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.roles_frame.pack(pady=(12, 0))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(pady=(8, 0))
        self.label(row, self.tr("volume")).pack(side="left")
        self.volume_scale = ctk.CTkSlider(
            row, from_=0, to=100, variable=self.volume, width=WIDTH - LABEL - 40, height=16,
            fg_color=FIELD, progress_color=MUTED, button_color=TEXT, button_hover_color=TEXT,
            command=lambda _: self.show_volume(), state="disabled",
        )
        self.volume_scale.pack(side="left")
        self.volume_scale.bind("<ButtonRelease-1>", lambda _: self.apply_volume())
        self.volume_text = ctk.CTkLabel(
            row, width=40, anchor="e", font=self.font, text_color=MUTED
        )
        self.volume_text.pack(side="left")
        self.show_volume()

        self.start_button = ctk.CTkButton(
            body, text=self.tr("start"), command=self.toggle, width=WIDTH, height=44,
            corner_radius=11, font=self.bold, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=INK, text_color_disabled=MUTED,
        )
        self.start_button.pack(pady=(14, 8))

        self.level = ctk.CTkProgressBar(
            body, width=WIDTH, height=4, fg_color=FIELD, progress_color=FIELD, corner_radius=2
        )
        self.level.pack()
        self.show_level(0.0)

        # Three lines, whatever the text: the window never changes height
        self.status = ctk.CTkLabel(
            body, width=WIDTH, height=56, anchor="nw", justify="left", wraplength=WIDTH,
            font=self.small, text_color=MUTED,
        )
        self.status.pack(pady=(10, 0))

        self.settings_button = ctk.CTkButton(
            body, command=self.toggle_settings, width=WIDTH, height=26, anchor="w",
            font=self.small, fg_color="transparent", hover_color=FIELD, text_color=MUTED,
        )
        self.settings_button.pack()
        self.settings = ctk.CTkFrame(body, fg_color="transparent")

        row = self.setting_row(self.tr("source"))
        self.source_labels = [
            self.tr("source_item", name=name, channels=channels)
            for _, name, channels in self.sources
        ]
        self.source_box = self.menu(row, self.source_labels, self.set_source, WIDTH - LABEL)
        self.source_box.set(self.source_labels[self.source_index])
        self.source_box.pack(side="left")

        row = self.setting_row(self.tr("buffer"))
        self.buffer_labels = [self.tr("auto") if ms is None else f"{ms} ms" for ms in BUFFERS]
        self.buffer_box = self.menu(row, self.buffer_labels, self.set_buffer, WIDTH - LABEL)
        self.buffer_box.set(self.buffer_labels[BUFFERS.index(self.buffer)])
        self.buffer_box.pack(side="left")

        row = self.setting_row(self.tr("language"))
        self.language_box = ctk.CTkSegmentedButton(
            row, values=list(LANGUAGES.values()), command=self.set_language, font=self.font,
            width=WIDTH - LABEL, height=30, dynamic_resizing=False, corner_radius=8,
            fg_color=FIELD, unselected_color=FIELD, unselected_hover_color=FIELD_HOVER,
            selected_color=SELECTED, selected_hover_color=SELECTED, text_color=TEXT,
        )
        self.language_box.set(LANGUAGES[self.lang])
        self.language_box.pack(side="left")

        row = self.setting_row(self.tr("startup"))
        self.startup_var = tk.BooleanVar(value=startup_command() is not None)
        ctk.CTkSwitch(
            row, text=self.tr("startup_on"), variable=self.startup_var, font=self.font,
            command=lambda: set_startup(self.startup_var.get()), width=WIDTH - LABEL,
            height=30, text_color=TEXT, fg_color=FAINT, progress_color=GREEN,
            button_color=KNOB, button_hover_color=KNOB,
        ).pack(side="left")

        row = self.setting_row(self.tr("version"))
        self.version_button = ctk.CTkButton(
            row, command=self.open_release, width=WIDTH - LABEL, height=26, anchor="w",
            font=self.font, fg_color="transparent",
        )
        self.version_button.pack(side="left")
        self.show_version()

        self.build_roles()
        self.place_settings()
        self.set_status(*self.status_text)
        self.set_controls(not self.running)

    def label(self, parent, text):
        return ctk.CTkLabel(
            parent, text=text, width=LABEL, anchor="w", font=self.font, text_color=TEXT
        )

    def icon_button(self, parent, glyph, command):
        return ctk.CTkButton(
            parent, text=glyph, command=command, width=34, height=34, corner_radius=9,
            font=self.symbol, fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT,
            text_color_disabled=FAINT,
        )

    def menu(self, parent, values, command, width):
        return ctk.CTkOptionMenu(
            parent, values=values, command=command, width=width, height=34,
            dynamic_resizing=False, corner_radius=9, anchor="w", font=self.font,
            dropdown_font=self.font, fg_color=FIELD, button_color=FIELD,
            button_hover_color=FIELD_HOVER, text_color=TEXT, text_color_disabled=MUTED,
            dropdown_fg_color=SELECTED, dropdown_hover_color=FIELD, dropdown_text_color=TEXT,
        )

    def setting_row(self, text):
        row = ctk.CTkFrame(self.settings, fg_color="transparent")
        row.pack(pady=3)
        self.label(row, text).pack(side="left")
        return row

    def build_roles(self):
        """Rebuild the speaker pickers for the selected layout."""
        for child in self.roles_frame.winfo_children():
            child.destroy()
        self.role_vars, self.role_boxes, self.role_dots, self.test_buttons = {}, {}, {}, {}
        for role in eng.MODES[self.mode]:
            row = ctk.CTkFrame(self.roles_frame, fg_color="transparent")
            row.pack(pady=3)
            self.label(row, self.tr(f"role_{role}")).pack(side="left")
            var = tk.StringVar(value=self.tr("choose"))
            box = self.menu(row, [], None, WIDTH - LABEL - 42 - 18)
            box.configure(variable=var)
            box.pack(side="left")
            test = self.icon_button(row, "♪", lambda role=role: self.test(role))
            test.pack(side="left", padx=(8, 0))
            dot = ctk.CTkLabel(row, text="●", width=18, anchor="e", text_color=FAINT,
                               font=ctk.CTkFont("Segoe UI", 11))
            dot.pack(side="left")
            self.role_vars[role], self.role_boxes[role] = var, box
            self.role_dots[role], self.test_buttons[role] = dot, test
            if self.testing:
                test.configure(state="disabled")
        self.fill_roles()

    def fill_roles(self):
        """Offer the scanned devices and restore the saved assignment."""
        labels = list(self.confs)
        saved = self.config.get("roles", {})
        for role, box in self.role_boxes.items():
            box.configure(values=labels)
            var = self.role_vars[role]
            if var.get() not in self.confs:
                match = [l for l, c in self.confs.items() if c.identifier == saved.get(role)]
                var.set(match[0] if match else self.tr("choose"))
        if len(labels) == 1 and len(self.role_vars) == 1:
            self.role_vars["mono"].set(labels[0])

    def show_version(self):
        if self.update:
            self.version_button.configure(
                text=self.tr("update", current=VERSION, latest=self.update),
                text_color=LINK, hover_color=FIELD, cursor="hand2",
            )
        else:
            self.version_button.configure(
                text=VERSION, text_color=MUTED, hover_color=SURFACE, cursor="",
            )

    def place_settings(self):
        arrow = "▴" if self.show_settings else "▾"
        hint = f"    {self.tr('update_hint', latest=self.update)}" if self.update else ""
        self.settings_button.configure(text=f"{self.tr('settings')}  {arrow}{hint}")
        if self.show_settings:
            self.settings.pack(pady=(2, 0))
        else:
            self.settings.pack_forget()

    def show_level(self, value: float):
        # An empty bar still draws a sliver, so it takes the track colour at zero
        self.level.configure(progress_color=GREEN if value > 0 else FIELD)
        self.level.set(value)

    def show_volume(self):
        self.volume_text.configure(text=f"{self.volume.get():.0f}")

    def set_status(self, key: str, values: dict = None, extra: str = ""):
        """Show a translatable status; remembered so a language switch can redo it."""
        self.status_text = (key, values or {}, extra)
        text = self.tr(key, **(values or {})) + extra
        channels = self.sources[self.source_index][2]
        if self.mode == "quad" and channels < 4 and not extra:
            text += "\n" + self.tr("quad_hint", channels=channels)
        self.status.configure(text=text)

    def set_controls(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for widget in [*self.role_boxes.values(), self.source_box, self.buffer_box,
                       self.mode_box, self.language_box, self.scan_button]:
            widget.configure(state=state)
        # The volume belongs to the stream: live while streaming, dimmed otherwise
        self.volume_scale.configure(
            state="disabled" if enabled else "normal",
            button_color=FAINT if enabled else TEXT,
            button_hover_color=FAINT if enabled else TEXT,
            progress_color=FAINT if enabled else MUTED,
        )

    def remember(self):
        """Store the current choices in self.config."""
        roles = dict(self.config.get("roles", {}))
        for role, var in self.role_vars.items():
            if var.get() in self.confs:
                roles[role] = self.confs[var.get()].identifier
        self.config = {
            "language": self.lang,
            "mode": self.mode,
            "source": self.sources[self.source_index][0],
            "buffer": self.buffer,
            "roles": roles,
        }

    # --- choices ----------------------------------------------------------------

    def set_mode(self, label):
        self.remember()
        self.mode = self.mode_labels[label]
        self.build_roles()
        self.set_status(*self.status_text)

    def set_source(self, label):
        self.source_index = self.source_labels.index(label)
        self.set_status(*self.status_text)

    def set_buffer(self, label):
        self.buffer = BUFFERS[self.buffer_labels.index(label)]

    def set_language(self, label):
        self.remember()
        self.lang = next(key for key, name in LANGUAGES.items() if name == label)
        self.config["language"] = self.lang
        save_config(self.config)
        self.build()

    def toggle_settings(self):
        self.show_settings = not self.show_settings
        self.place_settings()

    # --- actions ----------------------------------------------------------------

    def scan(self):
        self.scan_button.configure(state="disabled")
        self.set_status("scanning")
        self.pending.append((self.engine.scan(), self.scanned))

    def scanned(self, confs, error):
        if not self.running:
            self.scan_button.configure(state="normal")
        if error:
            self.set_status("scan_failed", {"error": error})
            if self.autostart and not self.running:
                self.retry_autostart()
            return
        names = [c.name for c in confs]
        self.confs = {
            # Two speakers may share a name; the address tells them apart
            (c.name if names.count(c.name) == 1 else f"{c.name} ({c.address})"): c
            for c in confs
        }
        self.fill_roles()
        if confs:
            self.set_status("found", {"count": len(confs)})
        else:
            self.set_status("none_found")
        if self.autostart and not self.running:
            chosen = [self.role_vars[role].get() for role in eng.MODES[self.mode]]
            if all(label in self.confs for label in chosen):
                self.toggle()
            else:
                self.retry_autostart()

    def retry_autostart(self):
        """Scan again in a while: at sign-in the speakers may not be reachable yet."""
        self.autostart -= 1
        if self.autostart > 0:
            self.root.after(AUTOSTART_RETRY_MS, self.rescan)

    def rescan(self):
        if self.autostart and not self.running and not self.testing:
            self.scan()

    def checked(self, version, error):
        self.update = None if error else version
        self.show_version()
        self.place_settings()

    def open_release(self):
        if self.update:
            webbrowser.open(RELEASES)

    def test(self, role):
        """Play a test sound on the speaker picked for this channel."""
        label = self.role_vars[role].get()
        if label not in self.confs or (self.running and not self.streaming):
            return
        conf = self.confs[label]
        if not self.streaming:
            # A short stream of its own: nothing else can start meanwhile
            self.testing += 1
            self.start_button.configure(state="disabled")
            for button in self.test_buttons.values():
                button.configure(state="disabled")
            self.role_dots[role].configure(text_color=AMBER)
            self.set_status("testing", {"name": conf.name})
        self.pending.append((self.engine.identify(conf), self.tested))

    def tested(self, _, error):
        if self.streaming or not self.testing:
            return
        self.testing -= 1
        self.start_button.configure(state="normal")
        for button in self.test_buttons.values():
            button.configure(state="normal")
        for dot in self.role_dots.values():
            dot.configure(text_color=FAINT)
        if error:
            self.set_status("test_failed", {"error": str(error) or type(error).__name__})
        elif self.confs:
            self.set_status("found", {"count": len(self.confs)})

    def toggle(self):
        self.autostart = 0
        if self.running:
            self.start_button.configure(state="disabled")
            self.pending.append((self.engine.stop(), lambda *_: self.stopped("stopped")))
            return

        chosen = [self.role_vars[role].get() for role in eng.MODES[self.mode]]
        if not all(label in self.confs for label in chosen):
            self.set_status("pick_all")
            return
        if len(set(chosen)) != len(chosen):
            self.set_status("no_duplicates")
            return

        self.remember()
        save_config(self.config)
        source_id, _, channels = self.sources[self.source_index]
        confs = [self.confs[label] for label in chosen]

        self.running = True
        self.volume_synced = False
        self.set_controls(False)
        self.start_button.configure(state="disabled")
        self.set_status("connecting")
        for dot in self.role_dots.values():
            dot.configure(text_color=AMBER)
        future = self.engine.start(confs, self.mode, source_id, channels, self.buffer)
        self.pending.append((future, self.started))

    def started(self, _, error):
        self.start_button.configure(state="normal")
        if error:
            reason = f"{type(error).__name__} {error}".lower()
            hint = "\n" + self.tr("access_hint") if "auth" in reason or "pair" in reason else ""
            self.stopped(
                "connect_failed", {"error": str(error) or type(error).__name__}, hint
            )
            return
        self.streaming = True
        self.start_button.configure(
            text=self.tr("stop"), fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT
        )

    def stopped(self, key: str, values: dict = None, extra: str = ""):
        self.running = self.streaming = False
        self.set_controls(True)
        self.start_button.configure(
            state="normal", text=self.tr("start"), fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=INK,
        )
        self.set_status(key, values, extra)
        self.show_level(0.0)
        for dot in self.role_dots.values():
            dot.configure(text_color=FAINT)

    def apply_volume(self):
        if self.streaming:
            self.engine.set_volume(self.volume.get())

    def sync_volume(self, outputs):
        """Show the receivers' volume; level several receivers to the quietest one."""
        self.volume_synced = True
        volumes = [o["volume"] for o in outputs if o["volume"] is not None]
        if not volumes:
            return
        self.volume.set(min(volumes))
        self.show_volume()
        if len(outputs) > 1:
            self.apply_volume()

    def quit(self):
        try:
            self.engine.stop().result(3)
        except Exception:  # pylint: disable=broad-except
            pass
        self.root.destroy()

    # --- periodic update --------------------------------------------------------

    def tick(self):
        for item in [p for p in self.pending if p[0].done()]:
            self.pending.remove(item)
            future, callback = item
            error = future.exception()
            callback(None if error else future.result(), error)

        st = self.engine.status()
        if self.streaming and st["state"] == "streaming":
            if not self.volume_synced:
                self.sync_volume(st["outputs"])
            decibel = 20 * math.log10(max(st["level"], 1e-6))
            self.show_level(min(max(0.0, 1 + decibel / 60), 1.0))

            silent, lost, busy = [], [], []
            for role, out in zip(eng.MODES[self.mode], st["outputs"]):
                synced = out["online"] and out["timing"] > 0
                late = out["online"] and not synced and out["age"] > 5
                self.role_dots[role].configure(
                    text_color=GREEN if synced else RED if late else AMBER
                )
                if late:
                    silent.append(out["name"])
                elif not out["online"]:
                    (busy if out["busy"] else lost).append(out["name"])
            if lost:
                self.set_status("reconnecting", {"names": ", ".join(lost)})
            elif busy:
                self.set_status("waiting", {"names": ", ".join(busy)})
            elif silent:
                self.set_status("firewall_hint", {"names": ", ".join(silent)})
            else:
                self.set_status(
                    "streaming_auto" if st["auto"] else "streaming",
                    {"latency": round(st["latency"]), "buffer": round(st["buffer"])},
                )
        elif self.streaming and st["state"] == "idle" and st["error"]:
            code, detail = st["error"]
            self.stopped("interrupted", {"error": self.tr(f"err_{code}", detail=detail)})

        self.root.after(100, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
