"""Outreach Agent (Pillar 3) — HIGH RISK, gated.

Finds a public hiring contact, drafts a tailored email, runs a basic compliance
check, then routes the *send* through an approval gate (blocked by default).
Email only — no automated social-platform DMs (see docs/feasibility.md).
"""
from __future__ import annotations

from typing import Optional

from ..gates import ApprovalGate, OutwardAction
from ..llm import LLM
from ..profile import Profile
from ..tools.email import EmailMessage, EmailSender
from ..tools.enrichment import Contact, EnrichmentProvider
from ..tools.job_data import Job


class Outreach:
    name = "Outreach Agent"
    risk = "high"
    TAG = "task:draft_outreach"

    def __init__(self, llm: LLM, enrichment: EnrichmentProvider, email_sender: EmailSender):
        self.llm = llm
        self.enrichment = enrichment
        self.email_sender = email_sender

    def find_contact(self, job: Job) -> Optional[Contact]:
        # Pass the whole posting so the provider can read a contact from the posting itself.
        return self.enrichment.find_hiring_contact(job.company, job.title, posting=job)

    def draft(self, profile: Profile, job: Job, contact: Optional[Contact], signoff: str = "compliance") -> dict:
        """Draft an outreach email. signoff='personal' writes a clean 1:1 email signed
        with the candidate's real details; 'compliance' (default) ends with CAN-SPAM
        placeholders for the product's automated/bulk path."""
        who = f"{contact.name}, {contact.title}" if contact else "the hiring team"
        if signoff == "personal":
            footer = (
                "End with a warm, professional sign-off using the candidate's real name "
                f"('{profile.identity.name or 'the candidate'}') and their contact details where "
                f"available (email: {profile.identity.email or 'n/a'}, phone: {profile.identity.phone or 'n/a'}). "
                "This is a personal 1:1 email — do NOT add unsubscribe links or marketing footers."
            )
        else:
            footer = (
                "End the body with this exact footer on its own lines: "
                "[[SENDER_NAME]] / [[UNSUBSCRIBE]] / [[POSTAL_ADDRESS]] "
                "(required compliance placeholders the user fills before sending)."
            )
        system = (
            f"[{self.TAG}] Draft a concise, personalized outreach email to a hiring contact. "
            'Return ONLY JSON: {"subject":"","body":""}. Professional, specific, under 160 words. '
            "Reference the candidate's most relevant experience for THIS role; do not invent facts. " + footer
        )
        user = (
            f"CANDIDATE:\n{profile.to_prompt_text()}\n\n"
            f"ROLE: {job.title} @ {job.company}\n{(job.description or '')[:400]}\nCONTACT: {who}"
        )
        return self.llm.complete_json(system, user)

    @staticmethod
    def compliance_check(message: dict) -> list[str]:
        """Basic CAN-SPAM presence checks. Real impl verifies *resolved* values."""
        body = message.get("body", "") if isinstance(message, dict) else ""
        issues = []
        if "[[UNSUBSCRIBE]]" not in body:
            issues.append("missing opt-out (CAN-SPAM)")
        if "[[POSTAL_ADDRESS]]" not in body:
            issues.append("missing postal address (CAN-SPAM)")
        if "[[SENDER_NAME]]" not in body:
            issues.append("missing sender identity")
        return issues

    def send(self, message: dict, contact: Optional[Contact], gate: ApprovalGate) -> dict:
        if contact is None:
            return {"status": "no_contact", "reason": "no hiring contact found"}
        issues = self.compliance_check(message)
        action = OutwardAction(
            kind="send_email",
            target=contact.email,
            summary=f"Email '{message.get('subject', '')}' to {contact.name} <{contact.email}>",
            payload={"message": message, "compliance_issues": issues},
        )
        decision = gate.review(action)
        if not decision.approved:
            return {
                "status": "pending_approval",
                "reason": decision.reason,
                "compliance_issues": issues,
                "action": action,
            }
        result = self.email_sender.send(
            EmailMessage(to=contact.email, subject=message.get("subject", ""), body=message.get("body", ""))
        )
        return {"status": "sent_via_stub", "result": result, "compliance_issues": issues}
