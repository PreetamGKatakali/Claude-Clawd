"""Shared helpers for clawd-companion.

Stdlib only, Python 3.8+. Every function here is written to be cheap to import:
statusline.py runs on a 1 second timer and must finish well inside its budget.

Nothing in this module ever stores prompt text. State files hold a topic label,
an integer seed, a project folder name and timestamps -- nothing else.
"""

import json
import os
import tempfile
import time

STATES = ("idle", "thinking", "confirm", "done", "hydrate")

# Highest priority first. The window and the status line both resolve a single
# visible state across sessions using this order.
PRIORITY = {"confirm": 40, "hydrate": 30, "thinking": 20, "done": 10, "idle": 0}

DEFAULTS = {
    "hydration_interval_minutes": 45,
    "hydration_display_seconds": 20,
    "confirm_ttl_minutes": 10,
    "session_idle_timeout_minutes": 30,
    "status_style": "sprite",
    "window_mode": "tab",
    "auto_open": False,
    "port": 4756,
    "sound_enabled": False,
    "display_name": "",
}

_NUMERIC = {
    "hydration_interval_minutes": (1, 1440),
    "hydration_display_seconds": (3, 300),
    "confirm_ttl_minutes": (1, 120),
    "session_idle_timeout_minutes": (1, 1440),
    "port": (1024, 65535),
}


def now():
    """Wall clock, overridable so tests never sleep."""
    override = os.environ.get("CLAWD_NOW")
    if override:
        try:
            return float(override)
        except (TypeError, ValueError):
            pass
    return time.time()


def config_dir():
    """The Claude config directory, honoring CLAUDE_CONFIG_DIR."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".claude")


def base_dir():
    """Stable directory shared by hooks, the status line and the server.

    Deliberately not CLAUDE_PLUGIN_ROOT: that path moves on every plugin update.
    """
    env = os.environ.get("CLAWD_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(config_dir(), "clawd-companion")


def state_dir():
    return os.path.join(base_dir(), "state")


def ensure_dirs():
    for d in (base_dir(), state_dir()):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def write_atomic(path, text):
    """Write via temp file + rename so a reader never sees a half-written file."""
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, fallback=None):
    """Read JSON, returning fallback for missing, empty or corrupt files."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {} if fallback is None else fallback
        return data
    except Exception:
        return {} if fallback is None else fallback


def coerce_config(raw):
    """Apply defaults and clamp to valid ranges. Never raises."""
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            if key not in raw:
                continue
            val = raw[key]
            if isinstance(default, bool):
                if isinstance(val, bool):
                    cfg[key] = val
                elif isinstance(val, str):
                    cfg[key] = val.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                lo, hi = _NUMERIC.get(key, (None, None))
                if lo is not None:
                    num = max(lo, min(hi, num))
                cfg[key] = int(num)
            else:
                if isinstance(val, str):
                    cfg[key] = val
    if cfg["status_style"] not in ("sprite", "text", "off"):
        cfg["status_style"] = DEFAULTS["status_style"]
    if cfg["window_mode"] not in ("tab", "app"):
        cfg["window_mode"] = DEFAULTS["window_mode"]
    # Not a plugin option: setup writes this when the user chooses to chain
    # onto a status line they already had.
    if isinstance(raw, dict) and isinstance(raw.get("chain_command"), str):
        cfg["chain_command"] = raw["chain_command"]
    # Not a plugin option either: the name setup asked for. The plugin option
    # display_name wins when it is set.
    if isinstance(raw, dict) and isinstance(raw.get("setup_name"), str):
        cfg["setup_name"] = raw["setup_name"]
    return cfg


NAME_MAX = 20


def clean_name(value):
    """Reduce a user-supplied name to something safe to print anywhere.

    Letters (any script), digits, spaces, hyphens, apostrophes and dots only,
    whitespace collapsed, at most NAME_MAX characters. Escape codes, control
    characters and markup all fall out.
    """
    if not isinstance(value, str):
        return ""
    kept = "".join(ch if (ch.isalnum() or ch in " -'.") else " " for ch in value)
    return " ".join(kept.split())[:NAME_MAX].strip()


def user_name(cfg):
    """The name Clawd greets you with, or "" for none."""
    cfg = cfg or {}
    return clean_name(cfg.get("display_name")) or clean_name(cfg.get("setup_name"))


def welcome_text(name):
    return ("hey %s, welcome!" % name) if name else WELCOME_TEXT


def config_path():
    return os.path.join(base_dir(), "config.json")


def load_config():
    return coerce_config(read_json(config_path(), {}))


def config_from_env():
    """Build config from CLAUDE_PLUGIN_OPTION_* , which only hooks receive."""
    raw = {}
    for key in DEFAULTS:
        val = os.environ.get("CLAUDE_PLUGIN_OPTION_" + key.upper())
        if val is not None and val != "":
            raw[key] = val
    return coerce_config(raw)


def refresh_config_from_env():
    """Mirror the plugin options into config.json for the server and status line.

    Those two processes never receive CLAUDE_PLUGIN_OPTION_*, so every hook run
    republishes the values to a file they can read.
    """
    cfg = config_from_env()
    try:
        existing = read_json(config_path(), None)
        # Preserve keys setup owns that are not plugin options.
        if isinstance(existing, dict) and existing.get("chain_command"):
            cfg["chain_command"] = existing["chain_command"]
        if isinstance(existing, dict) and existing.get("setup_name"):
            cfg["setup_name"] = existing["setup_name"]
        if existing == cfg:
            return cfg
        write_atomic(config_path(), json.dumps(cfg, indent=2) + "\n")
    except Exception:
        pass
    return cfg


def session_path(session_id):
    return os.path.join(state_dir(), safe_name(session_id) + ".json")


def safe_name(value):
    """Reduce an id to characters that are safe in a filename."""
    out = []
    for ch in str(value or "unknown"):
        out.append(ch if (ch.isalnum() or ch in "-_") else "-")
    name = "".join(out)[:120]
    return name or "unknown"


# Shown one at a time while a session is idle, each for READY_SECONDS. The
# first one always comes first, so a session that just went idle says it is
# ready before it starts joking. The companion window (web/app.js) keeps its
# own copy of this list; the tests check the two match.
READY_PHRASES = [
    "ready for next task",
    "tests green, mood green",
    "no bugs, only features",
    "coffee loaded, brain compiling",
    'git commit -m "progress"',
    "it works on my machine",
]
READY_SECONDS = 5
WELCOME_SECONDS = 10
WELCOME_TEXT = "hey! welcome"


def welcoming(record, at=None):
    """True while a freshly launched session is still saying hello."""
    at = now() if at is None else at
    rec = record or {}
    if rec.get("state", "idle") != "idle":
        return False
    try:
        return at < float(rec.get("welcome_until"))
    except (TypeError, ValueError):
        return False


def ready_phrase(record, at=None):
    """The idle phrase for this moment, counted from when idle began."""
    at = now() if at is None else at
    rec = record or {}
    try:
        since = float(rec.get("state_since"))
    except (TypeError, ValueError):
        # No record yet, for example the moment after /clear before its hook
        # has run. Say "ready" rather than jumping into the middle of the list.
        since = at
    # A finished turn shows "done" for 5 seconds before it reads as idle.
    if rec.get("state") == "done":
        since += 5
    # After a hello, the jokes begin when the hello ends.
    try:
        since = max(since, float(rec.get("welcome_until")))
    except (TypeError, ValueError):
        pass
    step = int(max(0.0, at - since) // READY_SECONDS)
    return READY_PHRASES[step % len(READY_PHRASES)]


def expire(record, cfg, at=None):
    """Collapse a session record to its currently valid state.

    Returns a (possibly) adjusted copy; does not write anything.
    """
    at = now() if at is None else at
    rec = dict(record or {})
    state = rec.get("state", "idle")
    if state not in STATES:
        state = "idle"
    ts = rec.get("state_since")
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = at

    if state == "confirm" and at - ts > cfg["confirm_ttl_minutes"] * 60:
        state = "idle"
        rec["topic"] = None
    elif state == "done" and at - ts > 5:
        state = "idle"
    elif state == "thinking" and at - ts > 6 * 60 * 60:
        # A session killed mid-turn never sends Stop. Do not think forever.
        state = "idle"
    rec["state"] = state
    return rec


def live_sessions(cfg, at=None):
    """Every session file that has not gone idle-stale, already expired."""
    at = now() if at is None else at
    out = []
    try:
        names = os.listdir(state_dir())
    except OSError:
        return out
    cutoff = cfg["session_idle_timeout_minutes"] * 60
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        rec = read_json(os.path.join(state_dir(), name), None)
        if not rec:
            continue
        try:
            seen = float(rec.get("updated", 0))
        except (TypeError, ValueError):
            seen = 0
        if at - seen > cutoff:
            continue
        out.append(expire(rec, cfg, at))
    return out


def merge_sessions(records, cfg, at=None):
    """Pick the highest-priority state across live sessions.

    Ties break toward the most recently updated session, so the window follows
    whichever terminal you touched last.
    """
    at = now() if at is None else at
    best = None
    for rec in records or []:
        state = rec.get("state", "idle")
        if state not in PRIORITY:
            state = "idle"
        try:
            updated = float(rec.get("updated", 0))
        except (TypeError, ValueError):
            updated = 0
        key = (PRIORITY[state], updated)
        if best is None or key > best[0]:
            best = (key, rec)
    if best is None:
        return {"state": "idle", "topic": None, "seed": 0,
                "project": None, "updated": at, "state_since": at, "sessions": 0}
    rec = dict(best[1])
    rec["sessions"] = len(records)
    return rec


def topics_path():
    """Find topics.json in the stable copy first, then the plugin layout."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(base_dir(), "web", "topics.json"),
        os.path.join(here, "..", "companion", "web", "topics.json"),
        os.path.join(here, "web", "topics.json"),
    ):
        cand = os.path.normpath(cand)
        if os.path.isfile(cand):
            return cand
    return None


_TOPICS_CACHE = {}


def load_topics():
    """Load the thought library, cached per process. Never raises."""
    path = topics_path()
    if not path:
        return {"order": ["default"],
                "topics": {"default": {"label": "Working", "keywords": [],
                                       "lines": ["Reading the request."]}},
                "hydration": [{"headline": "Drink some water.",
                               "sub": "One glass. Then back to it."}]}
    cached = _TOPICS_CACHE.get(path)
    if cached is not None:
        return cached
    doc = read_json(path, None)
    if not doc or "topics" not in doc:
        doc = {"order": ["default"],
               "topics": {"default": {"label": "Working", "keywords": [],
                                      "lines": ["Reading the request."]}},
               "hydration": [{"headline": "Drink some water.",
                              "sub": "One glass. Then back to it."}]}
    _TOPICS_CACHE[path] = doc
    return doc


def topic_label(key):
    doc = load_topics()
    entry = doc.get("topics", {}).get(key or "default")
    if not entry:
        return None
    return entry.get("label")


def thought_lines(key):
    doc = load_topics()
    entry = doc.get("topics", {}).get(key or "default") or {}
    lines = entry.get("lines") or []
    return [str(x) for x in lines if isinstance(x, str)] or ["Working on it."]


def pick_thought(key, seed, rotation=0):
    """Deterministic line choice: same seed and rotation always gives the same line."""
    lines = thought_lines(key)
    try:
        idx = (int(seed) + int(rotation)) % len(lines)
    except (TypeError, ValueError):
        idx = 0
    return lines[idx]


def hydration_message(seed, name=""):
    doc = load_topics()
    msgs = doc.get("hydration") or [{"headline": "Drink some water.",
                                     "sub": "One glass. Then back to it."}]
    try:
        idx = int(seed) % len(msgs)
    except (TypeError, ValueError):
        idx = 0
    msg = dict(msgs[idx])
    if name:
        msg["headline"] = "%s, time to drink water." % name
    return msg


def global_path():
    return os.path.join(base_dir(), "global.json")


def hydration_tick(gl, cfg, at=None, suppressed=False):
    """Advance the hydration timer.

    Single source of truth lives in one file so every session agrees. The server
    drives it when it is running; the status line drives it when it is not.

    Returns (active, new_global). new_global is None when nothing changed.
    """
    at = now() if at is None else at
    gl = dict(gl or {})
    interval = cfg["hydration_interval_minutes"] * 60
    display = cfg["hydration_display_seconds"]

    try:
        last_end = float(gl.get("last_hydration_end") or 0)
    except (TypeError, ValueError):
        last_end = 0
    started = gl.get("hydration_started")
    try:
        started = float(started) if started else None
    except (TypeError, ValueError):
        started = None

    if last_end <= 0:
        # First ever run: start counting from now, so no reminder fires instantly.
        gl["last_hydration_end"] = at
        gl["hydration_started"] = None
        return False, gl

    if suppressed:
        # Never interrupt a permission request. Push the next one a full interval out.
        if started is not None:
            gl["hydration_started"] = None
            gl["last_hydration_end"] = at
            return False, gl
        return False, None

    if started is not None:
        if at - started >= display:
            gl["hydration_started"] = None
            gl["last_hydration_end"] = at
            return False, gl
        return True, None

    if at - last_end >= interval:
        gl["hydration_started"] = at
        gl["hydration_seed"] = int(at) % 1000
        return True, gl

    return False, None
