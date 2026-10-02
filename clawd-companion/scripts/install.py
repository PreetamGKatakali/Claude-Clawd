"""Install, repair and remove the clawd-companion status line.

Run by the setup, stop and uninstall skills. Unlike the hooks, this is a normal
CLI: it prints a short human-readable report and uses exit codes.

    python3 install.py copy        copy scripts and web assets to the stable dir
    python3 install.py status      what is configured right now
    python3 install.py install --mode replace|chain
    python3 install.py uninstall   restore the previous statusLine
    python3 install.py name [--set NAME | --clear]   show, set or clear the greeting name
"""

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clawd_common as common  # noqa: E402

PLUGIN_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def settings_path():
    return os.path.join(common.config_dir(), "settings.json")


def backup_path():
    return os.path.join(common.base_dir(), "settings-backup-%d.json" % int(time.time()))


def stable_statusline():
    return os.path.join(common.base_dir(), "scripts", "statusline.py")


def read_settings():
    path = settings_path()
    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path) as f:
            raw = f.read()
        return json.loads(raw), raw
    except Exception as exc:
        raise SystemExit("clawd-companion: cannot parse %s (%s). Fix it first." % (path, exc))


def copy_assets():
    """Copy scripts and web assets into the stable directory.

    CLAUDE_PLUGIN_ROOT moves on every plugin update, so the status line and the
    server must run from a path that does not.
    """
    common.ensure_dirs()
    base = common.base_dir()
    pairs = [
        (os.path.join(PLUGIN_ROOT, "scripts"), os.path.join(base, "scripts")),
        (os.path.join(PLUGIN_ROOT, "companion", "web"), os.path.join(base, "web")),
        (os.path.join(PLUGIN_ROOT, "companion"), os.path.join(base, "companion")),
        # Swift source for the macOS menu bar; menubar.py compiles it.
        (os.path.join(PLUGIN_ROOT, "companion", "menubar"), os.path.join(base, "companion", "menubar")),
    ]
    copied = []
    for src, dst in pairs:
        if not os.path.isdir(src):
            continue
        for name in sorted(os.listdir(src)):
            s = os.path.join(src, name)
            if not os.path.isfile(s):
                continue
            if name.endswith((".pyc",)) or name.startswith("."):
                continue
            try:
                os.makedirs(dst, exist_ok=True)
                shutil.copy2(s, os.path.join(dst, name))
                copied.append(os.path.join(os.path.basename(dst), name))
            except OSError as exc:
                print("  could not copy %s (%s)" % (s, exc))
    # Fonts live in a subdirectory of web/.
    fsrc = os.path.join(PLUGIN_ROOT, "companion", "web", "fonts")
    if os.path.isdir(fsrc):
        fdst = os.path.join(base, "web", "fonts")
        try:
            os.makedirs(fdst, exist_ok=True)
            for name in sorted(os.listdir(fsrc)):
                s_ = os.path.join(fsrc, name)
                if os.path.isfile(s_):
                    shutil.copy2(s_, os.path.join(fdst, name))
                    copied.append(os.path.join("web", "fonts", name))
        except OSError:
            pass

    # The server imports clawd_common from a sibling scripts/ directory.
    try:
        os.makedirs(os.path.join(base, "companion", "scripts"), exist_ok=True)
        shutil.copy2(os.path.join(PLUGIN_ROOT, "scripts", "clawd_common.py"),
                     os.path.join(base, "companion", "scripts", "clawd_common.py"))
    except OSError:
        pass
    return copied


def cmd_copy(_args):
    copied = copy_assets()
    print("clawd-companion: copied %d files to %s" % (len(copied), common.base_dir()))
    return 0


def cmd_status(_args):
    settings, _ = read_settings()
    sl = settings.get("statusLine")
    print("settings file : %s" % settings_path())
    print("stable dir    : %s" % common.base_dir())
    print("status line   : %s" % (json.dumps(sl) if sl else "(not set)"))
    if sl and isinstance(sl, dict):
        cmd = sl.get("command", "")
        print("is clawd      : %s" % ("yes" if "clawd-companion" in str(cmd) else "no"))
    cfg = common.load_config()
    print("config        : %s" % json.dumps(cfg))
    saved = common.read_json(os.path.join(common.base_dir(), "previous-statusline.json"), None)
    print("saved previous: %s" % (json.dumps(saved) if saved else "(none)"))
    return 0


def cmd_install(args):
    settings, raw = read_settings()
    common.ensure_dirs()

    # 1. Always back up first.
    if raw is not None:
        bak = backup_path()
        common.write_atomic(bak, raw)
        print("backed up settings to %s" % bak)
    else:
        print("no existing settings.json; a new one will be created")

    existing = settings.get("statusLine")
    target = stable_statusline()
    if not os.path.isfile(target):
        copy_assets()

    if existing and isinstance(existing, dict) and "clawd-companion" not in str(existing.get("command", "")):
        if args.mode not in ("replace", "chain"):
            print("A status line is already configured:")
            print("  %s" % json.dumps(existing))
            print("Re-run with --mode replace or --mode chain.")
            return 2
        # Remember it so uninstall can restore it exactly.
        common.write_atomic(
            os.path.join(common.base_dir(), "previous-statusline.json"),
            json.dumps(existing, indent=2) + "\n")
        if args.mode == "chain":
            prev_cmd = existing.get("command")
            if not isinstance(prev_cmd, str) or not prev_cmd.strip():
                print("the existing status line has no command string; cannot chain")
                return 2
            cfg = common.read_json(common.config_path(), {})
            cfg["chain_command"] = prev_cmd
            common.write_atomic(common.config_path(), json.dumps(cfg, indent=2) + "\n")
            print("chaining onto: %s" % prev_cmd)
        else:
            cfg = common.read_json(common.config_path(), {})
            cfg.pop("chain_command", None)
            common.write_atomic(common.config_path(), json.dumps(cfg, indent=2) + "\n")
            print("replacing the previous status line (saved for uninstall)")
    elif existing:
        print("clawd-companion status line already installed; refreshing it")

    # 2. Merge, never clobber: only the statusLine key is touched.
    settings["statusLine"] = {
        "type": "command",
        "command": "python3 " + json.dumps(target),
        "refreshInterval": 1,
    }
    common.write_atomic(settings_path(), json.dumps(settings, indent=2) + "\n")
    print("wrote statusLine to %s" % settings_path())
    print("  command: %s" % settings["statusLine"]["command"])
    print("  refreshInterval: 1 second (the documented minimum)")
    return 0


def cmd_uninstall(args):
    settings, raw = read_settings()
    if raw is not None:
        bak = backup_path()
        common.write_atomic(bak, raw)
        print("backed up settings to %s" % bak)

    current = settings.get("statusLine")
    is_ours = isinstance(current, dict) and "clawd-companion" in str(current.get("command", ""))
    prev_file = os.path.join(common.base_dir(), "previous-statusline.json")
    previous = common.read_json(prev_file, None)

    if not is_ours and current:
        print("the configured status line is not clawd-companion; leaving it alone")
    elif previous:
        settings["statusLine"] = previous
        print("restored the previous status line: %s" % json.dumps(previous))
    elif "statusLine" in settings:
        del settings["statusLine"]
        print("removed the statusLine setting")
    else:
        print("no statusLine was set")

    common.write_atomic(settings_path(), json.dumps(settings, indent=2) + "\n")
    try:
        os.unlink(prev_file)
    except OSError:
        pass

    if args.purge:
        base = common.base_dir()
        try:
            shutil.rmtree(base)
            print("removed %s" % base)
        except OSError as exc:
            print("could not remove %s (%s)" % (base, exc))
    else:
        print("kept %s (pass --purge to delete it)" % common.base_dir())
    return 0


def login_name():
    """A suggestion only: the full name, else the login name, of this account."""
    try:
        import pwd
        entry = pwd.getpwuid(os.getuid())
        full = (entry.pw_gecos or "").split(",")[0].strip()
        first = full.split()[0] if full else ""
        return common.clean_name(first or entry.pw_name)
    except Exception:
        return common.clean_name(os.environ.get("USER") or os.environ.get("USERNAME") or "")


def cmd_name(args):
    path = common.config_path()
    cfg = common.read_json(path, {})
    if args.set is not None or args.clear:
        name = "" if args.clear else common.clean_name(args.set)
        if args.set is not None and not name:
            print("clawd-companion: that name has no usable characters; nothing changed")
            return 1
        if name:
            cfg["setup_name"] = name
        else:
            cfg.pop("setup_name", None)
        common.ensure_dirs()
        common.write_atomic(path, json.dumps(cfg, indent=2) + "\n")
    full = common.coerce_config(cfg)
    print("name in use   : %s" % (common.user_name(full) or "(none)"))
    print("from setup    : %s" % (common.clean_name(cfg.get("setup_name")) or "(none)"))
    print("plugin option : %s" % (common.clean_name(cfg.get("display_name")) or "(none)"))
    print("suggestion    : %s" % (login_name() or "(none)"))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="clawd-companion install")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("copy")
    sub.add_parser("status")
    pi = sub.add_parser("install")
    pi.add_argument("--mode", choices=["replace", "chain"], default=None)
    pu = sub.add_parser("uninstall")
    pu.add_argument("--purge", action="store_true")
    pn = sub.add_parser("name")
    pn.add_argument("--set", default=None)
    pn.add_argument("--clear", action="store_true")
    args = p.parse_args(argv)
    fn = {"copy": cmd_copy, "status": cmd_status, "install": cmd_install,
          "uninstall": cmd_uninstall, "name": cmd_name}.get(args.cmd)
    if not fn:
        p.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
