"""Typed, revisioned application records; not a model ownership/consent guard.

The workflow must establish a current applicant statement and the intended
record before constructing a command. This module checks literal provenance,
schema, optimistic concurrency and replay. A matching quote alone does NOT
prove that a statement belongs to the applicant or that the document is valid.
No command grants consent, confirms a profile or changes a release decision.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecordKind = Literal["travel", "uk_contact"]
RECORD_KINDS: tuple[RecordKind, ...] = ("travel", "uk_contact")
DeclarationState = Literal["unknown", "partial", "none_declared", "complete_declared"]
CollectionState = Literal["unasked", "unknown", "partial", "none_declared", "complete_declared"]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class QuotedText(BaseModel):
    """Exact supplied spelling/precision; normalization needs separate evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str = Field(min_length=1, max_length=400)
    source_excerpt: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def literal_value(self) -> QuotedText:
        if not self.value.strip() or self.value not in self.source_excerpt:
            raise ValueError("Record value must occur verbatim in its source excerpt")
        return self


class RecordFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def supplied(self) -> dict[str, QuotedText]:
        return {name: item for name in type(self).model_fields
                if isinstance(item := getattr(self, name), QuotedText)}


class TravelFields(RecordFields):
    country: QuotedText | None = None
    # Keep "May 2023" or "2023年夏天" as supplied, never invent a calendar day.
    period: QuotedText | None = None
    purpose: QuotedText | None = None


class UKContactFields(RecordFields):
    name: QuotedText | None = None
    relationship: QuotedText | None = None
    address: QuotedText | None = None
    phone: QuotedText | None = None
    passport_number: QuotedText | None = None
    # Accommodation/support information is not a profile funding-source update.
    support_details: QuotedText | None = None


class TravelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["travel"] = "travel"
    fields: TravelFields


class UKContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["uk_contact"] = "uk_contact"
    fields: UKContactFields


RecordInput = Annotated[TravelInput | UKContactInput, Field(discriminator="kind")]


class RecordCommand(BaseModel):
    """Trusted-planner transaction; never a direct instruction from an LLM."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["add", "amend", "withdraw"]
    record: RecordInput
    target_id: str | None = None
    expected_revision_digest: str | None = None
    # An amendment/retraction needs its own current quoted correction statement.
    change_excerpt: str | None = Field(default=None, min_length=1, max_length=1200)

    @model_validator(mode="after")
    def command_shape(self) -> RecordCommand:
        supplied = self.record.fields.supplied()
        if self.action == "add":
            if self.target_id is not None or self.expected_revision_digest is not None or self.change_excerpt is not None:
                raise ValueError("New record identifiers are assigned by the ledger")
            identifying = {"country"} if self.record.kind == "travel" else {"name", "relationship"}
            if not identifying.intersection(supplied):
                raise ValueError("A new record needs a country or a contact identity")
        else:
            if not self.target_id or not self.expected_revision_digest or not self.change_excerpt:
                raise ValueError("A correction needs an exact target, current digest and source statement")
            if self.action == "withdraw" and supplied:
                raise ValueError("A withdrawal cannot smuggle in replacement facts")
            if self.action == "amend" and not supplied:
                raise ValueError("An amendment must supply at least one field")
        return self


class RecordedText(QuotedText):
    source_event_id: str = Field(min_length=1)
    source_body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance_state: Literal["extracted_unverified"] = "extracted_unverified"


class RecordRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: str
    kind: RecordKind
    revision: int = Field(ge=1)
    active: bool
    fields: dict[str, RecordedText]
    changed_by_event_id: str
    predecessor_digest: str | None = None
    change_excerpt: str | None = None

    @model_validator(mode="after")
    def typed_snapshot(self) -> RecordRevision:
        schema = TravelFields if self.kind == "travel" else UKContactFields
        if not self.fields or not set(self.fields) <= set(schema.model_fields):
            raise ValueError("Record snapshot has fields outside its record type")
        identifying = {"country"} if self.kind == "travel" else {"name", "relationship"}
        if not identifying.intersection(self.fields):
            raise ValueError("Record snapshot lost its identifying field")
        return self

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class CollectionDeclaration(BaseModel):
    """Trusted interpretation of an explicit statement, not a release approval.

    The planner must check ownership, scope and meaning before using this type.
    `complete_declared` means the applicant says the list is exhaustive; it does
    not certify sufficient detail, documents, truth or policy applicability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: RecordKind
    state: DeclarationState
    source_excerpt: str = Field(min_length=1, max_length=1200)
    expected_records_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def nonblank_source(self) -> CollectionDeclaration:
        if not self.source_excerpt.strip():
            raise ValueError("A declaration needs an explicit source statement")
        return self


class DeclarationRevision(CollectionDeclaration):
    revision: int = Field(ge=1)
    source_event_id: str = Field(min_length=1)
    source_body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    predecessor_digest: str | None = None

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def _records_digest(case_id: str, kind: RecordKind, current: dict[str, RecordRevision]) -> str:
    return _digest({"case_id": case_id, "kind": kind, "records": {
        key: record.model_dump(mode="json") for key, record in current.items() if record.kind == kind
    }})


class ApplicationRecordLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1)
    revisions: list[RecordRevision] = Field(default_factory=list)
    declarations: list[DeclarationRevision] = Field(default_factory=list)
    processed_batches: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def intact_history(self) -> ApplicationRecordLedger:
        current: dict[str, RecordRevision] = {}
        snapshots = {_records_digest(self.case_id, kind, current): (kind, False)
                     for kind in RECORD_KINDS}
        for revision in self.revisions:
            prior = current.get(revision.record_id)
            if prior is None:
                if revision.revision != 1 or revision.predecessor_digest is not None or not revision.active:
                    raise ValueError("Record history must start with an active first revision")
            elif (not prior.active or prior.kind != revision.kind or revision.revision != prior.revision + 1
                  or revision.predecessor_digest != prior.digest() or not revision.change_excerpt):
                raise ValueError("Record history has a broken or withdrawn revision chain")
            current[revision.record_id] = revision
            snapshots[_records_digest(self.case_id, revision.kind, current)] = (
                revision.kind, any(r.active and r.kind == revision.kind for r in current.values()))
        prior_declarations: dict[RecordKind, DeclarationRevision] = {}
        for declaration in self.declarations:
            previous = prior_declarations.get(declaration.kind)
            if (declaration.revision != (previous.revision + 1 if previous else 1)
                    or declaration.predecessor_digest != (previous.digest() if previous else None)):
                raise ValueError("Collection declaration history has a broken revision chain")
            snapshot = snapshots.get(declaration.expected_records_digest)
            if snapshot is None or snapshot[0] != declaration.kind:
                raise ValueError("Collection declaration refers to an unknown record snapshot")
            if ((declaration.state == "none_declared" and snapshot[1])
                    or (declaration.state == "complete_declared" and not snapshot[1])):
                raise ValueError("Collection declaration contradicts its record snapshot")
            prior_declarations[declaration.kind] = declaration
        return self

    def current(self, *, include_withdrawn: bool = False) -> dict[str, RecordRevision]:
        current = {revision.record_id: revision for revision in self.revisions}
        return {key: value for key, value in current.items() if include_withdrawn or value.active}

    def fingerprint(self) -> str:
        # Include tombstones: a withdrawal is a material profile change, not an
        # invitation to accept an old confirmation for the surviving records.
        payload: dict[str, object] = {"case_id": self.case_id, "records": {
            key: record.model_dump(mode="json")
            for key, record in self.current(include_withdrawn=True).items()
        }}
        if self.declarations:
            payload["declarations"] = {kind: declaration.model_dump(mode="json")
                                       for kind, declaration in self.latest_declarations().items()}
        return _digest(payload)

    def records_digest(self, kind: RecordKind) -> str:
        return _records_digest(self.case_id, kind, self.current(include_withdrawn=True))

    def latest_declarations(self) -> dict[RecordKind, DeclarationRevision]:
        return {declaration.kind: declaration for declaration in self.declarations}

    def collection_state(self, kind: RecordKind) -> CollectionState:
        declaration = self.latest_declarations().get(kind)
        if declaration is None:
            return "partial" if any(r.kind == kind for r in self.revisions) else "unasked"
        # Any material edit invalidates a former exhaustive-list assertion. An
        # explicit uncertainty remains deferred until the applicant updates it.
        if (declaration.state in {"none_declared", "complete_declared"}
                and declaration.expected_records_digest != self.records_digest(kind)):
            return "partial"
        return declaration.state

    def customer_snapshot(self) -> dict[str, object]:
        """Current facts with sources, never withdrawn records or internal replay history."""
        return {
            "completeness": "not_assessed",
            "collections": {kind: {
                "state": self.collection_state(kind),
                "latest_declaration": declaration.model_dump(mode="json", exclude={"predecessor_digest"})
                if (declaration := self.latest_declarations().get(kind)) else None,
            } for kind in RECORD_KINDS},
            "records": [record.model_dump(mode="json", exclude={"predecessor_digest", "change_excerpt"})
                        for record in self.current().values()],
        }


def application_record_rows(ledger: ApplicationRecordLedger, language: str = "en") -> list[str]:
    """Readable summary of collected records, not a declaration of completeness."""
    zh = language == "zh"
    labels = ({"country": "国家／地区", "period": "旅行时间", "purpose": "目的",
               "name": "姓名", "relationship": "与你的关系", "address": "地址", "phone": "电话",
               "passport_number": "护照号码", "support_details": "住宿或其他支持安排"} if zh else
              {"country": "Country/territory", "period": "Travel period", "purpose": "Purpose",
               "name": "Name", "relationship": "Relationship to you", "address": "Address", "phone": "Phone",
               "passport_number": "Passport number", "support_details": "Accommodation or other support"})
    rows = ["以下是已提供的记录，尚未确认旅行历史或联系人信息完整。" if zh else
            "Recorded entries below; travel-history and contact completeness have not yet been confirmed."]
    for kind in RECORD_KINDS:
        label = ({"travel": "旅行记录", "uk_contact": "英国亲属或联系人"} if zh else
                 {"travel": "Travel history", "uk_contact": "UK relatives or contacts"})[kind]
        records = [record for record in ledger.current().values() if record.kind == kind]
        rows.append(label)
        state = ledger.collection_state(kind)
        status_labels = ({"unasked": "尚未确认这部分情况。", "unknown": "你表示暂时不确定，留待核实。",
                          "partial": "已记录部分信息，尚未确认是否齐全。", "none_declared": "你已明确表示没有相关记录。",
                          "complete_declared": "你表示已列出全部记录；具体信息仍需核对。"} if zh else
                         {"unasked": "This section has not been assessed.", "unknown": "You said you are unsure; deferred for checking.",
                          "partial": "Partly recorded; the list has not been confirmed as exhaustive.",
                          "none_declared": "You explicitly stated there are no relevant records.",
                          "complete_declared": "You said this is the full list; the details still need checking."})
        rows.append(status_labels[state])
        if not records and state != "none_declared":
            rows.append("尚未收集到有效记录，不代表没有相关情况。" if zh else
                        "No active entries collected; this does not mean there are none.")
        for index, record in enumerate(records, 1):
            rows.append(f"{label} {index}")
            schema = TravelFields if kind == "travel" else UKContactFields
            for field in schema.model_fields:
                fact = record.fields.get(field)
                # Missing contact phone/passport data can be inapplicable;
                # do not invent a blanket request just to fill a rendered row.
                if fact is not None:
                    rows.append(f"{labels[field]}: {fact.value}")
                elif kind == "travel":
                    rows.append(f"{labels[field]}: " + ("尚未提供" if zh else "Not provided"))
    return rows


def apply_record_commands(
    ledger: ApplicationRecordLedger, *, case_id: str, event_id: str, body: str,
    commands: list[RecordCommand],
    declarations: list[CollectionDeclaration] | None = None,
) -> ApplicationRecordLedger:
    """Return an atomic new ledger; reject stale/foreign/reinterpreted batches.

    Calling code supplies the consented, unquoted latest applicant body. These
    integrity checks intentionally do not decide ownership, current intent,
    truth, history completeness, UK residence or permission to process data.
    """
    if case_id != ledger.case_id or not event_id.strip() or not body.strip():
        raise ValueError("A record transaction must bind its case and original event")
    if len(commands) > 20:
        raise ValueError("Review record batches larger than 20 commands")
    declarations = declarations or []
    if len({declaration.kind for declaration in declarations}) != len(declarations):
        raise ValueError("Resolve conflicting declarations for the same collection before applying")
    batch: dict[str, object] = {"case_id": case_id, "event_id": event_id, "body": body,
                               "commands": [command.model_dump(mode="json") for command in commands]}
    if declarations:
        batch["declarations"] = [declaration.model_dump(mode="json") for declaration in declarations]
    batch_digest = _digest(batch)
    if previous := ledger.processed_batches.get(event_id):
        if previous != batch_digest:
            raise ValueError("An already processed event cannot be reinterpreted")
        return ledger.model_copy(deep=True)
    result = ledger.model_copy(deep=True)
    body_digest = hashlib.sha256(body.encode()).hexdigest()
    touched: set[str] = set()
    for index, command in enumerate(commands):
        fields = command.record.fields.supplied()
        if any(value.source_excerpt not in body for value in fields.values()):
            raise ValueError("Record source excerpt is absent from the current event")
        if command.change_excerpt is not None and command.change_excerpt not in body:
            raise ValueError("Correction source is absent from the current event")
        prior = None
        if command.action == "add":
            record_id = "record-" + _digest([case_id, event_id, index, command.record.kind])[:24]
        else:
            record_id = str(command.target_id)
            prior = result.current().get(record_id)
            if (prior is None or prior.kind != command.record.kind
                    or prior.digest() != command.expected_revision_digest):
                raise ValueError("Correction target is unknown, withdrawn, foreign or stale")
        if record_id in touched:
            raise ValueError("Resolve multiple commands for the same record before applying")
        touched.add(record_id)
        next_fields = dict(prior.fields) if prior else {}
        next_fields.update({name: RecordedText(**value.model_dump(), source_event_id=event_id,
                                             source_body_sha256=body_digest)
                            for name, value in fields.items()
                            if name not in next_fields or next_fields[name].value != value.value})
        if prior is not None and command.action == "amend" and next_fields == prior.fields:
            # Repeating an unchanged value is not a fresh source or a material
            # correction. The event still receives replay bookkeeping below.
            continue
        result.revisions.append(RecordRevision(
            record_id=record_id, kind=command.record.kind, revision=prior.revision + 1 if prior else 1,
            active=command.action != "withdraw", fields=next_fields, changed_by_event_id=event_id,
            predecessor_digest=prior.digest() if prior else None, change_excerpt=command.change_excerpt,
        ))
    for declaration in declarations:
        if declaration.source_excerpt not in body:
            raise ValueError("Declaration source is absent from the current event")
        if declaration.expected_records_digest != result.records_digest(declaration.kind):
            raise ValueError("Declaration targets a stale or foreign record snapshot")
        has_records = any(record.kind == declaration.kind for record in result.current().values())
        if declaration.state == "none_declared" and has_records:
            raise ValueError("Withdraw conflicting records explicitly before declaring none")
        if declaration.state == "complete_declared" and not has_records:
            raise ValueError("An empty list needs an explicit none declaration, not completion")
        previous_declaration = result.latest_declarations().get(declaration.kind)
        result.declarations.append(DeclarationRevision(
            **declaration.model_dump(), revision=previous_declaration.revision + 1 if previous_declaration else 1,
            source_event_id=event_id, source_body_sha256=body_digest,
            predecessor_digest=previous_declaration.digest() if previous_declaration else None,
        ))
    result.processed_batches[event_id] = batch_digest
    return result
