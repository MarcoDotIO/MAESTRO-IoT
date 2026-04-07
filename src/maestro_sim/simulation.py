from __future__ import annotations

import json
import math
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Iterable

import networkx as nx
import pandas as pd
import simpy

from .analysis import analyze_run_directory
from .config import FailureSchedule, NodeSpec, SimulationConfig, SweepMatrixConfig
from .drivers import SimEndpointDriver
from .models import ArmName, ExperimentResult, MessageTrace, NodeMetrics, PolicyDecision, RuntimeMessage
from .policy import FAMEPolicy, fragmentation_count
from .topology import build_generated_config

ARM_OFFSETS: dict[ArmName, int] = {"zigbee": 101, "matter_thread": 211, "maestro": 307}
ROLE_PRIORITY = {
    "controller": 0,
    "coordinator": 1,
    "border_router": 1,
    "router": 2,
    "actuator": 3,
    "sensor": 4,
}


@dataclass
class LinkState:
    a: str
    b: str
    margin: float
    latency_ms: int
    jitter_ms: int
    original_margin: float
    active: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return tuple(sorted((self.a, self.b)))


@dataclass
class NodeState:
    spec: NodeSpec
    metrics: NodeMetrics
    active: bool
    current_parent: str | None = None
    next_repair_at_s: float = 0.0
    hold_down_until_s: float = 0.0
    busy_until_s: float = 0.0
    current_interval_s: float = 0.0
    recent_timeouts: deque[bool] = field(default_factory=deque)
    recent_retries: deque[int] = field(default_factory=deque)


class SimulationEngine:
    def __init__(self, config: SimulationConfig, arm: ArmName, output_dir: Path) -> None:
        self.config = config
        self.arm = arm
        self.output_dir = output_dir
        self.env = simpy.Environment()
        self.rng = random.Random(config.seed + ARM_OFFSETS[arm])
        self.fame = FAMEPolicy(config.policy)
        self.driver = SimEndpointDriver(self)
        self.events: list[dict[str, object]] = []
        self.state_log: list[dict[str, object]] = []
        self.message_traces: list[MessageTrace] = []
        self.policy_decision_objects: list[PolicyDecision] = []
        self.policy_decisions: list[dict[str, object]] = []
        self.message_counter = 0

        window = self.config.policy.sliding_window_size
        self.nodes: dict[str, NodeState] = {
            spec.id: NodeState(
                spec=spec,
                metrics=NodeMetrics(node_id=spec.id),
                active=spec.initial_active,
                current_interval_s=self.config.traffic.telemetry_interval_s,
                recent_timeouts=deque(maxlen=window),
                recent_retries=deque(maxlen=window),
            )
            for spec in config.nodes
        }
        self.links: dict[tuple[str, str], LinkState] = {
            tuple(sorted((spec.a, spec.b))): LinkState(
                a=spec.a,
                b=spec.b,
                margin=spec.margin,
                latency_ms=spec.latency_ms,
                jitter_ms=spec.jitter_ms,
                original_margin=spec.margin,
            )
            for spec in config.links
        }

    @property
    def root_roles(self) -> set[str]:
        return {"coordinator"} if self.arm == "zigbee" else {"border_router"}

    @property
    def command_source(self) -> str:
        return "coordinator" if self.arm == "zigbee" else "controller"

    def run(self) -> ExperimentResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for node_id in sorted(self.nodes, key=lambda item: ROLE_PRIORITY[self.nodes[item].spec.role]):
            self.driver.commission(node_id)
        self.initialise_parents()
        self.snapshot_state(reason="post_commission")

        for failure in self.config.failures:
            self.env.process(self.failure_process(failure))

        for node_id, node in self.nodes.items():
            if node.spec.role == "sensor":
                self.env.process(self.telemetry_process(node_id))
        self.env.process(self.command_process())
        self.env.run(until=self.config.duration_s)
        self.close_open_outages()
        result = self.build_result()
        self.write_outputs(result)
        return result

    def commission_node(self, node_id: str) -> None:
        self.record_event("commission", node_id=node_id, role=self.nodes[node_id].spec.role)

    def publish_telemetry(self, node_id: str, urgent: bool = False) -> RuntimeMessage:
        node = self.nodes[node_id]
        payload_profile = self.config.traffic.payload_profile
        payload_bytes = payload_profile.base_bytes + sum(payload_profile.optional_fields)
        if urgent:
            payload_bytes += payload_profile.urgent_extra_bytes
        optional_fields = tuple(payload_profile.optional_fields)
        if self.arm == "maestro":
            outcome = self.fame.evaluate(
                engine=self,
                node_id=node_id,
                predicted_payload_bytes=payload_bytes,
                optional_fields=optional_fields,
                urgent=urgent,
                reason="telemetry",
            )
            payload_bytes = outcome.payload_bytes
            optional_fields = outcome.kept_optional_fields
            node.current_interval_s = outcome.interval_s
            self.policy_decision_objects.append(outcome.decision)
            self.policy_decisions.append(outcome.decision.to_dict())
            self.record_event(
                "policy_evaluated",
                node_id=node_id,
                selected_parent=outcome.selected_parent,
                interval_s=outcome.interval_s,
                payload_after_bytes=outcome.payload_bytes,
            )
        else:
            node.current_interval_s = self.config.traffic.telemetry_interval_s
        message = self.make_message(
            kind="telemetry",
            source=node_id,
            target="controller" if self.arm != "zigbee" else "coordinator",
            payload_bytes=payload_bytes,
            optional_fields=optional_fields,
            urgent=urgent,
        )
        self.env.process(self.dispatch_message_process(message))
        return message

    def send_command(self, source_id: str, target_id: str) -> RuntimeMessage:
        message = self.make_message(
            kind="command",
            source=source_id,
            target=target_id,
            payload_bytes=max(24, self.config.traffic.payload_profile.base_bytes // 2),
            optional_fields=(),
            urgent=True,
        )
        self.env.process(self.dispatch_message_process(message))
        return message

    def receive(self, node_id: str, message: RuntimeMessage) -> None:
        self.record_event(
            "receive",
            node_id=node_id,
            message_id=message.message_id,
            message_kind=message.kind,
        )

    def set_node_active(self, target_id: str, active: bool, reason: str = "manual") -> None:
        node = self.nodes[target_id]
        node.active = active
        self.record_event("node_state", node_id=target_id, active=active, reason=reason)
        if active:
            self.snapshot_state(reason=f"node_restored:{target_id}")
            return
        for child_id, child in self.nodes.items():
            if child.current_parent == target_id:
                self.clear_parent(child_id, reason=f"{reason}:parent_down")
                self.schedule_repair(child_id, reason)
            elif target_id in self.path_to_root(child_id):
                if child.metrics.outage_started_at_s is None:
                    child.metrics.outage_started_at_s = self.env.now
                self.record_event("path_impacted", node_id=child_id, target=target_id, reason=reason)
        self.snapshot_state(reason=f"node_down:{target_id}")

    def snapshot_metrics(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "nodes": {node_id: state.metrics.to_dict() for node_id, state in self.nodes.items()},
        }

    def make_message(
        self,
        *,
        kind: str,
        source: str,
        target: str,
        payload_bytes: int,
        optional_fields: tuple[int, ...],
        urgent: bool,
    ) -> RuntimeMessage:
        self.message_counter += 1
        return RuntimeMessage(
            message_id=f"{self.arm}-{self.message_counter:06d}",
            kind=kind,
            source=source,
            target=target,
            payload_bytes=payload_bytes,
            optional_fields=optional_fields,
            urgent=urgent,
            created_at_s=self.env.now,
        )

    def initialise_parents(self) -> None:
        candidates = sorted(self.nodes, key=lambda item: ROLE_PRIORITY[self.nodes[item].spec.role])
        for node_id in candidates:
            if self.nodes[node_id].spec.role in self.root_roles | {"controller"}:
                continue
            best_parent = self.best_available_parent(node_id)
            if best_parent:
                self.assign_parent(node_id=node_id, parent_id=best_parent, reason="commission", hold_down_s=0.0)

    def telemetry_process(self, node_id: str):
        start_delay = self.config.traffic.warmup_s + self.rng.uniform(0.0, self.config.traffic.telemetry_jitter_s)
        yield self.env.timeout(start_delay)
        while self.env.now < self.config.duration_s:
            urgent = self.rng.random() < self.config.traffic.payload_profile.urgent_probability
            self.driver.publish_telemetry(node_id=node_id, urgent=urgent)
            interval = self.nodes[node_id].current_interval_s
            jitter = self.rng.uniform(
                -self.config.traffic.telemetry_jitter_s, self.config.traffic.telemetry_jitter_s
            )
            yield self.env.timeout(max(0.25, interval + jitter))

    def command_process(self):
        yield self.env.timeout(self.config.traffic.warmup_s)
        while self.env.now < self.config.duration_s:
            self.driver.send_command(self.command_source, self.config.traffic.command_target)
            jitter = self.rng.uniform(
                -self.config.traffic.command_jitter_s, self.config.traffic.command_jitter_s
            )
            yield self.env.timeout(max(1.0, self.config.traffic.command_interval_s + jitter))

    def failure_process(self, failure: FailureSchedule):
        yield self.env.timeout(failure.at_s)
        if failure.kind in {"power_off", "border_router_loss"} and failure.target:
            self.set_node_active(failure.target, False, reason=failure.kind)
            if failure.duration_s:
                yield self.env.timeout(failure.duration_s)
                self.set_node_active(failure.target, True, reason=f"{failure.kind}:restore")
        elif failure.kind == "degrade_link" and failure.link:
            self.set_link_margin(
                failure.link[0],
                failure.link[1],
                margin=failure.degrade_to_margin or 0.3,
                reason=failure.kind,
            )
            if failure.duration_s:
                yield self.env.timeout(failure.duration_s)
                self.restore_link(failure.link[0], failure.link[1], reason=f"{failure.kind}:restore")

    def dispatch_message_process(self, message: RuntimeMessage):
        source_state = self.nodes[message.source]
        source_state.metrics.sent += 1
        budget = self.config.policy.fragmentation_budget_bytes
        fragments = fragmentation_count(message.payload_bytes, budget)
        source_state.metrics.fragments += fragments
        self.record_event(
            "message_sent",
            message_id=message.message_id,
            message_kind=message.kind,
            source=message.source,
            target=message.target,
            payload_bytes=message.payload_bytes,
            fragments=fragments,
            urgent=message.urgent,
        )
        retry_count = 0
        failure_reason = "unknown"
        while retry_count <= self.config.protocol.max_retries:
            self.repair_all_disconnected_nodes()
            path = self.resolve_path(message.source, message.target)
            if not path:
                failure_reason = "no_path"
                source_state.metrics.ack_timeouts += 1
                if retry_count == self.config.protocol.max_retries:
                    break
                retry_count += 1
                yield self.env.timeout(
                    self.config.protocol.ack_timeout_s + self.config.protocol.retry_backoff_s
                )
                continue

            delivered, forward_delay_s, attempt_reason = self.simulate_path_attempt(path, fragments)
            failure_reason = attempt_reason or "link_loss"
            rtt_s = forward_delay_s * 2.0
            if delivered and rtt_s <= self.config.protocol.ack_timeout_s:
                yield self.env.timeout(rtt_s)
                source_state.metrics.retries += retry_count
                source_state.metrics.mark_success(self.env.now)
                source_state.recent_timeouts.append(False)
                source_state.recent_retries.append(retry_count)
                self.receive(message.target, message)
                self.message_traces.append(
                    MessageTrace(
                        message_id=message.message_id,
                        arm=self.arm,
                        kind=message.kind,
                        source=message.source,
                        target=message.target,
                        created_at_s=message.created_at_s,
                        completed_at_s=self.env.now,
                        delivered=True,
                        payload_bytes=message.payload_bytes,
                        fragments=fragments,
                        retries=retry_count,
                        path=tuple(path),
                        rtt_s=rtt_s,
                        urgent=message.urgent,
                    )
                )
                self.record_event(
                    "message_delivered",
                    message_id=message.message_id,
                    message_kind=message.kind,
                    source=message.source,
                    target=message.target,
                    fragments=fragments,
                    retries=retry_count,
                    path="->".join(path),
                    rtt_s=rtt_s,
                )
                return

            source_state.metrics.ack_timeouts += 1
            if retry_count == self.config.protocol.max_retries:
                break
            retry_count += 1
            wait_s = min(self.config.protocol.ack_timeout_s, max(0.05, forward_delay_s))
            yield self.env.timeout(wait_s + self.config.protocol.retry_backoff_s)

        source_state.metrics.retries += retry_count
        source_state.metrics.mark_failure(self.env.now)
        source_state.recent_timeouts.append(True)
        source_state.recent_retries.append(retry_count)
        self.message_traces.append(
            MessageTrace(
                message_id=message.message_id,
                arm=self.arm,
                kind=message.kind,
                source=message.source,
                target=message.target,
                created_at_s=message.created_at_s,
                completed_at_s=None,
                delivered=False,
                payload_bytes=message.payload_bytes,
                fragments=fragments,
                retries=retry_count,
                path=tuple(),
                rtt_s=None,
                failure_reason=failure_reason,
                urgent=message.urgent,
            )
        )
        self.record_event(
            "message_failed",
            message_id=message.message_id,
            message_kind=message.kind,
            source=message.source,
            target=message.target,
            failure_reason=failure_reason,
            retries=retry_count,
        )

    def simulate_path_attempt(self, path: list[str], fragments: int) -> tuple[bool, float, str | None]:
        total_delay_s = 0.0
        for hop_from, hop_to in zip(path, path[1:]):
            link = self.links.get(tuple(sorted((hop_from, hop_to))))
            if link is None or not link.active:
                return False, total_delay_s, "link_down"
            if not self.nodes[hop_from].active or not self.nodes[hop_to].active:
                return False, total_delay_s, "node_down"

            current_time = self.env.now + total_delay_s
            service_s = max(0.005, self.config.protocol.service_time_per_fragment_s * fragments)
            queue_wait_s = max(0.0, self.nodes[hop_from].busy_until_s - current_time)
            queue_depth = int(queue_wait_s / max(0.001, self.config.protocol.service_time_per_fragment_s))
            self.nodes[hop_from].metrics.queue_depth_peak = max(
                self.nodes[hop_from].metrics.queue_depth_peak, queue_depth
            )
            success_prob = self.success_probability(link.margin, fragments, queue_depth)
            hop_latency_s = (
                link.latency_ms + self.rng.uniform(-float(link.jitter_ms), float(link.jitter_ms))
            ) / 1000.0
            hop_delay_s = queue_wait_s + service_s + max(0.001, hop_latency_s)
            start_s = max(current_time, self.nodes[hop_from].busy_until_s)
            self.nodes[hop_from].busy_until_s = start_s + service_s
            self.nodes[hop_to].busy_until_s = max(self.nodes[hop_to].busy_until_s, start_s + service_s * 0.5)
            energy = fragments * (1.0 + queue_depth * self.config.protocol.queue_energy_factor)
            self.nodes[hop_from].metrics.energy_cost += energy
            self.nodes[hop_to].metrics.energy_cost += 0.35 * energy
            total_delay_s += hop_delay_s
            if self.rng.random() > success_prob:
                return False, total_delay_s, "link_loss"
        return True, total_delay_s, None

    def success_probability(self, margin: float, fragments: int, queue_depth: int) -> float:
        base = 0.48 + 0.47 * margin
        fragment_penalty = 0.06 * max(0, fragments - 1)
        queue_penalty = 0.015 * queue_depth
        return max(0.05, min(0.99, base - fragment_penalty - queue_penalty))

    def resolve_path(self, source: str, target: str) -> list[str] | None:
        self.repair_all_disconnected_nodes()
        graph = nx.Graph()
        for node_id, node in self.nodes.items():
            if node.active:
                graph.add_node(node_id)

        for node_id, node in self.nodes.items():
            if not node.active or node.current_parent is None:
                continue
            if not self.parent_is_usable(node_id, node.current_parent):
                continue
            graph.add_edge(
                node_id,
                node.current_parent,
                weight=self.link_latency_seconds(node_id, node.current_parent),
            )

        if self.arm != "zigbee":
            for border in ("border-a", "border-b"):
                if self.nodes.get("controller") and self.nodes["controller"].active and self.nodes.get(border):
                    if self.nodes[border].active and self.link_is_active("controller", border):
                        graph.add_edge(
                            "controller",
                            border,
                            weight=self.link_latency_seconds("controller", border),
                        )

        if source not in graph or target not in graph:
            return None
        try:
            return nx.shortest_path(graph, source, target, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def link_latency_seconds(self, a: str, b: str) -> float:
        link = self.links[tuple(sorted((a, b)))]
        return max(0.001, link.latency_ms / 1000.0)

    def get_candidate_parents(self, node_id: str) -> list[str]:
        node = self.nodes[node_id]
        allowed_roles = {"router", "coordinator"} if self.arm == "zigbee" else {"router", "border_router"}
        candidates = []
        for candidate in node.spec.candidate_parents:
            if candidate not in self.nodes:
                continue
            if self.nodes[candidate].spec.role not in allowed_roles:
                continue
            if self.parent_is_usable(node_id, candidate):
                candidates.append(candidate)
        return candidates

    def best_available_parent(self, node_id: str) -> str | None:
        candidates = self.get_candidate_parents(node_id)
        if not candidates:
            return None
        best = max(candidates, key=lambda candidate: self.links[tuple(sorted((node_id, candidate)))].margin)
        return best

    def parent_is_usable(self, node_id: str, parent_id: str) -> bool:
        if parent_id not in self.nodes or node_id not in self.nodes:
            return False
        parent = self.nodes[parent_id]
        if not parent.active or not self.link_is_active(node_id, parent_id):
            return False
        if parent.spec.role in self.root_roles:
            return True
        if parent.current_parent is None:
            return False
        return bool(self.path_to_root(parent_id))

    def link_is_active(self, a: str, b: str) -> bool:
        link = self.links.get(tuple(sorted((a, b))))
        return bool(link and link.active)

    def path_to_root(self, node_id: str) -> list[str]:
        if node_id not in self.nodes or not self.nodes[node_id].active:
            return []
        path = [node_id]
        seen = {node_id}
        current = node_id
        while True:
            node = self.nodes[current]
            if node.spec.role in self.root_roles:
                return path
            parent_id = node.current_parent
            if parent_id is None or parent_id in seen or parent_id not in self.nodes:
                return []
            if not self.parent_is_directly_active(current, parent_id):
                return []
            path.append(parent_id)
            if self.nodes[parent_id].spec.role in self.root_roles:
                return path
            seen.add(parent_id)
            current = parent_id

    def parent_is_directly_active(self, node_id: str, parent_id: str) -> bool:
        return self.nodes[node_id].active and self.nodes[parent_id].active and self.link_is_active(node_id, parent_id)

    def assign_parent(self, node_id: str, parent_id: str, reason: str, hold_down_s: float) -> None:
        node = self.nodes[node_id]
        old_parent = node.current_parent
        node.current_parent = parent_id
        node.hold_down_until_s = self.env.now + hold_down_s
        node.next_repair_at_s = self.env.now
        if old_parent and old_parent != parent_id:
            node.metrics.parent_switches += 1
        self.record_event(
            "parent_assigned" if old_parent is None else "parent_switched",
            node_id=node_id,
            old_parent=old_parent,
            new_parent=parent_id,
            reason=reason,
        )
        self.snapshot_state(reason=f"parent:{node_id}")

    def clear_parent(self, node_id: str, reason: str) -> None:
        node = self.nodes[node_id]
        if node.current_parent is None:
            return
        old_parent = node.current_parent
        node.current_parent = None
        self.record_event("parent_cleared", node_id=node_id, old_parent=old_parent, reason=reason)
        self.snapshot_state(reason=f"clear_parent:{node_id}")

    def schedule_repair(self, node_id: str, reason: str) -> None:
        node = self.nodes[node_id]
        delay = (
            self.config.protocol.zigbee_repair_delay_s
            if self.arm == "zigbee"
            else self.config.protocol.thread_repair_delay_s
        )
        node.next_repair_at_s = self.env.now if self.arm == "maestro" else self.env.now + delay
        if self.arm == "maestro":
            payload = self.config.traffic.payload_profile.base_bytes + sum(
                self.config.traffic.payload_profile.optional_fields
            )
            outcome = self.fame.evaluate(
                engine=self,
                node_id=node_id,
                predicted_payload_bytes=payload,
                optional_fields=tuple(self.config.traffic.payload_profile.optional_fields),
                urgent=False,
                reason=f"disruption:{reason}",
            )
            self.policy_decision_objects.append(outcome.decision)
            self.policy_decisions.append(outcome.decision.to_dict())
        self.record_event("repair_scheduled", node_id=node_id, at_s=node.next_repair_at_s, reason=reason)

    def repair_all_disconnected_nodes(self) -> None:
        ordered = sorted(
            (node_id for node_id, state in self.nodes.items() if state.spec.role not in self.root_roles | {"controller"}),
            key=lambda item: ROLE_PRIORITY[self.nodes[item].spec.role],
        )
        for node_id in ordered:
            node = self.nodes[node_id]
            if not node.active:
                continue
            if node.current_parent and self.parent_is_usable(node_id, node.current_parent):
                continue
            if self.env.now < node.next_repair_at_s:
                continue
            best_parent = self.best_available_parent(node_id)
            if best_parent:
                self.assign_parent(node_id=node_id, parent_id=best_parent, reason="repair", hold_down_s=0.0)

    def set_link_margin(self, a: str, b: str, margin: float, reason: str) -> None:
        key = tuple(sorted((a, b)))
        link = self.links[key]
        link.margin = margin
        self.record_event("link_degraded", a=a, b=b, margin=margin, reason=reason)
        direct_child = a if self.nodes[a].current_parent == b else b if self.nodes[b].current_parent == a else None
        if direct_child:
            self.schedule_repair(direct_child, reason=reason)
        for node_id in self.nodes:
            if key in self.path_link_keys(node_id):
                if self.nodes[node_id].metrics.outage_started_at_s is None:
                    self.nodes[node_id].metrics.outage_started_at_s = self.env.now
                self.record_event("path_impacted", node_id=node_id, reason=reason, link="::".join(key))
        self.snapshot_state(reason=f"link_degraded:{a}:{b}")

    def restore_link(self, a: str, b: str, reason: str) -> None:
        key = tuple(sorted((a, b)))
        link = self.links[key]
        link.margin = link.original_margin
        self.record_event("link_restored", a=a, b=b, margin=link.margin, reason=reason)
        self.snapshot_state(reason=f"link_restored:{a}:{b}")

    def path_link_keys(self, node_id: str) -> set[tuple[str, str]]:
        path = self.path_to_root(node_id)
        return {tuple(sorted(edge)) for edge in zip(path, path[1:])}

    def compute_rhat(self, node_id: str) -> float:
        node = self.nodes[node_id]
        if not node.recent_timeouts:
            return 0.0
        timeout_ratio = sum(1 for value in node.recent_timeouts if value) / len(node.recent_timeouts)
        retry_ratio = sum(node.recent_retries) / (
            len(node.recent_retries) * max(1, self.config.protocol.max_retries)
        )
        return min(1.0, timeout_ratio * 0.6 + retry_ratio * 0.4)

    def compute_ehat(self, node_id: str, candidate: str) -> float:
        margin = self.links[tuple(sorted((node_id, candidate)))].margin
        return max(0.0, min(1.0, 1.0 - margin))

    def compute_lhat(self, candidate: str) -> float:
        child_count = sum(1 for node in self.nodes.values() if node.current_parent == candidate)
        queue_depth = max(
            0.0,
            (self.nodes[candidate].busy_until_s - self.env.now) / max(0.01, self.config.protocol.service_time_per_fragment_s),
        )
        return max(0.0, min(1.0, child_count / 8.0 + queue_depth / 12.0))

    def record_event(self, kind: str, **data: object) -> None:
        self.events.append({"timestamp_s": round(self.env.now, 6), "arm": self.arm, "event": kind, **data})

    def snapshot_state(self, reason: str) -> None:
        for node_id, node in self.nodes.items():
            self.state_log.append(
                {
                    "timestamp_s": round(self.env.now, 6),
                    "arm": self.arm,
                    "reason": reason,
                    "node_id": node_id,
                    "role": node.spec.role,
                    "active": node.active,
                    "parent": node.current_parent,
                    "next_repair_at_s": round(node.next_repair_at_s, 6),
                    "hold_down_until_s": round(node.hold_down_until_s, 6),
                    "busy_until_s": round(node.busy_until_s, 6),
                    "queue_depth_peak": node.metrics.queue_depth_peak,
                }
            )

    def close_open_outages(self) -> None:
        for node in self.nodes.values():
            if node.metrics.outage_started_at_s is not None:
                window = self.config.duration_s - node.metrics.outage_started_at_s
                node.metrics.outages.append(window)
                node.metrics.outage_started_at_s = None

    def build_result(self) -> ExperimentResult:
        summary = summarize_result(self.arm, self.message_traces, self.nodes.values())
        return ExperimentResult(
            run_name=self.config.name,
            arm=self.arm,
            summary=summary,
            message_traces=tuple(self.message_traces),
            policy_decisions=tuple(self.policy_decision_objects),
            node_metrics=tuple(node.metrics for node in self.nodes.values()),
        )

    def write_outputs(self, result: ExperimentResult) -> None:
        events_df = pd.DataFrame(self.events)
        state_df = pd.DataFrame(self.state_log)
        traces_df = pd.DataFrame(trace.to_dict() for trace in self.message_traces)
        metrics_df = pd.DataFrame(node.metrics.to_dict() for node in self.nodes.values())
        decisions_df = pd.DataFrame(self.policy_decisions)

        events_df.to_csv(self.output_dir / "events.csv", index=False)
        state_df.to_csv(self.output_dir / "node_state.csv", index=False)
        traces_df.to_csv(self.output_dir / "message_traces.csv", index=False)
        metrics_df.to_csv(self.output_dir / "node_metrics.csv", index=False)
        decisions_df.to_csv(self.output_dir / "policy_decisions.csv", index=False)
        (self.output_dir / "summary.json").write_text(json.dumps(result.summary, indent=2))


def summarize_result(
    arm: ArmName, traces: list[MessageTrace], nodes: Iterable[NodeState]
) -> dict[str, object]:
    delivered = [trace for trace in traces if trace.delivered]
    command_latencies = [trace.rtt_s for trace in delivered if trace.kind == "command" and trace.rtt_s is not None]
    rtts = [trace.rtt_s for trace in delivered if trace.rtt_s is not None]
    total_messages = len(traces)
    total_delivered = len(delivered)
    node_metrics = [node.metrics for node in nodes]
    recovery_windows = [window for metric in node_metrics for window in metric.recovery_windows]
    outages = [window for metric in node_metrics for window in metric.outages]
    summary = {
        "arm": arm,
        "total_messages": total_messages,
        "delivered_messages": total_delivered,
        "delivery_ratio": round(total_delivered / total_messages, 6) if total_messages else 0.0,
        "avg_rtt_s": round(mean(rtts), 6) if rtts else None,
        "p50_rtt_s": _percentile(rtts, 50),
        "p95_rtt_s": _percentile(rtts, 95),
        "command_p50_s": _percentile(command_latencies, 50),
        "command_p95_s": _percentile(command_latencies, 95),
        "route_recovery_time_avg_s": round(mean(recovery_windows), 6) if recovery_windows else 0.0,
        "application_outage_window_avg_s": round(mean(outages), 6) if outages else 0.0,
        "fragment_count": sum(trace.fragments for trace in traces),
        "retransmission_rate": round(
            sum(trace.retries for trace in traces) / total_messages, 6
        )
        if total_messages
        else 0.0,
        "parent_switches": sum(metric.parent_switches for metric in node_metrics),
        "queue_depth_peak": max((metric.queue_depth_peak for metric in node_metrics), default=0),
        "relative_energy_cost": round(sum(metric.energy_cost for metric in node_metrics), 6),
        "ack_timeouts": sum(metric.ack_timeouts for metric in node_metrics),
    }
    return summary


def _percentile(values: list[float | None], percentile: int) -> float | None:
    numeric = sorted(value for value in values if value is not None)
    if not numeric:
        return None
    index = min(len(numeric) - 1, max(0, math.ceil((percentile / 100) * len(numeric)) - 1))
    return round(float(numeric[index]), 6)


def run_experiment(config: SimulationConfig, output_root: str | Path | None = None) -> Path:
    root = Path(output_root or config.output_dir)
    run_dir = root / f"{config.name}_seed{config.seed}"
    if run_dir.exists():
        suffix = 1
        while (root / f"{config.name}_seed{config.seed}_{suffix}").exists():
            suffix += 1
        run_dir = root / f"{config.name}_seed{config.seed}_{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)

    resolved_config = {
        "name": config.name,
        "duration_s": config.duration_s,
        "seed": config.seed,
        "arms": list(config.arms),
    }
    (run_dir / "resolved_config.json").write_text(json.dumps(resolved_config, indent=2))

    combined_rows: list[dict[str, object]] = []
    for arm in config.arms:
        engine = SimulationEngine(config=config, arm=arm, output_dir=run_dir / arm)
        result = engine.run()
        combined_rows.append(result.summary)

    pd.DataFrame(combined_rows).to_csv(run_dir / "combined_summary.csv", index=False)
    analyze_run_directory(run_dir)
    return run_dir


def run_sweep(matrix: SweepMatrixConfig, output_root: str | Path | None = None) -> Path:
    sweep_root = Path(output_root or matrix.output_dir) / f"{matrix.name}_sweep"
    sweep_root.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, object]] = []
    scenario_index = 0
    load_to_interval = {"low": 8.0, "medium": 5.0, "high": 2.5}
    payload_profiles = {
        "small": {"base_bytes": 50, "optional_fields": (10, 8, 6), "urgent_extra_bytes": 10},
        "near_budget": {"base_bytes": 68, "optional_fields": (10, 8, 6), "urgent_extra_bytes": 14},
        "fragmenting": {"base_bytes": 84, "optional_fields": (18, 14, 10), "urgent_extra_bytes": 18},
    }

    for node_count in matrix.node_counts:
        for load in matrix.load_levels:
            for payload_mode in matrix.payload_modes:
                for disruption in matrix.disruption_modes:
                    for repetition in range(matrix.repetitions):
                        scenario_index += 1
                        seed = matrix.base_seed + scenario_index + repetition
                        payload_profile = matrix.traffic.payload_profile.__class__(
                            **payload_profiles[payload_mode],
                            urgent_probability=matrix.traffic.payload_profile.urgent_probability,
                        )
                        config = build_generated_config(
                            name=f"{matrix.name}_{node_count}n_{load}_{payload_mode}_{disruption}_{repetition + 1}",
                            duration_s=matrix.duration_s,
                            seed=seed,
                            node_count=node_count,
                            telemetry_interval_s=load_to_interval[load],
                            payload_profile=payload_profile,
                            disruption_mode=disruption,
                            policy=matrix.policy,
                            protocol=matrix.protocol,
                            arms=matrix.arms,
                            output_dir=str(sweep_root),
                        )
                        run_dir = run_experiment(config, output_root=sweep_root)
                        manifest_rows.append(
                            {
                                "run_dir": str(run_dir),
                                "node_count": node_count,
                                "load": load,
                                "payload_mode": payload_mode,
                                "disruption": disruption,
                                "seed": seed,
                            }
                        )
    pd.DataFrame(manifest_rows).to_csv(sweep_root / "manifest.csv", index=False)
    analyze_run_directory(sweep_root)
    return sweep_root
