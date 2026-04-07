from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING

from .models import PolicyDecision

if TYPE_CHECKING:
    from .simulation import SimulationEngine


@dataclass(frozen=True)
class PolicyOutcome:
    selected_parent: str | None
    payload_bytes: int
    kept_optional_fields: tuple[int, ...]
    interval_s: float
    decision: PolicyDecision


class FAMEPolicy:
    def __init__(self, config) -> None:
        self.config = config

    def evaluate(
        self,
        engine: "SimulationEngine",
        node_id: str,
        predicted_payload_bytes: int,
        optional_fields: tuple[int, ...],
        urgent: bool,
        reason: str,
    ) -> PolicyOutcome:
        node = engine.nodes[node_id]
        current_parent = node.current_parent
        interval_before = node.current_interval_s
        budget = self.config.fragmentation_budget_bytes
        kept_optional_fields = tuple(optional_fields)
        payload_after = predicted_payload_bytes

        if not urgent and predicted_payload_bytes > budget:
            drop_order = sorted(optional_fields, reverse=True)
            dropped = 0
            for field_size in drop_order:
                if payload_after <= budget:
                    break
                payload_after -= field_size
                dropped += 1
            kept_optional_fields = tuple(sorted(drop_order[dropped:]))
            optional_fields_dropped = dropped
        else:
            optional_fields_dropped = 0

        fhat = max(0.0, (payload_after - budget) / budget)
        interval_after = interval_before
        if not urgent:
            interval_after = _clip(
                interval_before * (1.0 + self.config.beta * fhat),
                self.config.min_interval_s,
                self.config.max_interval_s,
            )

        rhat = engine.compute_rhat(node_id)
        candidate_scores: dict[str, float] = {}
        candidate_inputs: dict[str, tuple[float, float]] = {}
        for candidate in engine.get_candidate_parents(node_id):
            ehat = engine.compute_ehat(node_id, candidate)
            lhat = engine.compute_lhat(candidate)
            candidate_scores[candidate] = (
                self.config.w1 * ehat
                + self.config.w2 * rhat
                + self.config.w3 * fhat
                + self.config.w4 * lhat
            )
            candidate_inputs[candidate] = (ehat, lhat)

        selected_parent = current_parent
        score_current = candidate_scores.get(current_parent) if current_parent else None
        score_selected = score_current
        if candidate_scores:
            best_candidate = min(candidate_scores, key=candidate_scores.get)
            best_score = candidate_scores[best_candidate]
            current_valid = current_parent in candidate_scores if current_parent else False
            improvement = (score_current - best_score) if score_current is not None else None
            hold_down_expired = engine.env.now >= node.hold_down_until_s
            broken_parent = current_parent is None or not engine.parent_is_usable(node_id, current_parent)
            if broken_parent:
                selected_parent = best_candidate
                score_selected = best_score
            elif (
                best_candidate != current_parent
                and improvement is not None
                and improvement > self.config.delta
                and hold_down_expired
            ):
                selected_parent = best_candidate
                score_selected = best_score
            elif current_parent is None:
                selected_parent = best_candidate
                score_selected = best_score

        switched = selected_parent != current_parent and selected_parent is not None
        if switched:
            engine.assign_parent(
                node_id=node_id,
                parent_id=selected_parent,
                reason=f"policy:{reason}",
                hold_down_s=self.config.hold_down_s,
            )
        elif current_parent and not engine.parent_is_usable(node_id, current_parent):
            engine.clear_parent(node_id=node_id, reason=f"policy:{reason}:broken_parent")

        selected_ehat = candidate_inputs[selected_parent][0] if selected_parent in candidate_inputs else 1.0
        selected_lhat = candidate_inputs[selected_parent][1] if selected_parent in candidate_inputs else 1.0
        decision = PolicyDecision(
            timestamp_s=engine.env.now,
            node_id=node_id,
            arm=engine.arm,
            reason=reason,
            current_parent=current_parent,
            selected_parent=selected_parent,
            switched=switched,
            payload_before_bytes=predicted_payload_bytes,
            payload_after_bytes=payload_after,
            optional_fields_dropped=optional_fields_dropped,
            interval_before_s=interval_before,
            interval_after_s=interval_after,
            ehat=round(selected_ehat, 6),
            rhat=round(rhat, 6),
            fhat=round(fhat, 6),
            lhat=round(selected_lhat, 6),
            score_selected=round(score_selected, 6) if score_selected is not None else None,
            score_current=round(score_current, 6) if score_current is not None else None,
        )
        return PolicyOutcome(
            selected_parent=selected_parent,
            payload_bytes=payload_after,
            kept_optional_fields=kept_optional_fields,
            interval_s=interval_after,
            decision=decision,
        )


def fragmentation_count(payload_bytes: int, budget: int) -> int:
    return max(1, ceil(payload_bytes / max(1, budget)))


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
