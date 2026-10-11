"""Capture Windows system audio and stream it to several HomePods in sync.

pyatv does the AirPlay handshake and encryption. The send loop is our own: every
receiver gets the same timeline (start_ts) and the same clock, so frame N is played
at the same instant on all of them.

Latency model (measured on a HomePod mini by withholding packets and timing its
retransmit requests): a frame with nominal time T is played at
T + declared latency + 250 ms, and must arrive `arrivalToRenderLatencyMs` before
that. The declared latency stays fixed; we instead send every frame late, just
`buffer` milliseconds ahead of its arrival deadline. That buffer is the only part
of the latency we can choose, and what the automatic mode tunes.
"""

import asyncio
from collections import deque
import ctypes
from random import randrange
import socket
import struct
import threading
import time
import warnings

import numpy as np
import pyatv
from pyatv.const import Protocol
from pyatv.protocols.airplay.auth import extract_credentials
from pyatv.protocols.airplay.utils import (
    AirPlayMajorVersion,
    dbfs_to_pct,
    get_protocol_version,
    pct_to_dbfs,
)
from pyatv.protocols.raop.fifo import PacketFifo
from pyatv.protocols.raop.packets import AudioPacketHeader, SyncPacket, TimingPacket
from pyatv.protocols.raop.protocols import StreamContext, airplayv1, airplayv2
from pyatv.protocols.raop.stream_client import AudioProtocol, ControlClient
from pyatv.settings import AirPlayVersion
from pyatv.support.http import decode_bplist_from_body, http_connect
from pyatv.support.rtsp import FRAMES_PER_PACKET, RtspSession

RATE = 44100
FPP = FRAMES_PER_PACKET
MASK32 = 0xFFFFFFFF

# Capture buffer between WASAPI and the send loop (frames). WASAPI delivers 10 ms
# chunks and a packet is 8 ms, so the fill swings by about 20 ms; targets of 25 ms
# and below underrun now and then.
TARGET_FILL = RATE * 30 // 1000
FILL_SLACK = RATE * 5 // 1000
MAX_FILL = TARGET_FILL + RATE * 80 // 1000

DECLARED_LATENCY_MS = 250  # the minimum iOS announces; receivers add RENDER_OFFSET_MS
RENDER_OFFSET_MS = 250
DEFAULT_RENDER_MS = 70  # arrivalToRenderLatencyMs when the receiver does not say
# Capture side of the latency: loopback period plus average capture buffer
CAPTURE_MS = 40

# Automatic buffer
AUTO_START_MS = 60
MIN_BUFFER_MS = 15
MAX_BUFFER_MS = 400
GUARD_MS = 8
HEADROOM_MS = 5  # added when growing so small increases do not cause another jump
STATS_WINDOW = 120.0  # seconds of network history the buffer has to cover
LOSS_MEMORY = 120.0  # seconds a lost packet keeps the buffer large enough to resend

# A receiver that drops out of a running stream is reconnected (seconds)
RETRY_S = 3.0  # between attempts
RETRY_MAX_S = 300.0
SETTLED_S = 30.0  # a connection that lasted this long was a good one
SILENT_S = 10.0  # no answer on the RTSP connection for this long: gone
CONNECT_TIMEOUT_S = 10.0
SESSION_ACTIVE = 0x20000  # statusFlags bit: the receiver has an AirPlay session

MODES = {
    "mono": ["mono"],
    "stereo": ["L", "R"],
    "quad": ["FL", "FR", "RL", "RR"],
}

TEST_BUFFER_MS = 100  # network buffer while only a test sound is played
TEST_LEAD = 1.0  # seconds of silence first, so the receiver has synced its clock


def chime() -> np.ndarray:
    """Two soft rising notes (mono float32) to tell which speaker is which."""
    length = int(RATE * 0.5)
    t = np.arange(length) / RATE
    envelope = np.minimum(t / 0.008, 1.0) * np.exp(-t * 7.0)
    tone = np.zeros(int(RATE * 0.8), np.float32)
    for start, pitch in ((0.0, 880.0), (0.28, 1318.5)):
        at = int(RATE * start)
        tone[at:at + length] += (0.1 * envelope * np.sin(2 * np.pi * pitch * t)).astype(np.float32)
    return tone


class Clock:
    """Wall clock in NTP format, driven by the monotonic performance counter."""

    def __init__(self):
        self._t0 = time.time_ns()
        self._p0 = time.perf_counter_ns()

    def ntp_now(self) -> int:
        sec, rem = divmod(self._t0 + time.perf_counter_ns() - self._p0, 10**9)
        return ((sec + 0x83AA7E80) << 32) | ((rem << 32) // 10**9)

    def ts_now(self) -> int:
        return (self.ntp_now() * RATE) >> 32


class Window:
    """Samples of the last `span` seconds."""

    def __init__(self, span: float):
        self.span = span
        self.items = deque()
        self.lock = threading.Lock()

    def add(self, value: float):
        now = time.monotonic()
        with self.lock:
            self.items.append((now, value))
            while self.items[0][0] < now - self.span:
                self.items.popleft()

    def values(self):
        limit = time.monotonic() - self.span
        with self.lock:
            return [value for stamp, value in self.items if stamp >= limit]


class TimingServer(threading.Thread):
    """Answers NTP-style timing requests from all receivers.

    Runs in its own thread so replies are stamped without event loop jitter.
    """

    def __init__(self, clock: Clock):
        super().__init__(daemon=True)
        self.clock = clock
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.port = self.sock.getsockname()[1]
        self.requests = {}  # remote ip -> number of requests

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(256)
            except ConnectionResetError:
                continue  # Windows reports ICMP errors from earlier replies here
            except OSError:
                return
            sec, frac = divmod(self.clock.ntp_now(), 1 << 32)
            try:
                req = TimingPacket.decode(data)
                resp = TimingPacket.encode(
                    req.proto, 0x53 | 0x80, 7, 0,
                    req.sendtime_sec, req.sendtime_frac, sec, frac, sec, frac,
                )
                self.sock.sendto(resp, addr)
            except Exception:
                continue
            self.requests[addr[0]] = self.requests.get(addr[0], 0) + 1


class Pinger(threading.Thread):
    """Measures round trip times to the receivers with ICMP echo."""

    INTERVAL = 0.2
    TIMEOUT_MS = 300

    def __init__(self, addresses):
        super().__init__(daemon=True)
        self.addresses = addresses
        self.rtt = Window(STATS_WINDOW)  # milliseconds, all receivers together
        self._stop_event = threading.Event()

    def run(self):
        icmp = ctypes.windll.iphlpapi
        icmp.IcmpCreateFile.restype = ctypes.c_void_p
        icmp.IcmpSendEcho.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint16,
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32,
        ]
        icmp.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
        handle = icmp.IcmpCreateFile()
        payload = b"homepod-cast"
        reply = ctypes.create_string_buffer(128)
        try:
            while not self._stop_event.wait(self.INTERVAL):
                for address in self.addresses:
                    target = struct.unpack("<I", socket.inet_aton(address))[0]
                    started = time.perf_counter()
                    answered = icmp.IcmpSendEcho(
                        handle, target, payload, len(payload), None,
                        reply, len(reply), self.TIMEOUT_MS,
                    )
                    if answered:
                        self.rtt.add((time.perf_counter() - started) * 1000)
        finally:
            icmp.IcmpCloseHandle(handle)

    def stop(self):
        self._stop_event.set()


class Ring:
    """Thread-safe ring buffer of float32 frames."""

    def __init__(self, channels: int, capacity: int = RATE * 2):
        self.buf = np.zeros((capacity, channels), np.float32)
        self.cap = capacity
        self.r = 0
        self.n = 0
        self.lock = threading.Lock()

    def write(self, x: np.ndarray):
        k = len(x)
        if k == 0:
            return
        with self.lock:
            if k >= self.cap:
                x, k = x[-self.cap:], self.cap
            over = self.n + k - self.cap
            if over > 0:
                self.r = (self.r + over) % self.cap
                self.n -= over
            w = (self.r + self.n) % self.cap
            first = min(k, self.cap - w)
            self.buf[w:w + first] = x[:first]
            self.buf[:k - first] = x[first:]
            self.n += k

    def read(self, k: int) -> np.ndarray:
        with self.lock:
            first = min(k, self.cap - self.r)
            out = np.concatenate((self.buf[self.r:self.r + first], self.buf[:k - first]))
            self.r = (self.r + k) % self.cap
            self.n -= k
        return out

    def drop(self, k: int):
        with self.lock:
            k = min(k, self.n)
            self.r = (self.r + k) % self.cap
            self.n -= k


def list_sources():
    """Return (id, name, channels) of all output devices, default first."""
    import soundcard as sc

    default = sc.default_speaker()
    speakers = [default] + [s for s in sc.all_speakers() if s.id != default.id]
    return [(s.id, s.name, s.channels) for s in speakers]


class Capture(threading.Thread):
    """WASAPI loopback capture of an output device into a ring buffer."""

    def __init__(self, source_id: str, channels: int):
        super().__init__(daemon=True)
        self.source_id = source_id
        self.ring = Ring(channels)
        self.channels = channels
        self.error = None
        self._stop_event = threading.Event()

    def run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            import soundcard as sc

            warnings.simplefilter("ignore", sc.SoundcardRuntimeWarning)
            mic = sc.get_microphone(self.source_id, include_loopback=True)
            with mic.recorder(RATE, channels=self.channels, blocksize=RATE // 100) as rec:
                while not self._stop_event.is_set():
                    self.ring.write(rec.record(numframes=None))
        except Exception as ex:  # pylint: disable=broad-except
            self.error = ex
        finally:
            ctypes.windll.ole32.CoUninitialize()

    def stop(self):
        self._stop_event.set()


def routing_matrix(mode: str, src: int) -> np.ndarray:
    """Return weights of shape (outputs * 2, src) mapping source to stream channels.

    Windows channel order: FL FR FC LFE BL BR SL SR.
    """

    def vec(**gains):
        order = ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"]
        if src == 4:
            order = ["FL", "FR", "BL", "BR"]
        row = np.zeros(src, np.float32)
        for name, gain in gains.items():
            if name in order and order.index(name) < src:
                row[order.index(name)] = gain
        return row

    if src == 1:
        centre = np.ones(1, np.float32)
        front_l = front_r = rear_l = rear_r = left = right = centre
    else:
        front_l = vec(FL=1, FC=0.707, LFE=0.5)
        front_r = vec(FR=1, FC=0.707, LFE=0.5)
        if src >= 4:
            rear_l = vec(BL=1, SL=1)
            rear_r = vec(BR=1, SR=1)
        else:  # stereo source: rear speakers repeat the front ones
            rear_l, rear_r = front_l, front_r
        left = front_l + (0.707 * rear_l if src >= 4 else 0)
        right = front_r + (0.707 * rear_r if src >= 4 else 0)

    rows = {
        # A single receiver gets real stereo and mixes it down itself
        "mono": (left, right),
        "L": (left, left),
        "R": (right, right),
        "FL": (front_l, front_l),
        "FR": (front_r, front_r),
        "RL": (rear_l, rear_l),
        "RR": (rear_r, rear_r),
    }
    return np.array([row for role in MODES[mode] for row in rows[role]], np.float32)


class Context(StreamContext):
    """Stream context whose RTP time wraps instead of overflowing."""

    @property
    def rtptime(self) -> int:
        return (self.head_ts - self.start_ts + self.latency) & MASK32


class Control(ControlClient):
    """Control channel with sync packets derived from the shared timeline."""

    def __init__(self, context, packet_backlog, clock: Clock):
        super().__init__(context, packet_backlog)
        self.clock = clock
        self.resends = 0
        self.last_resend = float("-inf")

    async def _sync_task(self, dest):
        first = True
        while self.transport is not None:
            ctx = self.context
            # "The frame with this RTP time is nominally due now". What we have
            # actually sent so far is deliberately not part of it.
            now = self.clock.ts_now()
            due = (now - ctx.start_ts) & MASK32
            sec, frac = divmod((now << 32) // RATE, 1 << 32)
            self.transport.sendto(
                SyncPacket.encode(
                    0x90 if first else 0x80, 0xD4, 0x0007,
                    due, sec & MASK32, frac, (due + ctx.latency) & MASK32,
                ),
                dest,
            )
            first = False
            await asyncio.sleep(1.0)

    def _retransmit_lost_packets(self, request, addr):
        self.resends += request.lost_packets
        self.last_resend = time.monotonic()
        super()._retransmit_lost_packets(request, addr)


class Busy(Exception):
    """The receiver is playing for another sender."""


class Output:
    """One AirPlay receiver."""

    def __init__(self, conf, clock: Clock):
        self.conf = conf
        self.clock = clock
        self.name = conf.name
        self.address = str(conf.address)
        self.ctx = Context()
        self.ctx.latency = RATE * DECLARED_LATENCY_MS // 1000
        self.backlog = PacketFifo(1000)
        self.conn = self.rtsp = self.proto = self.control = self.audio = None
        self.info = {}
        self.render_ms = DEFAULT_RENDER_MS
        self.online = False  # connected and part of the stream
        self.busy = False  # dropped out and now plays for another sender
        self.polite = False  # its session flag can be trusted, see connect
        self.joined = 0.0  # when it came online
        self.heard = None  # when it last answered on the RTSP connection
        self.retry = RETRY_S  # wait before reconnecting, should it drop out
        self.first = True
        self.opened = False  # a session was set up, so there is one to tear down

    async def connect(self, timing_port: int, polite: bool = None):
        """Open the session. polite leaves a receiver alone that is in use.

        Left out for the first connection, which finds out whether that works.
        """
        loop = asyncio.get_running_loop()
        service = self.conf.get_service(Protocol.RAOP)
        self.ctx.credentials = extract_credentials(service)
        self.ctx.password = service.password
        self.ctx.rtpseq = randrange(2**16)

        self.conn = await http_connect(self.address, service.port)
        self.rtsp = RtspSession(self.conn)
        version = get_protocol_version(service, AirPlayVersion.Auto)
        proto_class = (
            airplayv1.AirPlayV1
            if version == AirPlayMajorVersion.AirPlayV1
            else airplayv2.AirPlayV2
        )
        self.proto = proto_class(self.ctx, self.rtsp)

        # The reply to the stream SETUP says how long the receiver needs to render
        rtsp_setup = self.rtsp.setup

        async def setup(headers=None, body=None):
            response = await rtsp_setup(headers=headers, body=body)
            try:
                stream = decode_bplist_from_body(response)["streams"][0]
                self.render_ms = stream.get("arrivalToRenderLatencyMs", self.render_ms)
            except Exception:  # pylint: disable=broad-except
                pass
            return response

        self.rtsp.setup = setup

        _, self.control = await loop.create_datagram_endpoint(
            lambda: Control(self.ctx, self.backlog, self.clock),
            local_addr=(self.conn.local_ip, 0),
        )
        self.info = await self.rtsp.info()
        flags = self.info.get("statusFlags")
        in_use = isinstance(flags, int) and bool(flags & SESSION_ACTIVE)
        if polite and in_use:
            raise Busy(self.name)
        # A receiver that shows the flag before we ever played to it shows it for
        # some other reason, so later on it cannot tell us that someone took over
        self.polite = not in_use if polite is None else polite
        await self.proto.setup(timing_port, self.control.port)
        self.opened = True
        self.audio, _ = await loop.create_datagram_endpoint(
            AudioProtocol, remote_addr=(self.conn.remote_ip, self.ctx.server_port)
        )
        await self.rtsp.record()
        await self.rtsp.flush(
            headers={
                "Range": "npt=0-",
                "Session": self.ctx.rtsp_session,
                "RTP-Info": f"seq={self.ctx.rtpseq};rtptime={self.ctx.rtptime}",
            }
        )

    @property
    def deadline_ms(self) -> float:
        """How long after its nominal time a frame may arrive at the latest."""
        return DECLARED_LATENCY_MS + RENDER_OFFSET_MS - self.render_ms

    def begin(self, start_ts: int, head_ts: int):
        """Join the shared timeline at head_ts."""
        self.ctx.start_ts, self.ctx.head_ts = start_ts, head_ts
        self.control.start(self.conn.remote_ip)
        self.online, self.joined = True, time.monotonic()

    async def send(self, audio: bytes):
        ctx = self.ctx
        header = AudioPacketHeader.encode(
            0x80, 0xE0 if self.first else 0x60, ctx.rtpseq, ctx.rtptime,
            self.rtsp.session_id,
        )
        self.first = False
        seq, packet = await self.proto.send_audio_packet(self.audio, header, audio)
        self.backlog[seq] = packet
        ctx.rtpseq = (ctx.rtpseq + 1) % 2**16
        ctx.head_ts += FPP

    @property
    def alive(self) -> bool:
        return self.conn is not None and self.conn.transport is not None

    @property
    def lost(self) -> bool:
        """The receiver closed the session, or no longer answers."""
        if not self.alive or self.audio.is_closing():
            return True
        return self.heard is not None and time.monotonic() - self.heard > SILENT_S

    @property
    def volume(self):
        """Volume reported by the receiver in percent, if known."""
        level = self.info.get("initialVolume")
        return dbfs_to_pct(level) if isinstance(level, float) else None

    async def set_volume(self, percent: float):
        await self.rtsp.set_parameter("volume", str(pct_to_dbfs(percent)))

    async def close(self):
        if self.control:
            self.control.stop()
        if self.opened and self.alive:
            try:
                await asyncio.wait_for(self.rtsp.teardown(self.ctx.rtsp_session), 2)
            except Exception:  # pylint: disable=broad-except
                pass
        if self.proto:
            self.proto.teardown()
        if self.control:
            self.control.close()
        if self.audio:
            self.audio.close()
        if self.conn:
            self.conn.close()


class Engine:
    """Owns the event loop thread; all public methods are safe to call from the GUI."""

    def __init__(self):
        self.clock = Clock()
        self.timing = TimingServer(self.clock)
        self.timing.start()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True).start()

        self.state = "idle"  # idle, connecting, streaming
        self.error = None  # (code, detail) of what ended the last stream
        self.outputs = []
        self.capture = None
        self.pinger = None
        self.level = 0.0
        self.underruns = 0
        self.started_at = 0.0
        self.auto = True
        self.buffer_target = float(AUTO_START_MS)  # wanted
        self.buffer_ms = float(AUTO_START_MS)  # in effect right now
        self.deadline_ms = 0.0
        self.render_ms = DEFAULT_RENDER_MS
        self.send_late = Window(STATS_WINDOW)  # how late the send loop ran, ms
        self.inject = {}  # output index -> test sound still to be mixed in
        self.testing = False  # a stream that exists only to play a test sound
        self.start_ts = self.head = 0  # the shared timeline: its origin, and what is sent
        self.volume = None  # percent, once it has been set for this stream
        self.reconnect = True
        self._task = None
        self._rejoins = {}  # output index -> task reconnecting that receiver

    def _run_loop(self):
        # Default Windows timer granularity (15.6 ms) is coarser than one packet
        ctypes.windll.winmm.timeBeginPeriod(1)
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # --- public API -------------------------------------------------------------

    def scan(self, timeout: float = 3.0):
        """Future resolving to AirPlay audio receivers on the network."""
        return self._submit(self._scan(timeout))

    def start(self, confs, mode: str, source_id: str, src_channels: int, buffer_ms=None,
              reconnect: bool = True):
        """Start streaming. confs are ordered like MODES[mode].

        buffer_ms is the network buffer; None lets the engine tune it. Without
        reconnect the stream ends as soon as one receiver is lost.
        """
        return self._submit(
            self._start(confs, mode, source_id, src_channels, buffer_ms, reconnect)
        )

    def stop(self):
        return self._submit(self._stop())

    def identify(self, conf):
        """Play a short test sound on one receiver, streaming or not."""
        return self._submit(self._identify(conf))

    def set_volume(self, percent: float):
        return self._submit(self._set_volume(percent))

    def status(self) -> dict:
        rtts = self.pinger.rtt.values() if self.pinger else []
        return {
            "state": self.state,
            "error": self.error,
            "level": self.level,
            "underruns": self.underruns,
            "elapsed": time.monotonic() - self.started_at,
            "auto": self.auto,
            "buffer": self.buffer_ms,
            "latency": self.buffer_ms + self.render_ms + CAPTURE_MS,
            "rtt": max(rtts, default=None),
            "outputs": [
                {
                    "name": o.name,
                    "online": o.online,
                    "busy": o.busy,
                    "age": time.monotonic() - o.joined,
                    "timing": self.timing.requests.get(o.address, 0),
                    "resends": o.control.resends if o.control else 0,
                    "volume": o.volume,
                }
                for o in self.outputs
            ],
        }

    # --- event loop side --------------------------------------------------------

    async def _scan(self, timeout):
        confs = await pyatv.scan(self.loop, timeout=timeout)
        confs = [c for c in confs if c.get_service(Protocol.RAOP)]
        return sorted(confs, key=lambda c: c.name)

    async def _start(self, confs, mode, source_id, src_channels, buffer_ms, reconnect):
        if self.state != "idle":
            raise RuntimeError("already running")
        self.state, self.error = "connecting", None
        self.reconnect = reconnect
        self.underruns = 0
        self.volume = None
        self.timing.requests.clear()
        self.outputs = [Output(conf, self.clock) for conf in confs]
        try:
            await asyncio.gather(*(o.connect(self.timing.port) for o in self.outputs))
        except BaseException:
            await self._close_outputs()
            self.state = "idle"
            raise

        # All receivers share one timeline, so the slowest one sets the deadline
        self.deadline_ms = min(o.deadline_ms for o in self.outputs)
        self.render_ms = max(o.render_ms for o in self.outputs)
        self.auto = buffer_ms is None
        self.buffer_target = float(AUTO_START_MS if self.auto else buffer_ms)
        self.buffer_ms = self.buffer_target
        self.send_late = Window(STATS_WINDOW)

        self.inject = {}
        if source_id is not None:
            self.capture = Capture(source_id, src_channels)
            self.capture.start()
        self.pinger = Pinger([o.address for o in self.outputs])
        self.pinger.start()
        self.started_at = time.monotonic()
        self.state = "streaming"
        self._task = asyncio.ensure_future(
            self._pump(routing_matrix(mode, src_channels))
        )

    async def _stop(self):
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rejoins, self._rejoins = list(self._rejoins.values()), {}
        for rejoin in rejoins:
            rejoin.cancel()
        await asyncio.gather(*rejoins, return_exceptions=True)
        if self.capture:
            self.capture.stop()
            self.capture = None
        if self.pinger:
            self.pinger.stop()
            self.pinger = None
        await self._close_outputs()
        self.level = 0.0
        self.state = "idle"

    async def _identify(self, conf):
        address = str(conf.address)
        if self.state == "streaming" and not self.testing:
            # Already streaming: mix the sound into that speaker's channel
            for index, out in enumerate(self.outputs):
                if out.address == address:
                    self.inject[index] = chime()
            return
        if self.state != "idle":
            raise RuntimeError("busy")

        self.testing = True
        try:
            # A stream without a source: silence plus the test sound
            await self._start([conf], "mono", None, 2, TEST_BUFFER_MS, False)
            await asyncio.sleep(TEST_LEAD)
            tone = chime()
            self.inject[0] = tone
            await asyncio.sleep(len(tone) / RATE + 0.8)
        finally:
            if self.state != "idle":
                await self._stop()
            self.testing = False

    async def _close_outputs(self):
        await asyncio.gather(*(o.close() for o in self.outputs), return_exceptions=True)
        self.outputs = []

    async def _set_volume(self, percent):
        self.volume = percent
        await asyncio.gather(*(o.set_volume(percent) for o in self.outputs if o.online))

    def _drop(self, index):
        """Take a lost receiver out of the stream and start getting it back."""
        out = self.outputs[index]
        out.online = False
        self._rejoins[index] = asyncio.ensure_future(self._rejoin(index, out))

    async def _rejoin(self, index, old):
        """Reconnect a receiver and put it back on the shared timeline."""
        # One that drops us again right away has most likely been taken by another
        # sender. Every such round quadruples the wait instead of fighting over it.
        brief = time.monotonic() - old.joined < SETTLED_S
        wait = min(old.retry * 4, RETRY_MAX_S) if brief else RETRY_S
        await old.close()
        while True:
            await asyncio.sleep(wait)
            new = Output(old.conf, self.clock)
            new.retry = wait
            # The FLUSH sent while connecting names the RTP time the stream is at.
            # Left at zero, a receiver would take a stream older than 13 hours
            # (half the 32-bit range) for one that lies before the flush.
            new.ctx.start_ts, new.ctx.head_ts = self.start_ts, self.head
            try:
                await asyncio.wait_for(
                    new.connect(self.timing.port, old.polite), CONNECT_TIMEOUT_S
                )
                if self.volume is not None:
                    await new.set_volume(self.volume)
                break
            except BaseException as ex:
                await new.close()
                if not isinstance(ex, Exception):
                    raise
                old.busy = isinstance(ex, Busy)

        self.timing.requests.pop(new.address, None)
        self.deadline_ms = min(self.deadline_ms, new.deadline_ms)
        self.render_ms = max(self.render_ms, new.render_ms)
        new.begin(self.start_ts, self.head)
        self.outputs[index] = new
        self._rejoins.pop(index, None)

    def _adapt(self):
        """Pick the smallest buffer that covers what the network did recently."""
        rtts = self.pinger.rtt.values()
        # A round trip bounds the one-way delay; without replies assume a busy link
        slowest = max(rtts, default=15.0)
        typical = min(rtts, default=5.0)
        late = max(self.send_late.values(), default=0.0)
        need = GUARD_MS + late + slowest

        now = time.monotonic()
        if any(now - o.control.last_resend < LOSS_MEMORY for o in self.outputs):
            # Packets do get lost: leave time for the receiver to notice (it needs
            # the next packet, 8 ms later), ask, and get the copy
            need = max(need, GUARD_MS + late + 8 + 2 * typical)

        need = min(max(need, MIN_BUFFER_MS), MAX_BUFFER_MS)
        if need > self.buffer_target:
            self.buffer_target = min(need + HEADROOM_MS, MAX_BUFFER_MS)
        elif need < self.buffer_target - HEADROOM_MS - 3:
            self.buffer_target = need + HEADROOM_MS

    async def _pump(self, matrix):
        outputs, clock = self.outputs, self.clock
        ring = self.capture.ring if self.capture else Ring(matrix.shape[1])
        silence = bytes(FPP * 4)
        weights = np.ascontiguousarray(matrix.T)
        grid = np.arange(FPP, dtype=np.float32)

        def lag_for(buffer_ms):
            return int((self.deadline_ms - buffer_ms) * RATE / 1000)

        self.start_ts = self.head = clock.ts_now()
        for out in outputs:
            out.begin(self.start_ts, self.head)
        # A frame with nominal time T is sent at T + lag
        lag = lag_for(self.buffer_target)
        prefill = True
        fill_avg = float(TARGET_FILL)
        packets = 0
        settled = False  # false while catching up after a jump
        late_max = 0
        seconds = 0
        next_second = time.monotonic() + 1.0

        try:
            while True:
                target = lag_for(self.buffer_target)
                if lag > target:
                    # More buffer wanted: jump. The burst below closes the gap and
                    # the capture buffer runs dry once, which is a short dropout.
                    lag, settled = target, False

                due = clock.ts_now() - lag - self.head
                if due > 2 * RATE:  # stalled for seconds, skip instead of bursting
                    skip = due // FPP * FPP
                    for out in outputs:
                        out.ctx.head_ts += skip
                    self.head += skip
                    due -= skip
                    settled = False
                if settled and due > late_max:
                    late_max = due

                count = min(due // FPP + 1, 25) if due >= 0 else 0
                for _ in range(count):
                    shrink = 0
                    if lag < target:
                        # Less buffer wanted: glide there by squeezing one frame
                        # out of every packet (0.3 %, inaudible)
                        lag += 1
                        shrink = 1

                    block = None
                    avail = ring.n
                    if prefill and avail >= TARGET_FILL:
                        ring.drop(avail - TARGET_FILL)
                        avail, prefill, fill_avg = TARGET_FILL, False, float(TARGET_FILL)
                    if not prefill:
                        if avail < FPP + 2:
                            prefill = True
                            # Loopback capture delivers nothing while the PC is
                            # silent; only running dry mid-sound is a dropout
                            if self.level > 1e-4:
                                self.underruns += 1
                        else:
                            if avail > MAX_FILL:
                                ring.drop(avail - TARGET_FILL)
                                avail = TARGET_FILL
                            fill_avg += (avail - fill_avg) * 0.01
                            # Follow the sound card's clock by stretching or
                            # squeezing every fourth packet by one frame (every
                            # packet when far off)
                            take = FPP + shrink
                            far = abs(fill_avg - TARGET_FILL) > 3 * FILL_SLACK
                            if far or packets % 4 == 0:
                                if fill_avg > TARGET_FILL + FILL_SLACK:
                                    take += 1
                                elif fill_avg < TARGET_FILL - FILL_SLACK:
                                    take -= 1
                            block = ring.read(take)
                            if take != FPP:
                                pos = grid * ((take - 1) / (FPP - 1))
                                src = np.arange(take, dtype=np.float32)
                                block = np.stack(
                                    [np.interp(pos, src, col) for col in block.T], axis=1
                                )

                    if block is None:
                        self.level *= 0.9
                    else:
                        self.level = max(float(np.abs(block).max()), self.level * 0.9)
                    if block is None and not self.inject:
                        payloads = [silence] * len(outputs)
                    else:
                        if block is None:
                            mixed = np.zeros((FPP, weights.shape[1]), np.float32)
                        else:
                            mixed = block @ weights
                        for index in list(self.inject):
                            tone = self.inject.pop(index)
                            mixed[:len(tone[:FPP]), 2 * index:2 * index + 2] += tone[:FPP, None]
                            if len(tone) > FPP:
                                self.inject[index] = tone[FPP:]
                        mixed = (np.clip(mixed, -1.0, 1.0) * 32767.0).astype(">i2")
                        payloads = [
                            mixed[:, 2 * i:2 * i + 2].tobytes() for i in range(len(outputs))
                        ]

                    for out, payload in zip(outputs, payloads):
                        if out.online:
                            await out.send(payload)
                    self.head += FPP
                    packets += 1
                if count < 25:
                    settled = True

                now = time.monotonic()
                if now >= next_second:
                    next_second = now + 1.0
                    seconds += 1
                    self.send_late.add(late_max * 1000 / RATE)
                    late_max = 0
                    self.buffer_ms = self.deadline_ms - lag * 1000 / RATE
                    if self.auto:
                        self._adapt()
                    if seconds % 2 == 0:
                        for index, out in enumerate(outputs):
                            if not out.online:
                                continue
                            if not out.lost:
                                asyncio.ensure_future(self._feedback(out))
                            elif self.reconnect:
                                self._drop(index)
                            else:
                                raise ConnectionError("disconnected", out.name)
                        if self.capture and self.capture.error:
                            raise RuntimeError("capture", str(self.capture.error))

                await asyncio.sleep(0.002)
        except asyncio.CancelledError:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            known = len(ex.args) == 2 and ex.args[0] in ("disconnected", "capture")
            self.error = tuple(ex.args) if known else ("error", str(ex) or type(ex).__name__)
            self._task = None
            await self._stop()

    @staticmethod
    async def _feedback(out):
        try:
            await out.rtsp.feedback(allow_error=True)
            out.heard = time.monotonic()
        except Exception:  # pylint: disable=broad-except
            pass
