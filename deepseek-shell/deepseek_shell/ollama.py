"""Minimal client for the local Ollama server (standard library only)."""
import json
import socket
import threading
import urllib.error
import urllib.request


def full_name(model: str) -> str:
    """Ollama stores a model without a tag as 'name:latest'."""
    model = (model or "").strip()
    return model if not model or ":" in model.rsplit("/", 1)[-1] else model + ":latest"


class OllamaError(Exception):
    """Ollama answered with an error."""


class OllamaUnavailable(OllamaError):
    """Ollama is not running or not reachable."""


def _error_text(err: urllib.error.HTTPError) -> str:
    try:
        body = err.read().decode("utf-8", "replace")
        data = json.loads(body)
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
        return body.strip() or str(err)
    except Exception:
        return str(err)


class Ollama:
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        self.base_url = base_url.rstrip("/")
        self._caps: dict = {}
        self._active = None
        self._lock = threading.Lock()

    # ── plumbing ────────────────────────────────────────────────────────────
    def _open(self, path, body=None, timeout=15.0, method=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers={"Content-Type": "application/json"},
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise OllamaError(_error_text(e)) from None
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            raise OllamaUnavailable(
                f"The AI engine (Ollama) is not reachable at {self.base_url}: {reason}"
            ) from None

    def _json(self, path, body=None, timeout=15.0, method=None) -> dict:
        with self._open(path, body, timeout, method) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}

    def _stream(self, path, body, cancel=None, timeout=900.0):
        resp = self._open(path, body, timeout=timeout)
        with self._lock:
            self._active = resp
        try:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    return
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("error"):
                    raise OllamaError(str(obj["error"]))
                yield obj
        except (OSError, ValueError, AttributeError) as e:
            if cancel is not None and cancel.is_set():
                return
            raise OllamaUnavailable(f"Lost the connection to the AI engine: {e}") from None
        finally:
            with self._lock:
                self._active = None
            try:
                resp.close()
            except Exception:
                pass

    def abort(self) -> None:
        """Interrupt a running stream from another thread."""
        with self._lock:
            resp = self._active
        if resp is None:
            return
        try:
            sock = resp.fp.raw._sock  # type: ignore[attr-defined]
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    # ── API ─────────────────────────────────────────────────────────────────
    def version(self) -> str:
        return str(self._json("/api/version", timeout=5).get("version", "?"))

    def list_models(self) -> list:
        models = self._json("/api/tags", timeout=10).get("models") or []
        return sorted((m for m in models if isinstance(m, dict) and m.get("name")),
                      key=lambda m: m["name"])

    def model_names(self) -> list:
        return [m["name"] for m in self.list_models()]

    def show(self, model: str) -> dict:
        return self._json("/api/show", {"model": model}, timeout=30)

    def capabilities(self, model: str) -> set:
        """Capabilities such as 'tools' and 'thinking' (cached per model)."""
        if model not in self._caps:
            try:
                info = self.show(model)
            except OllamaError:
                return set()
            caps = set(info.get("capabilities") or [])
            if not caps:  # very old Ollama: guess from the chat template
                caps = {"completion"}
                if ".Tools" in str(info.get("template", "")):
                    caps.add("tools")
            self._caps[model] = caps
        return self._caps[model]

    def delete(self, model: str) -> None:
        self._json("/api/delete", {"model": model}, method="DELETE")
        self._caps.pop(model, None)

    def loaded_models(self) -> list:
        """Names of the models that are in memory right now."""
        models = self._json("/api/ps", timeout=3).get("models") or []
        return [str(m.get("name") or m.get("model") or "") for m in models if isinstance(m, dict)]

    def release(self, model: str, after_seconds: int = 60) -> None:
        """Let a loaded model leave memory soon (0 = now) instead of after the engine's
        usual idle time. Does nothing if it is not loaded: asking would load it."""
        try:
            if full_name(model) not in [full_name(name) for name in self.loaded_models()]:
                return
            keep = f"{int(after_seconds)}s" if after_seconds > 0 else 0
            self._json("/api/generate", {"model": model, "keep_alive": keep}, timeout=4)
        except OllamaError:
            pass

    def chat(self, model, messages, tools=None, think=None, options=None, cancel=None):
        body = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        if think is not None:
            body["think"] = think
        if options:
            body["options"] = options
        yield from self._stream("/api/chat", body, cancel)

    def pull(self, model: str, cancel=None):
        yield from self._stream("/api/pull", {"model": model, "stream": True}, cancel, timeout=300)
