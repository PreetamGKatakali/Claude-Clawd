"""Local companion server for clawd-companion.

Binds 127.0.0.1 only. Serves a small page, a /state JSON snapshot and a /events
Server-Sent Events stream. There are no write endpoints and no CORS headers, so
nothing outside this machine can reach it or read from it cross-origin.

What leaves this process is built field by field in `snapshot()`: a state name,
a topic label, a thought line, the project folder's basename and timestamps.
Prompt text, absolute paths and tool inputs are never in the payload because
they are never in the state files.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import clawd_common as common  # noqa: E402

POLL_SECONDS = 0.25
HEARTBEAT_SECONDS = 2.0
MAX_PORT_PROBES = 20

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def web_root():
    """The bundled page, preferring the stable copy made by setup."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(common.base_dir(), "web"),
                 os.path.join(here, "web")):
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "index.html")):
            return cand
    return os.path.join(here, "web")


class Hub(object):
    """Watches the state directory and fans changes out to SSE listeners."""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = []
        self.current = None
        self.stop = threading.Event()

    def subscribe(self):
        q = []
        cv = threading.Condition()
        entry = (q, cv)
        with self.lock:
            self.listeners.append(entry)
        return entry

    def unsubscribe(self, entry):
        with self.lock:
            if entry in self.listeners:
                self.listeners.remove(entry)

    def publish(self, payload):
        with self.lock:
            listeners = list(self.listeners)
        for q, cv in listeners:
            with cv:
                q.append(payload)
                cv.notify()

    def run(self):
        while not self.stop.is_set():
            try:
                payload = snapshot()
                # `now` ticks every poll, so compare without it: otherwise every
                # 250ms poll would look like a change and flood the stream.
                key = dict(payload)
                key.pop("now", None)
                if key != self.current:
                    self.current = key
                    self.publish(payload)
                beat(payload)
            except Exception:
                pass
            self.stop.wait(POLL_SECONDS)


_last_beat = [0.0]


def beat(payload):
    """Tell the status line the server is alive and owns the hydration timer."""
    at = common.now()
    if at - _last_beat[0] < HEARTBEAT_SECONDS:
        return
    _last_beat[0] = at
    gl = common.read_json(common.global_path(), {})
    gl["server_heartbeat"] = at
    gl["server_port"] = payload.get("port")
    try:
        common.write_atomic(common.global_path(), json.dumps(gl))
    except Exception:
        pass


_PORT = [0]


def snapshot():
    """Build the public state payload. Allowlist only: nothing is passed through."""
    at = common.now()
    cfg = common.load_config()
    records = common.live_sessions(cfg, at)
    merged = common.merge_sessions(records, cfg, at)
    state = merged.get("state", "idle")

    # Hydration: the server owns the timer whenever it is running.
    gl = common.read_json(common.global_path(), {})
    active, new_gl = common.hydration_tick(
        gl, cfg, at, suppressed=(state == "confirm"))
    if new_gl is not None:
        try:
            keep = common.read_json(common.global_path(), {})
            keep.update(new_gl)
            common.write_atomic(common.global_path(), json.dumps(keep))
            gl = keep
        except Exception:
            pass

    hydration = None
    if active and state != "confirm":
        state = "hydrate"
        hydration = common.hydration_message(gl.get("hydration_seed", 0),
                                             common.user_name(cfg))

    topic_key = merged.get("topic")
    seed = merged.get("seed", 0)
    return {
        "state": state,
        "topic_key": topic_key if state == "thinking" else None,
        "topic": common.topic_label(topic_key) if (state == "thinking" and topic_key) else None,
        "thoughts": common.thought_lines(topic_key) if state == "thinking" else [],
        "seed": int(seed) if isinstance(seed, (int, float)) else 0,
        "project": merged.get("project"),
        "sessions": merged.get("sessions", 0),
        "hydration": hydration,
        "state_since": merged.get("state_since", at),
        "welcome_until": _number(merged.get("welcome_until")),
        # The one field outside the original payload list, added by the
        # user's choice: the name to greet, already cleaned. Never leaves
        # 127.0.0.1, like everything else here.
        "name": common.user_name(cfg),
        # Also the user's choice: Clawd's body colour, one of a fixed list,
        # for the model of the session shown (the status line records it).
        "color": common.body_hex(cfg, common.session_model(merged.get("session_id"))),
        "updated": merged.get("updated", at),
        "now": at,
        "sound_enabled": bool(cfg.get("sound_enabled")),
        "port": _PORT[0],
    }


def _number(value):
    """A timestamp from a session file, or None if it is not a number."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


class Handler(BaseHTTPRequestHandler):
    server_version = "clawd-companion"
    protocol_version = "HTTP/1.1"
    hub = None

    def log_message(self, fmt, *args):
        pass  # no request logging: paths and timing stay out of any log

    def _host_ok(self):
        """Reject anything that is not an explicit loopback Host (DNS rebinding)."""
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0].strip("[]") if host else ""
        return name in ("127.0.0.1", "localhost", "::1")

    def _deny(self, code=403):
        body = b"forbidden\n"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        # No Access-Control-Allow-Origin header at all: same-origin only.

    def do_POST(self):
        self._deny(405)

    def do_PUT(self):
        self._deny(405)

    def do_DELETE(self):
        self._deny(405)

    def do_HEAD(self):
        self._deny(405)

    def do_GET(self):
        if not self._host_ok():
            return self._deny()
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._static("index.html")
        if path == "/state":
            return self._json(snapshot())
        if path == "/events":
            return self._events()
        if path == "/healthz":
            return self._json({"ok": True})
        return self._static(path.lstrip("/"))

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _static(self, rel):
        root = web_root()
        # Contain the path: no traversal out of the web directory.
        target = os.path.normpath(os.path.join(root, rel))
        if not target.startswith(os.path.normpath(root) + os.sep) and target != os.path.normpath(root):
            return self._deny(404)
        if not os.path.isfile(target):
            return self._deny(404)
        ext = os.path.splitext(target)[1].lower()
        try:
            with open(target, "rb") as f:
                body = f.read()
        except OSError:
            return self._deny(404)
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self._security_headers()
        self.end_headers()
        entry = Handler.hub.subscribe()
        q, cv = entry
        try:
            self._send_event(snapshot())
            while True:
                with cv:
                    if not q:
                        cv.wait(timeout=10.0)
                    items, q[:] = list(q), []
                if items:
                    for payload in items:
                        self._send_event(payload)
                else:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            Handler.hub.unsubscribe(entry)

    def _send_event(self, payload):
        data = json.dumps(payload)
        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
        self.wfile.flush()


def bind(preferred):
    """Take the preferred port, else the next free one. Loopback only."""
    last = None
    for offset in range(MAX_PORT_PROBES):
        port = preferred + offset
        if port > 65535:
            break
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            httpd.daemon_threads = True
            return httpd, port
        except OSError as exc:
            last = exc
            continue
    raise SystemExit("clawd-companion: no free port near %d (%s)" % (preferred, last))


def port_file():
    return os.path.join(common.base_dir(), "port")


def pid_file():
    return os.path.join(common.base_dir(), "server.pid")


def running_port():
    """Return the port of a live server, or None."""
    try:
        with open(port_file()) as f:
            port = int(f.read().strip())
    except Exception:
        return None
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return port
    except OSError:
        return None
    finally:
        s.close()


def open_browser(url, mode):
    """Open the page. App mode tries a Chromium app window, else the default browser."""
    import subprocess
    import webbrowser
    if mode == "app":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]
        for exe in candidates:
            if os.path.isfile(exe) and os.access(exe, os.X_OK):
                try:
                    subprocess.Popen(
                        [exe, "--app=" + url, "--window-size=360,520"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return "app:" + os.path.basename(exe)
                except Exception:
                    break
    try:
        webbrowser.open(url)
        return "tab"
    except Exception:
        return "failed"


def serve(args):
    common.ensure_dirs()
    cfg = common.load_config()

    existing = running_port()
    if existing:
        url = "http://127.0.0.1:%d/" % existing
        if args.open:
            open_browser(url, cfg["window_mode"])
        sys.stdout.write(url + "\n")
        return 0

    httpd, port = bind(int(args.port or cfg["port"]))
    _PORT[0] = port
    common.write_atomic(port_file(), str(port) + "\n")
    common.write_atomic(pid_file(), str(os.getpid()) + "\n")

    hub = Hub()
    Handler.hub = hub
    watcher = threading.Thread(target=hub.run, name="clawd-watch")
    watcher.daemon = True
    watcher.start()

    url = "http://127.0.0.1:%d/" % port
    sys.stdout.write(url + "\n")
    sys.stdout.flush()
    if args.open:
        open_browser(url, cfg["window_mode"])

    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop.set()
        for path in (port_file(), pid_file()):
            try:
                os.unlink(path)
            except OSError:
                pass
    return 0


def stop(_args):
    try:
        with open(pid_file()) as f:
            pid = int(f.read().strip())
    except Exception:
        sys.stdout.write("clawd-companion: not running\n")
        return 0
    # A stale pid file may name a process that is not ours any more.
    if not common.pid_matches(pid, "server.py"):
        sys.stdout.write("clawd-companion: not running\n")
    else:
        try:
            os.kill(pid, 15)
            sys.stdout.write("clawd-companion: stopped (pid %d)\n" % pid)
        except OSError:
            sys.stdout.write("clawd-companion: not running\n")
    for path in (port_file(), pid_file()):
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="clawd-companion server")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--stop", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args(argv)
    if args.stop:
        return stop(args)
    if args.status:
        port = running_port()
        sys.stdout.write(("running on http://127.0.0.1:%d/\n" % port) if port else "not running\n")
        return 0
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
