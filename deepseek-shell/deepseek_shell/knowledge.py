"""Local library of tested fix guides (Markdown files) and a small keyword
search over them. Small local models make far fewer mistakes when they follow
a written recipe instead of relying on memory.

Guide format:
    # Title
    Keywords: comma, separated, words
    ...Markdown body...
Users can add their own guides in ~/.config/deepseek-shell/knowledge/.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

_STOP = {
    "a", "an", "the", "is", "are", "my", "me", "i", "it", "to", "of", "and", "or", "in", "on",
    "for", "with", "how", "do", "does", "can", "cant", "can't", "not", "no", "doesnt", "dont",
    "what", "why", "when", "this", "that", "be", "have", "has", "get", "please", "help", "want",
    "work", "working", "works", "problem", "issue", "fix", "linux", "computer", "pc", "so",
}


def _stem(word: str) -> str:
    for suffix in ("ing", "ers", "ies", "ed", "es", "er", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list:
    words = re.findall(r"[a-z0-9][a-z0-9+.-]*", text.lower())
    return [_stem(w.strip(".-")) for w in words if w not in _STOP and len(w) > 1]


@dataclass
class Guide:
    title: str
    keywords: list
    body: str
    path: Path

    @property
    def text(self) -> str:
        return f"# {self.title}\n{self.body}".strip()


def parse_guide(path: Path):
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    title, keywords, body_lines = path.stem.replace("-", " ").title(), [], []
    for line in raw.splitlines():
        if line.startswith("# ") and not body_lines and title == path.stem.replace("-", " ").title():
            title = line[2:].strip()
            continue
        if line.lower().startswith("keywords:") and not keywords:
            keywords = [k.strip().lower() for k in line.split(":", 1)[1].split(",") if k.strip()]
            continue
        body_lines.append(line)
    return Guide(title, keywords, "\n".join(body_lines).strip(), path)


class Knowledge:
    def __init__(self, dirs=None):
        self.dirs = [Path(d) for d in (dirs or paths.KNOWLEDGE_DIRS)]
        self.guides = []
        self.reload()

    def reload(self) -> None:
        guides = {}
        for d in self.dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                g = parse_guide(f)
                if g:
                    guides[f.name] = g  # later dirs (the user's) override
        self.guides = list(guides.values())

    def search(self, query: str, limit: int = 3) -> list:
        q = tokenize(query)
        if not q:
            return []
        scored = []
        for g in self.guides:
            title = set(tokenize(g.title))
            keys = set()
            for k in g.keywords:
                keys.update(tokenize(k))
            body = tokenize(g.body)
            body_counts = {}
            for w in body:
                body_counts[w] = body_counts.get(w, 0) + 1
            score = 0.0
            for w in q:
                if w in title:
                    score += 4
                if w in keys:
                    score += 3
                score += min(body_counts.get(w, 0), 3) * 0.5
            phrase = " ".join(query.lower().split())
            if phrase and any(k in phrase for k in g.keywords if " " in k):
                score += 4  # a multi-word keyword appears in the question
            if score > 0:
                scored.append((score, g))
        scored.sort(key=lambda x: -x[0])
        best = scored[0][0] if scored else 0
        return [g for s, g in scored[:limit] if s >= max(3.0, best * 0.35)]
