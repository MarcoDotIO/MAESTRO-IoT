from __future__ import annotations

from pathlib import Path

from maestro_sim.config import PayloadProfile, PolicyConfig, ProtocolConfig
from maestro_sim.policy import FAMEPolicy
from maestro_sim.simulation import SimulationEngine
from maestro_sim.topology import build_generated_config


def build_engine(tmp_path: Path) -> SimulationEngine:
    config = build_generated_config(
        name="policy-test",
        duration_s=10.0,
        seed=7,
        node_count=10,
        telemetry_interval_s=5.0,
        payload_profile=PayloadProfile(
            base_bytes=84,
            optional_fields=(18, 14, 10),
            urgent_extra_bytes=18,
            urgent_probability=0.1,
        ),
        disruption_mode="router_power_off",
        policy=PolicyConfig(),
        protocol=ProtocolConfig(),
        arms=("maestro",),
        output_dir=str(tmp_path),
    )
    engine = SimulationEngine(config=config, arm="maestro", output_dir=tmp_path / "maestro")
    for node_id in engine.nodes:
        engine.commission_node(node_id)
    engine.initialise_parents()
    return engine


def test_nonurgent_fame_drops_optional_fields_and_stretches_interval(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    node = engine.nodes["sensor-1"]
    policy = FAMEPolicy(engine.config.policy)

    outcome = policy.evaluate(
        engine=engine,
        node_id="sensor-1",
        predicted_payload_bytes=126,
        optional_fields=(18, 14, 10),
        urgent=False,
        reason="test",
    )

    assert outcome.payload_bytes >= engine.config.policy.fragmentation_budget_bytes
    assert outcome.interval_s >= node.current_interval_s
    assert outcome.decision.optional_fields_dropped >= 1


def test_urgent_fame_preserves_interval_and_payload(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    policy = FAMEPolicy(engine.config.policy)
    original_interval = engine.nodes["sensor-1"].current_interval_s

    outcome = policy.evaluate(
        engine=engine,
        node_id="sensor-1",
        predicted_payload_bytes=144,
        optional_fields=(18, 14, 10),
        urgent=True,
        reason="urgent",
    )

    assert outcome.interval_s == original_interval
    assert outcome.payload_bytes == 144
    assert outcome.decision.optional_fields_dropped == 0


def test_parent_switch_requires_hold_down_expiry(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    node = engine.nodes["sensor-1"]
    current_parent = node.current_parent
    assert current_parent is not None
    alternative = next(candidate for candidate in engine.get_candidate_parents("sensor-1") if candidate != current_parent)

    engine.links[tuple(sorted(("sensor-1", current_parent)))].margin = 0.35
    engine.links[tuple(sorted(("sensor-1", alternative)))].margin = 0.95
    node.hold_down_until_s = 100.0

    blocked = engine.fame.evaluate(
        engine=engine,
        node_id="sensor-1",
        predicted_payload_bytes=80,
        optional_fields=(10, 8),
        urgent=False,
        reason="hold-down",
    )
    assert blocked.selected_parent == current_parent

    node.hold_down_until_s = 0.0
    switched = engine.fame.evaluate(
        engine=engine,
        node_id="sensor-1",
        predicted_payload_bytes=80,
        optional_fields=(10, 8),
        urgent=False,
        reason="hold-down-expired",
    )
    assert switched.selected_parent == alternative
