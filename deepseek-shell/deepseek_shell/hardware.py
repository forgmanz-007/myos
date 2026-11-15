"""Detect the hardware that matters for the AI and pick a DeepSeek model."""
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field

VENDORS = {"10de": "NVIDIA", "1002": "AMD", "8086": "Intel"}

# Models offered in Settings, grouped by family. Sizes are the Ollama download sizes.
# Any other model from ollama.com/library can be typed in by name.
CATALOG = [
    ("deepseek-r1:1.5b", "1.1 GB", "Tiny and fast. Runs on any PC but makes many mistakes."),
    ("deepseek-r1:7b", "4.7 GB", "Older small model (Qwen 2.5 based)."),
    ("deepseek-r1:8b", "5.2 GB", "Recommended DeepSeek. Newest small DeepSeek (R1-0528). "
                                 "Needs 8 GB of graphics memory, or 16 GB RAM (slower)."),
    ("deepseek-r1:14b", "9.0 GB", "More knowledge. Needs a graphics card with 12 GB+."),
    ("deepseek-r1:32b", "20 GB", "The smartest DeepSeek that fits a home PC. "
                                 "Needs a 24 GB graphics card (RTX 3090/4090/5090)."),
    ("deepseek-r1:70b", "43 GB", "Needs 48 GB+ of graphics memory. Very slow otherwise."),
    ("qwen3.5:4b", "about 4 GB", "Small Qwen 3.5. For graphics cards with 6 GB, or PCs without one."),
    ("qwen3.5:9b", "about 7 GB", "Qwen 3.5, much newer than the DeepSeek models above and built "
                                 "for using tools. Needs 10 GB+ of graphics memory (8 GB is tight)."),
    ("qwen3.8:27b", "18 GB", "Newest Qwen (3.8), strong at multi-step jobs. "
                             "Needs a 24 GB graphics card (RTX 3090/4090/5090)."),
]


def catalog_size(model: str) -> str:
    """Download size of a model in the catalog, '' for others."""
    return next((size for name, size, _note in CATALOG if name == model), "")


def first_start_model(configured: str, rec: "Recommendation") -> str:
    """The model to offer on first start: the one chosen in the settings
    (or in config.env when the system was built), else the best fit for this PC."""
    configured = (configured or "").strip()
    return configured or rec.model


def gb(mb: int) -> int:
    """Memory in whole GB as people know it: 16 GB of RAM reports about 15.6 GB to
    Linux, and an 8 GB graphics card 8188 MB, so round instead of cutting off."""
    return round(mb / 1024)


@dataclass
class GPU:
    vendor: str
    name: str
    driver: str = ""
    vram_mb: int = 0

    @property
    def accelerated(self) -> bool:
        """Can the AI engine use this GPU right now?"""
        if self.vendor == "NVIDIA":
            return self.driver == "nvidia"
        if self.vendor == "AMD":
            return self.driver == "amdgpu" and os.path.isdir("/usr/local/lib/ollama/rocm")
        return False


@dataclass
class Hardware:
    cpu: str = "unknown CPU"
    cores: int = 1
    ram_mb: int = 0
    gpus: list = field(default_factory=list)
    live_session: bool = False

    def summary(self) -> str:
        gpu = ", ".join(
            f"{g.name} ({gb(g.vram_mb)} GB, driver: {g.driver or 'none'})" if g.vram_mb
            else f"{g.name} (driver: {g.driver or 'none'})" for g in self.gpus) or "no dedicated GPU found"
        return f"{self.cpu} ({self.cores} threads), {gb(self.ram_mb)} GB RAM, GPU: {gpu}"


@dataclass
class Recommendation:
    model: str
    num_ctx: int
    reason: str
    notes: list = field(default_factory=list)


def _run(argv, timeout=5) -> str:
    if not shutil.which(argv[0]):
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    out = _run(["sysctl", "-n", "hw.memsize"])  # macOS, for development
    return int(out) // (1024 * 1024) if out.strip().isdigit() else 0


def _cpu() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def parse_lspci(text: str) -> list:
    gpus, current = [], None
    for line in text.splitlines():
        m = re.match(r"^\S+\s+(VGA compatible controller|3D controller|Display controller)"
                     r"\s+\[03..\]:\s+(.*?)\s+\[([0-9a-f]{4}):([0-9a-f]{4})\]", line)
        if m:
            vendor = VENDORS.get(m.group(3), "")
            if vendor:
                name = re.sub(r"^(NVIDIA Corporation|Advanced Micro Devices, Inc\. \[AMD/ATI\]|"
                              r"Intel Corporation)\s*", "", m.group(2)).strip()
                br = re.search(r"\[([^\]]+)\]\s*$", name)
                label = f"{vendor} {br.group(1) if br else name}"
            else:  # other makers (virtual machines, server boards): lspci's own name
                label = m.group(2).strip()
            current = GPU(vendor, label)
            gpus.append(current)
            continue
        if current is not None:
            if line.startswith(("\t", " ")):
                d = re.match(r"\s+Kernel driver in use:\s*(\S+)", line)
                if d:
                    current.driver = d.group(1)
            else:
                current = None
    return gpus


def detect() -> Hardware:
    hw = Hardware(cpu=_cpu(), cores=os.cpu_count() or 1, ram_mb=_ram_mb())
    hw.gpus = parse_lspci(_run(["lspci", "-nnk"]))
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    nv = [g for g in hw.gpus if g.vendor == "NVIDIA"]
    for g, line in zip(nv, [l for l in smi.splitlines() if l.strip()]):
        try:
            g.vram_mb = int(float(line.rsplit(",", 1)[1]))
        except (ValueError, IndexError):
            pass
    amd_sizes = []
    for card in sorted(os.listdir("/sys/class/drm")) if os.path.isdir("/sys/class/drm") else []:
        p = f"/sys/class/drm/{card}/device/mem_info_vram_total"
        if re.fullmatch(r"card\d+", card) and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    amd_sizes.append(int(f.read().strip()) // (1024 * 1024))
            except (OSError, ValueError):
                pass
    for g, size in zip([g for g in hw.gpus if g.vendor == "AMD"], amd_sizes):
        g.vram_mb = size
    try:
        with open("/proc/cmdline", encoding="utf-8") as f:
            hw.live_session = "boot=live" in f.read()
    except OSError:
        pass
    hw.live_session = hw.live_session or os.path.isdir("/run/live/medium")
    return hw


def recommend(hw: Hardware) -> Recommendation:
    notes = []
    best_vram = max((g.vram_mb for g in hw.gpus if g.vendor in ("NVIDIA", "AMD")), default=0)
    usable = [g for g in hw.gpus if g.accelerated]
    vram = max((g.vram_mb for g in usable), default=0)

    for g in hw.gpus:
        if g.vendor == "NVIDIA" and g.driver != "nvidia":
            notes.append(f"Your {g.name} is not used by the AI yet because the NVIDIA driver is "
                         "missing. Ask me: \"install the NVIDIA driver\".")
        if g.vendor == "AMD" and g.driver == "amdgpu" and not g.accelerated and g.vram_mb >= 8000:
            notes.append(f"Your {g.name} can speed up the AI with AMD ROCm. "
                         "Ask me: \"enable AMD GPU acceleration for the AI\".")

    if vram >= 22000:
        return Recommendation("deepseek-r1:32b", 16384, f"{gb(vram)} GB graphics card", notes)
    if vram >= 10000:
        return Recommendation("deepseek-r1:8b", 16384, f"{gb(vram)} GB graphics card", notes)
    if vram >= 7000:
        return Recommendation("deepseek-r1:8b", 8192, f"{gb(vram)} GB graphics card", notes)
    if best_vram >= 7000:
        # A capable GPU without its driver yet: plan for the GPU, it gets fixed soon.
        return Recommendation("deepseek-r1:8b", 8192,
                              "graphics card found (driver still needed)", notes)
    if hw.ram_mb >= 15000:
        notes.append("No usable graphics card: the AI runs on the processor, so answers come slowly.")
        return Recommendation("deepseek-r1:8b", 8192, f"{gb(hw.ram_mb)} GB RAM, no GPU", notes)
    notes.append("This PC has little memory, so only the smallest model fits. "
                 "Expect simple answers and mistakes.")
    return Recommendation("deepseek-r1:1.5b", 8192, f"{gb(hw.ram_mb)} GB RAM", notes)
