from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class CaseStatus(StrEnum):
    DRAFT = "DRAFT"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    DELIVERED_AFTER_CONFIRMATION = "DELIVERED_AFTER_CONFIRMATION"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class WorkflowStage(StrEnum):
    NEW = "NEW"
    CONSENTED = "CONSENTED"
    ROUTE_SCREENING = "ROUTE_SCREENING"
    INTAKE = "INTAKE"
    PROFILE_CONFIRMATION = "PROFILE_CONFIRMATION"
    COLLECTING_DOCUMENTS = "COLLECTING_DOCUMENTS"
    DOCUMENT_REVIEW = "DOCUMENT_REVIEW"
    FINAL_CONFIRMATION = "FINAL_CONFIRMATION"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    DELIVERED_AFTER_CONFIRMATION = "DELIVERED_AFTER_CONFIRMATION"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class DocumentStatus(StrEnum):
    REQUESTED = "REQUESTED"
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    ACCEPTED_FOR_REVIEW = "ACCEPTED_FOR_REVIEW"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NEEDS_REPLACEMENT = "NEEDS_REPLACEMENT"
    NEEDS_CERTIFIED_TRANSLATION = "NEEDS_CERTIFIED_TRANSLATION"
    SUPERSEDED = "SUPERSEDED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class IssueSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ProvenanceState(StrEnum):
    EXTRACTED_UNVERIFIED = "extracted_unverified"
    VERIFIED = "verified"
    DEMO_SYNTHETIC = "demo_synthetic"
    STALE = "stale"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    fact_key: str
    value: Any
    source_event_id: str
    source_document_id: str | None = None
    source_excerpt: str
    page: int | None = None
    extraction_method: str
    model_version: str
    confidence: float = Field(ge=0, le=1)
    confirmed: bool = False
    superseded: bool = False
    provenance_state: ProvenanceState = ProvenanceState.DEMO_SYNTHETIC
    created_at: datetime = Field(default_factory=utc_now)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    kind: str
    sha256: str
    mime_type: str
    status: DocumentStatus
    source_event_id: str
    path: str
    language: str = "en"
    page_count: int = 0
    received_at: datetime = Field(default_factory=utc_now)
    supersedes_document_id: str | None = None
    translation_for_document_id: str | None = None


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    title: str
    detail: str
    severity: IssueSeverity
    status: IssueStatus = IssueStatus.OPEN
    related_document_ids: list[str] = Field(default_factory=list)
    resolution: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    blocker: bool
    applicable: bool
    satisfied: bool = False
    document_ids: list[str] = Field(default_factory=list)
    rule_version: str
    source_urls: list[str]


class CaseProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    date_of_birth: date | None = None
    nationality: str | None = None
    nationality_country: str | None = None
    application_country: str | None = None
    planned_arrival_date: date | None = None
    planned_departure_date: date | None = None
    visit_purpose: str | None = None
    uk_accommodation: str | None = None
    estimated_trip_cost_gbp: int | None = None
    current_address: str | None = None
    occupation_status: str | None = None
    annual_income_gbp: int | None = None
    funding_source: str | None = None
    sponsor_name: str | None = None
    sponsor_relationship: str | None = None
    sponsor_is_in_uk: bool | None = None
    # Tri-state by design: silence is not an explicit negative declaration.
    has_serious_history: bool | None = None
    route_confirmed_standard_visitor: bool = False


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    external_thread_id: str = Field(
        validation_alias=AliasChoices("external_thread_id", "email_thread_id")
    )
    applicant_contact: str = Field(
        validation_alias=AliasChoices("applicant_contact", "applicant_email")
    )
    primary_channel: str = "email"
    status: CaseStatus = CaseStatus.DRAFT
    stage: WorkflowStage = WorkflowStage.NEW
    profile: CaseProfile = Field(default_factory=CaseProfile)
    profile_confirmed: bool = False
    final_summary_confirmed: bool = False
    customer_language: str = "en"
    customer_answers: list[str] = Field(default_factory=list)
    latest_changes: dict[str, str] = Field(default_factory=dict)
    latest_received_facts: dict[str, str] = Field(default_factory=dict)
    deferred_fields: list[str] = Field(default_factory=list)
    latest_deferred_fields: list[str] = Field(default_factory=list)
    latest_customer_message: str = ""
    latest_document_names: list[str] = Field(default_factory=list)
    last_requested_fields: list[str] = Field(default_factory=list)
    confirmation_fingerprint: str | None = None
    confirmation_kind: str | None = None
    confirmation_request_event_id: str | None = None
    human_review_reason: str | None = None
    policy_version: str
    requirements: list[Requirement] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    outbound_message_ids: list[str] = Field(default_factory=list)
    delivery_path: str | None = None
    last_inbound_received_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def active_evidence(self, fact_key: str) -> list[Evidence]:
        return [item for item in self.evidence if item.fact_key == fact_key and not item.superseded]

    def open_blockers(self) -> list[Issue]:
        return [
            item
            for item in self.issues
            if item.severity == IssueSeverity.BLOCKER and item.status == IssueStatus.OPEN
        ]


class InboundEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    channel: str = "email_fixture"
    external_thread_id: str
    sender: str
    subject: str
    body: str
    requested_fields: list[str] = Field(default_factory=list)
    known_profile: dict[str, Any] = Field(default_factory=dict)
    attachment_paths: list[str] = Field(default_factory=list)
    rfc_message_id: str | None = None
    references: str | None = None
    received_at: datetime


class GateResult(BaseModel):
    allowed: bool
    checks: dict[str, bool]
    reasons: list[str]
