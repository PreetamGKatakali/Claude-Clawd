---
name: stop
description: Stop the Clawd companion server. Use when the user asks to stop, close or shut down the Clawd window, companion or server.
---

# Stop the Clawd companion server

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/companion/server.py" --stop
```

This stops the local server and removes its port and pid files. The browser tab
stays open and stops updating; the user can close it.

This does **not** touch the status line. The status line reads the same state
files directly and keeps working with the server stopped, which is the point of
the file-based design. To remove the status line too, use
`/clawd-companion:uninstall`.

To check whether it is running:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/companion/server.py" --status
```
