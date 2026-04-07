from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ArmName, FailureKind, NodeRole

DEFAULT_ARMS: tuple[ArmName, ...] = ("zigbee", "matter_thread", "maestro")


@dataclass(frozen=True)
class NodeSpec:
    id: str
    role: NodeRole
    candidate_parents: tuple[str, ...] = ()
    always_on: bool = True
    initial_active: bool = True


@dataclass(frozen=True)
class LinkSpec:
    a: str
    b: str
    margin: float
    latency_ms: int = 25
    jitter_ms: int = 4


@dataclass(frozen=True)
class PayloadProfile:
    base_bytes: int
    optional_fields: tuple[int, ...] = ()
    urgent_extra_bytes: int = 20
    urgent_probability: float = 0.05


@dataclass(frozen=True)
class TrafficProfile:
    telemetry_interval_s: float
    telemetry_jitter_s: float
    command_interval_s: float
    command_jitter_s: float
    payload_profile: PayloadProfile
    command_target: str
    warmup_s: float = 1.0


@dataclass(frozen=True)
class FailureSchedule:
    at_s: float
    kind: FailureKind
    target: str | None = None
    duration_s: float | None = None
    link: tuple[str, str] | None = None
    degrade_to_margin: float | None = None


@dataclass(frozen=True)
class PolicyConfig:
    w1: float = 0.35
    w2: float = 0.25
    w3: float = 0.25
    w4: float = 0.15
    delta: float = 0.10
    beta: float = 0.40
    gamma: float = 0.60
    fragmentation_budget_bytes: int = 80
    min_interval_s: float = 1.0
    max_interval_s: float = 60.0
    sliding_window_size: int = 10
    hold_down_s: float = 5.0


@dataclass(frozen=True)
class ProtocolConfig:
    ack_timeout_s: float = 0.75
    max_retries: int = 3
    retry_backoff_s: float = 0.15
    thread_repair_delay_s: float = 3.0
    zigbee_repair_delay_s: float = 1.5
    parent_search_interval_s: float = 5.0
    controller_hop_latency_ms: int = 10
    service_time_per_fragment_s: float = 0.03
    queue_energy_factor: float = 0.08


@dataclass(frozen=True)
class SimulationConfig:
    name: str
    duration_s: float
    seed: int
    arms: tuple[ArmName, ...] = DEFAULT_ARMS
    nodes: tuple[NodeSpec, ...] = ()
    links: tuple[LinkSpec, ...] = ()
    traffic: TrafficProfile = field(
        default_factory=lambda: TrafficProfile(
            telemetry_interval_s=5.0,
            telemetry_jitter_s=0.5,
            command_interval_s=15.0,
            command_jitter_s=0.0,
            payload_profile=PayloadProfile(base_bytes=60, optional_fields=(12, 10, 8)),
            command_target="actuator-1",
        )
    )
    failures: tuple[FailureSchedule, ...] = ()
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)
    output_dir: str = "outputs"


@dataclass(frozen=True)
class SweepMatrixConfig:
    name: str
    output_dir: str
    duration_s: float
    base_seed: int
    repetitions: int
    arms: tuple[ArmName, ...] = DEFAULT_ARMS
    node_counts: tuple[int, ...] = (10, 20, 30)
    load_levels: tuple[str, ...] = ("low", "medium", "high")
    payload_modes: tuple[str, ...] = ("small", "near_budget", "fragmenting")
    disruption_modes: tuple[str, ...] = (
        "router_power_off",
        "link_degradation",
        "border_router_loss",
    )
    traffic: TrafficProfile = field(
        default_factory=lambda: TrafficProfile(
            telemetry_interval_s=5.0,
            telemetry_jitter_s=0.5,
            command_interval_s=15.0,
            command_jitter_s=0.0,
            payload_profile=PayloadProfile(base_bytes=60, optional_fields=(12, 10, 8)),
            command_target="actuator-1",
        )
    )
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)


def _load_path(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if config_path.suffix in {".toml", ".tml"}:
        return tomllib.loads(config_path.read_text())
    if config_path.suffix == ".json":
        return json.loads(config_path.read_text())
    raise ValueError(f"Unsupported config format: {config_path}")


def _tuple_strings(items: Any, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if items is None:
        return default
    return tuple(str(item) for item in items)


def _node_spec(raw: dict[str, Any]) -> NodeSpec:
    return NodeSpec(
        id=str(raw["id"]),
        role=str(raw["role"]),
        candidate_parents=_tuple_strings(raw.get("candidate_parents")),
        always_on=bool(raw.get("always_on", True)),
        initial_active=bool(raw.get("initial_active", True)),
    )


def _link_spec(raw: dict[str, Any]) -> LinkSpec:
    return LinkSpec(
        a=str(raw["a"]),
        b=str(raw["b"]),
        margin=float(raw["margin"]),
        latency_ms=int(raw.get("latency_ms", 25)),
        jitter_ms=int(raw.get("jitter_ms", 4)),
    )


def _payload_profile(raw: dict[str, Any]) -> PayloadProfile:
    return PayloadProfile(
        base_bytes=int(raw["base_bytes"]),
        optional_fields=tuple(int(value) for value in raw.get("optional_fields", ())),
        urgent_extra_bytes=int(raw.get("urgent_extra_bytes", 20)),
        urgent_probability=float(raw.get("urgent_probability", 0.05)),
    )


def _traffic_profile(raw: dict[str, Any]) -> TrafficProfile:
    return TrafficProfile(
        telemetry_interval_s=float(raw["telemetry_interval_s"]),
        telemetry_jitter_s=float(raw.get("telemetry_jitter_s", 0.0)),
        command_interval_s=float(raw["command_interval_s"]),
        command_jitter_s=float(raw.get("command_jitter_s", 0.0)),
        payload_profile=_payload_profile(raw["payload_profile"]),
        command_target=str(raw["command_target"]),
        warmup_s=float(raw.get("warmup_s", 1.0)),
    )


def _failure_schedule(raw: dict[str, Any]) -> FailureSchedule:
    link_value = raw.get("link")
    link = tuple(str(item) for item in link_value) if link_value else None
    return FailureSchedule(
        at_s=float(raw["at_s"]),
        kind=str(raw["kind"]),
        target=str(raw["target"]) if raw.get("target") else None,
        duration_s=float(raw["duration_s"]) if raw.get("duration_s") is not None else None,
        link=link,  # type: ignore[arg-type]
        degrade_to_margin=float(raw["degrade_to_margin"])
        if raw.get("degrade_to_margin") is not None
        else None,
    )


def _policy_config(raw: dict[str, Any] | None) -> PolicyConfig:
    raw = raw or {}
    return PolicyConfig(
        w1=float(raw.get("w1", 0.35)),
        w2=float(raw.get("w2", 0.25)),
        w3=float(raw.get("w3", 0.25)),
        w4=float(raw.get("w4", 0.15)),
        delta=float(raw.get("delta", 0.10)),
        beta=float(raw.get("beta", 0.40)),
        gamma=float(raw.get("gamma", 0.60)),
        fragmentation_budget_bytes=int(raw.get("fragmentation_budget_bytes", 80)),
        min_interval_s=float(raw.get("min_interval_s", 1.0)),
        max_interval_s=float(raw.get("max_interval_s", 60.0)),
        sliding_window_size=int(raw.get("sliding_window_size", 10)),
        hold_down_s=float(raw.get("hold_down_s", 5.0)),
    )


def _protocol_config(raw: dict[str, Any] | None) -> ProtocolConfig:
    raw = raw or {}
    return ProtocolConfig(
        ack_timeout_s=float(raw.get("ack_timeout_s", 0.75)),
        max_retries=int(raw.get("max_retries", 3)),
        retry_backoff_s=float(raw.get("retry_backoff_s", 0.15)),
        thread_repair_delay_s=float(raw.get("thread_repair_delay_s", 3.0)),
        zigbee_repair_delay_s=float(raw.get("zigbee_repair_delay_s", 1.5)),
        parent_search_interval_s=float(raw.get("parent_search_interval_s", 5.0)),
        controller_hop_latency_ms=int(raw.get("controller_hop_latency_ms", 10)),
        service_time_per_fragment_s=float(raw.get("service_time_per_fragment_s", 0.03)),
        queue_energy_factor=float(raw.get("queue_energy_factor", 0.08)),
    )


def load_simulation_config(path: str | Path) -> SimulationConfig:
    raw = _load_path(path)
    if "generated_topology" in raw:
        from .topology import build_generated_config

        generated = raw["generated_topology"]
        return build_generated_config(
            name=str(raw["name"]),
            duration_s=float(raw["duration_s"]),
            seed=int(raw["seed"]),
            node_count=int(generated["node_count"]),
            telemetry_interval_s=float(generated.get("telemetry_interval_s", raw["traffic"]["telemetry_interval_s"])),
            payload_profile=_payload_profile(generated.get("payload_profile", raw["traffic"]["payload_profile"])),
            disruption_mode=str(generated["disruption_mode"]),
            policy=_policy_config(raw.get("policy")),
            protocol=_protocol_config(raw.get("protocol")),
            arms=tuple(raw.get("arms", DEFAULT_ARMS)),
            output_dir=str(raw.get("output_dir", "outputs")),
        )
    return SimulationConfig(
        name=str(raw["name"]),
        duration_s=float(raw["duration_s"]),
        seed=int(raw["seed"]),
        arms=tuple(raw.get("arms", DEFAULT_ARMS)),
        nodes=tuple(_node_spec(item) for item in raw["nodes"]),
        links=tuple(_link_spec(item) for item in raw["links"]),
        traffic=_traffic_profile(raw["traffic"]),
        failures=tuple(_failure_schedule(item) for item in raw.get("failures", ())),
        policy=_policy_config(raw.get("policy")),
        protocol=_protocol_config(raw.get("protocol")),
        output_dir=str(raw.get("output_dir", "outputs")),
    )


def load_sweep_matrix_config(path: str | Path) -> SweepMatrixConfig:
    raw = _load_path(path)
    return SweepMatrixConfig(
        name=str(raw["name"]),
        output_dir=str(raw.get("output_dir", "outputs")),
        duration_s=float(raw["duration_s"]),
        base_seed=int(raw.get("base_seed", 1)),
        repetitions=int(raw.get("repetitions", 30)),
        arms=tuple(raw.get("arms", DEFAULT_ARMS)),
        node_counts=tuple(int(item) for item in raw.get("node_counts", (10, 20, 30))),
        load_levels=tuple(raw.get("load_levels", ("low", "medium", "high"))),
        payload_modes=tuple(raw.get("payload_modes", ("small", "near_budget", "fragmenting"))),
        disruption_modes=tuple(
            raw.get(
                "disruption_modes",
                ("router_power_off", "link_degradation", "border_router_loss"),
            )
        ),
        traffic=_traffic_profile(raw["traffic"]),
        policy=_policy_config(raw.get("policy")),
        protocol=_protocol_config(raw.get("protocol")),
    )
