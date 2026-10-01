---
name: open
description: Start the Clawd companion window beside the terminal. Use when the user asks to open, show or start the Clawd window or companion.
---

# Open the Clawd companion window

Start the server (if it is not already up) and open the page:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/companion/server.py" --serve --open
```

The command prints the URL, for example `http://127.0.0.1:4756/`. It binds
`127.0.0.1` only. If the configured port is busy it takes the next free one and
records it, so the printed URL is the authoritative one. Only one server runs at
a time: if one is already up, this reuses it rather than starting a second.

It blocks while serving. Run it in the background so the session stays usable,
and give the user the URL.

Tell the user:

- The window follows every Claude Code session on this machine, showing the
  highest-priority state across them.
- It keeps animating while a permission prompt is open, unlike the status line.
- Nothing leaves the machine. The page makes no external requests, and the
  payload carries only a state name, a topic label, a thought line, the project
  folder's name and timestamps. Prompt text is never stored or sent.

If they want it as a small always-visible window rather than a browser tab, set
**Companion window mode** to `app` in `/config`. That tries a Chromium-based
browser's app window and falls back to the default browser when none is found.

To stop it, use `/clawd-companion:stop`.
