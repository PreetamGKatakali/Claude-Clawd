---
name: setup
description: Install the Clawd companion status line and copy its assets to a stable directory. Use when the user asks to set up, install or repair clawd-companion.
---

# Set up clawd-companion

A plugin cannot set `statusLine` itself: only `agent` and `subagentStatusLine`
take effect from a plugin's `settings.json`. So the status line has to be
written into the user's own settings, which is what this skill does.

Work through these steps in order. Report what each one printed.

## 1. Copy the assets to a stable directory

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" copy
```

This copies `scripts/` and the web assets into `~/.claude/clawd-companion/`
(or `$CLAUDE_CONFIG_DIR/clawd-companion/`). The status line and the server must
run from a path that survives plugin updates, and `${CLAUDE_PLUGIN_ROOT}` does
not: it changes every time the plugin updates.

## 2. Look at what is already configured

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" status
```

Read the `status line` line of the output:

- **`(not set)`** — go straight to step 3 and run it without `--mode`.
- **already clawd-companion** — this is a repair. Run step 3 with `--mode replace`.
- **some other command** — stop and ask the user which they want, quoting the
  existing command back to them:
  - **replace** — Clawd takes over. The old value is saved and `/clawd-companion:uninstall` puts it back.
  - **chain** — the old command still runs, on the same stdin JSON, and its rows print above Clawd's.

  Never pick for them, and never overwrite an existing status line silently.

## 3. Install

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" install --mode replace
```

or `--mode chain`, or no `--mode` when nothing was set. The command backs up
`settings.json` first and prints the backup path. It merges: only the
`statusLine` key is touched, every other setting is left exactly as it was.

## 4. Ask what Clawd should call them

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" name
```

If `name in use` is already set, say so and skip this step unless they want to
change it. Otherwise ask one question: "What should Clawd call you? It's used
in the welcome and the water reminder, and stays on this machine." Offer the
printed `suggestion` as the default, and offer "no name" too. Then run one of:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" name --set "<their answer>"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" name --clear
```

Report the `name in use` line it prints back. They can change it later by
running this skill again, or with the **Your name** option in `/config`, which
wins over the name saved here.

## 5. Tell the user what to expect

- The status line appears after the **next** status line update. If it does not
  show up, it is almost always one of: the folder is not trusted yet (the status
  line stays blank until the workspace trust dialog is accepted),
  `disableAllHooks` is set, or the organization sets `allowManagedHooksOnly`.
  All three gate `statusLine` the same way they gate hooks.
- The status line is **hidden while a permission prompt is open**. That is
  Claude Code behavior, not a bug: it hides during certain UI interactions
  including the help menu and permission prompts. The companion window keeps
  animating, which is the reason the window exists.
- Run `/clawd-companion:open` to start the window.

If the user wants the plain one-line version instead of the sprite, tell them to
set **Status line style** to `text` in `/config`. Multi-row output with escape
codes is more prone to rendering glitches in some terminals, so that switch is
one flip away.
