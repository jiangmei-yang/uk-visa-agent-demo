from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import getaddresses
from typing import Protocol

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.privacy.consent import ConsentLedger
from visa_agent.storage.sqlite import SQLiteStore


class InboundWorkflow(Protocol):
    def process(self, event: InboundEvent) -> tuple[Case, bool, str]: ...


@dataclass(frozen=True)
class InboundWorkOutcome:
    event_id: str
    status: str


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {' '.join(str(error).split())[:200]}"


class InboundEventWorker:
    def __init__(
        self,
        store: SQLiteStore,
        workflow: InboundWorkflow,
        *,
        channel: str,
        max_attempts: int = 3,
        base_backoff_seconds: int = 30,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.store = store
        self.workflow = workflow
        self.channel = channel
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds

    def process_due(self, now: datetime, limit: int = 20) -> list[InboundWorkOutcome]:
        outcomes: list[InboundWorkOutcome] = []
        ledger = ConsentLedger(self.store)
        self._resume_authorized_deferred(ledger)
        for row in self.store.claim_inbound(now, channel=self.channel, limit=limit):
            event_id = str(row["id"])
            attempt = int(row["attempt_count"])
            try:
                event = InboundEvent.model_validate_json(str(row["payload_json"]))
                _, _, plan = self.workflow.process(event)
            except Exception as error:
                safe_error = _safe_error(error)
                if attempt >= self.max_attempts:
                    self.store.mark_inbound_failed(event_id, safe_error)
                    outcomes.append(InboundWorkOutcome(event_id, "FAILED"))
                else:
                    available_at = now + timedelta(
                        seconds=self.base_backoff_seconds * (2 ** (attempt - 1))
                    )
                    self.store.mark_inbound_retry(event_id, safe_error, available_at)
                    outcomes.append(InboundWorkOutcome(event_id, "RETRY"))
            else:
                if plan == "processing_notice":
                    self.store.mark_inbound_awaiting_consent(event_id)
                    outcomes.append(InboundWorkOutcome(event_id, "AWAITING_CONSENT"))
                else:
                    ledger.mark_completed(event_id)
                    self.store.mark_inbound_processed(event_id, now)
                    outcomes.append(InboundWorkOutcome(event_id, "PROCESSED"))
        return outcomes

    def _resume_authorized_deferred(self, ledger: ConsentLedger) -> None:
        for row in self.store.consent_resume_candidates(self.channel):
            try:
                event = InboundEvent.model_validate_json(str(row["payload_json"]))
            except ValueError:
                continue  # Retain corrupt/unbound source material for operator review.
            case = self.store.get_case(str(row["case_id"]))
            # Reviewed queues retain the original Gmail envelope. The candidate
            # query and atomic resume both require its exact review/held binding;
            # this is not permission for arbitrary cross-channel replay.
            matching_channel = event.channel == self.channel or (
                self.channel == "gmail_review" and event.channel == "gmail"
            )
            if (case is None or event.id != row["id"] or not matching_channel
                    or case.primary_channel != event.channel
                    or event.external_thread_id != case.external_thread_id
                    or not self._same_sender(event, case) or not ledger.allowed(case)):
                continue
            self.store.resume_inbound_after_consent(
                event.id, case_id=case.id, channel=self.channel, consent_epoch=int(row["epoch"]),
                payload_json=str(row["payload_json"]), case_snapshot_json=case.model_dump_json(),
            )

    @staticmethod
    def _same_sender(event: InboundEvent, case: Case) -> bool:
        if event.channel.startswith("whatsapp"):
            return bool(event.sender.strip()) and event.sender.strip().casefold() == case.applicant_contact.strip().casefold()
        senders = getaddresses([event.sender])
        applicants = getaddresses([case.applicant_contact])
        return bool(len(senders) == len(applicants) == 1 and senders[0][1] and "@" in senders[0][1]
                    and senders[0][1].casefold() == applicants[0][1].casefold())
