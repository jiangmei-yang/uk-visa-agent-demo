from __future__ import annotations

from dataclasses import dataclass

from visa_agent.channels.email_ingestion import EmailIngestionBoundary
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.domain.models import Case
from visa_agent.workflow.service import WorkflowService


@dataclass(frozen=True)
class GmailProcessingOutcome:
    provider_message_id: str
    status: str
    case: Case | None = None


class GmailInboxProcessor:
    """Small polling boundary; scheduling and OAuth stay in deployment configuration."""

    def __init__(
        self,
        adapter: GmailAdapter,
        ingestion: EmailIngestionBoundary,
        workflow: WorkflowService,
    ) -> None:
        self.adapter = adapter
        self.ingestion = ingestion
        self.workflow = workflow

    def process_query(self, query: str, max_results: int = 20) -> list[GmailProcessingOutcome]:
        outcomes: list[GmailProcessingOutcome] = []
        for message_id in self.adapter.list_message_ids(query, max_results=max_results):
            raw_message = self.adapter.get_raw_message(message_id)
            result = self.ingestion.ingest(
                raw_message.raw,
                provider_message_id=raw_message.provider_message_id,
                provider_thread_id=raw_message.provider_thread_id,
            )
            if result.event is None:
                outcomes.append(
                    GmailProcessingOutcome(
                        provider_message_id=raw_message.provider_message_id,
                        status=result.failure_code or "INGESTION_FAILED",
                    )
                )
                continue
            case, duplicate, plan = self.workflow.process(result.event)
            outcomes.append(
                GmailProcessingOutcome(
                    provider_message_id=raw_message.provider_message_id,
                    status="duplicate_ignored" if duplicate else plan,
                    case=case,
                )
            )
        return outcomes
