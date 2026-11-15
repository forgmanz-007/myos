"""A tiny fake Ollama server for tests. Scripted replies per model."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = {
    "fake-native:1b": ["completion", "tools", "thinking"],
    "fake-text:1b": ["completion"],
    "fake-jobs:1b": ["completion", "tools"],
}


def _jobs_reply(messages):
    """A model that does a whole job: plan, install, pin (or two commands)."""
    last = max(i for i, m in enumerate(messages) if m.get("role") == "user")
    request = messages[last]["content"]
    done = [m for m in messages[last:] if m.get("role") == "tool"]
    if "two files" in request:
        steps = [{"name": "run_command", "arguments": {"command": "touch /tmp/dss-job-a"}},
                 {"name": "run_command", "arguments": {"command": "touch /tmp/dss-job-b"}}]
    else:
        steps = [{"name": "propose_plan", "arguments": {
                     "summary": "Install Spotify and pin it to the taskbar",
                     "steps": ["Install Spotify from Flathub", "Pin it to the taskbar"]}},
                 {"name": "install_app", "arguments": {"app": "Spotify"}},
                 {"name": "create_shortcut", "arguments": {"target": "spotify", "where": "taskbar"}}]
    if done and "did NOT" in done[-1]["content"]:
        return {"content": "OK, I stopped. What would you like to change?"}
    if len(done) < len(steps):
        return {"content": "", "tool_calls": [{"function": steps[len(done)]}]}
    return {"content": "All done. " + done[-1]["content"][:200]}


def _native_reply(messages):
    last = max(i for i, m in enumerate(messages) if m.get("role") == "user")
    last_user = messages[last]["content"]
    tool_msgs = [m for m in messages[last:] if m.get("role") == "tool"]
    if "touch" in last_user and not tool_msgs:
        return {"thinking": "The user wants a file.", "content": "",
                "tool_calls": [{"function": {"name": "run_command",
                                             "arguments": {"command": "touch /tmp/dss-test-file",
                                                           "reason": "create the file"}}}]}
    if not tool_msgs:
        return {"thinking": "I should check.", "content": "Let me check.",
                "tool_calls": [{"function": {"name": "run_command",
                                             "arguments": {"command": "echo hello-from-tool"}}}]}
    return {"content": f"Done. The tool said: {tool_msgs[-1]['content'].splitlines()[-1]}"}


def _text_reply(messages):
    responses = [m for m in messages if m.get("role") == "user" and
                 m["content"].startswith("<tool_response>")]
    if not responses:
        # Stop sequence "</tool_call>" means the closing tag never arrives.
        return {"content": 'I will look.\n<tool_call>\n{"name": "run_command", '
                           '"arguments": {"command": "echo hi-text"}}\n'}
    return {"content": "<think>it worked</think>All good: " + json.loads(
        responses[-1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])["result"].splitlines()[-1]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            return self._send({"version": "0.40.14-fake"})
        if self.path == "/api/ps":                  # models in memory right now
            return self._send({"models": [{"name": n, "model": n} for n in self.server.loaded]})
        if self.path == "/api/tags":
            return self._send({"models": [{"name": n, "size": 1_100_000_000} for n in self.server.models]})
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append((self.path, body))
        if self.path == "/api/show":
            caps = MODELS.get(body.get("model"))
            if caps is None:
                return self._send({"error": "model not found"}, 404)
            return self._send({"capabilities": caps, "template": ""})
        if self.path == "/api/chat":
            model = body["model"]
            if model.startswith("fake-jobs"):
                reply = _jobs_reply(body["messages"])
            elif model.startswith("fake-native"):
                reply = _native_reply(body["messages"])
            else:
                reply = _text_reply(body["messages"])
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            content = reply.get("content", "")
            if reply.get("thinking"):
                self.wfile.write((json.dumps({"message": {"role": "assistant", "content": "",
                                                          "thinking": reply["thinking"]}, "done": False}) + "\n").encode())
            for i in range(0, len(content), 7):  # stream in small pieces
                self.wfile.write((json.dumps({"message": {"role": "assistant", "content": content[i:i + 7]},
                                              "done": False}) + "\n").encode())
                self.wfile.flush()
            final = {"message": {"role": "assistant", "content": ""}, "done": True}
            if reply.get("tool_calls"):
                final["message"]["tool_calls"] = reply["tool_calls"]
            self.wfile.write((json.dumps(final) + "\n").encode())
            return None
        if self.path == "/api/generate":            # without a prompt: only sets how long it stays loaded
            return self._send({"model": body.get("model"), "response": "", "done": True,
                               "done_reason": "unload" if body.get("keep_alive") == 0 else "load"})
        if self.path == "/api/pull":
            self.send_response(200)
            self.end_headers()
            for done in (0, 50, 100):
                self.wfile.write((json.dumps({"status": "downloading", "total": 100, "completed": done}) + "\n").encode())
            self.wfile.write(b'{"status": "success"}\n')
            self.server.models.append(body["model"])
            return None
        self._send({"error": "not found"}, 404)


def start(models=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.loaded = []
    server.models = list(MODELS) if models is None else list(models)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
