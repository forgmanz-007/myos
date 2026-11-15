"""User settings, stored as JSON in ~/.config/deepseek-shell/config.json."""
import json
import os
import tempfile
from pathlib import Path

from . import paths

DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "model": "",              # empty: pick on first start
    "num_ctx": 0,             # 0: choose from the hardware
    "temperature": 0.6,       # DeepSeek recommends 0.5-0.7 for R1 models
    "think": True,            # let reasoning models think first
    "show_thinking": False,
    "tool_mode": "auto",      # auto | native | text
    "approval": "smart",      # always | smart | never
    "max_steps": 20,
    "command_timeout": 900,
    "snapshots": True,
    "unload_on_close": True,  # the AI model leaves memory a minute after the window closes
    "disabled_tools": [],
    "trusted_tools": [],      # custom tools the user chose to "always allow"
    "os_name": "",
    "window_geometry": "",
}

APPROVAL_MODES = {
    "always": "Ask before every action",
    "smart": "Ask before changes (recommended)",
    "never": "Never ask (not recommended)",
}


def read_json(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json_atomic(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def os_release() -> dict:
    info = {}
    for candidate in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(candidate, encoding="utf-8") as f:
                for line in f:
                    key, sep, value = line.strip().partition("=")
                    if sep:
                        info[key] = value.strip().strip('"')
            break
        except OSError:
            continue
    return info


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else paths.CONFIG_DIR / "config.json"
        self.data: dict = {}
        self.reload()

    def reload(self) -> None:
        system = read_json(paths.SYSTEM_CONFIG_DIR / "defaults.json")
        self.data = dict(DEFAULTS)
        self.data.update({k: v for k, v in system.items() if k in DEFAULTS})
        self.data.update({k: v for k, v in read_json(self.path).items() if k in DEFAULTS})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def save(self) -> None:
        write_json_atomic(self.path, self.data)

    @property
    def os_name(self) -> str:
        if self.data.get("os_name"):
            return self.data["os_name"]
        return os_release().get("PRETTY_NAME", "Linux")
