from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

def analyze_run_directory(run_dir: str | Path) -> Path:
    root = Path(run_dir)
    summaries = _collect_summaries(root)
    if summaries.empty:
        raise ValueError(f"No run summaries found under {root}")

    plots_dir = root / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    summary_path = root / "analysis_summary.csv"
    summaries.to_csv(summary_path, index=False)

    if "arm" in summaries.columns:
        _plot_bar(summaries, plots_dir / "delivery_ratio.png", "delivery_ratio", "Delivery Ratio")
        _plot_bar(summaries, plots_dir / "command_p95_s.png", "command_p95_s", "Command P95 (s)")
        _plot_bar(
            summaries,
            plots_dir / "route_recovery_time_avg_s.png",
            "route_recovery_time_avg_s",
            "Avg Recovery Window (s)",
        )
        _plot_bar(summaries, plots_dir / "relative_energy_cost.png", "relative_energy_cost", "Energy Cost")

    if "payload_mode" in summaries.columns and "delivery_ratio" in summaries.columns:
        pivot = summaries.pivot_table(
            index="payload_mode", columns="arm", values="delivery_ratio", aggfunc="mean"
        )
        _plot_heatmap(pivot, plots_dir / "delivery_ratio_by_payload.png", "Delivery Ratio by Payload Mode")

    return plots_dir


def _collect_summaries(root: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for summary_file in root.rglob("summary.json"):
        summary = json.loads(summary_file.read_text())
        row = dict(summary)
        relative = summary_file.parent.relative_to(root)
        parts = relative.parts
        if len(parts) >= 2:
            row.setdefault("scenario", parts[-2])
            row.setdefault("arm", parts[-1])
            scenario_bits = parts[-2].split("_")
            if len(scenario_bits) >= 5:
                row["node_count"] = scenario_bits[1].removesuffix("n") if scenario_bits[1].endswith("n") else None
                row["load"] = scenario_bits[2]
                row["payload_mode"] = scenario_bits[3]
                row["disruption"] = scenario_bits[4]
        elif len(parts) == 1:
            row.setdefault("arm", parts[0])
        rows.append(row)

    if not rows and (root / "combined_summary.csv").exists():
        return pd.read_csv(root / "combined_summary.csv")
    return pd.DataFrame(rows)


def _plot_bar(frame: pd.DataFrame, path: Path, value_column: str, title: str) -> None:
    numeric = frame.copy()
    numeric[value_column] = pd.to_numeric(numeric[value_column], errors="coerce")
    data = numeric.groupby("arm", as_index=False)[value_column].mean()
    if data.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(data["arm"], data[value_column], color=["#4c78a8", "#f58518", "#54a24b"])
    ax.set_title(title)
    ax.set_xlabel("Arm")
    ax.set_ylabel(value_column)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _plot_heatmap(pivot: pd.DataFrame, path: Path, title: str) -> None:
    if pivot.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    image = ax.imshow(pivot.fillna(0).to_numpy(), aspect="auto", cmap="viridis")
    ax.set_title(title)
    ax.set_xticks(range(len(pivot.columns)), pivot.columns)
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    fig.colorbar(image, ax=ax)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
