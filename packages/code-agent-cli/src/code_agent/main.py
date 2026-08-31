"""Entry point dispatching between the TUI (default) and the classic CLI."""

import sys


def main() -> None:
    """Run the TUI by default; fall back to the CLI via --cli or non-TTY."""
    use_cli = "--cli" in sys.argv[1:]
    if not use_cli and not sys.stdout.isatty():
        use_cli = True  # pipes and CI environments cannot host the TUI
    if use_cli:
        from .cli.app import main as cli_main

        cli_main()
    else:
        from .tui.app import run_tui

        run_tui()


if __name__ == "__main__":
    main()
