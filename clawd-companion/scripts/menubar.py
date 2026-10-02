"""Build and start Clawd in the macOS menu bar. macOS only.

    python3 menubar.py start     build if needed, then start it (the SessionStart hook)
    python3 menubar.py stop      quit it
    python3 menubar.py build     rebuild it now and report
    python3 menubar.py status    built? running?

The app is compiled on this Mac from companion/menubar/ClawdBar.swift with the
system swiftc, into <stable dir>/menubar/, and signed ad hoc. Nothing is
downloaded. On Windows and Linux every command does nothing.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clawd_common as common  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.normpath(os.path.join(HERE, ".."))
APP_NAME = "Clawd Companion"
BUNDLE_ID = "local.clawd-companion.menubar"
DEFAULT_TERMINAL = "com.apple.Terminal"
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>%s</string>
<key>CFBundleName</key><string>%s</string>
<key>CFBundleExecutable</key><string>ClawdBar</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1</string>
<key>LSUIElement</key><true/>
</dict></plist>
""" % (BUNDLE_ID, APP_NAME)


def supported():
    return sys.platform == "darwin"


def menubar_dir():
    return os.path.join(common.base_dir(), "menubar")


def app_path():
    return os.path.join(menubar_dir(), APP_NAME + ".app")


def binary_path():
    return os.path.join(app_path(), "Contents", "MacOS", "ClawdBar")


def stamp_path():
    return os.path.join(menubar_dir(), "build-stamp")


def pid_path():
    return os.path.join(common.base_dir(), "menubar.pid")


def source_path():
    """The Swift source: the installed plugin's copy, else the stable copy."""
    for cand in (os.path.join(PLUGIN_ROOT, "companion", "menubar", "ClawdBar.swift"),
                 os.path.join(common.base_dir(), "companion", "menubar", "ClawdBar.swift")):
        if os.path.isfile(cand):
            return cand
    return None


def source_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read() + INFO_PLIST.encode("utf-8")).hexdigest()


def needs_build(src):
    if not os.path.isfile(binary_path()):
        return True
    try:
        with open(stamp_path()) as f:
            return f.read().strip() != source_hash(src)
    except OSError:
        return True


def have_compiler():
    """swiftc from the Command Line Tools or Xcode.

    /usr/bin/swiftc is a stub that pops up an install dialog when the tools are
    missing, so check xcode-select first and never touch the stub otherwise.
    """
    try:
        r = subprocess.run(["xcode-select", "-p"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return False
    return r.returncode == 0 and shutil.which("swiftc") is not None


def build(src):
    """Compile the app. Returns None on success, else a short reason."""
    if not have_compiler():
        return "swiftc not found: install the Xcode Command Line Tools (xcode-select --install)"
    macos = os.path.dirname(binary_path())
    os.makedirs(macos, exist_ok=True)
    with open(os.path.join(app_path(), "Contents", "Info.plist"), "w") as f:
        f.write(INFO_PLIST)
    tmp = binary_path() + ".new"
    try:
        r = subprocess.run(["swiftc", "-swift-version", "5", "-O", src, "-o", tmp],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    except Exception as exc:
        return "swiftc failed (%s)" % exc
    if r.returncode != 0:
        return "swiftc failed:\n" + r.stdout.decode("utf-8", "replace")[-2000:]
    os.replace(tmp, binary_path())
    # Ad hoc signature: no Apple account, it just keeps macOS from refusing to run it.
    try:
        subprocess.run(["codesign", "-s", "-", "--force", app_path()],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except Exception:
        pass
    common.write_atomic(stamp_path(), source_hash(src) + "\n")
    return None


def running_pid():
    try:
        with open(pid_path()) as f:
            pid = int(f.read().strip())
        # Only ours: a stale pid file may name a reused process number.
        return pid if common.pid_matches(pid, "ClawdBar") else None
    except Exception:
        return None


def terminal_id():
    """The app Claude Code runs in, for "Bring ... to front"."""
    cand = os.environ.get("__CFBundleIdentifier", "")
    return cand if _BUNDLE_RE.match(cand) else DEFAULT_TERMINAL


def server_path():
    for cand in (os.path.join(common.base_dir(), "companion", "server.py"),
                 os.path.join(PLUGIN_ROOT, "companion", "server.py")):
        if os.path.isfile(cand):
            return cand
    return None


def launch():
    args = ["open", "-g", "-a", app_path(), "--args",
            "--base", common.base_dir(),
            "--python", sys.executable or "python3",
            "--terminal", terminal_id()]
    srv = server_path()
    if srv:
        args += ["--server", srv]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def stop():
    pid = running_pid()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    try:
        os.unlink(pid_path())
    except OSError:
        pass
    return pid


def start(cfg):
    """Build if needed and start. Returns a short report line."""
    if not supported():
        return "menu bar: macOS only, nothing to do"
    if not cfg.get("menu_bar"):
        return "menu bar: off (turn on Menu bar Clawd in /plugin)"
    src = source_path()
    if not src:
        return "menu bar: ClawdBar.swift not found"
    rebuilt = False
    if needs_build(src):
        err = build(src)
        if err:
            return "menu bar: " + err
        rebuilt = True
    if running_pid():
        if not rebuilt:
            return "menu bar: already running"
        stop()  # replace the old build
    launch()
    return "menu bar: started"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if not supported():
        print("clawd-companion: the menu bar is macOS only")
        return 0
    common.ensure_dirs()
    if cmd == "start":
        print("clawd-companion " + start(common.load_config()))
    elif cmd == "stop":
        print("clawd-companion menu bar: " + ("stopped" if stop() else "not running"))
    elif cmd == "build":
        src = source_path()
        err = build(src) if src else "ClawdBar.swift not found"
        print("clawd-companion menu bar: " + (err or "built " + app_path()))
        return 1 if err else 0
    elif cmd == "status":
        print(json.dumps({
            "built": os.path.isfile(binary_path()),
            "running": bool(running_pid()),
            "enabled": bool(common.load_config().get("menu_bar")),
            "app": app_path(),
        }, indent=2))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
