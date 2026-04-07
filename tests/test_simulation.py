from __future__ import annotations

from pathlib import Path

import pandas as pd

from maestro_sim.analysis import analyze_run_directory
from maestro_sim.config import PayloadProfile, PolicyConfig, ProtocolConfig, SweepMatrixConfig, TrafficProfile
from maestro_sim.simulation import run_experiment, run_sweep
from maestro_sim.topology import build_generated_config


def build_config(tmp_path: Path, *, name: str, payload_profile: PayloadProfile):
    return build_generated_config(
        name=name,
        duration_s=40.0,
        seed=19,
        node_count=10,
        telemetry_interval_s=4.0,
        payload_profile=payload_profile,
        disruption_mode="router_power_off",
        policy=PolicyConfig(),
        protocol=ProtocolConfig(),
        arms=("zigbee", "matter_thread", "maestro"),
        output_dir=str(tmp_path / "outputs"),
    )


def test_run_experiment_is_deterministic(tmp_path: Path) -> None:
    payload = PayloadProfile(base_bytes=68, optional_fields=(10, 8, 6), urgent_extra_bytes=14, urgent_probability=0.0)
    config = build_config(tmp_path, name="deterministic", payload_profile=payload)

    run_a = run_experiment(config, output_root=tmp_path / "run-a")
    run_b = run_experiment(config, output_root=tmp_path / "run-b")

    summary_a = pd.read_csv(run_a / "combined_summary.csv").sort_values("arm").reset_index(drop=True)
    summary_b = pd.read_csv(run_b / "combined_summary.csv").sort_values("arm").reset_index(drop=True)
    pd.testing.assert_frame_equal(summary_a, summary_b)


def test_maestro_emits_policy_decisions_and_reduces_fragmentation(tmp_path: Path) -> None:
    payload = PayloadProfile(base_bytes=84, optional_fields=(18, 14, 10), urgent_extra_bytes=18, urgent_probability=0.05)
    config = build_config(tmp_path, name="fragmenting", payload_profile=payload)

    run_dir = run_experiment(config, output_root=tmp_path / "fragment-run")

    combined = pd.read_csv(run_dir / "combined_summary.csv").set_index("arm")
    decisions = pd.read_csv(run_dir / "maestro" / "policy_decisions.csv")

    assert not decisions.empty
    assert combined.loc["maestro", "fragment_count"] < combined.loc["matter_thread", "fragment_count"]
    assert combined.loc["maestro", "retransmission_rate"] <= combined.loc["matter_thread", "retransmission_rate"]


def test_analyze_generates_plot_artifacts(tmp_path: Path) -> None:
    payload = PayloadProfile(base_bytes=68, optional_fields=(10, 8, 6), urgent_extra_bytes=14, urgent_probability=0.0)
    config = build_config(tmp_path, name="analysis", payload_profile=payload)

    run_dir = run_experiment(config, output_root=tmp_path / "analysis-run")
    plots_dir = analyze_run_directory(run_dir)

    assert (plots_dir / "delivery_ratio.png").exists()
    assert (plots_dir / "command_p95_s.png").exists()


def test_disruption_produces_recovery_metrics(tmp_path: Path) -> None:
    payload = PayloadProfile(base_bytes=68, optional_fields=(10, 8, 6), urgent_extra_bytes=14, urgent_probability=0.0)
    config = build_config(tmp_path, name="recovery", payload_profile=payload)

    run_dir = run_experiment(config, output_root=tmp_path / "recovery-run")
    combined = pd.read_csv(run_dir / "combined_summary.csv").set_index("arm")

    assert combined.loc["zigbee", "application_outage_window_avg_s"] >= 0
    assert combined.loc["matter_thread", "route_recovery_time_avg_s"] > 0
    assert combined.loc["maestro", "ack_timeouts"] >= 0


def test_small_sweep_writes_manifest(tmp_path: Path) -> None:
    matrix = SweepMatrixConfig(
        name="tiny",
        output_dir=str(tmp_path / "sweeps"),
        duration_s=18.0,
        base_seed=5,
        repetitions=1,
        node_counts=(10,),
        load_levels=("low",),
        payload_modes=("small",),
        disruption_modes=("router_power_off",),
        traffic=TrafficProfile(
            telemetry_interval_s=5.0,
            telemetry_jitter_s=0.25,
            command_interval_s=10.0,
            command_jitter_s=0.0,
            payload_profile=PayloadProfile(
                base_bytes=60,
                optional_fields=(10, 8, 6),
                urgent_extra_bytes=12,
                urgent_probability=0.0,
            ),
            command_target="actuator-1",
            warmup_s=1.0,
        ),
        policy=PolicyConfig(),
        protocol=ProtocolConfig(),
    )

    sweep_dir = run_sweep(matrix, output_root=tmp_path / "sweep-root")
    manifest = pd.read_csv(sweep_dir / "manifest.csv")

    assert len(manifest) == 1
    assert (sweep_dir / "analysis_summary.csv").exists()
