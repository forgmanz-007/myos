"""Text mode: `deepseek-shell --cli`. Works on a plain console (Ctrl+Alt+F3),
so the AI can repair the system even when the desktop does not start."""
import sys

from . import hardware
from .agent import Agent
from .config import Config
from .knowledge import Knowledge
from .ollama import Ollama, OllamaError, full_name
from .runner import CommandRunner
from .tools import ToolRegistry

DIM, BOLD, YELLOW, RED, RESET = "\033[2m", "\033[1m", "\033[33m", "\033[31m", "\033[0m"


def _color(enabled: bool):
    if enabled:
        return DIM, BOLD, YELLOW, RED, RESET
    return "", "", "", "", ""


def main(first_message: str = "") -> int:
    tty = sys.stdout.isatty()
    dim, bold, yellow, red, reset = _color(tty)
    config = Config()
    client = Ollama(config["ollama_url"])
    try:
        models = client.model_names()
    except OllamaError as e:
        print(f"{red}{e}{reset}\nStart it with:  sudo systemctl start ollama")
        return 1
    if config.get("model") not in models and full_name(config.get("model")) in models:
        config["model"] = full_name(config["model"])
    if not config.get("model") or config["model"] not in models:
        rec = hardware.recommend(hardware.detect())
        if models:
            config["model"] = rec.model if rec.model in models else models[0]
        else:
            wanted = hardware.first_start_model(config.get("model"), rec)
            print(f"No AI model is installed yet. Download one with:\n  ollama pull {wanted}")
            return 1
    runner = CommandRunner(terminal_auth=True)
    agent = Agent(config, client, ToolRegistry(config), Knowledge(), runner)
    print(f"{bold}DeepSeek Shell{reset} (text mode, model {config['model']}). "
          f"Type your question; 'exit' quits.\n")

    def emit(ev):
        if ev.kind == "content":
            print(ev.data, end="", flush=True)
        elif ev.kind == "thinking" and config.get("show_thinking"):
            print(f"{dim}{ev.data}{reset}", end="", flush=True)
        elif ev.kind == "assistant_end":
            print()
        elif ev.kind == "tool_start":
            args = ev.data.get("arguments", {})
            shown = args.get("command") or args.get("path") or args.get("query") or ""
            print(f"{dim}[{ev.data['name']}] {shown}{reset}")
        elif ev.kind == "tool_output":
            print(f"{dim}{ev.data}{reset}", end="", flush=True)
        elif ev.kind == "tool_end" and not ev.data["ok"]:
            print(f"{yellow}{ev.data['result'][:300]}{reset}")
        elif ev.kind == "error":
            print(f"{red}{ev.data}{reset}")

    def approve(req, _cid):
        plan = req.kind == "plan"
        print(f"\n{yellow}{bold}{'Plan: ' if plan else ''}{req.title}{reset}")
        if req.reason:
            print(f"  Why: {req.reason}")
        if req.warning:
            print(f"  {red}{req.warning}{reset}")
        print("  " + req.detail.replace("\n", "\n  "))
        if plan:
            question = "Go ahead with all of this? [y/N] "
        elif req.allow_task:
            question = "Allow? [y = yes, a = yes to everything in this task, N = no] "
        else:
            question = "Allow? [y/N] "
        try:
            answer = input(question).strip().lower()
        except EOFError:
            answer = ""
        if plan:
            req.respond("task" if answer in ("y", "yes") else "deny")
        elif answer in ("a", "all") and req.allow_task:
            req.respond("task")
        else:
            req.respond("allow" if answer in ("y", "yes") else "deny")
        return req.decision in ("allow", "task")

    history = []
    pending = first_message
    while True:
        try:
            text = pending or input(f"{bold}you>{reset} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        pending = ""
        if not text:
            continue
        if text in ("exit", "quit"):
            return 0
        try:
            history = agent.run(history, text, emit, approve)
        except KeyboardInterrupt:
            agent.stop()
            print(f"\n{yellow}Stopped.{reset}")
