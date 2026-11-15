"""Where DeepSeek Shell keeps and finds its files."""
import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_SOURCE_ROOT = _PKG_DIR.parent

if (_SOURCE_ROOT / "data" / "knowledge").is_dir():
    # Running from a source checkout (development and tests).
    DATA_DIR = _SOURCE_ROOT / "data"
    LIBEXEC_DIR = _SOURCE_ROOT / "libexec"
else:
    _PREFIX = Path(os.environ.get("DEEPSEEK_SHELL_PREFIX", "/usr"))
    DATA_DIR = _PREFIX / "share" / "deepseek-shell"
    LIBEXEC_DIR = _PREFIX / "libexec" / "deepseek-shell"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "deepseek-shell"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "deepseek-shell"
SYSTEM_CONFIG_DIR = Path(os.environ.get("DEEPSEEK_SHELL_SYSCONFDIR", "/etc/deepseek-shell"))

USER_TOOLS_DIR = CONFIG_DIR / "tools"
USER_KNOWLEDGE_DIR = CONFIG_DIR / "knowledge"
SYSTEM_TOOLS_DIRS = [DATA_DIR / "tools.d", SYSTEM_CONFIG_DIR / "tools.d"]
KNOWLEDGE_DIRS = [DATA_DIR / "knowledge", USER_KNOWLEDGE_DIR]
ACTIVITY_LOG = STATE_DIR / "activity.log"
