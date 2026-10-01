"""Single entry point for every clawd-companion hook.

    python3 hook.py <EventName>

Contract, enforced by construction:
  * never writes to stdout (stdout is replaced before any other work)
  * never returns a permission decision of any kind
  * always exits 0, including on malformed input or an unknown event
  * swallows every exception
  * does no network I/O and stores no prompt text

A UserPromptSubmit hook's stdout is injected into Claude's context and a
non-zero exit blocks the prompt, so both are closed off here rather than merely
avoided.
"""

import os
import sys

# Close stdout before anything else can print to it.
_real_stdout = sys.stdout
try:
    sys.stdout = open(os.devnull, "w")
except Exception:
    class _Null(object):
        def write(self, *a, **k):
            return 0

        def flush(self):
            pass
    sys.stdout = _Null()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONFIRM_NOTIFICATIONS = (
    "permission_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
)


def _project_name(cwd):
    try:
        name = os.path.basename(os.path.normpath(str(cwd or "")))
        return name or None
    except Exception:
        return None


def _seed():
    """A seed with no relationship to the prompt text."""
    try:
        return int.from_bytes(os.urandom(2), "big")
    except Exception:
        return 0


def _auto_open(common, cfg):
    """Start the server detached if the user opted in. Must not block."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    server = os.path.normpath(os.path.join(here, "..", "companion", "server.py"))
    stable = os.path.join(common.base_dir(), "companion", "server.py")
    if os.path.isfile(stable):
        server = stable
    if not os.path.isfile(server):
        return
    try:
        devnull = open(os.devnull, "wb")
        kwargs = {"stdout": devnull, "stderr": devnull, "stdin": subprocess.DEVNULL}
        if hasattr(os, "setsid"):
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [sys.executable or "python3", server, "--serve", "--open"], **kwargs)
    except Exception:
        pass


def handle(event, payload, common):
    cfg = common.refresh_config_from_env()
    common.ensure_dirs()

    session_id = payload.get("session_id") or "unknown"
    path = common.session_path(session_id)
    rec = common.read_json(path, {})
    at = common.now()

    prev_state = rec.get("state", "idle")
    rec.setdefault("seed", _seed())
    rec["session_id"] = session_id
    rec["project"] = _project_name(payload.get("cwd")) or rec.get("project")
    rec["updated"] = at

    def to(state, topic=None, reseed=False):
        if rec.get("state") != state:
            rec["state_since"] = at
        rec["state"] = state
        if reseed:
            rec["seed"] = _seed()
        if topic is not None:
            rec["topic"] = topic

    if event == "SessionStart":
        rec.setdefault("state_since", at)
        to("idle")
        rec["topic"] = None
        rec["turn_active"] = False
        # Wave hello only when `claude` was just launched. Resume, /clear and
        # compaction also send SessionStart, with a different source.
        if payload.get("source") == "startup":
            rec["welcome_until"] = at + common.WELCOME_SECONDS
        else:
            rec.pop("welcome_until", None)
        if cfg.get("auto_open"):
            _auto_open(common, cfg)

    elif event == "UserPromptSubmit":
        import classify
        # The prompt is read here, classified, and never written anywhere.
        topic = classify.classify(payload.get("prompt"))
        to("thinking", topic=topic, reseed=True)
        rec["turn_active"] = True
        rec.pop("welcome_until", None)   # the hello is over once you start

    elif event in ("PermissionRequest",):
        to("confirm")
        rec["turn_active"] = True

    elif event == "Notification":
        if payload.get("notification_type") in CONFIRM_NOTIFICATIONS:
            to("confirm")
        else:
            return  # nothing to record

    elif event in ("PermissionDenied", "PostToolUse"):
        if prev_state == "confirm":
            to("thinking" if rec.get("turn_active") else "idle")

    elif event == "Stop":
        to("done")
        rec["turn_active"] = False

    elif event == "SessionEnd":
        try:
            os.unlink(path)
        except OSError:
            pass
        return

    else:
        return  # unknown event: record nothing

    import json
    common.write_atomic(path, json.dumps(rec))


def main():
    try:
        event = sys.argv[1] if len(sys.argv) > 1 else ""
        raw = ""
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
        payload = {}
        if raw:
            try:
                import json
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
        import clawd_common as common
        handle(event, payload, common)
    except Exception:
        pass
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    # os._exit avoids any chance of an atexit handler writing to stdout.
    main()
    os._exit(0)
