#!/usr/bin/env python3
"""claude-timebox-mini — HTTP→RFCOMM bridge for a Divoom Timebox Mini.

Stdlib only. GET endpoints:
    /thinking  /waiting  /done  /reset  /ping

Requires CLAUDE_TIMEBOX_MINI_MAC env var (the Mini's Bluetooth MAC).
"""
import datetime
import hmac
import http.server
import os
import signal
import socket
import sys
import threading
import time

# -- Hardcoded config ---------------------------------------------------------

MAC = os.environ.get("CLAUDE_TIMEBOX_MINI_MAC")
if not MAC:
    sys.exit("CLAUDE_TIMEBOX_MINI_MAC env var is required (the Mini's Bluetooth MAC)")
API_KEY = os.environ.get("CLAUDE_TIMEBOX_MINI_API_KEY") or None
RFCOMM_PORT = 4
HTTP_PORT = 25293

CLOCK_COLOR = (0x00, 0x80, 0x80)  # teal
RED = (0xFF, 0x00, 0x00)
GREEN = (0x00, 0xFF, 0x00)
OFF = (0x00, 0x00, 0x00)

# 8-frame orange breathing ramp, 100ms/frame (see COLORS.md).
ORANGE_RAMP = [
    (0x40, 0x10, 0x00),
    (0x70, 0x20, 0x00),
    (0xB0, 0x30, 0x00),
    (0xF0, 0x40, 0x00),
    (0xF0, 0x40, 0x00),
    (0xB0, 0x30, 0x00),
    (0x70, 0x20, 0x00),
    (0x40, 0x10, 0x00),
]

# 11×11 Clawd silhouette. '#' = on, '.' = off.
CLAWD = """\
...........
..#######..
..#.###.#..
###########
###########
..#######..
..#######..
..#.#.#.#..
..#.#.#.#..
...........
...........
"""
BITS = [c == "#" for line in CLAWD.splitlines() for c in line]
assert len(BITS) == 121, f"CLAWD must be 11×11, got {len(BITS)}"

DONE_DURATION_S = 3
RECONNECT_BACKOFF_S = 5
INTER_FRAME_GAP_S = 0.05  # keep under the ~30 pkt/s community ceiling
PING_MAGIC = b"divoomctl ok\n"

# -- Protocol helpers (verbatim from proto.py) --------------------------------


def checksum(payload):
    s = sum(payload)
    return [s & 0xFF, (s >> 8) & 0xFF]


def escape(payload):
    out = []
    for b in payload:
        if 0x01 <= b <= 0x03:
            out.extend([0x03, b + 0x03])
        else:
            out.append(b)
    return out


def make_message(command, args):
    length = len(args) + 3
    payload = [length & 0xFF, (length >> 8) & 0xFF, command] + list(args)
    payload += checksum(payload)
    return bytes([0x01] + escape(payload) + [0x02])


def pack_pixels(pixels):
    """121 (R,G,B) triples → 182-byte 4-bit-per-channel payload."""
    out = []
    i = 0
    while i < len(pixels):
        c1 = pixels[i]
        c2 = pixels[i + 1] if i + 1 < len(pixels) else OFF
        out.append(((c1[0] >> 4) & 0x0F) | (c1[1] & 0xF0))
        out.append(((c1[2] >> 4) & 0x0F) | (c2[0] & 0xF0))
        out.append(((c2[1] >> 4) & 0x0F) | (c2[2] & 0xF0))
        i += 2
    return out[:182]


# -- Frame precomputation -----------------------------------------------------


def _mask(on_color):
    return [on_color if b else OFF for b in BITS]


def _static_frame(pixels):
    return make_message(0x44, [0x00, 0x0A, 0x0A, 0x04] + pack_pixels(pixels))


def _anim_frame(idx, delay, pixels):
    return make_message(
        0x49, [0x00, 0x0A, 0x0A, 0x04, idx, delay] + pack_pixels(pixels)
    )


def _clock_frame(color=CLOCK_COLOR):
    return make_message(0x45, [0x00, 0x01, *color])


FRAMES = {}


def build_frames():
    # Every animation is prefixed with a 0x44 static of its "on" state. 0x44
    # forces an immediate display update — without it, the Mini keeps looping
    # its currently-loaded animation buffer while our new 0x49 frames refill
    # it in the background, and the visual transition lags by a full cycle.
    on_red = _mask(RED)
    all_off = [OFF] * 121

    FRAMES["thinking"] = [_static_frame(_mask(ORANGE_RAMP[3]))] + [
        _anim_frame(i, 1, _mask(c)) for i, c in enumerate(ORANGE_RAMP)
    ]
    FRAMES["waiting"] = [_static_frame(on_red)] + [
        _anim_frame(i, 10 if i % 2 == 0 else 5, on_red if i % 2 == 0 else all_off)
        for i in range(8)
    ]
    FRAMES["done"] = [_static_frame(_mask(GREEN))]
    FRAMES["clock"] = [_clock_frame()]


# -- Socket management --------------------------------------------------------

_sock_lock = threading.Lock()
_sock = None  # guarded by _sock_lock


def _open_socket():
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(10.0)
    s.connect((MAC, RFCOMM_PORT))
    s.settimeout(1.0)
    try:
        s.recv(512)  # drain HELLO banner
    except socket.timeout:
        pass
    s.settimeout(None)
    return s


def _connect_forever():
    while True:
        try:
            print(f"[bt] connecting to {MAC}:{RFCOMM_PORT}", flush=True)
            s = _open_socket()
            print("[bt] connected", flush=True)
            return s
        except OSError as e:
            print(
                f"[bt] connect failed: {e}; retry in {RECONNECT_BACKOFF_S}s",
                flush=True,
            )
            time.sleep(RECONNECT_BACKOFF_S)


def _send(frames):
    """Send framed bytes over the shared socket. Reconnect once on failure."""
    global _sock
    with _sock_lock:
        if _sock is None:
            _sock = _connect_forever()
        for attempt in range(2):
            try:
                for f in frames:
                    _sock.sendall(f)
                    time.sleep(INTER_FRAME_GAP_S)
                return
            except OSError as e:
                print(f"[bt] send failed: {e}; reconnecting", flush=True)
                try:
                    _sock.close()
                except OSError:
                    pass
                _sock = None
                if attempt == 0:
                    _sock = _connect_forever()
        raise RuntimeError("send failed after reconnect")


# -- State machine ------------------------------------------------------------

_state_lock = threading.Lock()
_state_gen = 0
_done_timer = None  # threading.Timer, guarded by _state_lock


def _bump():
    """Increment generation; cancel any pending done-revert. Returns new gen."""
    global _state_gen, _done_timer
    with _state_lock:
        _state_gen += 1
        if _done_timer is not None:
            _done_timer.cancel()
            _done_timer = None
        return _state_gen


def _schedule_revert(captured_gen):
    global _done_timer

    def revert():
        with _state_lock:
            if _state_gen != captured_gen:
                return
        try:
            _send(FRAMES["clock"])
        except Exception as e:
            print(f"[revert] failed: {e}", flush=True)

    with _state_lock:
        if _state_gen != captured_gen:
            return
        _done_timer = threading.Timer(DONE_DURATION_S, revert)
        _done_timer.daemon = True
        _done_timer.start()


# path → (FRAMES key, schedule revert-to-clock after?)
STATES = {
    "thinking": ("thinking", False),
    "waiting":  ("waiting",  False),
    "done":     ("done",     True),
    "reset":    ("clock",    False),
}


def dispatch(path):
    frames_key, revert = STATES[path]
    gen = _bump()
    _send(FRAMES[frames_key])
    if revert:
        _schedule_revert(gen)


# -- HTTP ---------------------------------------------------------------------


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        path = self.path.strip("/").split("?", 1)[0]
        if path == "ping":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(PING_MAGIC)))
            self.end_headers()
            self.wfile.write(PING_MAGIC)
            return
        if API_KEY and not hmac.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {API_KEY}"
        ):
            self.send_error(401)
            return
        if path not in STATES:
            self.send_error(404)
            return
        try:
            dispatch(path)
        except Exception as e:
            print(f"[{path}] error: {e}", flush=True)
            self.send_error(502, f"device error: {e}")
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


# -- Startup / shutdown -------------------------------------------------------


def shutdown(*_):
    global _done_timer, _sock
    print("[shutdown] reverting to clock, closing socket", flush=True)
    with _state_lock:
        if _done_timer is not None:
            _done_timer.cancel()
            _done_timer = None
    try:
        _send(FRAMES["clock"])
    except Exception as e:
        print(f"[shutdown] revert failed: {e}", flush=True)
    with _sock_lock:
        if _sock is not None:
            try:
                _sock.close()
            except OSError:
                pass
            _sock = None
    sys.exit(0)


def main():
    build_frames()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # First-connect baseline: sync the Mini's drifting RTC, force clock view.
    # Blocks until Mini is reachable (the RFCOMM connect retries forever).
    now = datetime.datetime.now()
    _send([
        make_message(0x18, [now.year % 100, now.year // 100,
                            now.month, now.day,
                            now.hour, now.minute, now.second, 0]),
        _clock_frame(),
    ])

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(
        f"[http] listening on 0.0.0.0:{HTTP_PORT} "
        f"(auth: {'required' if API_KEY else 'disabled'})",
        flush=True,
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
