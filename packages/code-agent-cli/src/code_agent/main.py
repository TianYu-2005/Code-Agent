"""Entry point dispatching between the TUI (default) and the classic CLI."""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-agent",
        description="Terminal coding agent",
    )
    parser.add_argument("--cli", action="store_true", help="use the classic line-based CLI")
    parser.add_argument("--workspace", help="working directory for the agent")
    parser.add_argument("--model", help="model name or profile to use")
    parser.add_argument("--api-key", help="API key override")
    parser.add_argument("--base-url", help="API base URL override")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the TUI by default; fall back to the CLI via --cli or non-TTY."""
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if not args.cli and not sys.stdout.isatty():
        args.cli = True  # pipes and CI environments cannot host the TUI

    overrides = {
        "model": args.model,
        "api_key": args.api_key,
        "base_url": args.base_url,
    }

    if args.cli:
        from .cli.app import main as cli_main

        cli_main(workspace=args.workspace, overrides=overrides)
    else:
        from .tui.app import run_tui

        run_tui(workspace=args.workspace, overrides=overrides)


if __name__ == "__main__":
    main()
