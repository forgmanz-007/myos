"""python3 -m myos_security [app|run-exe|properties|scan|compress] ..."""
import sys


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "app"
    rest = sys.argv[2:]
    if cmd == "run-exe":
        from .guard import main as run
        return run(rest)
    if cmd == "properties":
        from .compat_dialog import main as run
        return run(rest)
    if cmd == "compress":
        from .compress_dialog import main as run
        return run(rest)
    if cmd == "scan":
        from .scan_cli import main as run
        return run(rest)
    from .app import main as run
    return run(rest if cmd == "app" else sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
