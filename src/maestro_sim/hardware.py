from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Protocol

import pandas as pd

from .analysis import analyze_run_directory
from .hardware_config import HardwareBenchmarkConfig, HardwareDeviceSpec
from .models import MessageTrace, NodeMetrics, PolicyDecision
from .results import summarize_metrics


class Session(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def send(self, payload: dict[str, object]) -> None: ...

    def poll(self) -> list[dict[str, object]]: ...


class SerialJsonlSession:
    def __init__(self, spec: HardwareDeviceSpec) -> None:
        self.spec = spec
        self._serial: Any | None = None

    def open(self) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - exercised only in real hardware environments
            raise RuntimeError(
                "pyserial is required for hardware runs; install dependencies with `uv sync`"
            ) from exc
        self._serial = serial.Serial(
            self.spec.port.path,
            self.spec.port.baudrate,
            timeout=self.spec.port.timeout_s,
            write_timeout=max(self.spec.port.timeout_s, 2.0),
        )
        # Let USB CDC endpoints settle before the first command.
        time.sleep(0.2)

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def send(self, payload: dict[str, object]) -> None:
        if self._serial is None:
            raise RuntimeError(f"session for {self.spec.node_id} is not open")
        encoded = _encode_jsonl(payload)
        last_error: Exception | None = None
        for _ in range(3):
            try:
                self._serial.write(encoded)
                return
            except Exception as exc:  # pragma: no cover - exercised only in real hardware environments
                last_error = exc
                time.sleep(0.1)
        if last_error is not None:
            raise last_error

    def poll(self) -> list[dict[str, object]]:
        if self._serial is None:
            raise RuntimeError(f"session for {self.spec.node_id} is not open")
        events: list[dict[str, object]] = []
        deadline = time.monotonic() + max(self.spec.port.timeout_s, 0.05)
        while len(events) < 64 and time.monotonic() < deadline:
            line = self._serial.readline()
            if not line:
                break
            event = _decode_jsonl(line)
            if event is not None:
                events.append(event)
        return events


def discover_serial_ports() -> list[dict[str, object]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return [{"device": str(path), "description": "unknown", "hwid": "unavailable"} for path in Path("/dev").glob("cu.*")]

    ports: list[dict[str, object]] = []
    for port in list_ports.comports():
        ports.append(
            {
                "device": port.device,
                "description": port.description,
                "hwid": port.hwid,
                "manufacturer": getattr(port, "manufacturer", None),
                "product": getattr(port, "product", None),
                "serial_number": getattr(port, "serial_number", None),
            }
        )
    return ports


class HardwareRunAccumulator:
    def __init__(self, config: HardwareBenchmarkConfig) -> None:
        self.config = config
        self.traces: list[MessageTrace] = []
        self.policy_decisions: list[PolicyDecision] = []
        self.events: list[dict[str, object]] = []
        self.state_log: list[dict[str, object]] = []
        self.identities: dict[str, dict[str, object]] = {}
        self.node_metrics: dict[str, NodeMetrics] = {
            device.node_id: NodeMetrics(node_id=device.node_id) for device in config.devices if device.role != "monitor"
        }

    def ingest(self, node_id: str, event: dict[str, object]) -> None:
        normalized = {
            "timestamp_s": round(float(event.get("timestamp_s", 0.0)), 6),
            "arm": self.config.arm,
            "node_id": node_id,
            **{key: value for key, value in event.items() if key not in {"timestamp_s", "node_id"}},
        }
        self.events.append(normalized)
        kind = str(event.get("event", "unknown"))
        if kind == "identify":
            self.identities[node_id] = normalized
            return
        if kind == "node_state":
            self.state_log.append(
                {
                    "timestamp_s": normalized["timestamp_s"],
                    "arm": self.config.arm,
                    "reason": str(event.get("reason", kind)),
                    "node_id": node_id,
                    "role": str(event.get("role", self._role_for(node_id))),
                    "active": bool(event.get("active", True)),
                    "parent": event.get("parent"),
                    "next_repair_at_s": round(float(event.get("next_repair_at_s", 0.0)), 6),
                    "hold_down_until_s": round(float(event.get("hold_down_until_s", 0.0)), 6),
                    "busy_until_s": round(float(event.get("busy_until_s", 0.0)), 6),
                    "queue_depth_peak": int(event.get("queue_depth_peak", 0)),
                }
            )
            return
        if kind == "message_result":
            trace = self._trace_from_event(node_id, event)
            self.traces.append(trace)
            self._update_metrics_from_trace(node_id, trace, event)
            return
        if kind == "policy_decision":
            self.policy_decisions.append(self._policy_decision_from_event(node_id, event))
            return
        if kind == "metric_snapshot":
            self._merge_metric_snapshot(node_id, event)

    def write_outputs(self, output_dir: Path) -> dict[str, object]:
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.events).to_csv(output_dir / "events.csv", index=False)
        pd.DataFrame(self.state_log).to_csv(output_dir / "node_state.csv", index=False)
        pd.DataFrame(trace.to_dict() for trace in self.traces).to_csv(output_dir / "message_traces.csv", index=False)
        pd.DataFrame(metric.to_dict() for metric in self.node_metrics.values()).to_csv(output_dir / "node_metrics.csv", index=False)
        pd.DataFrame(decision.to_dict() for decision in self.policy_decisions).to_csv(
            output_dir / "policy_decisions.csv", index=False
        )
        summary = summarize_metrics(self.config.arm, self.traces, self.node_metrics.values())
        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        if self.identities:
            (output_dir / "identities.json").write_text(json.dumps(self.identities, indent=2))
        return summary

    def dashboard_payload(self, elapsed_s: float) -> dict[str, object]:
        summary = summarize_metrics(self.config.arm, self.traces, self.node_metrics.values())
        active_nodes = [
            {
                "node_id": row["node_id"],
                "role": row["role"],
                "active": row["active"],
                "parent": row["parent"],
            }
            for row in self._latest_states()
        ]
        return {
            "cmd": "display_frame",
            "arm": self.config.arm,
            "run_name": self.config.name,
            "elapsed_s": round(elapsed_s, 3),
            "summary": summary,
            "nodes": active_nodes,
        }

    def _latest_states(self) -> list[dict[str, object]]:
        by_node: dict[str, dict[str, object]] = {}
        for row in self.state_log:
            by_node[row["node_id"]] = row
        return list(by_node.values())

    def _role_for(self, node_id: str) -> str:
        for device in self.config.devices:
            if device.node_id == node_id:
                return device.role
        return "sensor"

    def _ensure_metric(self, node_id: str) -> NodeMetrics:
        return self.node_metrics.setdefault(node_id, NodeMetrics(node_id=node_id))

    def _trace_from_event(self, node_id: str, event: dict[str, object]) -> MessageTrace:
        path = event.get("path", ())
        if isinstance(path, str):
            path_value = tuple(segment for segment in path.split("->") if segment)
        elif isinstance(path, list):
            path_value = tuple(str(segment) for segment in path)
        else:
            path_value = tuple(path) if isinstance(path, tuple) else ()
        created_at_s = float(event.get("created_at_s", event.get("timestamp_s", 0.0)))
        completed_at_raw = event.get("completed_at_s")
        completed_at_s = float(completed_at_raw) if completed_at_raw is not None else None
        return MessageTrace(
            message_id=str(event["message_id"]),
            arm=self.config.arm,
            kind=str(event["kind"]),
            source=str(event.get("source", node_id)),
            target=str(event["target"]),
            created_at_s=created_at_s,
            completed_at_s=completed_at_s,
            delivered=bool(event.get("delivered", False)),
            payload_bytes=int(event["payload_bytes"]),
            fragments=int(event.get("fragments", 1)),
            retries=int(event.get("retries", 0)),
            path=path_value,
            rtt_s=float(event["rtt_s"]) if event.get("rtt_s") is not None else None,
            failure_reason=str(event["failure_reason"]) if event.get("failure_reason") else None,
            urgent=bool(event.get("urgent", False)),
        )

    def _update_metrics_from_trace(self, node_id: str, trace: MessageTrace, event: dict[str, object]) -> None:
        metric = self._ensure_metric(node_id)
        metric.sent += 1
        metric.fragments += trace.fragments
        metric.retries += trace.retries
        metric.queue_depth_peak = max(metric.queue_depth_peak, int(event.get("queue_depth_peak", 0)))
        metric.energy_cost += float(
            event.get(
                "energy_cost",
                trace.fragments * (1.0 + trace.retries * self.config.protocol.queue_energy_factor),
            )
        )
        if trace.delivered:
            metric.mark_success(trace.completed_at_s or trace.created_at_s)
        else:
            metric.ack_timeouts += 1
            metric.mark_failure(float(event.get("timestamp_s", trace.created_at_s)))

    def _merge_metric_snapshot(self, node_id: str, event: dict[str, object]) -> None:
        metric = self._ensure_metric(node_id)
        for field_name in (
            "sent",
            "delivered",
            "dropped",
            "retries",
            "fragments",
            "parent_switches",
            "queue_depth_peak",
            "ack_timeouts",
        ):
            if field_name in event:
                setattr(metric, field_name, max(getattr(metric, field_name), int(event[field_name])))
        if "energy_cost" in event:
            metric.energy_cost = max(metric.energy_cost, float(event["energy_cost"]))

    def _policy_decision_from_event(self, node_id: str, event: dict[str, object]) -> PolicyDecision:
        return PolicyDecision(
            timestamp_s=float(event.get("timestamp_s", 0.0)),
            node_id=node_id,
            arm=self.config.arm,
            reason=str(event.get("reason", "telemetry")),
            current_parent=str(event["current_parent"]) if event.get("current_parent") else None,
            selected_parent=str(event["selected_parent"]) if event.get("selected_parent") else None,
            switched=bool(event.get("switched", False)),
            payload_before_bytes=int(event.get("payload_before_bytes", event.get("payload_bytes", 0))),
            payload_after_bytes=int(event.get("payload_after_bytes", event.get("payload_bytes", 0))),
            optional_fields_dropped=int(event.get("optional_fields_dropped", 0)),
            interval_before_s=float(event.get("interval_before_s", self.config.traffic.telemetry_interval_s)),
            interval_after_s=float(event.get("interval_after_s", self.config.traffic.telemetry_interval_s)),
            ehat=float(event.get("ehat", 0.0)),
            rhat=float(event.get("rhat", 0.0)),
            fhat=float(event.get("fhat", 0.0)),
            lhat=float(event.get("lhat", 0.0)),
            score_selected=float(event["score_selected"]) if event.get("score_selected") is not None else None,
            score_current=float(event["score_current"]) if event.get("score_current") is not None else None,
        )


def run_hardware_benchmark(
    config: HardwareBenchmarkConfig,
    output_root: str | Path | None = None,
    session_factory: Callable[[HardwareDeviceSpec], Session] | None = None,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Path:
    config.validate()
    root = Path(output_root or config.output_dir)
    run_dir = _allocate_run_dir(root, f"{config.name}_{config.arm}")
    arm_dir = run_dir / config.arm
    arm_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "resolved_config.json").write_text(json.dumps(_serialize_config(config), indent=2))

    factory = session_factory or (lambda spec: SerialJsonlSession(spec))
    sessions = {device.node_id: factory(device) for device in config.devices}
    accumulator = HardwareRunAccumulator(config)
    pending_failures = [
        {"at_s": failure.at_s, "command": _failure_command(failure, active=False)}
        for failure in sorted(config.failures, key=lambda item: item.at_s)
    ]
    pending_restores = [
        {"at_s": failure.at_s + failure.duration_s, "command": _failure_command(failure, active=True)}
        for failure in config.failures
        if failure.duration_s is not None
    ]
    pending_restores.sort(key=lambda item: float(item["at_s"]))
    next_dashboard_at = 0.0
    stop_sent = False
    start_time = monotonic()

    try:
        for session in sessions.values():
            session.open()
        _broadcast(sessions, {"cmd": "identify"})
        _drain_sessions(sessions, accumulator)
        _broadcast(sessions, {"cmd": "configure", "config": _serialize_config(config)})
        route_id = next(
            (device.node_id for device in config.devices if device.role in {"border_router", "router"}),
            "border-router",
        )
        for device in config.devices:
            sessions[device.node_id].send(
                {
                    "cmd": "bind_device",
                    "node_id": device.node_id,
                    "role": device.role,
                    "board": device.board,
                    "route_id": route_id,
                    "display_enabled": device.role == "monitor" or "display" in device.capabilities,
                }
            )
        _broadcast(sessions, {"cmd": "start_run", "arm": config.arm, "duration_s": config.duration_s})

        while True:
            _drain_sessions(sessions, accumulator)
            elapsed_s = monotonic() - start_time
            while pending_failures and elapsed_s >= float(pending_failures[0]["at_s"]):
                _send_targeted_command(sessions, pending_failures.pop(0)["command"])
            while pending_restores and elapsed_s >= float(pending_restores[0]["at_s"]):
                _send_targeted_command(sessions, pending_restores.pop(0)["command"])

            if config.monitor_node_id and elapsed_s >= next_dashboard_at:
                sessions[config.monitor_node_id].send(accumulator.dashboard_payload(elapsed_s))
                next_dashboard_at = elapsed_s + config.dashboard_refresh_s

            if not stop_sent and elapsed_s >= config.duration_s:
                _broadcast(sessions, {"cmd": "stop_run"})
                stop_sent = True
            if stop_sent and elapsed_s >= config.duration_s + config.grace_period_s:
                break
            sleeper(config.poll_interval_s)
        _drain_sessions(sessions, accumulator)
    finally:
        for session in sessions.values():
            session.close()

    summary = accumulator.write_outputs(arm_dir)
    pd.DataFrame([summary]).to_csv(run_dir / "combined_summary.csv", index=False)
    analyze_run_directory(run_dir)
    return run_dir


def _broadcast(sessions: dict[str, Session], payload: dict[str, object]) -> None:
    for session in sessions.values():
        try:
            session.send(payload)
        except Exception:  # pragma: no cover - exercised only in real hardware environments
            continue


def _drain_sessions(sessions: dict[str, Session], accumulator: HardwareRunAccumulator) -> None:
    for node_id, session in sessions.items():
        for event in session.poll():
            accumulator.ingest(node_id, event)


def _send_targeted_command(sessions: dict[str, Session], command: dict[str, object]) -> None:
    target_id = command.get("target_id")
    if target_id is None:
        _broadcast(sessions, command)
        return
    session = sessions.get(str(target_id))
    if session is not None:
        try:
            session.send(command)
        except Exception:  # pragma: no cover - exercised only in real hardware environments
            return


def _failure_command(failure: Any, *, active: bool) -> dict[str, object]:
    if failure.kind in {"power_off", "border_router_loss"} and failure.target:
        if failure.kind == "border_router_loss":
            return {"cmd": "set_route_active", "route_id": failure.target, "active": active, "reason": failure.kind}
        return {"cmd": "set_active", "target_id": failure.target, "active": active, "reason": failure.kind}
    if failure.kind == "degrade_link" and failure.link:
        return {
            "cmd": "set_link_profile",
            "target_id": failure.link[0],
            "peer_id": failure.link[1],
            "active": not active,
            "degrade_to_margin": failure.degrade_to_margin,
            "reason": failure.kind,
        }
    raise ValueError(f"unsupported hardware failure schedule: {failure}")


def _allocate_run_dir(root: Path, stem: str) -> Path:
    run_dir = root / stem
    if run_dir.exists():
        suffix = 1
        while (root / f"{stem}_{suffix}").exists():
            suffix += 1
        run_dir = root / f"{stem}_{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _serialize_config(config: HardwareBenchmarkConfig) -> dict[str, object]:
    return {
        "name": config.name,
        "arm": config.arm,
        "duration_s": config.duration_s,
        "output_dir": config.output_dir,
        "controller_id": config.controller_id,
        "monitor_node_id": config.monitor_node_id,
        "poll_interval_s": config.poll_interval_s,
        "dashboard_refresh_s": config.dashboard_refresh_s,
        "grace_period_s": config.grace_period_s,
        "traffic": asdict(config.traffic),
        "failures": [asdict(item) for item in config.failures],
        "policy": asdict(config.policy),
        "protocol": asdict(config.protocol),
        "devices": [asdict(device) for device in config.devices],
    }


def _encode_jsonl(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _decode_jsonl(line: bytes | str) -> dict[str, object] | None:
    text = line.decode("utf-8", errors="replace").strip() if isinstance(line, bytes) else line.strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {"event": "raw_line", "line": text}
    if not isinstance(loaded, dict):
        return {"event": "raw_line", "line": text}
    return loaded
