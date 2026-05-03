import threading
import time
import unittest

from daemon.state import (
    COMPLETED_WINDOW_MS,
    SESSION_GC_AGE_S,
    State,
)


class TestSessionLifecycle(unittest.TestCase):
    def test_session_start_adds(self):
        s = State()
        s.session_start("a", "/tmp", now=100.0)
        snap = s.snapshot(now=100.0)
        self.assertEqual(snap["total"], 1)
        self.assertEqual(snap["running"], 0)

    def test_session_start_twice_is_idempotent(self):
        s = State()
        s.session_start("a", now=100.0)
        s.session_start("a", now=200.0)
        self.assertEqual(s.snapshot(now=200.0)["total"], 1)
        self.assertEqual(s.sessions["a"].last_hook_at, 200.0)

    def test_running_increments_on_user_prompt(self):
        s = State()
        s.session_start("a", now=100.0)
        s.session_running("a", now=110.0)
        self.assertEqual(s.snapshot(now=110.0)["running"], 1)

    def test_stop_removes_from_running_and_opens_completed_window(self):
        s = State()
        s.session_start("a", now=100.0)
        s.session_running("a", now=110.0)
        s.session_stop("a", now=200.0)
        # immediately after stop: completed=True, running=0
        snap = s.snapshot(now=200.0)
        self.assertEqual(snap["running"], 0)
        self.assertTrue(snap["completed"])
        # session itself is still tracked (total stays 1) until GC
        self.assertEqual(snap["total"], 1)

    def test_completed_window_expires(self):
        s = State()
        s.session_stop("a", now=200.0)
        # well past the window
        snap = s.snapshot(now=200.0 + COMPLETED_WINDOW_MS / 1000.0 + 1.0)
        self.assertFalse(snap["completed"])

    def test_gc_drops_stale_session(self):
        s = State()
        s.session_start("old", now=100.0)
        s.session_start("new", now=100.0 + SESSION_GC_AGE_S - 10)
        # GC at a time where 'old' is stale but 'new' is fresh
        dropped = s.gc(now=100.0 + SESSION_GC_AGE_S + 1)
        self.assertEqual(dropped, 1)
        self.assertNotIn("old", s.sessions)
        self.assertIn("new", s.sessions)


class TestPromptQueue(unittest.TestCase):
    def test_next_id_is_monotonic(self):
        s = State()
        ids = [s.next_prompt_id() for _ in range(3)]
        self.assertEqual(ids, ["p0001", "p0002", "p0003"])

    def test_enqueue_marks_waiting(self):
        s = State()
        s.session_start("sess1", now=100.0)
        pid = s.next_prompt_id()
        s.enqueue_prompt(pid, "sess1", "Bash", "ls")
        snap = s.snapshot(now=100.0)
        self.assertEqual(snap["waiting"], 1)
        self.assertEqual(snap["head_prompt"]["id"], pid)

    def test_dequeue_clears_waiting_when_last_for_sid(self):
        s = State()
        s.session_start("sess1", now=100.0)
        pid = s.enqueue_prompt(s.next_prompt_id(), "sess1", "Bash", "ls").prompt_id
        s.dequeue_prompt(pid)
        self.assertEqual(s.snapshot()["waiting"], 0)

    def test_dequeue_keeps_waiting_when_other_prompt_same_sid(self):
        s = State()
        s.session_start("sess1", now=100.0)
        p1 = s.enqueue_prompt(s.next_prompt_id(), "sess1", "Bash", "ls").prompt_id
        s.enqueue_prompt(s.next_prompt_id(), "sess1", "Edit", "x.py")
        s.dequeue_prompt(p1)
        # sess1 still has another pending prompt → still waiting
        self.assertEqual(s.snapshot()["waiting"], 1)

    def test_fifo_head(self):
        s = State()
        first = s.enqueue_prompt(s.next_prompt_id(), "a", "Bash", "ls").prompt_id
        s.enqueue_prompt(s.next_prompt_id(), "b", "Edit", "x")
        self.assertEqual(s.head_prompt().prompt_id, first)
        s.dequeue_prompt(first)
        # next head is the second one
        self.assertEqual(s.head_prompt().tool, "Edit")

    def test_two_sessions_two_waiting(self):
        s = State()
        s.session_start("a", now=100.0)
        s.session_start("b", now=100.0)
        s.enqueue_prompt("p1", "a", "Bash", "x")
        s.enqueue_prompt("p2", "b", "Edit", "y")
        self.assertEqual(s.snapshot()["waiting"], 2)

    def test_resolve_sets_decision_and_wakes_event(self):
        s = State()
        p = s.enqueue_prompt("p1", "a", "Bash", "ls")
        woke = {"value": False}

        def wait_for_event():
            woke["value"] = p.event.wait(timeout=2.0)

        t = threading.Thread(target=wait_for_event)
        t.start()
        time.sleep(0.05)  # let the thread actually wait
        ok = s.resolve_prompt("p1", "allow")
        t.join(timeout=2.0)

        self.assertTrue(ok)
        self.assertTrue(woke["value"])
        self.assertEqual(p.decision, "allow")

    def test_resolve_unknown_returns_false(self):
        s = State()
        self.assertFalse(s.resolve_prompt("nope", "allow"))


class TestSnapshot(unittest.TestCase):
    def test_no_prompt_head_is_none(self):
        s = State()
        snap = s.snapshot()
        self.assertIsNone(snap["head_prompt"])
        self.assertIsNone(snap["active_prompt_tool"])

    def test_entries_newest_first(self):
        s = State()
        s.add_entry("first")
        s.add_entry("second")
        s.add_entry("third")
        self.assertEqual(s.snapshot()["entries"], ["third", "second", "first"])

    def test_entries_capped(self):
        s = State()
        for i in range(20):
            s.add_entry(f"line{i}")
        # ENTRIES_DEQUE_LEN = 8
        self.assertEqual(len(s.snapshot()["entries"]), 8)

    def test_last_tool_propagates(self):
        s = State()
        s.set_last_tool("Bash")
        self.assertEqual(s.snapshot()["last_tool"], "Bash")


class TestBypassMode(unittest.TestCase):
    def test_default_not_bypass(self):
        s = State()
        s.session_start("a", now=100.0)
        self.assertFalse(s.is_bypass("a"))

    def test_set_bypass(self):
        s = State()
        s.session_start("a", now=100.0)
        s.set_permission_mode("a", "bypassPermissions")
        self.assertTrue(s.is_bypass("a"))

    def test_unknown_session(self):
        s = State()
        self.assertFalse(s.is_bypass("never-existed"))


if __name__ == "__main__":
    unittest.main()
