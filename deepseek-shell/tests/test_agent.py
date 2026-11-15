"""The agent loop against a fake Ollama server (native and text tool calling)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="dss-agent-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(TMP, "state")
os.environ["DEEPSEEK_SHELL_SYSCONFDIR"] = os.path.join(TMP, "etc")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_ollama  # noqa: E402
from deepseek_shell.agent import Agent  # noqa: E402
from deepseek_shell.config import Config  # noqa: E402
from deepseek_shell.knowledge import Knowledge  # noqa: E402
from deepseek_shell.ollama import Ollama, OllamaError, OllamaUnavailable  # noqa: E402
from deepseek_shell.runner import CommandRunner  # noqa: E402
from deepseek_shell.tools import ToolRegistry  # noqa: E402


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = fake_ollama.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def make(self, model, approve_answer=True):
        cfg = Config()
        cfg["ollama_url"] = self.url
        cfg["model"] = model
        client = Ollama(self.url)
        agent = Agent(cfg, client, ToolRegistry(cfg), Knowledge(), CommandRunner())
        events, approvals = [], []

        def approve(req, cid):
            approvals.append(req)
            req.respond("allow" if approve_answer else "deny")
            return approve_answer
        return agent, events, approvals, approve

    def test_native_tool_calling(self):
        self.server.requests.clear()
        agent, events, approvals, approve = self.make("fake-native:1b")
        history = agent.run([], "say hello", events.append, approve)
        kinds = [e.kind for e in events]
        self.assertIn("thinking", kinds)
        self.assertIn("tool_start", kinds)
        tool_end = next(e for e in events if e.kind == "tool_end")
        self.assertTrue(tool_end.data["ok"])
        self.assertIn("hello-from-tool", tool_end.data["result"])
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(approvals, [])  # read-only command: no question
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "tool", "assistant"])
        self.assertIn("hello-from-tool", history[-1]["content"])
        chat = [b for p, b in self.server.requests if p == "/api/chat"]
        self.assertTrue(chat[0]["tools"])
        self.assertTrue(chat[0]["think"])
        self.assertEqual(chat[0]["messages"][0]["role"], "system")
        self.assertNotIn("<tool_call>", chat[0]["messages"][0]["content"])

    def test_text_tool_calling(self):
        self.server.requests.clear()
        agent, events, approvals, approve = self.make("fake-text:1b")
        history = agent.run([], "check something", events.append, approve)
        visible = "".join(e.data for e in events if e.kind == "content")
        self.assertIn("I will look.", visible)
        self.assertNotIn("tool_call", visible)
        self.assertNotIn("it worked", visible)  # <think> goes to thinking, not the answer
        self.assertIn("All good: hi-text", visible)
        self.assertTrue(history[2]["content"].startswith("<tool_response>"))
        chat = [b for p, b in self.server.requests if p == "/api/chat"]
        self.assertNotIn("tools", chat[0])
        self.assertNotIn("think", chat[0])
        self.assertIn("</tool_call>", chat[0]["options"]["stop"])
        self.assertIn("<tool_call>", chat[0]["messages"][0]["content"])

    def test_change_needs_approval_and_denial_is_respected(self):
        target = Path("/tmp/dss-test-file")
        target.unlink(missing_ok=True)
        agent, events, approvals, approve = self.make("fake-native:1b", approve_answer=False)
        agent.run([], "please touch a file", events.append, approve)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].detail, "touch /tmp/dss-test-file")
        self.assertEqual(approvals[0].reason, "create the file")
        self.assertFalse(target.exists())
        result = next(e for e in events if e.kind == "tool_end").data["result"]
        self.assertIn("did NOT allow", result)

    def test_engine_restart_is_waited_out(self):
        # Seen in the test VM: the engine was killed for lack of memory while a tool
        # ran and was back 3 seconds later. The job should go on, not fail.
        agent, events, approvals, approve = self.make("fake-native:1b")
        real_chat, calls = agent.client.chat, []

        def flaky_chat(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise OllamaUnavailable("The AI engine (Ollama) is not reachable: Connection refused")
            yield from real_chat(*a, **kw)
        agent.client.chat = flaky_chat
        agent.run([], "say hello", events.append, approve)
        self.assertEqual([e.data for e in events if e.kind == "error"], [])
        self.assertTrue(any(e.kind == "tool_end" for e in events))
        self.assertGreaterEqual(len(calls), 2)

    def test_engine_lost_mid_answer_is_explained(self):
        agent, events, approvals, approve = self.make("fake-native:1b")

        def broken_chat(*a, **kw):
            yield {"message": {"content": "Let me"}}
            raise OllamaError("model runner has unexpectedly stopped, this may be due to "
                              "resource limitations or an internal error")
        agent.client.chat = broken_chat
        agent.run([], "say hello", events.append, approve)
        errors = [e.data for e in events if e.kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("ran out of memory", errors[0])
        self.assertIn("smaller model", errors[0])

    def test_engine_offline(self):
        client = Ollama("http://127.0.0.1:9")
        with self.assertRaises(OllamaUnavailable):
            client.model_names()

    def test_model_leaves_memory_soon_after_the_window_closes(self):
        client = Ollama(self.url)
        before = len(self.server.requests)
        client.release("fake-native:1b")                # not in memory: asking would load it
        self.assertEqual(len(self.server.requests), before)
        self.server.loaded = ["fake-native:1b", "other:latest"]
        try:
            self.assertEqual(client.loaded_models(), ["fake-native:1b", "other:latest"])
            client.release("fake-native:1b")
            self.assertEqual(self.server.requests[-1],
                             ("/api/generate", {"model": "fake-native:1b", "keep_alive": "60s"}))
            client.release("other")                     # "other" is stored as "other:latest"
            self.assertEqual(self.server.requests[-1], ("/api/generate", {"model": "other", "keep_alive": "60s"}))
            client.release("fake-native:1b", after_seconds=0)
            self.assertEqual(self.server.requests[-1],
                             ("/api/generate", {"model": "fake-native:1b", "keep_alive": 0}))
        finally:
            self.server.loaded = []
        Ollama("http://127.0.0.1:9").release("fake-native:1b")     # engine not running: no error

    def test_pull_stream(self):
        client = Ollama(self.url)
        statuses = [s.get("status") for s in client.pull("deepseek-r1:8b")]
        self.assertEqual(statuses[-1], "success")
        self.assertIn("deepseek-r1:8b", client.model_names())


if __name__ == "__main__":
    unittest.main()
