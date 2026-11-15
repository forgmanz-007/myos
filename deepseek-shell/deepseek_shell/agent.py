"""The agent loop: send the conversation to the model, run the tools it asks
for, feed the results back, repeat until it answers.

Two ways to call tools:
  * native - Ollama's tool calling, for models that support it;
  * text   - the model writes <tool_call>{...}</tool_call>; works with any model.
"""
import ast
import datetime
import functools
import getpass
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass

from . import hardware
from .ollama import OllamaError, OllamaUnavailable
from .tools import ToolContext

MAX_CALLS_PER_TURN = 4
ENGINE_RESTART_WAIT = 60   # seconds to wait for the AI engine to come back after a crash

# What the AI engine says when its model process was killed (usually: out of memory).
_RUNNER_DIED = ("unexpectedly stopped", "resource limitations", "runner process has terminated",
                "signal: killed")


def engine_restarted(err: Exception) -> bool:
    """True when the AI engine went away (crash, out of memory, update) rather than
    refusing the request. Ollama restarts by itself within seconds."""
    return isinstance(err, OllamaUnavailable) or any(t in str(err).lower() for t in _RUNNER_DIED)


def explain_engine_error(err: Exception) -> str:
    if not engine_restarted(err):
        return str(err)
    return ("The AI engine stopped in the middle of this job, most likely because the PC ran out "
            "of memory (the model and the other open programs needed more than there is). It "
            "starts again by itself: try again in a moment. If it keeps happening, close big "
            "programs or choose a smaller model in Settings > AI model.\n"
            f"Details: {err}")


@dataclass
class Event:
    kind: str           # assistant_start | thinking | content | assistant_end | tool_start |
                        # tool_output | tool_end | approval | error | done
    data: object = None
    call_id: int = 0


class TagFilter:
    """Splits streamed text into visible text and thinking, and hides
    <tool_call> blocks (they are shown as tool cards instead)."""
    TAGS = ("<think>", "<tool_call>")

    def __init__(self):
        self.buf = ""
        self.state = "text"

    def feed(self, s: str):
        self.buf += s
        visible, thinking = [], []
        while self.buf:
            if self.state == "text":
                i = self.buf.find("<")
                if i == -1:
                    visible.append(self.buf)
                    self.buf = ""
                    break
                visible.append(self.buf[:i])
                self.buf = self.buf[i:]
                if self.buf.startswith("<think>"):
                    self.state, self.buf = "think", self.buf[7:]
                elif self.buf.startswith("<tool_call>"):
                    self.state, self.buf = "tool", self.buf[11:]
                elif any(t.startswith(self.buf) for t in self.TAGS):
                    break  # partial tag, wait for more text
                else:
                    visible.append("<")
                    self.buf = self.buf[1:]
            elif self.state == "think":
                j = self.buf.find("</think>")
                if j == -1:
                    keep = next((k for k in range(min(8, len(self.buf)), 0, -1)
                                 if "</think>".startswith(self.buf[-k:])), 0)
                    thinking.append(self.buf[:len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                thinking.append(self.buf[:j])
                self.buf, self.state = self.buf[j + 8:], "text"
            else:  # inside a tool call: hide until it closes
                j = self.buf.find("</tool_call>")
                if j == -1:
                    break
                self.buf, self.state = self.buf[j + 12:], "text"
        return "".join(visible), "".join(thinking)

    def flush(self):
        rest, self.buf = self.buf, ""
        if self.state == "text":
            return rest, ""
        if self.state == "think":
            return "", rest
        return "", ""


_CALL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
_FENCE = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.S)


def _loads_lenient(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    blob = text[start:end + 1]
    try:
        return json.loads(blob)
    except ValueError:
        pass
    try:
        py = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", blob)))
        value = ast.literal_eval(py)
        return value if isinstance(value, dict) else None
    except (ValueError, SyntaxError):
        return None


def _normalize_call(obj):
    if not isinstance(obj, dict):
        return None
    if "function" in obj and isinstance(obj["function"], dict):
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    args = obj.get("arguments", obj.get("args", obj.get("parameters", obj.get("input", {}))))
    if isinstance(args, str):
        args = _loads_lenient(args) or {}
    if not isinstance(name, str) or not name:
        return None
    return {"name": name.strip(), "arguments": args if isinstance(args, dict) else {}}


def parse_text_tool_calls(text: str) -> list:
    calls = [c for c in (_normalize_call(_loads_lenient(m.group(1)))
                         for m in _CALL_BLOCK.finditer(text)) if c]
    if not calls:
        for m in _FENCE.finditer(text):
            c = _normalize_call(_loads_lenient(m.group(1)))
            if c and ("arguments" in m.group(1) or "args" in m.group(1)):
                calls.append(c)
    return calls


def strip_tool_blocks(text: str) -> str:
    return _CALL_BLOCK.sub("", re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S)).strip()


@functools.lru_cache(maxsize=1)
def _root_filesystem() -> str:
    try:
        return subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "/"], capture_output=True,
                              text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def build_system_prompt(config, registry, mode: str, hw=None) -> str:
    hw = hw or hardware.detect()
    fs = _root_filesystem()
    snapshots = (" with automatic snapshots before admin changes (undo: snapper)"
                 if fs == "btrfs" else "")
    session = os.environ.get("XDG_SESSION_TYPE", "wayland")
    live = ("\n- IMPORTANT: this is the LIVE USB session. Changes are lost on restart; "
            "suggest installing to disk for permanent fixes." if hw.live_session else "")
    prompt = f"""You are DeepSeek Shell, the built-in AI assistant of {config.os_name}. You run 100% locally on this computer and you can use tools to look at and change the system.

ABOUT THIS COMPUTER
- OS: {config.os_name}, based on Debian 13 "trixie", KDE Plasma 6 desktop ({session}), set up to look and work like Windows 10.
- Software: apt (Debian packages) and Flatpak with Flathub. Audio: PipeWire. Network: NetworkManager. Root filesystem: {fs or 'unknown'}{snapshots}.
- Windows programs (.exe): opening one runs a virus check (ClamAV, VirusTotal, ANY.RUN) and then starts it with Wine, or with Proton if switched on in the file's Properties > Windows compatibility. Command: myos-run-exe FILE.
- Installed apps: Firefox, System Settings, ClamAV Security, you, and possibly a file manager. Other apps only if the user installed them.
- User: {getpass.getuser()}. Hardware: {hw.summary()}. Today: {datetime.date.today().isoformat()}.{live}

HOW TO WORK
1. Look at the real system with your tools before answering questions about it. Never guess versions, devices or file contents.
2. For a problem: (a) diagnose with system_report and read-only commands, (b) call search_fixes to find a tested guide, (c) explain the fix in one or two plain sentences, (d) apply it with tools, (e) check that it worked.
3. Commands that change the system need administrator rights: call run_command with admin=true. Never write "sudo". The user sees every command and approves it, and types their password when needed.
4. Commands run without a terminal and cannot answer questions: use -y for apt and flatpak, and never start interactive programs (nano, vim, top, less).
5. Install desktop apps with install_app (Flathub, just for this user, no password needed). Use apt for drivers, firmware and system tools.
6. Never delete the user's files, format or partition disks, or remove system packages unless the user clearly asked for exactly that.
7. If a command fails, read the error and try another way. If you are stuck after a few attempts, say so and suggest what the user can do.
8. After installing drivers or a new kernel, tell the user to restart the PC.

DOING JOBS FOR THE USER
- You can do whole jobs from one request: install and set up apps, write scripts, make shortcuts, change settings, organise files. Do the job yourself; don't just explain how.
- For a job with more than one change, first call propose_plan with short steps. When the user says go ahead, do every step without asking again, check that it worked, then summarise.
- Apps: install_app. A Flathub app keeps its settings in ~/.var/app/<app id>/. Offer create_shortcut (desktop, taskbar or startup) when it helps.
- Scripts: create_script saves them in ~/Scripts, checks them for mistakes, and can add a Start menu entry and a schedule (like Windows Task Scheduler). Use bash for simple jobs and python3 for bigger ones. Scripts can't ask questions while they run, so put settings at the top. Test a script with run_command when that is safe.
- Wallpapers: set_wallpaper sets a picture or a video (live video wallpaper) on every screen.
- If the user wants you to remember how to do something, save it as a tool with create_tool.

STYLE
- The user knows Windows but is not a Linux expert. Be friendly, short and concrete. Compare with Windows when it helps.
- Reply in the user's language. Finish with a short summary of what you did."""
    if mode == "text":
        tools = [{"name": t.name, "description": t.description,
                  "parameters": t.schema()["function"]["parameters"]} for t in registry.enabled()]
        prompt += ("\n\nTOOLS\nYou can call these tools:\n" + json.dumps(tools, ensure_ascii=False) +
                   "\n\nTo call a tool, write exactly this and then STOP writing:\n<tool_call>\n"
                   '{"name": "tool_name", "arguments": {"parameter": "value"}}\n</tool_call>\n'
                   "The result comes back in a <tool_response> block. Call one tool at a time. "
                   "When you have everything you need, answer normally without a tool_call.")
    return prompt


class Agent:
    def __init__(self, config, client, registry, knowledge, runner):
        self.config = config
        self.client = client
        self.registry = registry
        self.knowledge = knowledge
        self.runner = runner
        self.cancel = threading.Event()
        self._hw = None

    @property
    def hw(self):
        if self._hw is None:
            self._hw = hardware.detect()
        return self._hw

    def stop(self) -> None:
        self.cancel.set()
        self.client.abort()

    def mode_for(self, model: str) -> str:
        mode = self.config.get("tool_mode", "auto")
        if mode in ("native", "text"):
            return mode
        return "native" if "tools" in self.client.capabilities(model) else "text"

    def options(self) -> dict:
        ctx = int(self.config.get("num_ctx") or 0) or hardware.recommend(self.hw).num_ctx
        opts = {"num_ctx": ctx, "temperature": float(self.config.get("temperature", 0.6))}
        return opts

    @staticmethod
    def _trim(history: list, num_ctx: int, reserved_chars: int = 0) -> list:
        """Keep the conversation inside the model's memory (context window).

        The current request and everything after it are never dropped: first old
        tool results are shortened, then earlier requests are dropped (oldest
        first), and only then are this job's own tool results shortened."""
        # ~3 characters per token; keep 30% for the answer, minus the system prompt.
        budget = max(2000, int(num_ctx * 3.0 * 0.7) - reserved_chars)
        msgs = [dict(m) for m in history]

        def is_result(m):
            return m.get("role") == "tool" or str(m.get("content", "")).startswith("<tool_response>")

        def size():
            return len(json.dumps(msgs, ensure_ascii=False))

        def shorten(limit, first, last):
            for m in msgs[first:last]:
                if is_result(m) and len(m.get("content", "")) > limit:
                    m["content"] = m["content"][:limit] + "\n[... shortened ...]"

        requests = [i for i, m in enumerate(msgs) if m.get("role") == "user" and not is_result(m)]
        start = requests[-1] if requests else 0
        shorten(600, 0, start)
        while size() > budget and start > 0:
            following = next((i for i in requests if 0 < i <= start), start)
            del msgs[:following]
            requests = [i - following for i in requests if i >= following]
            start -= following
        for limit in (2000, 600, 200):
            if size() <= budget:
                break
            shorten(limit, start, len(msgs) - 1)  # the newest result stays complete
        return msgs

    def _chat(self, model, messages, **kw):
        """One model step. If the AI engine went away before this step produced
        anything (it restarts itself, e.g. after running out of memory while a tool
        ran), wait until it is back and try once more instead of failing the job."""
        produced = False
        for attempt in (1, 2):
            try:
                for chunk in self.client.chat(model, messages, cancel=self.cancel, **kw):
                    produced = True
                    yield chunk
                return
            except OllamaError as e:
                if produced or attempt == 2 or self.cancel.is_set() or not engine_restarted(e):
                    raise
                if not self._wait_for_engine():
                    raise

    def _wait_for_engine(self) -> bool:
        for _ in range(ENGINE_RESTART_WAIT):
            try:
                self.client.version()
                return True
            except OllamaError:
                pass
            if self.cancel.wait(1):
                return False
        return False

    def run(self, history: list, user_text: str, emit, approve) -> list:
        """Process one user message. `history` is a list of Ollama messages
        (without the system prompt). Returns the updated history."""
        self.cancel.clear()
        model = self.config.get("model")
        history = list(history) + [{"role": "user", "content": user_text}]
        try:
            mode = self.mode_for(model)
            caps = self.client.capabilities(model)
            think = bool(self.config.get("think", True)) if "thinking" in caps else None
            options = self.options()
            if mode == "text":
                options["stop"] = ["<tool_response>", "</tool_call>"]
            call_id = 0
            trust = {"task": False}  # set by "Allow all for this task" or an approved plan
            for _step in range(int(self.config.get("max_steps", 20))):
                if self.cancel.is_set():
                    break
                # Rebuilt every step: create_tool can add a tool in the middle of a job.
                system = {"role": "system",
                          "content": build_system_prompt(self.config, self.registry, mode, self.hw)}
                schemas = self.registry.schemas() if mode == "native" else None
                emit(Event("assistant_start"))
                content, thinking, native_calls = "", "", []
                filt = TagFilter()
                trimmed = self._trim(history, options["num_ctx"], len(system["content"]))
                for chunk in self._chat(model, [system] + trimmed, tools=schemas, think=think,
                                        options=options):
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        thinking += msg["thinking"]
                        emit(Event("thinking", msg["thinking"]))
                    if msg.get("content"):
                        content += msg["content"]
                        vis, th = filt.feed(msg["content"])
                        if th:
                            thinking += th
                            emit(Event("thinking", th))
                        if vis:
                            emit(Event("content", vis))
                    for tc in msg.get("tool_calls") or []:
                        native_calls.append(tc)
                    if chunk.get("done"):
                        break
                vis, th = filt.flush()
                if th:
                    emit(Event("thinking", th))
                if vis:
                    emit(Event("content", vis))
                if self.cancel.is_set():
                    emit(Event("assistant_end", strip_tool_blocks(content)))
                    break

                if mode == "native":
                    calls = [c for c in (_normalize_call(tc) for tc in native_calls) if c]
                    if not calls and "<tool_call>" in content:
                        calls = parse_text_tool_calls(content)
                else:
                    calls = parse_text_tool_calls(content)
                    if not calls and not strip_tool_blocks(content) and "<tool_call>" in thinking:
                        calls = parse_text_tool_calls(thinking)
                visible = strip_tool_blocks(content)
                emit(Event("assistant_end", visible))

                if mode == "native":
                    msg = {"role": "assistant", "content": visible}
                    if calls:
                        msg["tool_calls"] = [{"function": c} for c in calls[:MAX_CALLS_PER_TURN]]
                    history.append(msg)
                else:
                    history.append({"role": "assistant", "content": content.strip()})

                if not calls:
                    emit(Event("done"))
                    return history

                for call in calls[:MAX_CALLS_PER_TURN]:
                    if self.cancel.is_set():
                        break
                    call_id += 1
                    cid = call_id
                    emit(Event("tool_start", call, cid))
                    ctx = ToolContext(
                        config=self.config, runner=self.runner,
                        approve=lambda req, cid=cid: approve(req, cid),
                        on_output=lambda text, cid=cid: emit(Event("tool_output", text, cid)),
                        cancel=self.cancel, knowledge=self.knowledge, trust=trust,
                        registry=self.registry)
                    result, ok = self.registry.call(call["name"], call["arguments"], ctx)
                    emit(Event("tool_end", {"result": result, "ok": ok}, cid))
                    if mode == "native":
                        history.append({"role": "tool", "content": result, "tool_name": call["name"]})
                    else:
                        history.append({"role": "user", "content": "<tool_response>\n" + json.dumps(
                            {"name": call["name"], "result": result}, ensure_ascii=False) +
                            "\n</tool_response>"})
            else:
                emit(Event("error", "I stopped after too many steps. Tell me how to continue, or "
                                    "raise the limit in Settings > Safety."))
        except OllamaError as e:
            emit(Event("error", explain_engine_error(e)))
        except Exception as e:  # keep the app alive whatever happens
            emit(Event("error", f"Unexpected problem: {type(e).__name__}: {e}"))
        emit(Event("done"))
        return history
