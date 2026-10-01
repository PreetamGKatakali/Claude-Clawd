"""Unit tests for clawd-companion. No sleeping: the clock is injected.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="clawd-test-")
        os.environ["CLAWD_HOME"] = self.home
        import clawd_common
        self.common = clawd_common
        clawd_common.ensure_dirs()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        os.environ.pop("CLAWD_HOME", None)
        os.environ.pop("CLAWD_NOW", None)

    def put(self, sid, **fields):
        rec = {"session_id": sid, "state": "idle", "seed": 1,
               "project": "proj", "state_since": 1000.0, "updated": 1000.0}
        rec.update(fields)
        self.common.write_atomic(self.common.session_path(sid), json.dumps(rec))
        return rec


class TestClassifier(Base):
    def test_known_topics(self):
        import classify
        cases = [
            ("fix the failing login test", "debugging"),
            ("refactor this module", "refactor"),
            ("add a unit test for the parser", "tests"),
            ("explain why this returns null", "explain"),
            ("commit this and push the branch", "git"),
            ("deploy to staging", "deploy"),
            ("rotate the auth token", "security"),
            ("update the readme", "docs"),
            ("this query is slow, optimize it", "performance"),
            ("tighten the button spacing in the css", "ui"),
            ("add an index on the users table", "data"),
        ]
        for prompt, expected in cases:
            self.assertEqual(classify.classify(prompt), expected, prompt)

    def test_edge_inputs(self):
        import classify
        self.assertEqual(classify.classify(""), "default")
        self.assertEqual(classify.classify("   "), "default")
        self.assertEqual(classify.classify(None), "default")
        self.assertEqual(classify.classify(12345), "default")
        self.assertEqual(classify.classify("hello"), "default")

    def test_very_long_prompt_is_a_big_task(self):
        import classify
        self.assertEqual(classify.classify("consider this carefully. " * 60), "big_task")

    def test_long_prompt_with_a_strong_topic_keeps_the_topic(self):
        import classify
        text = "fix the bug. the error is a crash. debug the failing traceback. " * 20
        self.assertEqual(classify.classify(text), "debugging")

    def test_is_deterministic(self):
        import classify
        self.assertEqual(classify.classify("fix the bug"), classify.classify("fix the bug"))

    def test_substring_does_not_match(self):
        import classify
        # "citest" must not match the "ci" keyword.
        self.assertEqual(classify.classify("rename citest to something"), "refactor")


class TestStatePriority(Base):
    def test_priority_order(self):
        p = self.common.PRIORITY
        self.assertGreater(p["confirm"], p["hydrate"])
        self.assertGreater(p["hydrate"], p["thinking"])
        self.assertGreater(p["thinking"], p["done"])
        self.assertGreater(p["done"], p["idle"])

    def test_confirm_wins_across_sessions(self):
        cfg = self.common.load_config()
        self.put("a", state="thinking", state_since=1900.0, updated=2000.0)
        self.put("b", state="confirm", project="other",
                 state_since=1900.0, updated=1500.0)
        merged = self.common.merge_sessions(
            self.common.live_sessions(cfg, 2000.0), cfg, 2000.0)
        self.assertEqual(merged["state"], "confirm")
        self.assertEqual(merged["project"], "other")
        self.assertEqual(merged["sessions"], 2)

    def test_an_aged_out_confirm_loses_to_a_live_thinking(self):
        cfg = self.common.load_config()
        self.put("a", state="thinking", state_since=1900.0, updated=2000.0)
        self.put("b", state="confirm", project="other",
                 state_since=1000.0, updated=1500.0)
        merged = self.common.merge_sessions(
            self.common.live_sessions(cfg, 2000.0), cfg, 2000.0)
        self.assertEqual(merged["state"], "thinking")

    def test_tie_breaks_to_most_recent(self):
        cfg = self.common.load_config()
        self.put("a", state="thinking", project="old", updated=1000.0)
        self.put("b", state="thinking", project="new", updated=1900.0)
        merged = self.common.merge_sessions(
            self.common.live_sessions(cfg, 2000.0), cfg, 2000.0)
        self.assertEqual(merged["project"], "new")

    def test_no_sessions_is_idle(self):
        cfg = self.common.load_config()
        merged = self.common.merge_sessions([], cfg, 5000.0)
        self.assertEqual(merged["state"], "idle")
        self.assertEqual(merged["sessions"], 0)

    def test_stale_session_is_dropped(self):
        cfg = self.common.load_config()
        self.put("old", state="confirm", updated=1000.0)
        at = 1000.0 + cfg["session_idle_timeout_minutes"] * 60 + 1
        self.assertEqual(self.common.live_sessions(cfg, at), [])


class TestExpiry(Base):
    def test_confirm_ttl(self):
        cfg = self.common.load_config()
        rec = {"state": "confirm", "state_since": 1000.0}
        ttl = cfg["confirm_ttl_minutes"] * 60
        self.assertEqual(self.common.expire(rec, cfg, 1000.0 + ttl - 1)["state"], "confirm")
        self.assertEqual(self.common.expire(rec, cfg, 1000.0 + ttl + 1)["state"], "idle")

    def test_done_becomes_idle_after_five_seconds(self):
        cfg = self.common.load_config()
        rec = {"state": "done", "state_since": 1000.0}
        self.assertEqual(self.common.expire(rec, cfg, 1004.0)["state"], "done")
        self.assertEqual(self.common.expire(rec, cfg, 1006.0)["state"], "idle")

    def test_corrupt_record_is_safe(self):
        cfg = self.common.load_config()
        self.assertEqual(self.common.expire({}, cfg, 1.0)["state"], "idle")
        self.assertEqual(self.common.expire(None, cfg, 1.0)["state"], "idle")
        self.assertEqual(
            self.common.expire({"state": "banana", "state_since": "x"}, cfg, 1.0)["state"],
            "idle")

    def test_corrupt_state_file_is_ignored(self):
        with open(self.common.session_path("bad"), "w") as f:
            f.write("{not json")
        cfg = self.common.load_config()
        self.assertEqual(self.common.live_sessions(cfg, 1.0), [])


class TestHydration(Base):
    def setUp(self):
        Base.setUp(self)
        self.cfg = self.common.coerce_config(
            {"hydration_interval_minutes": 45, "hydration_display_seconds": 20})

    def test_first_run_starts_the_clock_without_firing(self):
        active, gl = self.common.hydration_tick({}, self.cfg, 1000.0)
        self.assertFalse(active)
        self.assertEqual(gl["last_hydration_end"], 1000.0)

    def test_fires_after_the_interval(self):
        gl = {"last_hydration_end": 1000.0}
        active, _ = self.common.hydration_tick(gl, self.cfg, 1000.0 + 44 * 60)
        self.assertFalse(active)
        active, gl2 = self.common.hydration_tick(gl, self.cfg, 1000.0 + 45 * 60)
        self.assertTrue(active)
        self.assertEqual(gl2["hydration_started"], 1000.0 + 45 * 60)

    def test_ends_after_the_display_window(self):
        start = 5000.0
        gl = {"last_hydration_end": 1000.0, "hydration_started": start}
        active, _ = self.common.hydration_tick(gl, self.cfg, start + 19)
        self.assertTrue(active)
        active, gl2 = self.common.hydration_tick(gl, self.cfg, start + 21)
        self.assertFalse(active)
        self.assertIsNone(gl2["hydration_started"])
        self.assertEqual(gl2["last_hydration_end"], start + 21)

    def test_never_fires_during_confirm(self):
        gl = {"last_hydration_end": 1000.0}
        active, _ = self.common.hydration_tick(
            gl, self.cfg, 1000.0 + 90 * 60, suppressed=True)
        self.assertFalse(active)

    def test_an_active_reminder_is_cancelled_by_confirm(self):
        gl = {"last_hydration_end": 1000.0, "hydration_started": 5000.0}
        active, gl2 = self.common.hydration_tick(gl, self.cfg, 5005.0, suppressed=True)
        self.assertFalse(active)
        self.assertIsNone(gl2["hydration_started"])
        self.assertEqual(gl2["last_hydration_end"], 5005.0)

    def test_interval_is_measured_from_the_end_of_the_last_one(self):
        start = 5000.0
        gl = {"last_hydration_end": 1000.0, "hydration_started": start}
        _, gl = self.common.hydration_tick(gl, self.cfg, start + 21)
        end = gl["last_hydration_end"]
        active, _ = self.common.hydration_tick(gl, self.cfg, end + 45 * 60 - 1)
        self.assertFalse(active)
        active, _ = self.common.hydration_tick(gl, self.cfg, end + 45 * 60)
        self.assertTrue(active)


class TestConfig(Base):
    def test_defaults_when_missing(self):
        cfg = self.common.coerce_config(None)
        self.assertEqual(cfg["hydration_interval_minutes"], 45)
        self.assertEqual(cfg["status_style"], "sprite")

    def test_clamping_and_bad_values(self):
        cfg = self.common.coerce_config(
            {"port": "999999", "hydration_interval_minutes": "0",
             "status_style": "rainbow", "auto_open": "true", "sound_enabled": "nope"})
        self.assertEqual(cfg["port"], 65535)
        self.assertEqual(cfg["hydration_interval_minutes"], 1)
        self.assertEqual(cfg["status_style"], "sprite")
        self.assertTrue(cfg["auto_open"])
        self.assertFalse(cfg["sound_enabled"])

    def test_env_options_are_read(self):
        os.environ["CLAUDE_PLUGIN_OPTION_HYDRATION_INTERVAL_MINUTES"] = "7"
        os.environ["CLAUDE_PLUGIN_OPTION_STATUS_STYLE"] = "text"
        try:
            cfg = self.common.config_from_env()
            self.assertEqual(cfg["hydration_interval_minutes"], 7)
            self.assertEqual(cfg["status_style"], "text")
        finally:
            del os.environ["CLAUDE_PLUGIN_OPTION_HYDRATION_INTERVAL_MINUTES"]
            del os.environ["CLAUDE_PLUGIN_OPTION_STATUS_STYLE"]

    def test_clock_override(self):
        os.environ["CLAWD_NOW"] = "4242.5"
        self.assertEqual(self.common.now(), 4242.5)


class TestSprite(Base):
    def setUp(self):
        Base.setUp(self)
        import statusline
        self.sl = statusline

    def test_grid_shapes(self):
        # Four pixel rows, even width, so every pixel lands in a 2 x 2 cell.
        for name, frames in self.sl.FRAMES.items():
            for grid in frames:
                self.assertEqual(len(grid), 4, name)
                width = len(grid[0])
                self.assertEqual(width % 2, 0, name)
                for row in grid:
                    self.assertEqual(len(row), width, name)

    def test_grid_characters_are_all_in_the_palette(self):
        for name, frames in self.sl.FRAMES.items():
            for grid in frames:
                for row in grid:
                    for ch in row:
                        self.assertTrue(ch == "." or ch in self.sl.PALETTE,
                                        "%s: %r" % (name, ch))

    def test_no_cell_needs_two_colours(self):
        for name, frames in self.sl.FRAMES.items():
            for grid in frames:
                for r in range(0, 4, 2):
                    for c in range(0, len(grid[0]), 2):
                        px = {grid[r][c], grid[r][c + 1],
                              grid[r + 1][c], grid[r + 1][c + 1]} - {"."}
                        self.assertLessEqual(len(px), 1,
                                             "%s cell %d,%d: %s" % (name, r, c, px))

    def test_quadrant_table(self):
        # bits are top-left, top-right, bottom-left, bottom-right
        q = self.sl.QUADRANTS
        self.assertEqual(q[0b0000], " ")
        self.assertEqual(q[0b1111], "█")
        self.assertEqual(q[0b1100], "▀")
        self.assertEqual(q[0b0011], "▄")
        self.assertEqual(q[0b1010], "▌")
        self.assertEqual(q[0b0101], "▐")
        self.assertEqual(q[0b1000], "▘")
        self.assertEqual(q[0b0100], "▝")
        self.assertEqual(q[0b0010], "▖")
        self.assertEqual(q[0b0001], "▗")
        self.assertEqual(q[0b1110], "▛")
        self.assertEqual(q[0b1101], "▜")
        self.assertEqual(q[0b1011], "▙")
        self.assertEqual(q[0b0111], "▟")
        self.assertEqual(q[0b1001], "▚")
        self.assertEqual(q[0b0110], "▞")

    def _plain(self, rows):
        import re as _re
        return [_re.sub("\033\\[[0-9;]*m", "", r) for r in rows]

    def test_base_renders_as_drawn(self):
        self.assertEqual(self._plain(self.sl.render_sprite(self.sl.BASE)),
                         [" █▜█▛█ ",
                          "▀▛▛▀▜▜▀"])

    def test_eyes_are_holes(self):
        # Eyes cut out of the body, so the sprite needs no dark eye colour.
        top = self._plain(self.sl.render_sprite(self.sl.BASE))[0]
        self.assertIn("▜", top)
        self.assertIn("▛", top)

    def test_never_emits_a_background_colour(self):
        for name, frames in self.sl.FRAMES.items():
            for grid in frames:
                for tc in (True, False):
                    for row in self.sl.render_sprite(grid, truecolor=tc):
                        self.assertNotIn("\033[48;", row, name)

    def test_two_rows_out_of_four(self):
        self.assertEqual(len(self.sl.render_sprite(self.sl.BASE)), 2)

    def test_256_colour_fallback_emits_no_truecolor(self):
        out = "".join(self.sl.render_sprite(self.sl.BASE, truecolor=False))
        self.assertIn("38;5;", out)
        self.assertNotIn("38;2;", out)

    def test_nearest_256_is_sane(self):
        self.assertEqual(self.sl.to_256((0, 0, 0)), 16)
        self.assertEqual(self.sl.to_256((255, 255, 255)), 231)
        self.assertTrue(0 <= self.sl.to_256((217, 119, 87)) <= 255)

    def test_truecolor_detection(self):
        self.assertTrue(self.sl.truecolor_supported({"COLORTERM": "truecolor"}))
        self.assertTrue(self.sl.truecolor_supported({"COLORTERM": "24bit"}))
        self.assertFalse(self.sl.truecolor_supported({"COLORTERM": ""}))
        self.assertFalse(self.sl.truecolor_supported({}))

    def test_frame_cycles(self):
        self.assertIs(self.sl.frame_for("thinking", 0), self.sl.THINK_A)
        self.assertIs(self.sl.frame_for("thinking", 1), self.sl.THINK_B)
        self.assertIs(self.sl.frame_for("thinking", 2), self.sl.THINK_A)
        # idle runs a three second cycle with one blink
        self.assertIs(self.sl.frame_for("idle", 0), self.sl.BASE)
        self.assertIs(self.sl.frame_for("idle", 1), self.sl.BASE)
        self.assertIs(self.sl.frame_for("idle", 2), self.sl.BLINK)
        self.assertIs(self.sl.frame_for("confirm", 0), self.sl.ARM_UP)
        self.assertIs(self.sl.frame_for("hydrate", 0), self.sl.HYD_A)
        self.assertIs(self.sl.frame_for("done", 1), self.sl.DONE_B)

    def test_unknown_state_falls_back(self):
        self.assertIs(self.sl.frame_for("banana", 0), self.sl.BASE)

    def test_blink_closes_the_eyes(self):
        self.assertNotIn(".", self.sl.BLINK[1][2:12])
        self.assertIn(".", self.sl.BASE[1][2:12])

    def test_sprite_is_small(self):
        self.assertEqual(len(self.sl.BASE[0]) // 2, 7)
        self.assertEqual(len(self.sl.HYD_A[0]) // 2, 11)

    def test_two_rows_with_label(self):
        lines = self.sl.build_lines("thinking", "Debugging", "Where does this break",
                                    0, True, 80)
        self.assertEqual(len(lines), 2)
        self.assertIn("thinking", lines[0])
        self.assertIn("debugging", lines[0])
        self.assertIn("Where does this break", lines[1])

    def test_text_mode_has_no_escape_codes(self):
        line = self.sl.text_line("thinking", "Debugging", "x")
        self.assertNotIn("\033", line)
        self.assertEqual(line, "clawd: thinking · debugging")
        self.assertNotIn("\033", self.sl.text_line("idle", None, None))

    def test_long_text_is_truncated_to_the_terminal(self):
        lines = self.sl.build_lines("thinking", "Debugging", "x" * 300, 0, True, 50)
        plain = self._plain(lines)
        self.assertLessEqual(len(plain[1]), 50)
        self.assertIn("…", plain[1])

    def test_rows_keep_their_leading_blanks(self):
        # Claude Code trims leading spaces unless the row starts with an
        # escape code; without this the top row slid one column left.
        for st in ("idle", "thinking", "confirm", "hydrate", "done"):
            for tick in (0, 1, 2):
                for line in self.sl.build_lines(st, "Tests", "x", tick, True, 80):
                    self.assertTrue(line.startswith("\033["), (st, tick, line))

    def test_every_width_fits(self):
        states = ("idle", "thinking", "confirm", "hydrate", "done", "welcome")
        for gapfree in (False, True):
            for cols in range(self.sl.MIN_COLUMNS_FOR_SPRITE, 220):
                for st in states:
                    for tick in (0, 1, 2):
                        lines = self.sl.build_lines(st, "Documentation", "y" * 200, tick,
                                                    True, cols, message="z" * 200,
                                                    gapfree=gapfree)
                        for line in self._plain(lines):
                            self.assertLessEqual(len(line), cols,
                                                 "%s at %d cols" % (st, cols))


class TestGapFree(Base):
    """Terminal.app: reverse video closes the strip between the two rows."""

    def setUp(self):
        Base.setUp(self)
        import statusline
        self.sl = statusline

    def _cells(self, row):
        # Split a rendered row into (reversed, glyph) per text cell.
        import re as _re
        cells = []
        for m in _re.finditer("((?:\033\\[[0-9;]*m)*)([^\033])(?:\033\\[0m)?", row):
            cells.append(("\033[7m" in m.group(1), m.group(2)))
        return cells

    def test_detects_terminal_app_only(self):
        self.assertTrue(self.sl.gapfree_wanted({"TERM_PROGRAM": "Apple_Terminal"}))
        self.assertFalse(self.sl.gapfree_wanted({"TERM_PROGRAM": "vscode"}))
        self.assertFalse(self.sl.gapfree_wanted({"TERM_PROGRAM": "iTerm.app"}))
        self.assertFalse(self.sl.gapfree_wanted({}))

    def test_other_terminals_are_unchanged(self):
        for frames in self.sl.FRAMES.values():
            for grid in frames:
                self.assertEqual(self.sl.render_sprite(grid),
                                 self.sl.render_cells(grid))
                self.assertNotIn("\033[7m", "".join(self.sl.render_sprite(grid)))

    def test_same_width_as_the_normal_sprite(self):
        for name, frames in self.sl.FRAMES.items():
            for grid in frames:
                a = self.sl.render_cells(grid)
                b = self.sl.render_cells_gapfree(grid)
                for ra, rb in zip(a, b):
                    self.assertEqual(len(self._cells(ra)), len(self._cells(rb)), name)

    def test_reverse_cells_show_the_same_pixels(self):
        # A reversed cell draws the empty quadrants, so its glyph must be the
        # complement of what the normal renderer draws, for the same grid.
        q = self.sl.QUADRANTS
        for name, frames in self.sl.FRAMES.items():
            for grid in frames:
                tall = self.sl.tall_eyes(grid)
                plain = self.sl.render_cells(tall)
                gap = self.sl.render_cells_gapfree(grid)
                for pr, gr in zip(plain, gap):
                    for (_, pg), (rev, gg) in zip(self._cells(pr), self._cells(gr)):
                        want = q[15 ^ q.index(pg)] if rev else pg
                        self.assertEqual(gg, want, name)

    def test_no_background_escape_and_no_guessed_colour(self):
        for frames in self.sl.FRAMES.values():
            for grid in frames:
                for tc in (True, False):
                    out = "".join(self.sl.render_cells_gapfree(grid, tc))
                    self.assertNotIn("\033[48;", out)

    def test_body_below_the_head_is_reversed(self):
        # The arm row under the head must fill its top strip, or the head and
        # the arms split apart: the bug this mode exists for.
        bottom = self._cells(self.sl.render_cells_gapfree(self.sl.BASE)[1])
        self.assertTrue(all(rev for rev, _ in bottom[1:6]), bottom)
        # The outer arms have nothing above them, so they stay plain.
        self.assertFalse(bottom[0][0])
        self.assertFalse(bottom[6][0])

    def test_eyes_are_tall_inside_the_head_only(self):
        tall = self.sl.tall_eyes(self.sl.BASE)
        self.assertEqual(tall[0], "..BB.BBBB.BB..")
        self.assertEqual(tall[1:], self.sl.BASE[1:])
        self.assertEqual(self.sl.tall_eyes(self.sl.BLINK), self.sl.BLINK)
        # The glass's open top is not an eye.
        self.assertEqual(self.sl.tall_eyes(self.sl.HYD_B)[0][14:], self.sl.HYD_B[0][14:])

    def test_open_gaps_never_get_a_floating_sliver(self):
        # The glass walls sit beside open air; filling their strip would draw
        # a sliver over the empty half (it looked like "][").
        top = self._cells(self.sl.render_cells_gapfree(self.sl.HYD_B)[0])
        for rev, glyph in top[7:]:
            self.assertFalse(rev, top)


class TestHookSafety(Base):
    """The hook must never print, never fail and never store prompt text."""

    def run_hook(self, event, payload):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "hook.py"), event],
            input=json.dumps(payload), capture_output=True, text=True, env=env)
        return proc

    def test_stdout_is_always_empty(self):
        for event in ("UserPromptSubmit", "Stop", "PermissionRequest",
                      "Notification", "PostToolUse", "SessionStart", "Bogus"):
            proc = self.run_hook(event, {"session_id": "s", "prompt": "fix the bug",
                                         "cwd": "/tmp/app"})
            self.assertEqual(proc.stdout, "", event)
            self.assertEqual(proc.returncode, 0, event)

    def test_survives_garbage_input(self):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        for raw in ("", "not json", "[]", "null", '{"a":'):
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "hook.py"), "Stop"],
                input=raw, capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, raw)
            self.assertEqual(proc.stdout, "", raw)

    def test_missing_event_name(self):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "hook.py")],
            input="{}", capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)

    def test_prompt_text_is_never_written(self):
        secret = "my-secret-api-key-abcdef123456"
        self.run_hook("UserPromptSubmit",
                      {"session_id": "s", "prompt": "fix the bug " + secret,
                       "cwd": "/tmp/app"})
        for root, _dirs, files in os.walk(self.home):
            for name in files:
                with open(os.path.join(root, name)) as f:
                    self.assertNotIn(secret, f.read(), name)

    def test_only_the_folder_name_is_stored(self):
        self.run_hook("UserPromptSubmit",
                      {"session_id": "s", "prompt": "fix the bug",
                       "cwd": "/Users/someone/secret-path/my-app"})
        with open(self.common.session_path("s")) as fh:
            body = fh.read()
        self.assertIn("my-app", body)
        self.assertNotIn("secret-path", body)
        self.assertNotIn("/Users/someone", body)

    def test_state_transitions(self):
        p = {"session_id": "s", "cwd": "/tmp/app"}
        self.run_hook("UserPromptSubmit", dict(p, prompt="fix the bug"))
        rec = self.common.read_json(self.common.session_path("s"))
        self.assertEqual(rec["state"], "thinking")
        self.assertEqual(rec["topic"], "debugging")

        self.run_hook("PermissionRequest", dict(p, tool_name="Write"))
        self.assertEqual(self.common.read_json(self.common.session_path("s"))["state"],
                         "confirm")

        self.run_hook("PostToolUse", dict(p, tool_name="Write"))
        self.assertEqual(self.common.read_json(self.common.session_path("s"))["state"],
                         "thinking")

        self.run_hook("Stop", p)
        self.assertEqual(self.common.read_json(self.common.session_path("s"))["state"],
                         "done")

        self.run_hook("SessionEnd", p)
        self.assertFalse(os.path.exists(self.common.session_path("s")))

    def test_notification_only_confirms_on_the_right_type(self):
        p = {"session_id": "n", "cwd": "/tmp/app"}
        self.run_hook("SessionStart", p)
        self.run_hook("Notification", dict(p, notification_type="auth_success"))
        self.assertEqual(self.common.read_json(self.common.session_path("n"))["state"],
                         "idle")
        self.run_hook("Notification", dict(p, notification_type="permission_prompt"))
        self.assertEqual(self.common.read_json(self.common.session_path("n"))["state"],
                         "confirm")

    def test_weird_session_id_cannot_escape_the_state_dir(self):
        self.run_hook("UserPromptSubmit",
                      {"session_id": "../../escape", "prompt": "hi", "cwd": "/tmp/a"})
        for root, _dirs, files in os.walk(self.home):
            for name in files:
                self.assertTrue(
                    os.path.abspath(os.path.join(root, name)).startswith(
                        os.path.abspath(self.home)))

    def test_empty_and_huge_prompts(self):
        for prompt in ("", "x" * 200000):
            proc = self.run_hook("UserPromptSubmit",
                                 {"session_id": "s2", "prompt": prompt, "cwd": "/tmp/a"})
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")


class TestReadyPhrases(Base):
    def test_starts_with_ready_and_changes_every_five_seconds(self):
        rec = {"state": "idle", "state_since": 1000.0}
        phrases = self.common.READY_PHRASES
        self.assertEqual(self.common.ready_phrase(rec, 1000.0), "ready for next task")
        self.assertEqual(self.common.ready_phrase(rec, 1004.9), phrases[0])
        self.assertEqual(self.common.ready_phrase(rec, 1005.0), phrases[1])
        self.assertEqual(self.common.ready_phrase(rec, 1010.0), phrases[2])

    def test_loops_back_to_the_start(self):
        rec = {"state": "idle", "state_since": 1000.0}
        n = len(self.common.READY_PHRASES)
        self.assertEqual(self.common.ready_phrase(rec, 1000.0 + 5 * n),
                         self.common.READY_PHRASES[0])

    def test_idle_after_done_starts_when_done_ends(self):
        rec = {"state": "done", "state_since": 1000.0}
        self.assertEqual(self.common.ready_phrase(rec, 1005.0),
                         self.common.READY_PHRASES[0])
        self.assertEqual(self.common.ready_phrase(rec, 1010.0),
                         self.common.READY_PHRASES[1])

    def test_no_record_yet_says_ready(self):
        for at in (1000.0, 1007.0, 1234.5):
            self.assertEqual(self.common.ready_phrase({}, at), "ready for next task")

    def test_bad_or_future_timestamps_do_not_crash(self):
        for rec in ({}, {"state_since": "nope"}, {"state_since": 9e9}, None):
            self.assertIn(self.common.ready_phrase(rec, 1000.0),
                          self.common.READY_PHRASES)

    def test_phrases_are_short_and_plain(self):
        for p in self.common.READY_PHRASES:
            self.assertLessEqual(len(p), 30)
            self.assertTrue(all(ord(ch) < 128 for ch in p), p)  # no emoji

    def test_every_model_list_is_short_and_plain(self):
        for fam, phrases in self.common.READY_BY_FAMILY.items():
            self.assertGreaterEqual(len(phrases), 6, fam)
            self.assertTrue(phrases[0].startswith("ready"), fam)
            self.assertEqual(len(set(phrases)), len(phrases), fam)
            for p in phrases:
                self.assertLessEqual(len(p), 30, p)
                self.assertTrue(all(ord(ch) < 128 for ch in p), p)
        self.assertIs(self.common.READY_BY_FAMILY["sonnet"], self.common.READY_PHRASES)

    def test_phrases_follow_the_model(self):
        rec = {"state": "idle", "state_since": 1000.0}
        rp = self.common.ready_phrase
        for fam in ("opus", "sonnet", "haiku"):
            phrases = self.common.READY_BY_FAMILY[fam]
            self.assertEqual(rp(rec, 1000.0, fam), phrases[0])
            self.assertEqual(rp(rec, 1005.0, fam), phrases[1])
            self.assertEqual(rp(rec, 1000.0 + 5 * len(phrases), fam), phrases[0])
        # unknown model: Sonnet's list
        for fam in (None, "gpt", ""):
            self.assertEqual(rp(rec, 1005.0, fam), self.common.READY_PHRASES[1])

    def test_family_from_the_status_line_input(self):
        f = self.common.model_family
        # shapes observed live from Claude Code
        self.assertEqual(f({"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}}), "opus")
        self.assertEqual(f({"model": {"id": "claude-sonnet-5-5", "display_name": "Sonnet 5.5"}}), "sonnet")
        self.assertEqual(f({"model": {"id": "claude-haiku-4-5-20251001",
                                      "display_name": "Haiku 4.5"}}), "haiku")
        self.assertEqual(f({"model": {"display_name": "Opus"}}), "opus")
        self.assertEqual(f({"model": "claude-haiku-4-5"}), "haiku")
        for bad in ({}, {"model": None}, {"model": 7}, {"model": {"id": "something-else"}},
                    {"model": {"id": None}}, None, []):
            self.assertIsNone(f(bad), bad)

    def test_real_script_uses_the_model_list(self):
        self.put("x", state="idle", state_since=1000.0)
        env = dict(os.environ)
        env.update({"CLAWD_HOME": self.home, "CLAWD_NOW": "1000", "COLUMNS": "120"})
        env.pop("NO_COLOR", None)
        for model, fam in (("claude-opus-5-5", "opus"), ("claude-haiku-4-5", "haiku"),
                           ("claude-sonnet-5-5", "sonnet")):
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "statusline.py")],
                input=json.dumps({"session_id": "x", "model": {"id": model}}),
                capture_output=True, text=True, env=env)
            self.assertIn(self.common.READY_BY_FAMILY[fam][0], proc.stdout, fam)

    def test_window_uses_the_same_list(self):
        with open(os.path.join(ROOT, "companion", "web", "app.js")) as f:
            js = f.read()
        start = js.index("var READY_PHRASES = [")
        block = js[start:js.index("];", start)]
        # Each entry sits on its own line, quoted with " or '.
        found = [line.strip().rstrip(",")[1:-1]
                 for line in block.split("\n")[1:] if line.strip()]
        self.assertEqual(found, self.common.READY_PHRASES)

    def test_label_shows_the_phrase(self):
        import statusline
        lines = statusline.build_lines("idle", None, None, 0, True, 120,
                                       ready="tests green, mood green")
        self.assertIn("tests green, mood green", lines[0])
        self.assertEqual(statusline.text_line("idle", None, None, None, "ship it"),
                         "clawd: ship it")


class TestWelcome(Base):
    """A freshly launched session waves and says hello for 10 seconds."""

    def env(self, at, cols=120):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        env["CLAWD_NOW"] = str(at)
        env["COLUMNS"] = str(cols)
        env["COLORTERM"] = "truecolor"
        env.pop("NO_COLOR", None)
        return env

    def hook(self, event, at, **payload):
        payload.setdefault("session_id", "w")
        payload.setdefault("cwd", "/tmp/app")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "hook.py"), event],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=self.env(at))
        return self.common.read_json(self.common.session_path("w"), {})

    def status(self, at, cols=120):
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "statusline.py")],
            input=json.dumps({"session_id": "w"}), capture_output=True, text=True,
            env=self.env(at, cols))
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)

    def test_startup_sets_a_ten_second_hello(self):
        rec = self.hook("SessionStart", 1000, source="startup")
        self.assertEqual(rec.get("welcome_until"), 1010)

    def test_resume_clear_and_compact_do_not_wave(self):
        for source in ("resume", "clear", "compact", None):
            self.hook("SessionStart", 1000, source="startup")
            payload = {} if source is None else {"source": source}
            rec = self.hook("SessionStart", 1002, **payload)
            self.assertNotIn("welcome_until", rec, source)

    def test_hello_then_jokes(self):
        self.hook("SessionStart", 1000, source="startup")
        for t in (1000, 1004, 1009):
            self.assertIn("hey! welcome", self.status(t), t)
        self.assertIn("ready for next task", self.status(1010))
        self.assertIn("ready for next task", self.status(1014))
        self.assertIn(self.common.READY_PHRASES[1], self.status(1015))

    def test_the_hand_moves_every_second(self):
        self.hook("SessionStart", 1000, source="startup")
        a, b = self.status(1000), self.status(1001)
        self.assertNotEqual(a, b)
        self.assertEqual(self.status(1002), a)

    def test_label_does_not_jump_while_waving(self):
        self.hook("SessionStart", 1000, source="startup")
        cols = {self.status(t).split("\n")[0].index("hey") for t in (1000, 1001)}
        self.assertEqual(len(cols), 1)

    def test_a_prompt_ends_the_hello(self):
        self.hook("SessionStart", 1000, source="startup")
        rec = self.hook("UserPromptSubmit", 1003, prompt="fix the failing test")
        self.assertNotIn("welcome_until", rec)
        self.assertIn("thinking", self.status(1004))
        self.hook("Stop", 1005)
        self.assertNotIn("hey! welcome", self.status(1011))

    def test_narrow_terminal_says_hello_in_text(self):
        self.hook("SessionStart", 1000, source="startup")
        self.assertEqual(self.status(1001, cols=28).strip(), "clawd: hey! welcome")

    def test_bad_welcome_values_are_ignored(self):
        for bad in ("soon", None, True, [1]):
            rec = {"state": "idle", "welcome_until": bad}
            self.assertFalse(self.common.welcoming(rec, 1000))
            self.assertIn(self.common.ready_phrase(rec, 1000), self.common.READY_PHRASES)

    def test_wave_frames_are_clean(self):
        import statusline
        a, b = statusline.WAVE_A, statusline.WAVE_B
        self.assertEqual(len(a[0]), len(b[0]))
        self.assertTrue(all(len(r) == len(a[0]) for r in a + b))


class TestName(Base):
    """The optional name Clawd greets you with."""

    def test_clean_name(self):
        c = self.common.clean_name
        self.assertEqual(c("Preetam"), "Preetam")
        self.assertEqual(c("  Mary   Jane "), "Mary Jane")
        self.assertEqual(c("O'Neil-Smith Jr."), "O'Neil-Smith Jr.")
        self.assertEqual(c("José"), "José")
        self.assertEqual(c("\x1b[31mevil\x1b[0m"), "31mevil 0m")
        self.assertEqual(c("<script>x</script>"), "script x script")
        self.assertEqual(c("a" * 50), "a" * self.common.NAME_MAX)
        for bad in (None, 42, "", "   ", "!!!", ["x"]):
            self.assertEqual(c(bad), "")
        self.assertNotIn("\n", c("line\nbreak"))

    def test_option_wins_over_setup(self):
        u = self.common.user_name
        self.assertEqual(u({"display_name": "Opt", "setup_name": "Setup"}), "Opt")
        self.assertEqual(u({"display_name": "", "setup_name": "Setup"}), "Setup")
        self.assertEqual(u({}), "")

    def test_greeting_and_water_use_the_name(self):
        self.assertEqual(self.common.welcome_text("preetam"), "hey preetam, welcome!")
        self.assertEqual(self.common.welcome_text(""), "hey! welcome")
        msg = self.common.hydration_message(0, "preetam")
        self.assertEqual(msg["headline"], "Time to Drink water, preetam!")
        self.assertEqual(self.common.hydration_message(3)["headline"], "Time to Drink water!")
        self.assertTrue(msg["sub"])
        self.assertNotEqual(self.common.hydration_message(0)["headline"], msg["headline"])

    def test_hooks_keep_the_setup_name(self):
        self.common.write_atomic(self.common.config_path(),
                                 json.dumps({"setup_name": "preetam"}))
        cfg = self.common.refresh_config_from_env()
        self.assertEqual(cfg.get("setup_name"), "preetam")
        self.assertEqual(self.common.load_config().get("setup_name"), "preetam")

    def test_option_from_env(self):
        os.environ["CLAUDE_PLUGIN_OPTION_DISPLAY_NAME"] = "Opt"
        try:
            self.assertEqual(self.common.user_name(self.common.config_from_env()), "Opt")
        finally:
            del os.environ["CLAUDE_PLUGIN_OPTION_DISPLAY_NAME"]

    def install(self, *args):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "install.py"), "name"] + list(args),
            capture_output=True, text=True, env=env)

    def test_install_name_set_and_clear(self):
        proc = self.install("--set", "  Preetam  ")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("name in use   : Preetam", proc.stdout)
        self.assertEqual(self.common.read_json(self.common.config_path())["setup_name"], "Preetam")
        self.assertEqual(self.install("--set", "!!!").returncode, 1)
        self.assertEqual(self.common.read_json(self.common.config_path())["setup_name"], "Preetam")
        self.install("--clear")
        self.assertNotIn("setup_name", self.common.read_json(self.common.config_path()))
        self.assertIn("suggestion", self.install().stdout)

    def status(self, at):
        env = dict(os.environ)
        env.update({"CLAWD_HOME": self.home, "CLAWD_NOW": str(at), "COLUMNS": "120",
                    "COLORTERM": "truecolor"})
        env.pop("NO_COLOR", None)
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "statusline.py")],
            input=json.dumps({"session_id": "n"}), capture_output=True, text=True, env=env)
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)

    def test_status_line_greets_by_name(self):
        self.common.write_atomic(self.common.config_path(),
                                 json.dumps({"setup_name": "preetam"}))
        self.put("n", state="idle", state_since=1000.0, welcome_until=1010.0)
        self.assertIn("hey preetam, welcome!", self.status(1001))
        self.assertIn("ready for next task", self.status(1011))

    def test_status_line_water_by_name(self):
        self.common.write_atomic(self.common.config_path(),
                                 json.dumps({"setup_name": "preetam"}))
        self.common.write_atomic(self.common.global_path(), json.dumps(
            {"hydration_started": 1000.0, "last_hydration_end": 500.0, "hydration_seed": 0}))
        self.put("n", state="idle", state_since=900.0)
        out = self.status(1001)
        self.assertIn("Time to Drink water, preetam!", out)
        # the tip sits on the second line, not a repeat of the headline
        self.assertIn(self.common.hydration_message(0)["sub"], out.split("\n")[1])
        self.assertNotIn("hydration", out)

    def test_window_payload_carries_only_the_clean_name(self):
        sys.path.insert(0, os.path.join(ROOT, "companion"))
        import server
        self.common.write_atomic(self.common.config_path(),
                                 json.dumps({"setup_name": "pree\x1btam<b>"}))
        snap = server.snapshot()
        self.assertEqual(snap["name"], "pree tam b")
        self.common.write_atomic(self.common.config_path(), json.dumps({}))
        self.assertEqual(server.snapshot()["name"], "")


class TestStatusLineProcess(Base):
    def run_sl(self, payload, env_extra=None):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        env.pop("NO_COLOR", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "statusline.py")],
            input=json.dumps(payload), capture_output=True, text=True, env=env)

    def test_always_prints_something_and_exits_zero(self):
        proc = self.run_sl({"session_id": "x", "model": {"display_name": "Opus"},
                            "workspace": {"current_dir": "/tmp"}})
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip())

    def test_survives_empty_stdin(self):
        env = dict(os.environ)
        env["CLAWD_HOME"] = self.home
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "statusline.py")],
            input="", capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip())

    def test_no_color_falls_back_to_plain_text(self):
        proc = self.run_sl({"session_id": "x"}, {"NO_COLOR": "1"})
        self.assertNotIn("\033", proc.stdout)
        self.assertEqual(len(proc.stdout.strip().split("\n")), 1)

    def test_narrow_terminal_falls_back_to_one_line(self):
        proc = self.run_sl({"session_id": "x"}, {"COLUMNS": "30"})
        self.assertEqual(len(proc.stdout.strip().split("\n")), 1)

    def test_very_narrow_terminal_still_fits(self):
        for cols in (8, 12, 20):
            proc = self.run_sl({"session_id": "x"}, {"COLUMNS": str(cols)})
            line = proc.stdout.rstrip("\n")
            self.assertTrue(line)
            self.assertLessEqual(len(line), cols)

    def test_sprite_is_two_rows(self):
        proc = self.run_sl({"session_id": "x"}, {"COLUMNS": "120",
                                                 "COLORTERM": "truecolor"})
        self.assertEqual(len(proc.stdout.rstrip("\n").split("\n")), 2)

    def test_idle_phrase_rotates_in_the_real_script(self):
        self.put("x", state="idle", state_since=1000.0)
        seen = []
        for t in (1000, 1005, 1010):
            proc = self.run_sl({"session_id": "x"},
                               {"COLUMNS": "120", "CLAWD_NOW": str(t)})
            seen.append(proc.stdout)
        for out, phrase in zip(seen, self.common.READY_PHRASES):
            self.assertIn(phrase, out)

    def test_off_prints_a_blank_line_not_nothing(self):
        self.common.write_atomic(self.common.config_path(),
                                 json.dumps({"status_style": "off"}))
        proc = self.run_sl({"session_id": "x"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "\n")

    def test_never_leaks_prompt_text(self):
        self.put("x", state="thinking", topic="debugging")
        proc = self.run_sl({"session_id": "x", "workspace": {"current_dir": "/secret/p"}})
        self.assertNotIn("secret", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
