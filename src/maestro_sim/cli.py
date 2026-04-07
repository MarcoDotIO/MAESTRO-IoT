from __future__ import annotations

import argparse
from pathlib import Path

from .analysis import analyze_run_directory
from .config import load_simulation_config, load_sweep_matrix_config
from .simulation import run_experiment, run_sweep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maestro-sim", description="MAESTRO simulation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a single simulation config")
    run_parser.add_argument("config", help="Path to simulation TOML/JSON config")
    run_parser.add_argument("--output-root", help="Override the root output directory")

    sweep_parser = subparsers.add_parser("sweep", help="Run a matrix sweep")
    sweep_parser.add_argument("config", help="Path to sweep matrix TOML/JSON config")
    sweep_parser.add_argument("--output-root", help="Override the root output directory")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze an existing run directory")
    analyze_parser.add_argument("run_dir", help="Run directory produced by `maestro-sim run` or `sweep`")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        config = load_simulation_config(args.config)
        run_dir = run_experiment(config, output_root=args.output_root)
        print(run_dir)
        return 0
    if args.command == "sweep":
        matrix = load_sweep_matrix_config(args.config)
        sweep_dir = run_sweep(matrix, output_root=args.output_root)
        print(sweep_dir)
        return 0
    if args.command == "analyze":
        plots_dir = analyze_run_directory(Path(args.run_dir))
        print(plots_dir)
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2
