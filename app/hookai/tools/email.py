"""Email sender connector (Pillar 3).

Real implementation: an email provider (e.g. SES/SendGrid/SMTP) used ONLY after a
compliance review — CAN-SPAM (truthful headers, identity, postal address, working
opt-out) and, for EU recipients, a documented GDPR lawful basis. The stub logs to
an outbox and never sends anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmailMessage:
    to: str
    subject: str
    body: str


@runtime_checkable
class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> dict: ...


class StubEmailSender:
    """Records messages to an in-memory outbox. NEVER transmits."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> dict:
        self.outbox.append(message)
        return {"status": "logged_not_sent", "to": message.to}
