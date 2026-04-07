from __future__ import annotations

from .config import FailureSchedule, LinkSpec, NodeSpec, PayloadProfile, SimulationConfig, TrafficProfile


def build_generated_config(
    *,
    name: str,
    duration_s: float,
    seed: int,
    node_count: int,
    telemetry_interval_s: float,
    payload_profile: PayloadProfile,
    disruption_mode: str,
    policy,
    protocol,
    arms,
    output_dir: str,
) -> SimulationConfig:
    if node_count < 8:
        raise ValueError("node_count must be at least 8 to create a meaningful topology")

    routers = max(2, node_count // 5)
    sensors = node_count - routers - 3
    if sensors < 3:
        raise ValueError("node_count too small after allocating routers, border routers, and actuator")

    nodes = [
        {"id": "controller", "role": "controller", "candidate_parents": []},
        {"id": "coordinator", "role": "coordinator", "candidate_parents": []},
        {"id": "border-a", "role": "border_router", "candidate_parents": []},
        {"id": "border-b", "role": "border_router", "candidate_parents": []},
        {"id": "actuator-1", "role": "actuator", "candidate_parents": []},
    ]

    router_ids = [f"router-{index + 1}" for index in range(routers)]
    sensor_ids = [f"sensor-{index + 1}" for index in range(sensors)]

    links: list[LinkSpec] = [
        LinkSpec("controller", "border-a", margin=1.0, latency_ms=10, jitter_ms=1),
        LinkSpec("controller", "border-b", margin=1.0, latency_ms=10, jitter_ms=1),
    ]

    for index, router_id in enumerate(router_ids):
        candidates: list[str] = []
        if index == 0:
            candidates.extend(["border-a", "coordinator"])
        elif index == 1:
            candidates.extend(["border-b", "coordinator", "router-1"])
        else:
            candidates.extend([router_ids[index - 1]])
            if index - 2 >= 0:
                candidates.append(router_ids[index - 2])
            candidates.append("border-b" if index % 2 else "border-a")
        nodes.append({"id": router_id, "role": "router", "candidate_parents": candidates[:3]})

        if index == 0:
            links.extend(
                [
                    LinkSpec(router_id, "border-a", margin=0.92, latency_ms=24, jitter_ms=3),
                    LinkSpec(router_id, "coordinator", margin=0.90, latency_ms=20, jitter_ms=2),
                ]
            )
        elif index == 1:
            links.extend(
                [
                    LinkSpec(router_id, "border-b", margin=0.91, latency_ms=24, jitter_ms=3),
                    LinkSpec(router_id, "coordinator", margin=0.89, latency_ms=22, jitter_ms=2),
                    LinkSpec(router_id, "router-1", margin=0.86, latency_ms=26, jitter_ms=3),
                ]
            )
        else:
            links.append(LinkSpec(router_id, router_ids[index - 1], margin=max(0.62, 0.88 - 0.03 * index)))
            if index - 2 >= 0:
                links.append(
                    LinkSpec(router_id, router_ids[index - 2], margin=max(0.58, 0.82 - 0.02 * index))
                )
            links.append(
                LinkSpec(
                    router_id,
                    "border-b" if index % 2 else "border-a",
                    margin=max(0.52, 0.74 - 0.015 * index),
                    latency_ms=28,
                    jitter_ms=3,
                )
            )

    actuator_candidates = ["router-1", "router-2" if len(router_ids) > 1 else "border-a", "coordinator"]
    nodes[4]["candidate_parents"] = actuator_candidates
    links.extend(
        [
            LinkSpec("actuator-1", actuator_candidates[0], margin=0.87, latency_ms=25, jitter_ms=4),
            LinkSpec("actuator-1", actuator_candidates[1], margin=0.84, latency_ms=26, jitter_ms=4),
            LinkSpec("actuator-1", "coordinator", margin=0.81, latency_ms=21, jitter_ms=3),
        ]
    )

    for index, sensor_id in enumerate(sensor_ids):
        primary = router_ids[index % len(router_ids)]
        secondary = router_ids[(index + 1) % len(router_ids)]
        tertiary = "coordinator" if index < 2 else ("border-a" if index % 2 == 0 else "border-b")
        candidates = [primary, secondary, tertiary]
        nodes.append({"id": sensor_id, "role": "sensor", "candidate_parents": candidates})
        links.extend(
            [
                LinkSpec(sensor_id, primary, margin=max(0.60, 0.86 - 0.01 * (index % 5)), latency_ms=27),
                LinkSpec(sensor_id, secondary, margin=max(0.55, 0.80 - 0.01 * (index % 6)), latency_ms=29),
                LinkSpec(sensor_id, tertiary, margin=max(0.50, 0.76 - 0.015 * (index % 4)), latency_ms=22),
            ]
        )

    failures = _disruption_schedule(disruption_mode, router_ids)
    return SimulationConfig(
        name=name,
        duration_s=duration_s,
        seed=seed,
        arms=tuple(arms),
        nodes=tuple(
            NodeSpec(
                id=item["id"],
                role=item["role"],
                candidate_parents=tuple(item["candidate_parents"]),
            )
            for item in nodes
        ),
        links=tuple(links),
        traffic=TrafficProfile(
            telemetry_interval_s=telemetry_interval_s,
            telemetry_jitter_s=0.75,
            command_interval_s=max(5.0, telemetry_interval_s * 2.5),
            command_jitter_s=0.25,
            payload_profile=payload_profile,
            command_target="actuator-1",
            warmup_s=1.0,
        ),
        failures=tuple(
            FailureSchedule(
                at_s=item["at_s"],
                kind=item["kind"],
                target=item.get("target"),
                duration_s=item.get("duration_s"),
                link=item.get("link"),
                degrade_to_margin=item.get("degrade_to_margin"),
            )
            for item in failures
        ),
        policy=policy,
        protocol=protocol,
        output_dir=output_dir,
    )


def _disruption_schedule(disruption_mode: str, router_ids: list[str]) -> list[dict[str, object]]:
    if disruption_mode == "router_power_off":
        return [
            {
                "at_s": 20.0,
                "kind": "power_off",
                "target": router_ids[min(2, len(router_ids) - 1)],
                "duration_s": 12.0,
            }
        ]
    if disruption_mode == "border_router_loss":
        return [
            {
                "at_s": 20.0,
                "kind": "border_router_loss",
                "target": "border-a",
                "duration_s": 14.0,
            }
        ]
    if disruption_mode == "link_degradation":
        target_router = router_ids[min(1, len(router_ids) - 1)]
        return [
            {
                "at_s": 20.0,
                "kind": "degrade_link",
                "link": (target_router, "border-b" if len(router_ids) > 1 else "border-a"),
                "degrade_to_margin": 0.28,
                "duration_s": 18.0,
            }
        ]
    raise ValueError(f"Unsupported disruption mode: {disruption_mode}")
