from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from maestro_sim.hardware import run_hardware_benchmark
from maestro_sim.hardware_config import (
    HardwareBenchmarkConfig,
    HardwareDeviceSpec,
    SerialPortConfig,
    load_hardware_benchmark_config,
)
from maestro_sim.config import FailureSchedule, PayloadProfile, PolicyConfig, ProtocolConfig, TrafficProfile


class MockSession:
    def __init__(self, spec: HardwareDeviceSpec, *, arm: str) -> None:
        self.spec = spec
        self.arm = arm
        self.commands: list[dict[str, object]] = []
        self._events: list[dict[str, object]] = []

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def send(self, payload: dict[str, object]) -> None:
        self.commands.append(payload)
        command = str(payload["cmd"])
        if command == "identify":
            self._events.append(
                {
                    "timestamp_s": 0.0,
                    "event": "identify",
                    "firmware": "mock-bench",
                    "role": self.spec.role,
                    "board": self.spec.board,
                }
            )
            if self.spec.role != "monitor":
                self._events.append(
                    {
                        "timestamp_s": 0.0,
                        "event": "node_state",
                        "role": self.spec.role,
                        "active": True,
                        "parent": None,
                    }
                )
        elif command == "start_run" and self.spec.role == "sensor":
            self._events.extend(
                [
                    {
                        "timestamp_s": 0.15,
                        "event": "message_result",
                        "message_id": f"{self.arm}-000001",
                        "kind": "telemetry",
                        "source": self.spec.node_id,
                        "target": "controller",
                        "created_at_s": 0.10,
                        "completed_at_s": 0.15,
                        "delivered": True,
                        "payload_bytes": 76,
                        "fragments": 1,
                        "retries": 0,
                        "path": [self.spec.node_id, "border-router", "controller"],
                        "rtt_s": 0.05,
                        "urgent": False,
                    },
                    {
                        "timestamp_s": 0.25,
                        "event": "policy_decision",
                        "reason": "telemetry",
                        "current_parent": "border-router",
                        "selected_parent": "border-router",
                        "switched": False,
                        "payload_before_bytes": 92,
                        "payload_after_bytes": 76,
                        "optional_fields_dropped": 1,
                        "interval_before_s": 1.0,
                        "interval_after_s": 1.4,
                        "ehat": 0.2,
                        "rhat": 0.0,
                        "fhat": 0.0,
                        "lhat": 0.1,
                        "score_selected": 0.135,
                        "score_current": 0.135,
                    },
                    {
                        "timestamp_s": 0.30,
                        "event": "message_result",
                        "message_id": f"{self.arm}-000002",
                        "kind": "command",
                        "source": "controller",
                        "target": "actuator-1",
                        "created_at_s": 0.20,
                        "delivered": False,
                        "payload_bytes": 34,
                        "fragments": 1,
                        "retries": 2,
                        "failure_reason": "ack_timeout",
                        "urgent": True,
                    },
                ]
            )
        elif command == "set_active" and self.spec.node_id == str(payload["target_id"]):
            self._events.append(
                {
                    "timestamp_s": 0.4,
                    "event": "node_state",
                    "role": self.spec.role,
                    "active": bool(payload["active"]),
                    "parent": None,
                    "reason": payload.get("reason", "manual"),
                }
            )
        elif command == "stop_run" and self.spec.role == "sensor":
            self._events.append(
                {
                    "timestamp_s": 0.5,
                    "event": "metric_snapshot",
                    "sent": 2,
                    "delivered": 1,
                    "dropped": 1,
                    "retries": 2,
                    "fragments": 2,
                    "parent_switches": 1,
                    "queue_depth_peak": 3,
                    "energy_cost": 2.5,
                    "ack_timeouts": 1,
                }
            )

    def poll(self) -> list[dict[str, object]]:
        events = list(self._events)
        self._events.clear()
        return events


def build_config(tmp_path: Path) -> HardwareBenchmarkConfig:
    return HardwareBenchmarkConfig(
        name="hw-validation",
        arm="maestro",
        duration_s=0.01,
        output_dir=str(tmp_path / "hardware"),
        poll_interval_s=0.001,
        dashboard_refresh_s=0.001,
        grace_period_s=0.0,
        monitor_node_id="monitor",
        traffic=TrafficProfile(
            telemetry_interval_s=1.0,
            telemetry_jitter_s=0.0,
            command_interval_s=1.5,
            command_jitter_s=0.0,
            payload_profile=PayloadProfile(base_bytes=68, optional_fields=(10, 8, 6)),
            command_target="actuator-1",
            warmup_s=0.0,
        ),
        failures=(FailureSchedule(at_s=0.0, kind="power_off", target="sensor-a", duration_s=0.0),),
        policy=PolicyConfig(),
        protocol=ProtocolConfig(),
        devices=(
            HardwareDeviceSpec(
                node_id="monitor",
                role="monitor",
                board="sensecap-indicator",
                port=SerialPortConfig(path="/dev/null"),
                capabilities=("display",),
            ),
            HardwareDeviceSpec(
                node_id="sensor-a",
                role="sensor",
                board="esp32-generic",
                port=SerialPortConfig(path="/dev/null"),
                capabilities=("wifi",),
            ),
            HardwareDeviceSpec(
                node_id="border-router",
                role="border_router",
                board="esp-thread-br",
                port=SerialPortConfig(path="/dev/null"),
                capabilities=("thread", "matter"),
            ),
        ),
    )


def test_run_hardware_benchmark_writes_expected_artifacts(tmp_path: Path) -> None:
    config = build_config(tmp_path)

    def session_factory(spec: HardwareDeviceSpec) -> MockSession:
        return MockSession(spec, arm=config.arm)

    run_dir = run_hardware_benchmark(config, output_root=tmp_path / "runs", session_factory=session_factory)
    combined = pd.read_csv(run_dir / "combined_summary.csv").set_index("arm")
    decisions = pd.read_csv(run_dir / config.arm / "policy_decisions.csv")
    traces = pd.read_csv(run_dir / config.arm / "message_traces.csv")

    assert combined.loc["maestro", "total_messages"] == 2
    assert combined.loc["maestro", "delivered_messages"] == 1
    assert combined.loc["maestro", "parent_switches"] == 1
    assert not decisions.empty
    assert len(traces) == 2
    assert (run_dir / "plots" / "delivery_ratio.png").exists()


def test_load_hardware_benchmark_config_rejects_zigbee_without_capability(tmp_path: Path) -> None:
    config_path = tmp_path / "zigbee.toml"
    config_path.write_text(
        """
name = "zigbee-check"
arm = "zigbee"
duration_s = 45

[traffic]
telemetry_interval_s = 5.0
telemetry_jitter_s = 0.0
command_interval_s = 10.0
command_jitter_s = 0.0
command_target = "actuator-1"
warmup_s = 0.0

[traffic.payload_profile]
base_bytes = 68
optional_fields = [10, 8, 6]

[[devices]]
node_id = "sensor-a"
role = "sensor"
board = "esp32-generic"
capabilities = ["wifi"]

[devices.port]
path = "/dev/null"
        """.strip()
    )

    with pytest.raises(ValueError, match="zigbee hardware run requested"):
        load_hardware_benchmark_config(config_path)
