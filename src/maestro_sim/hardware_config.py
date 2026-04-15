from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .config import (
    FailureSchedule,
    PayloadProfile,
    PolicyConfig,
    ProtocolConfig,
    TrafficProfile,
    _failure_schedule,
    _load_path,
    _payload_profile,
    _policy_config,
    _protocol_config,
    _traffic_profile,
)
from .models import ArmName

HardwareDeviceRole = Literal["monitor", "sensor", "actuator", "border_router", "router"]
HardwareTransportKind = Literal["serial_jsonl"]


@dataclass(frozen=True)
class SerialPortConfig:
    path: str
    baudrate: int = 115200
    timeout_s: float = 0.1


@dataclass(frozen=True)
class HardwareDeviceSpec:
    node_id: str
    role: HardwareDeviceRole
    board: str
    transport: HardwareTransportKind = "serial_jsonl"
    port: SerialPortConfig = field(default_factory=lambda: SerialPortConfig(path=""))
    expected_identity: str | None = None
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardwareBenchmarkConfig:
    name: str
    arm: ArmName
    duration_s: float
    output_dir: str = "outputs/hardware"
    controller_id: str = "controller"
    monitor_node_id: str | None = None
    poll_interval_s: float = 0.05
    dashboard_refresh_s: float = 0.5
    grace_period_s: float = 1.0
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
    devices: tuple[HardwareDeviceSpec, ...] = ()

    def validate(self) -> None:
        if not self.devices:
            raise ValueError("hardware benchmark config must define at least one device")
        device_ids = {device.node_id for device in self.devices}
        if len(device_ids) != len(self.devices):
            raise ValueError("hardware benchmark config contains duplicate device IDs")
        if self.monitor_node_id and self.monitor_node_id not in device_ids:
            raise ValueError(f"monitor_node_id {self.monitor_node_id!r} was not found in devices")
        if self.arm == "zigbee":
            has_zigbee_capability = any("zigbee" in device.capabilities for device in self.devices)
            if not has_zigbee_capability:
                raise ValueError(
                    "zigbee hardware run requested, but no device declares zigbee capability; "
                    "keep zigbee in simulation or add explicit zigbee-capable hardware"
                )


def load_hardware_benchmark_config(path: str | Path) -> HardwareBenchmarkConfig:
    raw = _load_path(path)
    config = HardwareBenchmarkConfig(
        name=str(raw["name"]),
        arm=str(raw["arm"]),
        duration_s=float(raw["duration_s"]),
        output_dir=str(raw.get("output_dir", "outputs/hardware")),
        controller_id=str(raw.get("controller_id", "controller")),
        monitor_node_id=str(raw["monitor_node_id"]) if raw.get("monitor_node_id") else None,
        poll_interval_s=float(raw.get("poll_interval_s", 0.05)),
        dashboard_refresh_s=float(raw.get("dashboard_refresh_s", 0.5)),
        grace_period_s=float(raw.get("grace_period_s", 1.0)),
        traffic=_traffic_profile(raw["traffic"]),
        failures=tuple(_failure_schedule(item) for item in raw.get("failures", ())),
        policy=_policy_config(raw.get("policy")),
        protocol=_protocol_config(raw.get("protocol")),
        devices=tuple(_hardware_device_spec(item) for item in raw["devices"]),
    )
    config.validate()
    return config


def _hardware_device_spec(raw: dict[str, object]) -> HardwareDeviceSpec:
    port_raw = raw.get("port") or {}
    if not isinstance(port_raw, dict):
        raise ValueError("device port must be a table/object")
    return HardwareDeviceSpec(
        node_id=str(raw["node_id"]),
        role=str(raw["role"]),
        board=str(raw["board"]),
        transport=str(raw.get("transport", "serial_jsonl")),
        port=SerialPortConfig(
            path=str(port_raw["path"]),
            baudrate=int(port_raw.get("baudrate", 115200)),
            timeout_s=float(port_raw.get("timeout_s", 0.1)),
        ),
        expected_identity=str(raw["expected_identity"]) if raw.get("expected_identity") else None,
        capabilities=tuple(str(item) for item in raw.get("capabilities", ())),
    )
