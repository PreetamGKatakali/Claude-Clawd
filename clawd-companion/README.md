# clawd-companion

An animated mascot that reacts to what Claude Code is doing, in two places:

- **A companion window** beside your terminal — a small local page with a smooth
  animated SVG. This is the primary surface, and the only one that keeps moving
  while a permission prompt is open.
- **A status line sprite** in the terminal — the same character as pixel art,
  a small two-row Clawd drawn with colored quadrant block characters, about one frame per second.

Three behaviors:

| | |
|---|---|
| **Thinking** | When you submit a prompt, Clawd plays a thinking animation with a thought bubble matched to the topic of your prompt. |
| **Hydration** | Every 45 minutes (configurable), Clawd holds a glass of water and reminds you to drink. |
| **Needs your OK** | When Claude Code wants approval, Clawd switches to an attention animation. |

Everything runs on this machine. No network calls, no API tokens, no telemetry.
No emoji in the UI or the terminal output.

## How your prompt is handled

The prompt is read inside the hook process, scored against a keyword table, and
discarded. What gets written to disk is a topic key (`debugging`), a random
integer seed, the project folder's **basename**, and timestamps. There is no log
of prompt text anywhere, and no model call is made to classify it.

The window's `/state` payload is built field by field from an allowlist, so it
cannot leak tool inputs or absolute paths even if a state file somehow contained
them. There is a test that asserts this (`test_prompt_text_is_never_written`).

One deliberate addition to that allowlist: your **name**, if you gave one, so the
window can greet you. It is cleaned first (letters, digits, spaces, `-`, `'`
and `.` only, at most 20 characters), comes only from your own settings, and
like the rest of the payload is served on 127.0.0.1 alone.

## Install

### From GitHub

The repository is private, so this works only for accounts with access to it,
through your normal git login (SSH key or HTTPS credentials):

```bash
claude plugin marketplace add git@github.com:PreetamGKatakali/Claude-Clawd.git
claude plugin install clawd-companion@clawd-local
```

Then run `/clawd-companion:setup` inside a session (see below). To pick up a new
version after a push: `claude plugin marketplace update clawd-local` and
`claude plugin update clawd-companion@clawd-local`, then restart Claude Code.

### Locally, so it runs in every session

The status line is a user setting, but the hooks that drive it belong to the
plugin. If the plugin is not loaded, nothing updates the state and the status
line sits on "ready" forever. Install it from the local marketplace in the
parent directory so every plain `claude` session loads it:

```bash
cd ..   # the directory holding .claude-plugin/marketplace.json
claude plugin marketplace add ./
claude plugin install clawd-companion@clawd-local
```

The install copies the plugin, so after you edit the source run
`claude plugin marketplace update clawd-local` and
`claude plugin update clawd-companion@clawd-local`, then restart Claude Code.

For a one-off session without installing, `claude --plugin-dir ./clawd-companion`.

Then, inside a session:

```
/clawd-companion:setup     # copies assets, installs the status line
/clawd-companion:open      # starts the server and opens the window
```

`setup` backs up `~/.claude/settings.json` before touching it, and only ever
writes the `statusLine` key. If you already have a status line it stops and asks
whether to **replace** it (saved, restorable) or **chain** onto it (your command
still runs, on the same stdin, with its rows above Clawd's).

### From a marketplace

Add a `.claude-plugin/marketplace.json` to a repo that contains this directory,
then `/plugin marketplace add <owner>/<repo>` and `/plugin install clawd-companion`.
See the [marketplace reference](https://code.claude.com/docs/en/plugins/marketplace-reference).

### Removing it

```
/clawd-companion:stop
/clawd-companion:uninstall
```

`uninstall` restores whatever status line you had before, or removes the key if
you had none. It keeps `~/.claude/clawd-companion/` unless you pass `--purge`.

## Why Python, and what about Windows

One runtime, standard library only: no `pip`, no `npm`, nothing to install and
nothing to keep up to date. Python 3 ships with macOS and essentially every
Linux distribution, and the status line budget is comfortable — measured median
**24.5 ms** per run on Python 3.9.6, against a self-imposed 100 ms budget.

The hooks use **exec form** (`command: "python3"` plus an `args` array), so paths
with spaces need no quoting and no shell is involved at all.

**Windows caveat:** the hooks invoke `python3`. The python.org installer provides
`python.exe` and the `py` launcher but not always `python3.exe`; the Microsoft
Store build does provide `python3.exe`. If hooks silently do nothing on Windows,
check that `python3 --version` works in your shell, and if it does not, edit
`hooks/hooks.json` to use `python` instead. This is listed as UNVERIFIED below
because it was not tested on Windows.

## Configuration

Set these in `/config` under the plugin, or with `/plugin`. Hooks read them as
`CLAUDE_PLUGIN_OPTION_<KEY>` and mirror them into
`~/.claude/clawd-companion/config.json` on every run, because the server and the
status line are separate processes that never receive those variables.

| Option | Type | Default | What it does |
|---|---|---|---|
| `hydration_interval_minutes` | number, 1–1440 | `45` | Minutes between reminders, measured from the **end** of the last one |
| `hydration_display_seconds` | number, 3–300 | `20` | How long each reminder stays up |
| `confirm_ttl_minutes` | number, 1–120 | `10` | Give up on an unresolved "needs your OK" after this long |
| `session_idle_timeout_minutes` | number, 1–1440 | `30` | Forget a session with no events for this long |
| `status_style` | `sprite` \| `text` \| `off` | `sprite` | A small two-row Clawd, one plain line, or nothing |
| `window_mode` | `tab` \| `app` | `tab` | Default browser, or a Chromium app-style window |
| `auto_open` | boolean | `false` | Start the server and open the window at session start |
| `port` | number, 1024–65535 | `4756` | Preferred port on 127.0.0.1; the next free one is used if busy |
| `sound_enabled` | boolean | `false` | Show the "Enable sound" button. Still needs a click and browser permission |
| `display_name` | text | empty | Your name, for "hey <name>, welcome!" and "Time to Drink water, <name>!" Wins over the name setup asked for |

Values out of range are clamped rather than rejected, and a missing or corrupt
`config.json` falls back to these defaults.

**If the sprite looks wrong in your terminal**, set `status_style` to `text`.
Multi-row output with escape codes is the most fragile part of this plugin, and
that switch is one flip away. It also falls back to one plain line automatically
when `NO_COLOR` is set or when `COLUMNS` is under 32.

**Your name.** Setup asks what Clawd should call you and saves it in
`config.json` (change it with `install.py name --set NAME` or `--clear`). The
`display_name` option overrides it. With a name, the hello reads "hey preetam,
welcome!" and the water reminder "Time to Drink water, preetam!"; without one,
the text stays generic ("Time to Drink water!"). The line under the water
headline is one of the short tips from `companion/web/topics.json`.

**Hello on launch.** When you start `claude` fresh, Clawd waves and says
"hey! welcome" (or "hey <name>, welcome!") for 10 seconds, then the idle phrases begin. It keys off the
`source: "startup"` field of the SessionStart hook (seen in live payloads), so
`--resume`, `--continue`, `/clear` and compaction do not wave. Sending a prompt
ends the hello early.

**Idle phrases.** While a session is idle the label shows a new phrase every 5
seconds, looping, and the list follows the session's model: reasoning lines on
**Opus** ("ready for a hard problem", "weighing the tradeoffs", ...), coding
jokes on **Sonnet** ("ready for next task", "tests green, mood green", ...), and
low, lazy ones on **Haiku** ("ready... ish", "five more minutes", ...). A model
it does not recognise gets the Sonnet list. The model comes from the `model`
field Claude Code sends the status line on every refresh, so a `/model` switch
shows within about a second. The lists are `READY_BY_FAMILY` in
`scripts/clawd_common.py`; Sonnet's is `READY_PHRASES`. The companion window
does not know the model and always uses the Sonnet list (it keeps a copy in
`companion/web/app.js`; a test checks they match). There is no option for it:
edit the lists and reinstall. It relies on the `refreshInterval: 1` that setup
writes into your `statusLine`; without it the label only changes when Claude
Code happens to refresh the status line.

## Swapping in your own art

Replace `companion/web/clawd.svg` with your own drawing and keep these ids.
`app.js` injects the file inline so the stylesheet can animate its parts.

| id | What drives it |
|---|---|
| `#clawd` | Whole character. Sways while thinking, hops when done |
| `#body` | Torso |
| `#legs` | The four legs |
| `#arm-left` | Left arm |
| `#arm-right` | Right arm. Waves during "needs your OK" |
| `#eyes` | Eye group. Shifts up-left while thinking |
| `#eye-left`, `#eye-right` | Blink individually when idle; hidden in hydrate and done |
| `#badge` | The gold disc for "needs your OK". The `?` glyph is an HTML layer, not part of the SVG |
| `#glass` | Water glass. Hidden unless hydrating; tilts to drink |
| `#water` | Water level inside the glass. Drops as Clawd drinks |
| `#drops` | Falling droplets |
| `#sparkle` | Gold sparkles on the done state |

Two helpers are not part of the contract but are used by the stylesheet:
`#clawd-breathe` (the group that breathes) and `#clawd-shadow` (the ground
shadow that shrinks on the hop). `.eye-happy` paths are the curved eyes shown in
hydrate and done. Keep them or delete them; the card still works without them.

The thought bubble, the caption, the badge glyph and the expanding rings are
**HTML layers over the SVG**, not part of it, so your art only has to be the
character.

## Troubleshooting

**The status line never leaves "ready for next task".** While idle it should
step through a few coding jokes, one every 5 seconds. If it never changes, or
never shows "thinking" either, the plugin is not loaded in that session, so no
hook writes state. Check with `claude plugin list` (you want
`clawd-companion@clawd-local` with `Status: ✔ enabled`) and restart Claude Code.

**Clawd's rows look shifted against each other.** Claude Code strips leading
spaces from each status line row (observed on v2.1.285). Every row the script
prints starts with an invisible reset code so its leading blanks survive; if you
chain another status line in front, keep that in mind for its rows too.

**Clawd has thin dark lines between its rows.** Your terminal's line spacing is
above 1.0, which leaves a gap between text rows that no character can fill. Set
line spacing to 1.0 in your terminal's profile or font settings. The sprite never uses background colours, so a terminal that
drops them in the status line does not affect it.

**Clawd doesn't react while I type.** That's expected. Claude Code has no hook
or status line trigger for keystrokes in the input box. The first signal is
`UserPromptSubmit`, when you press Enter.

**The status line is blank.** In order of likelihood:

1. **The folder is not trusted yet.** `statusLine` runs under the same workspace
   trust rule as hooks. Until you accept the trust dialog it stays blank and
   `claude --debug` logs `Status line command skipped: workspace trust not accepted`.
2. **`disableAllHooks` is set.** Outside managed settings, only a managed
   `statusLine` runs; with none, the status line is disabled entirely.
3. **Your organization sets `allowManagedHooksOnly`.** Your custom status line
   disappears with no warning. Only a `statusLine` from managed settings runs.
   Ask your administrator.
4. Run `python3 ~/.claude/clawd-companion/scripts/statusline.py < /dev/null` by
   hand. It should always print something and exit 0.

**The status line disappears when Claude asks for permission.** Expected. Claude
Code hides the status line during certain UI interactions, including the help
menu and permission prompts. This is exactly why the companion window exists —
it keeps animating. Verified by reading the docs; see the table below for the
part that still needs your eyes.

**The sprite is split into two bands in Terminal.app.** macOS Terminal draws
block characters from the font, and in its default font (SF Mono Terminal) a
block covers only about 84% of the row height, leaving a strip at the top of
each row. VS Code, Cursor and similar terminals draw blocks themselves and fill
the cell. When `TERM_PROGRAM` is `Apple_Terminal` the status line switches to a
gap-free renderer: the body becomes the cell background (reverse video) and the
glyph marks the empty pixels in your terminal's own background colour. The eyes
are also drawn two pixels tall there, since a one pixel eye fills only the
bottom of its row. A faint line can remain under the gaps between the legs: the
font leaves a sliver at the bottom of each row too. Other terminals get exactly
the same output as before.

**The sprite shows blocks, question marks or mojibake.** Your terminal font has
no quadrant block glyphs (U+2580 to U+259F). Switch `status_style` to `text`.

**Colors look flat.** Truecolor is used when `COLORTERM` contains `truecolor` or
`24bit`, otherwise the nearest xterm-256 color. `NO_COLOR` forces plain text.

**The window says "no session".** No live session has reported yet. Submit a
prompt. Sessions are forgotten after `session_idle_timeout_minutes`.

**Port already in use.** The server takes the next free port and writes it to
`~/.claude/clawd-companion/port`. The URL printed by `/clawd-companion:open` is
the authoritative one.

**Clawd is stuck on "thinking".** A session killed mid-turn never sends `Stop`.
It self-clears after six hours, or immediately on your next prompt. Deleting the
file in `~/.claude/clawd-companion/state/` also works.

## Security notes

- **Hook safety.** Every hook replaces `stdout` with `/dev/null` before doing
  anything else, exits 0 unconditionally via `os._exit(0)`, swallows every
  exception, and never emits a permission decision. This matters because a
  `UserPromptSubmit` hook's stdout is injected into Claude's context and a
  non-zero exit blocks the prompt.
- **The server binds `127.0.0.1` only** and validates the `Host` header against
  `127.0.0.1`, `localhost` and `::1`, so a hostile page cannot reach it by DNS
  rebinding. There are no CORS headers at all.
- **There are no write endpoints.** `POST`, `PUT`, `DELETE` and `HEAD` return
  405. Static paths are contained to the web directory, so `..` cannot escape.
- **No prompt text, ever.** See "How your prompt is handled" above.
- **Request logging is off**, so paths and timings do not reach any log.
- **State is written atomically** (temp file then `os.replace`), so a reader
  never sees a half-written file.
- **State lives in `~/.claude/clawd-companion/`**, not in the plugin directory,
  because `CLAUDE_PLUGIN_ROOT` moves on every plugin update. `CLAUDE_CONFIG_DIR`
  is honored.
- **Fonts are bundled, not fetched.** Geist and Geist Mono come from
  `vercel/geist-font` under the SIL Open Font License 1.1, which permits
  redistribution with software. The license ships at `companion/web/fonts/LICENSE.txt`.
  The page makes no external request of any kind.
- **No Anthropic artwork is used.** `clawd.svg` is original to this plugin.

## Verified vs UNVERIFIED

Verified against the docs at `code.claude.com/docs` and against Claude Code
**2.1.277** on macOS (Darwin 25.5.0), Python **3.9.6**.

| Claim | Status | How |
|---|---|---|
| `UserPromptSubmit` delivers the prompt in a field named `prompt` | **Verified** | Logged a real payload from a live session; matches the docs' input example |
| `PermissionRequest` delivers `tool_name`, `tool_input`, `permission_suggestions`, and **no** `tool_use_id` | **Verified** | Logged a real payload from a denied `Write` |
| `PostToolUse` carries `tool_response` (not `tool_result`) | **Verified** | Live payload |
| `PermissionDenied` and `SessionStart`/`SessionEnd` exist as events | **Verified** | Docs event table; `SessionStart`/`SessionEnd` also seen live |
| `notification_type` values include `permission_prompt` and `elicitation_dialog` | **Verified (docs)** | Matcher table in the hooks reference. Not observed live — headless runs do not raise an interactive permission prompt |
| Hooks receive `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_OPTION_*` | **Verified** | Dumped the hook process environment in a live session |
| `COLORTERM=truecolor` reaches the **hook** process | **Verified** | Same environment dump |
| `COLORTERM` reaches the **status line** process | **UNVERIFIED** | The status line runs as a separate process. The docs promise only `COLUMNS` and `LINES` there. The code degrades to 256-color if it is absent, so a wrong guess costs color fidelity, nothing else |
| `TERM_PROGRAM` reaches the status line process, and reverse video (`\e[7m`) survives Claude Code's rendering | **Verified** | Live run in a pty with `TERM_PROGRAM=Apple_Terminal`: the script logged the variable, and the terminal received `\e[7m` cells. The gap measurement (block glyph 1000 units, row 1193 units in SF Mono Terminal) was read from the font with Core Text. Other Terminal.app fonts or line spacing may differ |
| The status line input carries `model.id` and `model.display_name`, and they follow `/model` | **Verified** | Live run on 2026-10-01: `/model haiku`, `/model opus` and `/model sonnet` in a real session; the script received `claude-haiku-4-5-20251001`, `claude-opus-5-5` and `claude-sonnet-5-5` within about a second of each. Ids from Bedrock, Vertex or custom aliases were not tested; one without `opus`, `sonnet` or `haiku` in it gets the Sonnet phrases |
| Exec-form hooks (`args` present) run with no shell | **Verified (docs)** | Hooks reference, "Exec form and shell form" |
| A plugin's `settings.json` cannot set `statusLine` | **Verified (docs)** | Only `agent` and `subagentStatusLine` take effect |
| `statusLine.refreshInterval` minimum is 1 second | **Verified (docs)** | Status line reference |
| Status line updates debounce at 300 ms, and an in-flight script is cancelled | **Verified (docs)** | Status line reference. Note: the 100 ms figure is this project's own budget, not a documented threshold |
| `COLUMNS` and `LINES` are set before the script runs; `tput cols` does **not** work | **Verified (docs)** | Status line reference |
| The status line is hidden during permission prompts | **Verified (docs)** | "It temporarily hides during certain UI interactions, including the help menu and permission prompts." **Not yet confirmed by eye** — see the manual checklist |
| `statusLine` is gated by workspace trust, `disableAllHooks`, `allowManagedHooksOnly` | **Verified (docs)** | Status line reference |
| The companion window keeps animating during a permission prompt | **UNVERIFIED** | It is an ordinary browser page and nothing in Claude Code can reach it, so it should. Needs your eyes; the headless runs used here never raise an interactive prompt |
| Status line median runtime 24.5 ms (max 29.8 ms over 30 runs) | **Verified** | Measured on this machine |
| `claude plugin validate` passes with no warnings | **Verified** | Run against this directory |
| Windows behavior | **UNVERIFIED** | Not tested. See the Windows caveat above |
| Document Picture-in-Picture for always-on-top | **Not implemented** | Out of scope, listed as a stretch goal. Browser support was not investigated |
| Inline terminal images | **Not implemented** | Out of scope per the brief |

## Manual UI checklist

Screenshots and the demo GIF are **not included**. Chrome is installed on this
machine but `--headless=new --screenshot` would not complete a capture, and
neither Playwright nor ffmpeg is available, so there was no way to record them
honestly. One capture did succeed before the tooling stalled, and it earned its
keep: it caught the gold badge rendering in every state instead of only in
"needs your OK". That is fixed.

To capture them yourself once you have a working headless browser:

```bash
python3 companion/server.py --serve &
# then, per state, write a state file and screenshot http://127.0.0.1:4756/
```

Walk this list with the window open beside your terminal.

**Idle**
- [ ] Card is 320x460 with a 24px radius, a hairline border and a soft shadow
- [ ] Header reads `CLAWD` on the left, a pill with a **grey** dot and `IDLE` on the right
- [ ] Clawd breathes slowly and blinks every few seconds
- [ ] No glass, no gold badge, no sparkles, no rings
- [ ] Caption: `IDLE` / "ready for next task" / "Waiting for your next prompt."
- [ ] The headline changes every 5 seconds through the coding jokes, then loops
- [ ] On a fresh `claude` launch: the right arm waves and the headline reads "hey! welcome" for 10 seconds
- [ ] Footer: project folder name on the left, `Local only` on the right

**Thinking** — submit a prompt such as "fix the failing login test"
- [ ] Pill dot turns purple and pulses
- [ ] Thought bubble fades in, tinted, with a 1.5px purple border and three trailing circles pointing at Clawd
- [ ] Bubble eyebrow reads `DEBUGGING`; three dots pulse in sequence
- [ ] Two thought lines cross-fade about every 4 seconds, without flicker
- [ ] Clawd sways; eyes shift up and to the left
- [ ] Caption eyebrow reads `THINKING · DEBUGGING`

**Needs your OK** — trigger a permission prompt
- [ ] Pill dot turns gold and pulses
- [ ] Right arm waves
- [ ] Gold disc appears with a `?` and pulses — **and is absent in every other state**
- [ ] Two purple rings expand outward behind Clawd, offset from each other
- [ ] Caption: "Claude needs your OK." / "`<project>` · permission request."
- [ ] **The window keeps animating while the permission dialog is open.** This is the main thing the window exists for and the one claim still marked UNVERIFIED
- [ ] **The status line disappears while the dialog is open**, and comes back after you answer

**Hydration** — set `hydration_interval_minutes` to 1 and wait
- [ ] Clawd holds a glass, tilts it, and the water level drops
- [ ] Droplets fall, staggered
- [ ] Eyes become happy curves
- [ ] Caption rotates among five messages across reminders
- [ ] A reminder never appears while a permission request is pending

**Done**
- [ ] Clawd hops once; the ground shadow shrinks on the way up
- [ ] Green check badge and gold sparkles appear
- [ ] Settles back to idle after about 5 seconds

**Both themes and motion**
- [ ] Switch your OS between light and dark: background, card, text and accent all follow
- [ ] Turn on "reduce motion" in your OS: animations stop and the card stays readable
- [ ] Narrow the window to phone width: no horizontal scrolling

**Status line**
- [ ] A small two-row Clawd, rows lined up, animating about one frame per second
- [ ] Text to the right matches the state and carries no emoji
- [ ] `status_style: text` gives one plain line
- [ ] `NO_COLOR=1` gives one plain line
- [ ] A terminal under 32 columns falls back to one plain line

## Layout

```
clawd-companion/
├── .claude-plugin/plugin.json   manifest and userConfig
├── hooks/hooks.json             8 events, exec form
├── scripts/
│   ├── clawd_common.py          paths, config, atomic IO, priority merge, hydration clock
│   ├── classify.py              prompt -> topic, keyword heuristics only
│   ├── hook.py                  single entry point for every event
│   ├── statusline.py            sprite grids, quadrant renderer, text fallback
│   └── install.py               status line install/uninstall, asset copy
├── companion/
│   ├── server.py                127.0.0.1 only; /, /state, /events
│   └── web/                     index.html, style.css, app.js, clawd.svg, topics.json, fonts/
├── skills/{setup,open,stop,uninstall}/SKILL.md
├── tests/test_clawd.py          56 tests, injectable clock, no sleeping
└── README.md
```

## Running the tests

```bash
cd clawd-companion
python3 -m unittest discover -s tests -t .
```

The clock is injectable through `CLAWD_NOW`, and the state directory through
`CLAWD_HOME`, so the suite never sleeps and never touches your real state.
