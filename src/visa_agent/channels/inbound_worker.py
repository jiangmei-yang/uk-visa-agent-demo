from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from visa_agent.domain.models import Case, InboundEvent
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
        for row in self.store.claim_inbound(now, channel=self.channel, limit=limit):
            event_id = str(row["id"])
            attempt = int(row["attempt_count"])
            try:
                event = InboundEvent.model_validate_json(str(row["payload_json"]))
                self.workflow.process(event)
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
                self.store.mark_inbound_processed(event_id, now)
                outcomes.append(InboundWorkOutcome(event_id, "PROCESSED"))
        return outcomes
