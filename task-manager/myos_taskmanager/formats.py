"""Numbers as text, the way Windows' Task Manager writes them."""

KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3


def percent(value: float) -> str:
    """Whole machine share: '0%', '0.3%', '12.5%', '100%'."""
    if value < 0.05:
        return "0%"
    return f"{value:.1f}%" if value < 99.95 else "100%"


def whole_percent(value: float) -> str:
    return f"{round(value)}%"


def memory(nbytes: float) -> str:
    """Memory of a process: always MB with one decimal, like Windows ('1,234.5 MB')."""
    return f"{nbytes / MB:,.1f} MB"


def size(nbytes: float) -> str:
    """A size in the unit that fits: '512 MB', '15.6 GB', '1.8 TB'."""
    if nbytes >= 1024 * GB:
        return f"{nbytes / (1024 * GB):.1f} TB"
    if nbytes >= GB:
        return f"{nbytes / GB:.1f} GB"
    if nbytes >= MB:
        return f"{nbytes / MB:.0f} MB"
    return f"{nbytes / KB:.0f} KB"


def rate(bytes_per_s: float) -> str:
    """Disk speed: '0 MB/s', '0.1 MB/s', '153 KB/s' is written in MB/s like Windows."""
    if bytes_per_s < 0.05 * MB:
        return "0 MB/s" if bytes_per_s < 512 else f"{bytes_per_s / KB:.0f} KB/s"
    return f"{bytes_per_s / MB:.1f} MB/s"


def bits(bytes_per_s: float) -> str:
    """Network speed in bits per second: '0 Kbps', '16.0 Kbps', '1.2 Mbps', '1.0 Gbps'."""
    b = bytes_per_s * 8
    if b >= 1e9:
        return f"{b / 1e9:.1f} Gbps"
    if b >= 1e6:
        return f"{b / 1e6:.1f} Mbps"
    if b >= 100:
        return f"{b / 1e3:.1f} Kbps"
    return "0 Kbps"


def uptime(seconds: float) -> str:
    """days:hours:minutes:seconds, like Windows ('0:05:17:07')."""
    s = int(seconds)
    return f"{s // 86400}:{s % 86400 // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def speed(mhz: float) -> str:
    return f"{mhz / 1000:.2f} GHz" if mhz > 0 else ""


def nice_ceiling(value: float, minimum: float) -> float:
    """The next 'round' number (1, 2 or 5 times a power of ten) at or above value."""
    value = max(value, minimum)
    step = 1.0
    while step * 10 <= value:
        step *= 10
    for mult in (1, 2, 5, 10):
        if step * mult >= value:
            return step * mult
    return step * 10


def rate_scale(bytes_per_s: float) -> float:
    """Top of a disk speed graph in bytes per second: a round number of KB/s or MB/s."""
    if bytes_per_s >= MB:
        return nice_ceiling(bytes_per_s / MB, 1) * MB
    return min(nice_ceiling(bytes_per_s / KB, 100) * KB, MB)


def bits_scale(bytes_per_s: float) -> float:
    """Top of a network graph in bytes per second: a round number of Kbps, Mbps or Gbps."""
    return nice_ceiling(bytes_per_s * 8, 100e3) / 8


def pressure(percent: float) -> str:
    """How hard the PC has to work for memory, in words (like the pressure graph of a Mac)."""
    if percent < 10:
        return "Normal"
    return f"High ({percent:.0f}%)" if percent < 40 else f"Very high ({percent:.0f}%)"


def heat_level(kind: str, value: float) -> int:
    """How strongly a cell is tinted, 1 (calm) to 4 (heavy use)."""
    if kind == "cpu":
        steps = (0.5, 5, 25)
    elif kind == "mem":
        steps = (100 * MB, 500 * MB, 1500 * MB)
    else:  # disk
        steps = (0.1 * MB, 5 * MB, 50 * MB)
    return 1 + sum(1 for s in steps if value >= s)
