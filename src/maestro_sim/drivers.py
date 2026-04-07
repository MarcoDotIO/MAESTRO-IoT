from __future__ import annotations

from abc import ABC, abstractmethod

from .models import RuntimeMessage


class EndpointDriver(ABC):
    @abstractmethod
    def commission(self, node_id: str) -> None: ...

    @abstractmethod
    def publish_telemetry(self, node_id: str, urgent: bool = False) -> RuntimeMessage: ...

    @abstractmethod
    def send_command(self, source_id: str, target_id: str) -> RuntimeMessage: ...

    @abstractmethod
    def receive(self, node_id: str, message: RuntimeMessage) -> None: ...

    @abstractmethod
    def inject_failure(self, target_id: str, active: bool) -> None: ...

    @abstractmethod
    def snapshot_metrics(self) -> dict[str, object]: ...


class SimEndpointDriver(EndpointDriver):
    def __init__(self, engine: "SimulationEngine") -> None:
        self.engine = engine

    def commission(self, node_id: str) -> None:
        self.engine.commission_node(node_id)

    def publish_telemetry(self, node_id: str, urgent: bool = False) -> RuntimeMessage:
        return self.engine.publish_telemetry(node_id=node_id, urgent=urgent)

    def send_command(self, source_id: str, target_id: str) -> RuntimeMessage:
        return self.engine.send_command(source_id=source_id, target_id=target_id)

    def receive(self, node_id: str, message: RuntimeMessage) -> None:
        self.engine.receive(node_id=node_id, message=message)

    def inject_failure(self, target_id: str, active: bool) -> None:
        self.engine.set_node_active(target_id, active)

    def snapshot_metrics(self) -> dict[str, object]:
        return self.engine.snapshot_metrics()
