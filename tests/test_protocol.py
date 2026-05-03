import unittest

from daemon.protocol import (
    ENTRY_MAX_BYTES,
    ENTRIES_MAX_LINES,
    MSG_MAX,
    PROMPT_HINT_MAX,
    PROMPT_ID_MAX,
    PROMPT_TOOL_MAX,
    build_heartbeat,
    build_owner,
    build_status_query,
    build_time,
    make_entry,
    make_hint,
    make_msg,
    parse_device_line,
)


class TestBuildHeartbeat(unittest.TestCase):
    def test_no_prompt_omits_key(self):
        hb = build_heartbeat(total=1, running=0, waiting=0, msg="ready", entries=[])
        self.assertNotIn("prompt", hb)
        self.assertEqual(hb["total"], 1)
        self.assertEqual(hb["entries"], [])

    def test_prompt_present_truncates_fields(self):
        hb = build_heartbeat(
            total=1, running=1, waiting=1, msg="approve",
            entries=[],
            prompt={"id": "x" * 100, "tool": "T" * 100, "hint": "y" * 100},
        )
        self.assertEqual(len(hb["prompt"]["id"]), PROMPT_ID_MAX)
        self.assertEqual(len(hb["prompt"]["tool"]), PROMPT_TOOL_MAX)
        self.assertEqual(len(hb["prompt"]["hint"]), PROMPT_HINT_MAX)

    def test_completed_flag_only_when_true(self):
        hb_yes = build_heartbeat(total=0, running=0, waiting=0, msg="", entries=[], completed=True)
        hb_no = build_heartbeat(total=0, running=0, waiting=0, msg="", entries=[])
        self.assertIs(hb_yes["completed"], True)
        self.assertNotIn("completed", hb_no)

    def test_entries_capped(self):
        hb = build_heartbeat(
            total=0, running=0, waiting=0, msg="",
            entries=[f"line{i}" for i in range(20)],
        )
        self.assertEqual(len(hb["entries"]), ENTRIES_MAX_LINES)

    def test_long_entry_truncated(self):
        hb = build_heartbeat(total=0, running=0, waiting=0, msg="", entries=["x" * 500])
        self.assertEqual(len(hb["entries"][0]), ENTRY_MAX_BYTES)

    def test_msg_truncated(self):
        hb = build_heartbeat(total=0, running=0, waiting=0, msg="x" * 50, entries=[])
        self.assertEqual(len(hb["msg"]), MSG_MAX)

    def test_prompt_missing_optional_subfields(self):
        hb = build_heartbeat(
            total=0, running=0, waiting=0, msg="", entries=[],
            prompt={"id": "p001"},
        )
        self.assertEqual(hb["prompt"]["id"], "p001")
        self.assertEqual(hb["prompt"]["tool"], "")
        self.assertEqual(hb["prompt"]["hint"], "")


class TestMakeHint(unittest.TestCase):
    def test_bash_returns_command(self):
        self.assertEqual(make_hint("Bash", {"command": "ls -la /tmp"}), "ls -la /tmp")

    def test_edit_returns_basename_only(self):
        self.assertEqual(
            make_hint("Edit", {"file_path": "/Users/joe/proj/src/main.py"}),
            "main.py",
        )

    def test_write_returns_basename(self):
        self.assertEqual(make_hint("Write", {"file_path": "/tmp/x.txt"}), "x.txt")

    def test_webfetch_extracts_host(self):
        self.assertEqual(
            make_hint("WebFetch", {"url": "https://api.anthropic.com/v1/messages?foo=bar"}),
            "api.anthropic.com",
        )

    def test_webfetch_no_scheme(self):
        self.assertEqual(make_hint("WebFetch", {"url": "example.com/x"}), "example.com/x")

    def test_unknown_tool_dumps_json(self):
        h = make_hint("CustomTool", {"x": 1, "y": 2})
        self.assertIn("\"x\"", h)

    def test_strip_non_ascii(self):
        h = make_hint("Bash", {"command": "echo 你好 world"})
        self.assertNotIn("你", h)
        # all bytes must be ASCII
        h.encode("ascii")

    def test_truncates_to_43(self):
        h = make_hint("Bash", {"command": "x" * 200})
        self.assertEqual(len(h), PROMPT_HINT_MAX)

    def test_missing_input(self):
        self.assertEqual(make_hint("Bash", None), "")


class TestMakeMsg(unittest.TestCase):
    def test_active_prompt_wins(self):
        m = make_msg(active_prompt_tool="Bash", last_tool="Edit", total=3)
        self.assertEqual(m, "approve: Bash")

    def test_completed(self):
        self.assertEqual(make_msg(completed=True, last_tool="Bash"), "done: Bash")

    def test_running(self):
        self.assertEqual(make_msg(last_tool="Bash"), "running: Bash")

    def test_total_plural(self):
        self.assertEqual(make_msg(total=2), "2 sessions")

    def test_total_singular(self):
        self.assertEqual(make_msg(total=1), "1 session")

    def test_ready(self):
        self.assertEqual(make_msg(), "ready")

    def test_truncated(self):
        m = make_msg(active_prompt_tool="x" * 50)
        self.assertLessEqual(len(m), MSG_MAX)


class TestMakeEntry(unittest.TestCase):
    def test_format(self):
        e = make_entry("start", "abc1", now=0)
        self.assertRegex(e, r"^\d{2}:\d{2} start abc1$")

    def test_no_target(self):
        e = make_entry("idle", now=0)
        self.assertRegex(e, r"^\d{2}:\d{2} idle$")

    def test_truncated(self):
        e = make_entry("verb", "x" * 500, now=0)
        self.assertEqual(len(e), ENTRY_MAX_BYTES)

    def test_strip_non_ascii(self):
        e = make_entry("verb", "你好", now=0)
        self.assertNotIn("你", e)


class TestProtocolBuilders(unittest.TestCase):
    def test_status_query(self):
        self.assertEqual(build_status_query(), {"cmd": "status"})

    def test_time_shape(self):
        t = build_time(now=1735689600)
        self.assertIn("time", t)
        self.assertEqual(len(t["time"]), 2)
        self.assertEqual(t["time"][0], 1735689600)
        self.assertIsInstance(t["time"][1], int)

    def test_owner_basic(self):
        self.assertEqual(build_owner("Joe"), {"cmd": "owner", "name": "Joe"})

    def test_owner_strips_non_ascii(self):
        o = build_owner("Joé")
        self.assertNotIn("é", o["name"])


class TestParseDeviceLine(unittest.TestCase):
    def test_permission(self):
        d = parse_device_line('{"cmd":"permission","id":"p001","decision":"once"}')
        self.assertEqual(d["cmd"], "permission")
        self.assertEqual(d["decision"], "once")

    def test_with_whitespace(self):
        self.assertEqual(parse_device_line('  {"x":1}\n'), {"x": 1})

    def test_empty_returns_none(self):
        self.assertIsNone(parse_device_line(""))
        self.assertIsNone(parse_device_line("   "))

    def test_non_json_returns_none(self):
        self.assertIsNone(parse_device_line("hello"))
        self.assertIsNone(parse_device_line("{not json"))

    def test_array_top_level_returns_none(self):
        # The device only ever sends objects, not arrays.
        self.assertIsNone(parse_device_line("[1,2]"))


if __name__ == "__main__":
    unittest.main()
