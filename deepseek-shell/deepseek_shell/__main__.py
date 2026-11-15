"""deepseek-shell [--settings | --diagnose | --cli | --ask TEXT | --prefill TEXT | --run-script FILE]"""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="deepseek-shell",
                                     description="Your local AI assistant (DeepSeek, runs offline).")
    parser.add_argument("--settings", action="store_true", help="open Settings (model, tools, safety)")
    parser.add_argument("--diagnose", action="store_true", help="check this PC for problems")
    parser.add_argument("--cli", action="store_true", help="text mode for a console without a desktop")
    parser.add_argument("--ask", metavar="TEXT", default="", help="start with this request")
    parser.add_argument("--prefill", metavar="TEXT", default="",
                        help="put this text in the message box without sending it")
    parser.add_argument("--run-script", metavar="FILE", default="",
                        help="run one of your scripts in a window that shows its output")
    args = parser.parse_args()

    if args.cli:
        from .cli import main as cli_main
        return cli_main(args.ask)
    try:
        from .app import run_gui
    except ImportError as e:
        if args.run_script:
            print(f"The window could not open ({e}).", file=sys.stderr)
            import subprocess
            return subprocess.call([args.run_script])
        print(f"The window could not open ({e}). Falling back to text mode.\n", file=sys.stderr)
        from .cli import main as cli_main
        return cli_main(args.ask)
    return run_gui(settings=args.settings, diagnose=args.diagnose, ask=args.ask,
                   prefill=args.prefill, run_script=args.run_script)


if __name__ == "__main__":
    sys.exit(main())
