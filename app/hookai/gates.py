"""Approval gates — the human-in-the-loop layer.

Any outward action taken in the job-seeker's name (submitting an application,
emailing a person) is wrapped in an OutwardAction and must clear a gate.
The default `AutoBlockGate` never auto-approves — it records the action as
pending so a human can approve it out-of-band. This is what keeps Hook AI on
the compliant side of the line described in docs/feasibility.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class OutwardAction:
    kind: str  # "submit_application" | "send_email"
    target: str  # platform or recipient
    summary: str
    payload: dict = field(default_factory=dict)


@dataclass
class GateDecision:
    approved: bool
    reason: str = ""


@runtime_checkable
class ApprovalGate(Protocol):
    def review(self, action: OutwardAction) -> GateDecision: ...


class AutoBlockGate:
    """Default safe gate. Never auto-approves; queues actions as pending.

    Use this in demos and any automated/CROO-order context: a service can prepare
    a ready-to-send artifact, but a human still authorizes the actual send."""

    def __init__(self) -> None:
        self.pending: list[OutwardAction] = []

    def review(self, action: OutwardAction) -> GateDecision:
        self.pending.append(action)
        return GateDecision(False, "blocked: awaiting user approval (AutoBlockGate)")


class ConsoleApprovalGate:
    """Interactive y/N prompt — human-in-the-loop for CLI use."""

    def review(self, action: OutwardAction) -> GateDecision:
        print(f"\n[APPROVAL NEEDED] {action.kind} -> {action.target}")
        print(action.summary)
        answer = input("Approve? [y/N] ").strip().lower()
        return GateDecision(answer == "y", "user choice")


class ApproveAllGate:
    """TEST ONLY. Approves everything — never use for real outward actions."""

    def review(self, action: OutwardAction) -> GateDecision:
        return GateDecision(True, "approved (test gate)")
