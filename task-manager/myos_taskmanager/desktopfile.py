""".desktop files: reading the main section, and switching the Hidden key on and off."""
import os
import re
from pathlib import Path


def parse(text: str) -> dict:
    """Keys of the [Desktop Entry] section."""
    out, inside = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = (s == "[Desktop Entry]")
        elif inside and s and not s.startswith("#") and "=" in s:
            key, _, value = s.partition("=")
            out[key.strip()] = value.strip()
    return out


def load(path) -> dict:
    try:
        return parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def _languages() -> list:
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "")
    lang = lang.split(".")[0].split("@")[0]
    return [x for x in (lang, lang.split("_")[0]) if x and x not in ("C", "POSIX")]


def localized(entry: dict, key: str) -> str:
    """The value in the user's language (Name[de]) if the file has one, else the plain one."""
    for lang in _languages():
        if entry.get(f"{key}[{lang}]"):
            return entry[f"{key}[{lang}]"]
    return entry.get(key, "")


def is_true(entry: dict, key: str) -> bool:
    return entry.get(key, "").strip().lower() == "true"


def set_keys(text: str, values: dict) -> str:
    """Set keys in the [Desktop Entry] section; a value of None removes the key."""
    out, inside, placed = [], False, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = (s == "[Desktop Entry]")
            out.append(line)
            if inside and not placed:
                out.extend(f"{k}={v}" for k, v in values.items() if v is not None)
                placed = True
            continue
        if inside and re.match(r"^(%s)\s*=" % "|".join(re.escape(k) for k in values), s):
            continue
        out.append(line)
    if not placed:
        out = ["[Desktop Entry]"] + [f"{k}={v}" for k, v in values.items() if v is not None] + out
    return "\n".join(out) + "\n"


def command_name(exec_line: str) -> str:
    """The program a desktop file's Exec line runs, without its arguments."""
    parts = exec_line.split()
    while parts and (parts[0] == "env" or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[0])):
        parts.pop(0)
    return os.path.basename(parts[0].strip('"')) if parts else ""


def clean_exec(exec_line: str) -> str:
    """An Exec line as a person would read it: without %u, %F and other placeholders."""
    return re.sub(r"\s+", " ", re.sub(r"(?<!%)%[a-zA-Z]", "", exec_line)).replace("%%", "%").strip()
