"""Local presentation checks: no provider transport or sandbox required."""
import contextlib
import io
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from goal_native import cli
from goal_native.store import Store
from goal_native.terminal import Terminal, terminal_text


class TTY(io.StringIO):
    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
    def test_color_gates_and_readline_spans(self):
        for environment, tty, colored in [
            ({"TERM": "xterm-256color"}, True, True),
            ({"TERM": "xterm", "NO_COLOR": ""}, True, False),
            ({"TERM": "dumb"}, True, False),
            ({}, True, False),
            ({"TERM": "xterm"}, False, False),
        ]:
            with self.subTest(environment=environment, tty=tty), patch.dict(os.environ, environment, clear=True):
                stream = TTY() if tty else io.StringIO()
                terminal = Terminal(stream)
                terminal.write("label", color="cyan")
                terminal.field("metadata", "unsafe\x1b[31m\r\x07text")
                prompt = terminal.prompt("you > ")
                self.assertEqual(colored, "\x1b" in stream.getvalue())
                self.assertNotIn("\x1b[31m", stream.getvalue())
                self.assertNotIn("\r", stream.getvalue())
                self.assertEqual("you > ", re.sub("\x01.*?\x02", "", prompt))
                self.assertEqual(2 if colored else 0, prompt.count("\x01"))

    def test_narrow_wrap_preserves_content_and_controls(self):
        text = "a long title " * 8 + "界" * 40 + "e\u0301" * 60
        with patch.object(Terminal, "width", return_value=48):
            wrapped = Terminal(io.StringIO()).wrap(text)
        self.assertEqual(text, wrapped.replace("\n", ""))
        import unicodedata
        for line in wrapped.splitlines():
            self.assertLessEqual(sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line), 48)
        self.assertEqual("ok[2J]52;c;x\n\t", terminal_text("ok\x1b[2J\x1b]52;c;x\x07\r\x9b\n\t"))

    def test_streaming_fallback_and_private_events(self):
        for completed in (False, True):
            output = io.StringIO()
            text = "unique-public\x1b[2J\r\x07 response"
            with contextlib.redirect_stdout(output):
                display = cli._RunDisplay()
                display.event("id", {"type": "message_start", "message": {"role": "assistant"}})
                for chunk in (text[:15], text[15:]):
                    display.event("id", {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": chunk}})
                display.event("id", {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "PRIVATE-REASONING"}})
                if completed:
                    display.event("id", {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
                display.event("id", {"type": "tool_execution_end", "result": "PRIVATE-RESULT", "isError": True})
                display.finish({"result": text})
                display.finish()
            value = output.getvalue()
            self.assertEqual(1, value.count(terminal_text(text)))
            self.assertNotIn("PRIVATE", value)
            self.assertNotIn("\x1b", value)
            self.assertNotIn("\r", value)

    def test_navigation_cancellation_and_recovery_preserve_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            with Store(state) as store:
                goal = store.create_goal("retained request")
            args = cli.build_parser().parse_args(["--state", str(state), "--provider", "openai", "--model", "test-model", "chat"])
            args.source_dir = "/selected/not-sent"
            calls = []
            def run(_state, goal_id, run_args, on_event):
                calls.append((goal_id, run_args.context_budget, run_args.max_rounds))
                if len(calls) == 1:
                    on_event("id", {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "partial-unique"}})
                    raise cli.CLICancelled({"status": "cancelled", "result": "partial-unique", "run_started": True})
                self.assertIsNone(run_args.source_dir)
                return {"status": "finished", "result": "done", "run_started": True}
            output = io.StringIO()
            with patch("builtins.input", side_effect=[KeyboardInterrupt(), "/help", "/sessions", "/resume 1", "/budget 32768", "/continue", "/status", "/continue --max-rounds 2", "/exit"]), patch.object(cli, "_run_existing", side_effect=run), patch.object(Terminal, "width", return_value=48), contextlib.redirect_stdout(output):
                self.assertEqual(0, cli._chat(args))
            self.assertEqual([(goal["id"], 32768, args.max_rounds), (goal["id"], 32768, 2)], calls)
            value = output.getvalue()
            self.assertIn(goal["id"][:8], value)
            self.assertIn(goal["status"], value)
            self.assertIn(goal["outcome"], value)
            self.assertIn(args.source_dir, value)
            self.assertEqual(1, value.count("partial-unique"))
            self.assertNotIn("\x1b", value)
            self.assertTrue(all(len(line) <= 48 for line in value.splitlines()))
            with Store(state) as store:
                saved = store.goal(goal["id"])
                self.assertEqual(["retained request"], [r["text"] for r in saved["requests"]])
                self.assertEqual([], saved["acceptances"])


if __name__ == "__main__":
    unittest.main()
