"""Virus checks: ClamAV (local), VirusTotal and ANY.RUN (online, need API keys).

Online checks look up the file's SHA-256 fingerprint first; the file itself
is only uploaded when the user allows it (or chose "always" in Settings).
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

USER_AGENT = "myos-security/1.0"

CLEAN, MALICIOUS, SUSPICIOUS, UNKNOWN, ERROR, SKIPPED, NOT_SET_UP = (
    "clean", "malicious", "suspicious", "unknown", "error", "skipped", "not_set_up")


@dataclass
class Finding:
    engine: str
    status: str
    summary: str
    link: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(d.get("engine", "?"), d.get("status", UNKNOWN), d.get("summary", ""),
                   d.get("link", ""), d.get("detail") or {})


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# ── ClamAV ───────────────────────────────────────────────────────────────────
_FOUND = re.compile(r"^(.*): (.+) FOUND$")


def clamav_version() -> str:
    """e.g. 'ClamAV 1.4.3/27800/Wed Nov  4 09:21:00 2026' (engine/signatures/date)."""
    for tool in ("clamdscan", "clamscan"):
        if shutil.which(tool):
            try:
                out = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=20)
                text = out.stdout.strip()
                if text.startswith("ClamAV"):
                    return text
            except (OSError, subprocess.SubprocessError):
                continue
    return ""


def signature_age_days(version: str):
    m = re.search(r"/\d+/(.+)$", version)
    if not m:
        return None
    try:
        when = datetime.datetime.strptime(" ".join(m.group(1).split()), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return max(0, (datetime.datetime.now() - when).days)


def _parse_clam_output(text: str) -> list:
    hits = []
    for line in text.splitlines():
        m = _FOUND.match(line.strip())
        if m:
            hits.append((m.group(1), m.group(2)))
    return hits


def clamav_scan(path, timeout: int = 600) -> Finding:
    """Scan one file (or a folder) and return a single result."""
    path = str(path)
    attempts = []
    if shutil.which("clamdscan"):
        attempts.append(["clamdscan", "--fdpass", "--no-summary", "--infected", path])
    if shutil.which("clamscan"):
        attempts.append(["clamscan", "--no-summary", "--infected", "-r", path])
    if not attempts:
        return Finding("ClamAV", ERROR, "ClamAV is not installed.")
    last_error = ""
    for argv in attempts:
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last_error = "The scan took too long."
            continue
        except OSError as e:
            last_error = str(e)
            continue
        hits = _parse_clam_output(res.stdout)
        if res.returncode == 0:
            return Finding("ClamAV", CLEAN, "No threats found.")
        if res.returncode == 1 and hits:
            names = sorted({h[1] for h in hits})
            return Finding("ClamAV", MALICIOUS, f"Threat found: {', '.join(names)}",
                           detail={"hits": hits})
        last_error = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["unknown error"]
        last_error = last_error[0]
    return Finding("ClamAV", ERROR, f"Could not scan: {last_error}")


def clamav_scan_tree(path, on_hit, cancel=None, on_line=None):
    """Scan a folder and call on_hit(file, threat) for each infected file.
    Returns (exit_code, error_text). Exit code 0 = clean, 1 = threats found."""
    def run(argv):
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    break
                if on_line:
                    on_line(line.rstrip())
                m = _FOUND.match(line.strip())
                if m:
                    on_hit(m.group(1), m.group(2))
            err = proc.stderr.read()
            proc.wait()
        return proc.returncode, (err.strip().splitlines() or [""])[-1]

    standalone = ["clamscan", "-r", "--no-summary", "--infected", str(path)]
    if shutil.which("clamdscan"):
        code, err = run(["clamdscan", "--fdpass", "--no-summary", "--infected", str(path)])
        if code == 2 and shutil.which("clamscan") and not (cancel is not None and cancel.is_set()):
            # the scanner service is not available: use the standalone scanner
            code, err = run(standalone)
        return code, err
    if shutil.which("clamscan"):
        return run(standalone)
    return 2, "ClamAV is not installed."


# ── HTTP helpers ─────────────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _request(method, url, headers=None, body=None, timeout=60):
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"message": raw[:300]}
        msg = (data.get("error", {}) or {}).get("message") if isinstance(data.get("error"), dict) \
            else data.get("message") or data.get("description") or data.get("error") or raw[:200]
        raise ApiError(e.code, str(msg or e.reason)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(0, f"No connection: {getattr(e, 'reason', e)}") from None


def _multipart(fields: dict, file_field: str, path) -> tuple:
    boundary = f"----myos{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode("utf-8"))
    filename = os.path.basename(str(path)).replace('"', "_")
    with open(path, "rb") as f:
        content = f.read()
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                 .encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _wait(seconds: float, cancel) -> bool:
    """Sleep; return False if cancelled."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel is not None and cancel.is_set():
            return False
        time.sleep(0.25)
    return True


# ── VirusTotal ───────────────────────────────────────────────────────────────
class VirusTotal:
    BASE = "https://www.virustotal.com/api/v3"
    GUI = "https://www.virustotal.com/gui/file/"

    def __init__(self, key: str, block_threshold: int = 3):
        self.key = key.strip()
        self.threshold = max(1, int(block_threshold))

    def _h(self) -> dict:
        return {"x-apikey": self.key}

    def _finding_from_stats(self, sha256: str, stats: dict, label: str = "") -> Finding:
        mal = int(stats.get("malicious", 0))
        sus = int(stats.get("suspicious", 0))
        total = sum(int(stats.get(k, 0)) for k in ("malicious", "suspicious", "undetected", "harmless"))
        text = f"{mal} of {total} security vendors flagged it as malicious"
        if label:
            text += f" ({label})"
        if mal >= self.threshold:
            status = MALICIOUS
        elif mal > 0 or sus >= 2:
            status = SUSPICIOUS
        else:
            status = CLEAN
            text = f"Clean: 0 of {total} security vendors flagged it"
        return Finding("VirusTotal", status, text, self.GUI + sha256,
                       {"malicious": mal, "suspicious": sus, "total": total})

    def lookup(self, sha256: str) -> Finding:
        if not self.key:
            return Finding("VirusTotal", NOT_SET_UP, "Not set up: add your free API key in ClamAV Security.")
        try:
            _, data = _request("GET", f"{self.BASE}/files/{sha256}", self._h(), timeout=30)
        except ApiError as e:
            if e.status == 404:
                return Finding("VirusTotal", UNKNOWN, "VirusTotal has never seen this file.",
                               self.GUI + sha256)
            return Finding("VirusTotal", ERROR, self._explain(e))
        attrs = (data.get("data") or {}).get("attributes") or {}
        label = ((attrs.get("popular_threat_classification") or {})
                 .get("suggested_threat_label") or "")
        return self._finding_from_stats(sha256, attrs.get("last_analysis_stats") or {}, label)

    def upload_and_wait(self, path, sha256: str, cancel=None, max_wait: int = 360) -> Finding:
        size = os.path.getsize(path)
        if size > 650 * 1024 * 1024:
            return Finding("VirusTotal", SKIPPED, "File is larger than 650 MB, too big to upload.")
        try:
            url = f"{self.BASE}/files"
            if size > 32 * 1024 * 1024:
                _, data = _request("GET", f"{self.BASE}/files/upload_url", self._h())
                url = data.get("data") or url
            body, ctype = _multipart({}, "file", path)
            _, data = _request("POST", url, {**self._h(), "Content-Type": ctype}, body, timeout=600)
            analysis = (data.get("data") or {}).get("id")
            if not analysis:
                return Finding("VirusTotal", ERROR, "Upload failed: no analysis id returned.")
            deadline = time.monotonic() + max_wait
            while time.monotonic() < deadline:
                if not _wait(20, cancel):  # free keys: 4 requests per minute
                    return Finding("VirusTotal", SKIPPED, "Cancelled.")
                _, res = _request("GET", f"{self.BASE}/analyses/{analysis}", self._h())
                attrs = (res.get("data") or {}).get("attributes") or {}
                if attrs.get("status") == "completed":
                    return self._finding_from_stats(sha256, attrs.get("stats") or {})
            return Finding("VirusTotal", UNKNOWN, "Uploaded; the analysis is still running. "
                           "Open the link later to see the result.", self.GUI + sha256)
        except ApiError as e:
            return Finding("VirusTotal", ERROR, self._explain(e))

    def test_key(self) -> str:
        """Empty string if the key works, otherwise an explanation."""
        try:
            _request("GET", f"{self.BASE}/users/{self.key}", self._h(), timeout=20)
            return ""
        except ApiError as e:
            return self._explain(e)

    @staticmethod
    def _explain(e: ApiError) -> str:
        if e.status in (401, 403):
            return "VirusTotal rejected the API key. Check it in ClamAV Security."
        if e.status == 429:
            return "VirusTotal limit reached (free keys: 4 per minute, 500 per day). Try again soon."
        return f"VirusTotal error: {e}"


# ── ANY.RUN ──────────────────────────────────────────────────────────────────
class AnyRun:
    API = "https://api.any.run/v1"
    APP = "https://app.any.run"
    VERDICTS = {0: CLEAN, 1: SUSPICIOUS, 2: MALICIOUS}

    def __init__(self, key: str):
        key = key.strip()
        if key and not key.lower().startswith(("api-key", "basic")):
            key = f"API-KEY {key}"
        self.auth = key

    def _h(self) -> dict:
        return {"Authorization": self.auth, "x-anyrun-connector": "myos-security"}

    def lookup(self, sha256: str) -> Finding:
        """Search ANY.RUN's threat intelligence for earlier sandbox runs of this file."""
        if not self.auth:
            return Finding("ANY.RUN", NOT_SET_UP, "Not set up: add your API key in ClamAV Security.")
        today = datetime.date.today()
        body = json.dumps({"query": f'sha256:"{sha256}"',
                           "startDate": (today - datetime.timedelta(days=365)).isoformat(),
                           "endDate": today.isoformat()}).encode()
        try:
            _, data = _request("POST", f"{self.API}/intelligence/api/search",
                               {**self._h(), "Content-Type": "application/json"}, body, timeout=40)
        except ApiError as e:
            return Finding("ANY.RUN", ERROR, self._explain(e))
        summary = data.get("summary") or {}
        tasks = [t.get("related") for t in (data.get("sourceTasks") or []) if t.get("related")]
        level = summary.get("threatLevel")
        link = tasks[0] if tasks else ""
        if level is None and not tasks:
            return Finding("ANY.RUN", UNKNOWN, "No earlier sandbox analysis of this file.")
        status = self.VERDICTS.get(level, UNKNOWN) if level is not None else UNKNOWN
        tags = ", ".join(summary.get("tags") or [])
        text = {CLEAN: "Earlier sandbox runs found no malicious activity",
                SUSPICIOUS: "Earlier sandbox runs showed suspicious activity",
                MALICIOUS: "Earlier sandbox runs showed malicious activity",
                UNKNOWN: "Analysed before, no verdict"}[status]
        return Finding("ANY.RUN", status, text + (f" ({tags})" if tags else ""), link,
                       {"tasks": tasks[:5]})

    def submit_and_wait(self, path, privacy: str = "bylink", run_seconds: int = 120,
                        cancel=None) -> Finding:
        """Run the file in a Windows 10 sandbox and wait for the verdict."""
        fields = {"obj_type": "file", "env_os": "windows", "env_version": "10",
                  "env_bitness": "64", "env_type": "complete", "opt_privacy_type": privacy,
                  "opt_timeout": str(int(run_seconds)), "obj_ext_startfolder": "temp"}
        try:
            body, ctype = _multipart(fields, "file", path)
            _, data = _request("POST", f"{self.API}/analysis",
                               {**self._h(), "Content-Type": ctype}, body, timeout=600)
            inner = data.get("data") or {}
            task = inner.get("taskid") or inner.get("task_id") or inner.get("taskId") or inner.get("id")
            if not task:
                return Finding("ANY.RUN", ERROR, "The sandbox did not return a task id.")
            link = f"{self.APP}/tasks/{task}"
            deadline = time.monotonic() + run_seconds + 240
            while time.monotonic() < deadline:
                if not _wait(15, cancel):
                    return Finding("ANY.RUN", SKIPPED, "Stopped waiting. The analysis continues online.", link)
                _, rep = _request("GET", f"{self.API}/analysis/{task}", self._h(), timeout=60)
                verdict = (((rep.get("data") or {}).get("analysis") or {}).get("scores") or {}).get("verdict") or {}
                if verdict.get("threatLevelText") or verdict.get("threatLevel") is not None:
                    status = self.VERDICTS.get(verdict.get("threatLevel"), UNKNOWN)
                    return Finding("ANY.RUN", status,
                                   f"Sandbox: {verdict.get('threatLevelText') or status}", link)
            return Finding("ANY.RUN", UNKNOWN, "The sandbox is still working. Open the link for the result.", link)
        except ApiError as e:
            return Finding("ANY.RUN", ERROR, self._explain(e))

    def test_key(self) -> str:
        try:
            _request("GET", f"{self.API}/user", self._h(), timeout=20)
            return ""
        except ApiError as e:
            return self._explain(e)

    @staticmethod
    def _explain(e: ApiError) -> str:
        if e.status in (401, 403):
            return ("ANY.RUN rejected the request: check the API key. API access needs an "
                    "ANY.RUN plan that includes it.")
        if e.status == 429:
            return "ANY.RUN limit reached. Try again later."
        return f"ANY.RUN error: {e}"


# ── combine results ──────────────────────────────────────────────────────────
@dataclass
class Verdict:
    level: str                 # clean | warn | block
    headline: str
    reasons: list


def decide(findings: list) -> Verdict:
    by = {f.engine: f for f in findings}
    reasons = []
    if any(f.status == MALICIOUS for f in findings):
        for f in findings:
            if f.status == MALICIOUS:
                reasons.append(f"{f.engine}: {f.summary}")
        return Verdict("block", "Dangerous file: do not run it", reasons)
    for f in findings:
        if f.status == SUSPICIOUS:
            reasons.append(f"{f.engine}: {f.summary}")
        elif f.status == ERROR:
            reasons.append(f"{f.engine} could not check it: {f.summary}")
    clam = by.get("ClamAV")
    online = [f for f in findings if f.engine != "ClamAV"]
    if online and all(f.status in (UNKNOWN, NOT_SET_UP, SKIPPED, ERROR) for f in online) and \
            any(f.status == UNKNOWN for f in online):
        reasons.append("No online service has seen this file before (new or rare program).")
    if any(f.status == SUSPICIOUS for f in findings):
        return Verdict("warn", "Be careful: this file looks suspicious", reasons)
    if clam is not None and clam.status == ERROR:
        return Verdict("warn", "The virus check could not finish", reasons)
    if reasons:
        return Verdict("warn", "Unknown program: only run it if you trust where it came from", reasons)
    return Verdict("clean", "No threats found", [])


def file_info(path) -> dict:
    p = Path(path)
    st = p.stat()
    return {"name": p.name, "size": st.st_size, "path": str(p)}
