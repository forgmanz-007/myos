"""Command safety: decide which shell commands are read-only, which change
the system, and which are dangerous. Also rewrites commands so they work
without a terminal (no sudo, no interactive questions).

The classification is deliberately conservative: anything not recognised as
read-only counts as a change and needs the user's approval in the default
mode. "Dangerous" commands always need approval, whatever the mode.
"""
import os
import re
import shlex

READ, CHANGE, DANGER = "read", "change", "danger"

# Commands that never change anything, whatever their arguments.
ALWAYS_READ = {
    "ls", "dir", "cat", "head", "tail", "grep", "egrep", "fgrep", "zgrep", "rg", "wc", "sort",
    "uniq", "cut", "tr", "column", "comm", "diff", "cmp", "stat", "file", "du", "df", "free",
    "uptime", "uname", "whoami", "id", "groups", "date", "cal", "lscpu", "lsblk", "lspci",
    "lsusb", "lsmod", "lshw", "lsb_release", "inxi", "nproc", "getconf", "ps", "pgrep", "pidof",
    "env", "printenv", "echo", "printf", "which", "whereis", "type", "realpath", "readlink",
    "basename", "dirname", "pwd", "test", "[", "true", "false", "sensors", "glxinfo", "eglinfo",
    "vulkaninfo", "vainfo", "vdpauinfo", "clinfo", "dig", "host", "nslookup", "getent",
    "locale", "fc-list", "fc-match", "xdpyinfo", "kreadconfig5", "kreadconfig6", "ss", "netstat",
    "lsattr", "getfacl", "findmnt", "blkid", "modinfo", "md5sum", "sha1sum", "sha256sum",
    "sha512sum", "b2sum", "tree", "jq", "od", "strings", "w", "who", "last", "upower",
    "apt-cache", "dpkg-query", "lsof", "ldd", "myos-scan", "zramctl", "seq", "sleep", "xxd",
    "hexdump", "command", "awk", "gawk", "mawk", "cd", "pushd", "popd", "export", "exit",
}

# Commands that would hang waiting for a person to type.
INTERACTIVE = {
    "nano", "vi", "vim", "nvim", "emacs", "less", "more", "most", "htop", "btop", "atop",
    "nmtui", "mc", "ranger", "alsamixer", "man", "info", "watch", "passwd", "su", "ipython",
    "tmux", "screen", "ftp", "telnet", "bluetoothctl-interactive",
}

_NEEDS_ARGS_TO_BE_SAFE = {"python", "python3", "bash", "sh", "zsh", "sqlite3", "bluetoothctl", "top"}

DANGER_PATTERNS = [
    (r"\brm\s+(-[^\s]*\s+)*(-[^\s]*[rR][^\s]*|--recursive)(\s+-[^\s]*)*\s+(--\s+)?(/|/\*|~|~/|~/\*|\$HOME|\$HOME/\*|/home|/etc|/usr|/boot|/var|/bin|/sbin|/lib|/lib64|/opt|/root|/srv)/?(\s|$)",
     "deletes a whole system or home folder"),
    (r"\bmkfs(\.\w+)?\b", "formats a disk"),
    (r"\bwipefs\b", "erases disk signatures"),
    (r"\b(fdisk|sfdisk|cfdisk|gdisk|sgdisk|parted)\b(?!\s+(-l|--list)\b)", "changes disk partitions"),
    (r"\bdd\b[^|;&]*\bof=/dev/", "writes directly to a disk"),
    (r">\s*/dev/(sd|nvme|hd|vd|mmcblk)", "writes directly to a disk"),
    (r"\bshred\b", "permanently destroys files"),
    (r"\bcryptsetup\s+(luksFormat|erase|luksErase)\b", "destroys encrypted disk data"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bchmod\s+(-[^\s]+\s+)*[0-7]*7[0-7]{2}\s+/(\s|$)", "opens up permissions on the whole system"),
    (r"\bchown\s+(-[^\s]+\s+)*-R\s+\S+\s+/(\s|$)|\bchown\s+-R\s+(-[^\s]+\s+)*\S+\s+/(\s|$)",
     "changes the owner of every file"),
    (r"\b(apt|apt-get|aptitude)\b[^|;&]*\b(remove|purge|autoremove)\b[^|;&]*\b("
     r"plasma-workspace|plasma-desktop|sddm|systemd|systemd-sysv|linux-image-\S*|grub-\S*|"
     r"network-manager|python3|sudo|pkexec|polkitd|libc6|bash|coreutils|dpkg|apt|kwin-wayland|"
     r"kwin-x11|xserver-xorg-core|init|dbus|login|passwd|util-linux|mount|firefox|"
     r"systemsettings)(\s|$|:)", "removes a core part of the system"),
    (r"--allow-remove-essential", "removes essential packages"),
    (r"\bdpkg\b[^|;&]*--force-(all|remove-essential|depends)", "forces package removal"),
    (r"\bkill\s+(-\S+\s+)*-1(\s|$)", "kills every process"),
    (r"\bsystemctl\s+(disable|mask|stop)\s+(--now\s+)?(sddm|display-manager|NetworkManager|dbus|polkit|systemd-\S+)",
     "stops a core system service"),
    (r"\buserdel\b", "deletes a user account"),
    (r"\bcrontab\s+-r\b", "deletes all scheduled tasks"),
    (r"\bmv\s+(-\S+\s+)*/(etc|usr|boot|bin|lib|home)(\s|/\s)", "moves a system folder"),
    (r"(^|[;&|]\s*)(systemctl\s+)?(reboot|poweroff|shutdown|halt)\b", "restarts or turns off the computer"),
    (r"\bsnapper\b[^|;&]*\b(delete|rollback)\b", "deletes or rolls back system snapshots"),
    (r"\bcurl\b[^|;&]*\|\s*(sudo\s+)?(ba)?sh\b|\bwget\b[^|;&]*\|\s*(sudo\s+)?(ba)?sh\b",
     "runs a script straight from the internet"),
]
_DANGER_RE = [(re.compile(p), why) for p, why in DANGER_PATTERNS]

_SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n"}
_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<", "<<", "<<<", ">&", "<&", "<>"}


# Options that take a value, so the value is not mistaken for a subcommand.
_VALUE_OPTIONS = {"-c", "--config", "-H", "--host", "-M", "--machine", "-u", "--unit", "-o",
                  "--output", "-t", "-f", "--fields", "-p", "--priority", "-n", "--lines",
                  "--root", "--image", "--installation"}


def _first_arg(args):
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _VALUE_OPTIONS:
            skip = True
            continue
        if not a.startswith("-"):
            return a
    return None


def _has_any(args, words):
    return any(a in words for a in args)


def _flag_args(args):
    return [a for a in args if a.startswith("-")]


def _rule_read(name: str, args: list) -> bool:
    """Is this command (with these arguments) read-only?"""
    if name in ALWAYS_READ:
        if name in ("awk", "gawk", "mawk"):
            prog = " ".join(args)
            return not re.search(r"system\s*\(|>\s*\"|\|\s*\"|getline", prog)
        if name == "command":
            return bool(args) and args[0] in ("-v", "-V")
        if name == "lsof" or name == "ldd":
            return True
        return True
    sub = _first_arg(args)
    if name == "systemctl":
        return sub is None or sub in {
            "status", "show", "cat", "list-units", "list-unit-files", "list-timers",
            "list-sockets", "list-dependencies", "is-active", "is-enabled", "is-failed",
            "is-system-running", "get-default", "list-jobs"}
    if name == "journalctl":
        return not any(a.startswith(("--vacuum", "--rotate", "--flush", "--sync", "--relinquish",
                                     "--smart-relinquish", "--setup-keys", "--update-catalog"))
                       for a in args)
    if name == "apt":
        return sub in {"list", "search", "show", "policy", "depends", "rdepends", "showsrc"}
    if name == "apt-mark":
        return bool(sub) and sub.startswith("show")
    if name == "dpkg":
        reads = {"-l", "--list", "-L", "--listfiles", "-s", "--status", "-S", "--search", "-p",
                 "--print-avail", "--print-architecture", "--print-foreign-architectures",
                 "--get-selections", "-c", "--contents", "-I", "--info", "--audit", "-C",
                 "--verify", "-V", "--version", "--compare-versions"}
        flags = _flag_args(args)
        return bool(flags) and flags[0] in reads
    if name == "flatpak":
        return args == ["--version"] or sub in {
            "list", "search", "info", "remotes", "remote-ls", "remote-info", "history", "ps"}
    if name == "ollama":
        return sub in {"list", "ls", "ps", "show"} or args in (["--version"], ["-v"])
    if name == "nmcli":
        return not _has_any(args, {"connect", "up", "down", "modify", "add", "delete", "del",
                                   "set", "on", "off", "reload", "disconnect", "import",
                                   "hotspot", "edit", "clone", "migrate", "monitor", "load"})
    if name == "ip":
        return not _has_any(args, {"add", "del", "delete", "set", "flush", "change", "replace",
                                   "append", "prepend", "exec", "save", "restore"})
    if name in ("hostnamectl", "timedatectl", "localectl"):
        return sub is None or sub in {"status", "show"} or sub.startswith("list")
    if name == "loginctl":
        return sub is None or sub.startswith(("list", "show")) or sub in {
            "session-status", "user-status", "seat-status"}
    if name == "resolvectl":
        return sub in {None, "status", "query", "statistics", "show-cache", "show-server-state"}
    if name == "find":
        return not _has_any(args, {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
                                   "-fprint0", "-fprintf", "-fls"})
    if name == "nvidia-smi":
        return not any(a.split("=")[0] in {
            "-pm", "--persistence-mode", "-r", "--gpu-reset", "-pl", "--power-limit", "-e",
            "--ecc-config", "-c", "--compute-mode", "-ac", "-rac", "-lgc", "-rgc", "-lmc",
            "-rmc", "-mig", "--applications-clocks"} for a in args)
    if name == "xrandr":
        return set(args) <= {"-q", "--query", "--listmonitors", "--listactivemonitors",
                             "--verbose", "--current", "--prop", "--props", "--listproviders"}
    if name == "kscreen-doctor":
        return set(args) <= {"-o", "--outputs", "-j", "--json"}
    if name == "wpctl":
        return sub in {"status", "inspect", "get-volume"}
    if name == "pactl":
        return bool(sub) and (sub in {"info", "stat"} or sub.startswith(("list", "get-")))
    if name in ("aplay", "arecord"):
        return bool(args) and set(args) <= {"-l", "-L", "--list-devices", "--list-pcms"}
    if name == "rfkill":
        return not _has_any(args, {"block", "unblock", "toggle"})
    if name == "iw":
        return not _has_any(args, {"set", "connect", "disconnect", "del", "add", "ibss", "mesh",
                                   "start", "stop", "switch", "offchannel"})
    if name == "bluetoothctl":
        return sub in {"show", "devices", "list", "info", "paired-devices", "version"}
    if name == "ping":
        return any(a == "-c" or (a.startswith("-c") and a[2:].isdigit()) for a in args)
    if name == "powerprofilesctl":
        return sub in {None, "get", "list", "version"}
    if name == "snapper":
        return sub in {"list", "ls", "status", "diff", "list-configs", "get-config", "xadiff"}
    if name == "mokutil":
        return bool(args) and set(args) <= {"--sb-state", "--list-enrolled", "-l", "--list-new",
                                            "-N", "--sb", "--pk", "--kek", "--db", "--dbx"}
    if name == "efibootmgr":
        return set(args) <= {"-v", "--verbose"}
    if name == "fwupdmgr":
        return sub in {"get-devices", "get-updates", "get-upgrades", "get-history",
                       "get-remotes", "security", "get-releases"}
    if name == "gsettings":
        return sub in {"get", "list-schemas", "list-keys", "list-children", "list-recursively",
                       "range", "describe", "writable"}
    if name == "xdg-mime":
        return sub == "query"
    if name == "xdg-settings":
        return sub in {"get", "check"}
    if name in ("swapon", "mount"):
        return not args or args == ["--show"]
    if name == "dmesg":
        return not _has_any(args, {"-C", "--clear", "-c", "--read-clear", "-n", "--console-level",
                                   "-D", "-E", "--console-off", "--console-on"})
    if name in ("clamscan", "clamdscan"):
        return not any(a.startswith(("--remove", "--move", "--copy")) for a in args)
    if name == "top":
        return any(a.startswith("-b") or a == "-bn1" for a in args)
    if name == "hostname":
        return not args
    return False


def _tokens(command: str):
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def split_commands(command: str):
    """Split into simple commands. Returns (list of [argv], wrote_files, parse_ok)."""
    try:
        toks = _tokens(command)
    except ValueError:
        return [], True, False
    commands, current = [], []
    writes = False
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _SEPARATORS or (set(t) <= set(";&|()") and t):
            if current:
                commands.append(current)
            current = []
            i += 1
            continue
        if t in _REDIRECTS or (t and set(t) <= set("<>&|")):
            target = toks[i + 1] if i + 1 < len(toks) else ""
            # "2>/dev/null", "2>&1" and input redirection are harmless.
            if t.startswith("<") or t in (">&", "<&") or target in ("/dev/null", "1", "2"):
                pass
            else:
                writes = True
            if current and current[-1].isdigit():
                current.pop()  # the "2" of "2>"
            i += 2
            continue
        current.append(t)
        i += 1
    if current:
        commands.append(current)
    return commands, writes, True


def _strip_assignments(argv):
    i = 0
    while i < len(argv) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[i]):
        i += 1
    rest = argv[i:]
    # Wrappers that just run the next command.
    while rest and rest[0] in ("env", "nice", "ionice", "time", "timeout", "stdbuf", "nohup",
                               "sudo", "pkexec", "doas", "exec"):
        if rest[0] == "env" and len(rest) == 1:
            return rest
        rest = rest[1:]
        while rest and (rest[0].startswith("-") or re.match(r"^\d+[smhd]?$", rest[0])
                        or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0])):
            rest = rest[1:]
    return rest


# Paths that must never be deleted recursively: the whole disk, a home folder,
# a system folder, or "everything here" (*, .).
_DANGER_TARGET = re.compile(
    r"^(/|/\*|\*|\.|\./|\./\*|\.\.|\.\./|~|~/|~/\*|\$HOME|\$HOME/|\$HOME/\*|\$\{HOME\}|\$\{HOME\}/|"
    r"\$\{HOME\}/\*|/home|/home/|/home/\*|/home/[^/]+|/home/[^/]+/|/home/[^/]+/\*|"
    r"/(etc|usr|boot|var|bin|sbin|lib|lib32|lib64|opt|root|srv|dev|proc|sys)(/|/\*)?)$")


def _dangerous_argv(argv) -> str:
    """Reason if this single command deletes a whole system or home folder."""
    if not argv:
        return ""
    name, args = os.path.basename(argv[0]), argv[1:]
    if name == "rm":
        recursive = any(a in ("-r", "-R", "--recursive") or
                        (a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a))
                        for a in args)
        targets = [a for a in args if not a.startswith("-")]
        if recursive and any(_DANGER_TARGET.match(t) for t in targets):
            return "deletes a whole system or home folder"
    if name == "find":
        paths = []
        for a in args:
            if a.startswith("-") or a in ("(", "!", ")"):
                break
            paths.append(a)
        deletes = "-delete" in args or any(
            a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args) and
            os.path.basename(args[i + 1]) in ("rm", "shred", "unlink") for i, a in enumerate(args))
        if deletes and any(_DANGER_TARGET.match(p) for p in paths):
            return "deletes files across a whole system or home folder"
    return ""


_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_SHELLS = {"sh", "bash", "dash", "zsh"}


def classify(command: str, _depth: int = 0):
    """Return (level, reason): level is READ, CHANGE or DANGER."""
    text = command.strip()
    if not text:
        return READ, ""
    commands, writes, ok = split_commands(text)
    # Quoted text is data (a message, a file name), so the whole-line check skips it ...
    masked = _QUOTED.sub("QUOTED", text)
    # ... and each command is checked again without quotes, because dd of="/dev/sda"
    # is as bad as dd of=/dev/sda. Words with spaces or ; | & inside are data too.
    unquoted = [" ".join("ARG" if re.search(r"[\s;&|<>()`]", t) else t for t in argv)
                for argv in (commands if ok else [])]
    for rx, why in _DANGER_RE:
        if rx.search(masked) or any(rx.search(c) for c in unquoted):
            return DANGER, why
    for argv in commands if ok else []:
        argv = _strip_assignments(argv)
        why = _dangerous_argv(argv)
        if why:
            return DANGER, why
        # bash -c "rm -rf /" hides the real command in a string: check that too.
        if argv and os.path.basename(argv[0]) in _SHELLS and "-c" in argv and _depth < 3:
            inner = argv[argv.index("-c") + 1:argv.index("-c") + 2]
            if inner and classify(inner[0], _depth + 1)[0] == DANGER:
                return DANGER, classify(inner[0], _depth + 1)[1]
    if "$(" in text or "`" in text or "<(" in text or ">(" in text:
        return CHANGE, "uses command substitution"
    if not ok:
        return CHANGE, "could not be analysed"
    if writes:
        return CHANGE, "writes to a file"
    for argv in commands:
        argv = _strip_assignments(argv)
        if not argv:
            continue
        name = os.path.basename(argv[0])
        if name == "tee":
            if all(a == "/dev/null" or a.startswith("-") for a in argv[1:]):
                continue
            return CHANGE, "writes to a file"
        if not _rule_read(name, argv[1:]):
            return CHANGE, f"'{name}' can change the system"
    return READ, ""


def interactive_problem(command: str):
    """A message if the command would wait for keyboard input, else None."""
    commands, _, ok = split_commands(command)
    if not ok:
        return None
    for argv in commands:
        argv = _strip_assignments(argv)
        if not argv:
            continue
        name = os.path.basename(argv[0])
        if name == "su":  # sudo and pkexec are stripped above: admin commands go through pkexec
            return ("'su' asks for a password on a keyboard, which cannot work "
                    "here. To run a command as administrator, use run_command with admin=true: the "
                    "user then confirms it in the system's password dialog.")
        if name in INTERACTIVE:
            return (f"'{name}' is interactive and cannot run here (there is no keyboard). "
                    "Use a non-interactive command instead, e.g. cat/grep to read files, "
                    "write_file to edit them, 'top -bn1' or 'ps' for processes.")
        if name in _NEEDS_ARGS_TO_BE_SAFE and len(argv) == 1:
            return f"'{name}' without arguments waits for input. Pass a command or script."
        if name == "top" and not any(a.startswith("-b") for a in argv[1:]):
            return "Use 'top -bn1' (batch mode) instead of interactive top."
        verbs = [a for a in argv[1:] if not a.startswith("-")]
        if name == "crontab" and any(a in ("-e", "--edit") for a in argv[1:]):
            return ("'crontab -e' opens an editor, which cannot work here. To run a script on a "
                    "schedule use create_script with its schedule (it sets up a timer). For other "
                    "cron jobs, write the file with write_file and load it with 'crontab FILE'.")
        if name == "systemctl" and verbs[:1] == ["edit"]:
            return ("'systemctl edit' opens an editor, which cannot work here. Write a drop-in file "
                    "with write_file instead (e.g. /etc/systemd/system/NAME.service.d/override.conf), "
                    "then run 'systemctl daemon-reload'.")
        if name in ("visudo", "vipw", "vigr", "sudoedit", "edquota"):
            return f"'{name}' opens an editor, which cannot work here. Use read_file and write_file."
    return None


_SUDO_RE = re.compile(r"(^|[;&|(]\s*|\b(?:then|do|else)\s+)sudo\s+((?:-[EHnSk]+\s+)*)(?!-)")


def strip_sudo(command: str):
    """Remove 'sudo' (we run admin commands through pkexec). Returns (cmd, had_sudo)."""
    new, count = _SUDO_RE.subn(lambda m: m.group(1), command)
    return new, count > 0


_SEGMENT_SPLIT = re.compile(r"(\s*(?:&&|\|\||;|\|)\s*)")
_APT_RE = re.compile(r"^(\s*(?:\w+=\S+\s+)*)(apt|apt-get|aptitude)(\s+(?:-\S+\s+)*)"
                     r"(install|remove|purge|upgrade|full-upgrade|dist-upgrade|autoremove|reinstall)\b")
_FLATPAK_RE = re.compile(r"^(\s*(?:\w+=\S+\s+)*)flatpak(\s+(?:-\S+\s+)*)"
                         r"(install|uninstall|update|remove|upgrade|repair)\b")


def ensure_noninteractive(command: str) -> str:
    """Add -y to apt and flatpak commands that would otherwise ask 'Continue? [Y/n]'."""
    parts = _SEGMENT_SPLIT.split(command)
    out = []
    for part in parts:
        if _SEGMENT_SPLIT.fullmatch(part or " ") and part.strip():
            out.append(part)
            continue
        words = part.split()
        m = _APT_RE.match(part)
        if m and not any(w in ("-y", "--yes", "--assume-yes", "-qy", "-yq") for w in words):
            part = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)} -y" + part[m.end():]
        m = _FLATPAK_RE.match(part)
        if m and not any(w in ("-y", "--assumeyes", "--noninteractive") for w in words):
            part = f"{m.group(1)}flatpak{m.group(2)}{m.group(3)} -y --noninteractive" + part[m.end():]
        out.append(part)
    return "".join(out)


def needs_approval(mode: str, level: str, admin: bool) -> bool:
    """Should the user confirm this action? mode: always | smart | never."""
    if level == DANGER:
        return True
    if mode == "never":
        return False
    if mode == "always" or admin:
        return True
    return level != READ
