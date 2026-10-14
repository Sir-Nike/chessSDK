import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(prog="chess-sdk")
    commands = parser.add_subparsers(dest="subcommand", required=True)
    run = commands.add_parser("run", help="Run the optional Python starter")
    run.add_argument("--agent", default="agent:choose")
    check = commands.add_parser("check", help="Check any harness against the local mock protocol")
    check.add_argument("--agent", default="agent:choose")
    check.add_argument("--colour", choices=["white", "black"], default="white")
    check.add_argument("--timeout", type=float, default=15)
    check.add_argument("--report")
    check.add_argument(
        "--command",
        dest="executable",
        nargs=argparse.REMAINDER,
        help="Alternate executable and arguments; put this option last",
    )
    practice = commands.add_parser(
        "practice", help="Play against the personal Stockfish API using your local agent"
    )
    practice.add_argument("--url", default=None)
    practice.add_argument("--session", help="Reconnect to an existing session")
    practice_agent = practice.add_mutually_exclusive_group()
    practice_agent.add_argument(
        "--agent", help="MODULE:CALLABLE taking a session and returning a UCI move"
    )
    practice_agent.add_argument(
        "--model", help="Local GGUF path; run it with llama.cpp for each practice move"
    )
    practice.add_argument(
        "--llama-server", help="llama-server executable (defaults to PATH or LLAMA_SERVER)"
    )
    practice.add_argument("--gpu-layers", type=int, default=99, help="llama.cpp GPU layers (0 for CPU)")
    practice.add_argument("--context-size", type=int, default=4096)
    practice.add_argument(
        "--colour", "--color", choices=["white", "black", "random"], default="random"
    )
    practice.add_argument("--level", type=int, choices=range(1, 11), default=5)
    practice.add_argument("--white-minutes", type=float, default=10)
    practice.add_argument("--black-minutes", type=float, default=10)
    practice.add_argument("--increment", type=float, default=0)
    args = parser.parse_args()
    if args.subcommand == "run":
        from .runner import Harness, load_agent

        Harness(load_agent(args.agent)).run()
    elif args.subcommand == "practice":
        from .practice import run_practice

        run_practice(args)
    else:
        from .check import check_harness

        result = check_harness(
            args.executable or [sys.executable, "-m", "chess_sdk", "run", "--agent", args.agent],
            colour=args.colour,
            timeout=args.timeout,
        )
        if args.report:
            from pathlib import Path

            path = Path(args.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["passed"] else 1)
