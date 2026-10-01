"""Render Clawd into the Claude Code status line.

Reads the status line JSON on stdin, reads shared state from disk, prints the
sprite (two rows) or a single plain line. Always exits 0 and always prints
something: an empty output or a non-zero exit blanks the status line.

Terminal size cannot be probed from here because Claude Code captures stdout,
so the COLUMNS environment variable is read instead. Claude Code sets it before
running this script.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESET = "\033[0m"

PALETTE = {
    "B": (0xD9, 0x77, 0x57),   # body, arms and legs
    "G": (0x81, 0x8C, 0xF8),   # glass
    "W": (0xC7, 0xD2, 0xFE),   # water
    "S": (0xFB, 0xCF, 0x4F),   # sparkle
}

LABEL_COLORS = {
    "thinking": (0xB7, 0x94, 0xF6),
    "confirm":  (0xFB, 0xCF, 0x4F),
    "hydrate":  (0x81, 0x8C, 0xF8),
    "done":     (0x16, 0xA3, 0x4A),
    "idle":     (0xA8, 0xA8, 0xAE),
    "welcome":  (0xD9, 0x77, 0x57),
}

# A small Clawd: 14 x 4 pixels drawn as 7 x 2 text cells with quadrant block
# characters, two pixels across and two down per cell.
#
# Rules every frame obeys (the tests enforce both):
#   * the four pixels in one cell are never two different colours, so each
#     cell needs a foreground colour only and never a background colour
#     (Terminal.app uses reverse video instead, see render_cells_gapfree);
#   * the eyes are holes, not a dark colour, so they read on any theme.
# The terminal background shows through holes, which is what makes the face.
BASE = [
    "..BBBBBBBBBB..",
    "..BB.BBBB.BB..",
    "BBBBBBBBBBBBBB",
    "..B.B....B.B..",
]
BLINK = list(BASE)     # eyes shut for one frame
BLINK[1] = "..BBBBBBBBBB.."

THINK_A = list(BASE)   # eyes glance left
THINK_A[1] = "..B.BBBB.BBB.."
THINK_B = list(BASE)   # eyes glance right
THINK_B[1] = "..BBB.BBBB.B.."

ARM_UP = [             # right arm raised
    "..BBBBBBBBBB.B",
    "..BB.BBBB.BBBB",
    "BBBBBBBBBBBB..",
    "..B.B....B.B..",
]
HAPPY = [              # both arms up, a cheer
    "B.BBBBBBBBBB.B",
    "BBBB.BBBB.BBBB",
    "..BBBBBBBBBB..",
    "..B.B....B.B..",
]
DONE_B = list(BASE)    # arms down, sparkles in the corners
DONE_B[0] = "S.BBBBBBBBBB.S"

# Hydration: a glass of water to Clawd's right. Eight extra pixels.
_GLASS_A = ["...G..G.", "...GWWG.", "...GWWG.", "...GWWG."]
_GLASS_B = ["...G..G.", "...G..G.", "...GWWG.", "...GWWG."]
HYD_A = [BASE[i] + _GLASS_A[i] for i in range(4)]
HYD_B = [BASE[i] + _GLASS_B[i] for i in range(4)]

# Hello on a fresh start: the right hand waves, raised then tipped outward.
# Both frames are 16 pixels wide so the label beside them stays put.
WAVE_A = [row + ".." for row in ARM_UP]
WAVE_B = [
    "..BBBBBBBBBB..BB",
    "..BB.BBBB.BB.BB.",
    "BBBBBBBBBBBBBB..",
    "..B.B....B.B....",
]

FRAMES = {
    "idle":     [BASE, BASE, BLINK],      # 3 second cycle
    "thinking": [THINK_A, THINK_B],
    "confirm":  [ARM_UP, BASE],
    "hydrate":  [HYD_A, HYD_B],
    "done":     [HAPPY, DONE_B],
    "welcome":  [WAVE_A, WAVE_B],
}

# Quadrant characters indexed by (top-left, top-right, bottom-left,
# bottom-right) as a 4 bit number, most significant bit first.
QUADRANTS = [
    " ", "▗", "▖", "▄", "▝", "▐", "▞", "▟",
    "▘", "▚", "▌", "▙", "▀", "▜", "▛", "█",
]
SPRITE_ROWS = 2

SAFETY_MARGIN = 2
MIN_COLUMNS_FOR_SPRITE = 32


def frame_for(state, tick):
    """Pick the grid for this state at this whole-second tick."""
    frames = FRAMES.get(state) or FRAMES["idle"]
    return frames[int(tick) % len(frames)]


# --- color -------------------------------------------------------------------

def _cube(v):
    """Map one 0-255 channel onto the xterm 6 level cube."""
    levels = (0, 95, 135, 175, 215, 255)
    best, bestd = 0, 1 << 30
    for i, lv in enumerate(levels):
        d = abs(lv - v)
        if d < bestd:
            best, bestd = i, d
    return best


def to_256(rgb):
    """Nearest xterm-256 index, considering both the cube and the gray ramp."""
    r, g, b = rgb
    ci = 16 + 36 * _cube(r) + 6 * _cube(g) + _cube(b)
    levels = (0, 95, 135, 175, 215, 255)
    cr, cg, cb = levels[_cube(r)], levels[_cube(g)], levels[_cube(b)]
    cube_err = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2

    avg = (r + g + b) // 3
    gi = int(round((avg - 8) / 10.0))
    gi = max(0, min(23, gi))
    gv = 8 + 10 * gi
    gray_err = (gv - r) ** 2 + (gv - g) ** 2 + (gv - b) ** 2
    if gray_err < cube_err:
        return 232 + gi
    return ci


def truecolor_supported(env=None):
    env = os.environ if env is None else env
    ct = (env.get("COLORTERM") or "").lower()
    return "truecolor" in ct or "24bit" in ct


def fg(rgb, truecolor):
    if truecolor:
        return "\033[38;2;%d;%d;%dm" % rgb
    return "\033[38;5;%dm" % to_256(rgb)


# --- sprite ------------------------------------------------------------------

def render_cells(grid, truecolor=True):
    """Encode a pixel grid as text rows, one cell per 2 x 2 pixel block.

    Each cell is drawn with one quadrant character in one foreground colour.
    No background colour is ever emitted. If a cell somehow held two colours
    (the frames never do) the first one found wins.
    """
    rows = []
    width = len(grid[0])
    for r in range(0, len(grid), 2):
        top = grid[r]
        bottom = grid[r + 1] if r + 1 < len(grid) else "." * width
        out = []
        for c in range(0, width, 2):
            px = (top[c], top[c + 1] if c + 1 < width else ".",
                  bottom[c], bottom[c + 1] if c + 1 < width else ".")
            bits, colour = 0, None
            for k in px:
                bits <<= 1
                if k in PALETTE:
                    bits |= 1
                    colour = colour or PALETTE[k]
            if bits:
                out.append(fg(colour, truecolor) + QUADRANTS[bits] + RESET)
            else:
                out.append(" ")
        rows.append("".join(out))
    return rows


# --- gap-free sprite for Terminal.app ----------------------------------------
#
# Terminal.app draws block characters from the font instead of filling the
# cell. In its default font (SF Mono Terminal) a block glyph covers only about
# 84% of the row height, leaving an empty strip at the top of every row that
# tore the sprite into two bands. A background colour does fill the whole
# cell, strip included. So where the strip should be body-coloured the cell is
# drawn in reverse video: the cell background takes the body colour and the
# glyph marks the empty quadrants in the terminal's own background colour.
# No colour is guessed for the terminal background; reverse video swaps in the
# terminal's own.

REVERSE = "\033[7m"


def gapfree_wanted(env=None):
    env = os.environ if env is None else env
    return env.get("TERM_PROGRAM") == "Apple_Terminal"


def _lit(grid, r, c):
    return 0 <= r < len(grid) and 0 <= c < len(grid[r]) and grid[r][c] in PALETTE


def tall_eyes(grid):
    """Grow each eye hole up one pixel, inside the head only.

    A one pixel eye fills only the lower part of its row in Terminal.app and
    reads as a dot; two pixels make the tall eye the other terminals show.
    """
    g = [list(row) for row in grid]
    for c in range(len(g[0])):
        if g[1][c] == "." and g[0][c] == "B" and g[2][c] == "B":
            g[0][c] = "."
    return ["".join(row) for row in g]


def _fill_strip(grid, r, c):
    """Should the strip at the top of the cell at pixel (r, c) be filled?

    Yes when some half has body there (its top pixel and the one above it are
    lit), unless a half is an open gap, empty in this cell and below it, where
    a filled strip would float as a stray sliver.
    """
    want, gap = False, False
    for cc in (c, c + 1):
        top = _lit(grid, r, cc)
        if top and (r == 0 or _lit(grid, r - 1, cc)):
            want = True
        if not top and not _lit(grid, r + 1, cc) and not _lit(grid, r + 2, cc):
            gap = True
    return want and not gap


def render_cells_gapfree(grid, truecolor=True):
    """Like render_cells, but with no strips between rows in Terminal.app."""
    grid = tall_eyes(grid)
    rows = []
    width = len(grid[0])
    for r in range(0, len(grid), 2):
        out = []
        for c in range(0, width, 2):
            px = ((r, c), (r, c + 1), (r + 1, c), (r + 1, c + 1))
            bits, colour = 0, None
            for p in px:
                bits <<= 1
                if _lit(grid, *p):
                    bits |= 1
                    colour = colour or PALETTE[grid[p[0]][p[1]]]
            if not bits:
                out.append(" ")
            elif _fill_strip(grid, r, c):
                out.append(fg(colour, truecolor) + REVERSE + QUADRANTS[15 ^ bits] + RESET)
            else:
                out.append(fg(colour, truecolor) + QUADRANTS[bits] + RESET)
        rows.append("".join(out))
    return rows


def render_sprite(grid, truecolor=True, gapfree=False):
    """Four pixel rows become two text rows."""
    if gapfree:
        return render_cells_gapfree(grid, truecolor)
    return render_cells(grid, truecolor)


# --- the status line ---------------------------------------------------------

def build_lines(state, topic_label, thought, tick, truecolor, columns, message=None,
                ready="ready", gapfree=False, sub=None):
    """Assemble the two sprite rows plus the text column beside them."""
    grid = frame_for(state, tick)
    rows = render_sprite(grid, truecolor, gapfree)

    label_rgb = LABEL_COLORS.get(state, LABEL_COLORS["idle"])
    if state == "thinking":
        label = "thinking"
        if topic_label:
            label = "thinking · " + topic_label.lower()
        dots = "." * (int(tick) % 3 + 1)
        second = (thought or "") + dots
    elif state == "confirm":
        label, second = "needs your OK", (message or "")
    elif state == "hydrate":
        label, second = (message or "Time to Drink water!"), (sub or "")
    elif state == "done":
        label, second = "done", ""
    elif state == "welcome":
        label, second = (message or "hey! welcome"), ""
    else:
        label, second = ready, ""

    # Keep every row inside the terminal. Claude Code may pad the status line,
    # so leave a small safety margin rather than filling the last column.
    width = (len(grid[0]) + 1) // 2
    budget = max(4, columns - width - 2 - SAFETY_MARGIN)
    label, second = _fit(label, budget), _fit(second, budget)

    text = [fg(label_rgb, truecolor) + label + RESET, second]
    # Claude Code strips leading spaces from each status line row, which
    # shifted rows that begin with a blank cell and tore the sprite apart.
    # Observed on v2.1.285: a row that begins with an escape code keeps its
    # spaces, so every row starts with a reset.
    out = []
    for i in range(SPRITE_ROWS):
        t = text[i]
        out.append(RESET + rows[i] + ("  " + t if t else ""))
    return out


def _fit(text, budget):
    """Cut plain text to budget visible columns, marking the cut."""
    if len(text) <= budget:
        return text
    return text[:max(0, budget - 1)] + "…"


def text_line(state, topic_label, thought, message=None, ready="ready"):
    """The one line, no-escape-codes fallback."""
    if state == "thinking":
        return "clawd: thinking · " + (topic_label or "working").lower()
    if state == "confirm":
        return "clawd: needs your OK"
    if state == "hydrate":
        return "clawd: " + (message or "Time to Drink water!")
    if state == "done":
        return "clawd: done"
    if state == "welcome":
        return "clawd: " + (message or "hey! welcome")
    return "clawd: " + ready


def chained_output(cfg, raw):
    """Run a previously configured status line command and return its rows.

    Setup offers to chain rather than replace. Keep this cheap: a slow chained
    command delays every refresh, and Claude Code cancels a script still running
    when the next update arrives.
    """
    cmd = cfg.get("chain_command")
    if not cmd:
        return []
    try:
        import subprocess
        proc = subprocess.run(
            cmd, shell=True, input=raw, capture_output=True, text=True, timeout=1.5)
        out = (proc.stdout or "").rstrip("\n")
        return out.split("\n") if out else []
    except Exception:
        return []


def main():
    try:
        import json
        import clawd_common as common

        raw = ""
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
        payload = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}

        cfg = common.load_config()
        if cfg["status_style"] == "off":
            sys.stdout.write("\n")
            return 0

        at = common.now()
        tick = int(at)
        session_id = payload.get("session_id") or "unknown"
        stored = common.read_json(common.session_path(session_id), {})
        rec = common.expire(stored, cfg, at)
        state = rec.get("state", "idle")
        ready = common.ready_phrase(stored, at, common.model_family(payload))
        welcome = common.welcoming(rec, at)

        # Hydration is global. The server owns the timer when it is up; this
        # script only advances it when the server is not running.
        message = sub = None
        gl = common.read_json(common.global_path(), {})
        server_live = _server_alive(common, gl, at)
        if not server_live:
            active, new_gl = common.hydration_tick(
                gl, cfg, at, suppressed=(state == "confirm"))
            if new_gl is not None:
                try:
                    common.write_atomic(common.global_path(), json.dumps(new_gl))
                except Exception:
                    pass
                gl = new_gl
        else:
            active = bool(gl.get("hydration_started")) and state != "confirm"

        if active and state != "confirm":
            state = "hydrate"
            water = common.hydration_message(
                gl.get("hydration_seed", 0), common.user_name(cfg))
            message, sub = water.get("headline"), water.get("sub")

        topic_label = common.topic_label(rec.get("topic")) if rec.get("topic") else None
        thought = common.pick_thought(
            rec.get("topic"), rec.get("seed", 0), rotation=int(at // 4))

        if welcome and state == "idle":
            state, message = "welcome", common.welcome_text(common.user_name(cfg))

        if state == "confirm":
            message = (rec.get("project") or "") and (rec["project"] + " · permission request")

        no_color = bool(os.environ.get("NO_COLOR"))
        try:
            columns = int(os.environ.get("COLUMNS") or 0)
        except (TypeError, ValueError):
            columns = 0

        # Chained status line: run whatever was configured before us on the
        # same stdin and print its rows above Clawd's.
        prefix = chained_output(cfg, raw)

        style = cfg["status_style"]
        if no_color or style == "text" or (columns and columns < MIN_COLUMNS_FOR_SPRITE):
            line = text_line(state, topic_label, thought, message, ready)
            if columns:
                line = _fit(line, max(1, columns - SAFETY_MARGIN))
            lines = [line]
        else:
            lines = build_lines(state, topic_label, thought, tick,
                                truecolor_supported(), columns or 80, message, ready,
                                gapfree_wanted(), sub)
        sys.stdout.write("\n".join(prefix + lines) + "\n")
        return 0
    except Exception:
        # Always print something: a blank status line is worse than a stale one.
        try:
            sys.stdout.write("clawd: ready\n")
        except Exception:
            pass
        return 0


def _server_alive(common, gl, at):
    """The server heartbeats into global.json; treat it as up if recent."""
    try:
        beat = float(gl.get("server_heartbeat") or 0)
    except (TypeError, ValueError):
        return False
    return (at - beat) < 5


if __name__ == "__main__":
    main()
    sys.exit(0)
