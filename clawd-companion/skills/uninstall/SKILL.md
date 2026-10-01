---
name: uninstall
description: Remove the Clawd companion status line and restore the previous one. Use when the user asks to uninstall, remove or disable clawd-companion.
---

# Uninstall clawd-companion

## 1. Stop the server

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/companion/server.py" --stop
```

## 2. Restore the status line

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" uninstall
```

This backs up `settings.json` first, then either restores the status line that
was there before Clawd, or removes the `statusLine` key if there was none. Every
other setting is left untouched. If the configured status line is not Clawd's,
it is left alone and the command says so.

By default the state directory `~/.claude/clawd-companion/` is kept, so a later
`/clawd-companion:setup` picks the user's settings back up. Ask the user whether
they want it deleted; only if they say yes:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" uninstall --purge
```

## 3. Disable the plugin

Removing the status line does not stop the hooks. To stop those too, the user
disables or uninstalls the plugin itself through `/plugin`. Tell them that;
do not edit their `enabledPlugins` for them.
