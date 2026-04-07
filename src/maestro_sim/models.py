from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ArmName = Literal["zigbee", "matter_thread", "maestro"]
NodeRole = Literal[
    "sensor",
    "router",
    "border_router",
    "coordinator",
    "actuator",
    "controller",
]
FailureKind = Literal["power_off", "degrade_link", "border_router_loss"]


@dataclass(frozen=True)
class PolicyDecision:
    timestamp_s: float
    node_id: str
    arm: ArmName
    reason: str
    current_parent: str | None
    selected_parent: str | None
    switched: bool
    payload_before_bytes: int
    payload_after_bytes: int
    optional_fields_dropped: int
    interval_before_s: float
    interval_after_s: float
    ehat: float
    rhat: float
    fhat: float
    lhat: float
    score_selected: float | None
    score_current: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class NodeMetrics:
    node_id: str
    sent: int = 0
    delivered: int = 0
    dropped: int = 0
    retries: int = 0
    fragments: int = 0
    parent_switches: int = 0
    queue_depth_peak: int = 0
    energy_cost: float = 0.0
    ack_timeouts: int = 0
    last_success_at_s: float | None = None
    outage_started_at_s: float | None = None
    outages: list[float] = field(default_factory=list)
    recovery_windows: list[float] = field(default_factory=list)

    def mark_success(self, timestamp_s: float) -> None:
        self.delivered += 1
        if self.outage_started_at_s is not None:
            recovery_window = timestamp_s - self.outage_started_at_s
            self.recovery_windows.append(recovery_window)
            self.outages.append(recovery_window)
            self.outage_started_at_s = None
        self.last_success_at_s = timestamp_s

    def mark_failure(self, timestamp_s: float) -> None:
        self.dropped += 1
        if self.outage_started_at_s is None:
            self.outage_started_at_s = timestamp_s

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "sent": self.sent,
            "delivered": self.delivered,
            "dropped": self.dropped,
            "retries": self.retries,
            "fragments": self.fragments,
            "parent_switches": self.parent_switches,
            "queue_depth_peak": self.queue_depth_peak,
            "energy_cost": round(self.energy_cost, 6),
            "ack_timeouts": self.ack_timeouts,
            "last_success_at_s": self.last_success_at_s,
            "avg_outage_s": round(sum(self.outages) / len(self.outages), 6)
            if self.outages
            else 0.0,
            "avg_recovery_window_s": round(
                sum(self.recovery_windows) / len(self.recovery_windows), 6
            )
            if self.recovery_windows
            else 0.0,
        }


@dataclass(frozen=True)
class MessageTrace:
    message_id: str
    arm: ArmName
    kind: str
    source: str
    target: str
    created_at_s: float
    completed_at_s: float | None
    delivered: bool
    payload_bytes: int
    fragments: int
    retries: int
    path: tuple[str, ...]
    rtt_s: float | None
    failure_reason: str | None = None
    urgent: bool = False

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["path"] = "->".join(self.path)
        return data


@dataclass(frozen=True)
class ExperimentResult:
    run_name: str
    arm: ArmName
    summary: dict[str, object]
    message_traces: tuple[MessageTrace, ...]
    policy_decisions: tuple[PolicyDecision, ...]
    node_metrics: tuple[NodeMetrics, ...]


@dataclass(frozen=True)
class RuntimeMessage:
    message_id: str
    kind: str
    source: str
    target: str
    payload_bytes: int
    optional_fields: tuple[int, ...]
    urgent: bool
    created_at_s: float
