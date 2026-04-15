from __future__ import annotations

import math
from statistics import mean
from typing import Iterable

from .models import ArmName, MessageTrace, NodeMetrics


def summarize_metrics(
    arm: ArmName,
    traces: Iterable[MessageTrace],
    node_metrics: Iterable[NodeMetrics],
) -> dict[str, object]:
    trace_list = list(traces)
    metric_list = list(node_metrics)
    delivered = [trace for trace in trace_list if trace.delivered]
    command_latencies = [trace.rtt_s for trace in delivered if trace.kind == "command" and trace.rtt_s is not None]
    rtts = [trace.rtt_s for trace in delivered if trace.rtt_s is not None]
    total_messages = len(trace_list)
    total_delivered = len(delivered)
    recovery_windows = [window for metric in metric_list for window in metric.recovery_windows]
    outages = [window for metric in metric_list for window in metric.outages]
    return {
        "arm": arm,
        "total_messages": total_messages,
        "delivered_messages": total_delivered,
        "delivery_ratio": round(total_delivered / total_messages, 6) if total_messages else 0.0,
        "avg_rtt_s": round(mean(rtts), 6) if rtts else None,
        "p50_rtt_s": percentile(rtts, 50),
        "p95_rtt_s": percentile(rtts, 95),
        "command_p50_s": percentile(command_latencies, 50),
        "command_p95_s": percentile(command_latencies, 95),
        "route_recovery_time_avg_s": round(mean(recovery_windows), 6) if recovery_windows else 0.0,
        "application_outage_window_avg_s": round(mean(outages), 6) if outages else 0.0,
        "fragment_count": sum(trace.fragments for trace in trace_list),
        "retransmission_rate": round(sum(trace.retries for trace in trace_list) / total_messages, 6)
        if total_messages
        else 0.0,
        "parent_switches": sum(metric.parent_switches for metric in metric_list),
        "queue_depth_peak": max((metric.queue_depth_peak for metric in metric_list), default=0),
        "relative_energy_cost": round(sum(metric.energy_cost for metric in metric_list), 6),
        "ack_timeouts": sum(metric.ack_timeouts for metric in metric_list),
    }


def percentile(values: list[float | None], percentile_value: int) -> float | None:
    numeric = sorted(value for value in values if value is not None)
    if not numeric:
        return None
    index = min(len(numeric) - 1, max(0, math.ceil((percentile_value / 100) * len(numeric)) - 1))
    return round(float(numeric[index]), 6)
