"""Offline recovery of retained ordinary attachments; never document approval."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import Case, CaseStatus, DocumentStatus, InboundEvent, IssueStatus
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.document_review import recover_document
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.service import WorkflowService


def setup(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "case.db")
    workflow = WorkflowService(store,
        load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
        OfflineFixtureLLM(), today_provider=lambda: date(2026, 9, 4))
    case = Case(id="case", external_thread_id="thread", applicant_contact="person@example.test",
                primary_channel="gmail", policy_version=workflow.policy.version)
    paths = []
    for filename in ("unclear.pdf", "clear.pdf", "unrelated.pdf"):
        path = tmp_path / filename
        # Byte identity only; no identity document is constructed or model is called.
        path.write_bytes(filename.encode())
        paths.append(path)
    def reader(path):
        if path.name != "clear.pdf":
            raise ValueError("Unreadable ordinary attachment")
        return DocumentReadResult("student_letter", "en", 1, {}, method="test_reader")
    monkeypatch.setattr(workflow, "document_reader", reader)
    event = InboundEvent(id="received", external_thread_id="thread", sender=case.applicant_contact,
                         channel="gmail", subject="Supporting documents", body="My attachments",
                         attachment_paths=[str(path) for path in paths], received_at=datetime.now(UTC))
    workflow._ingest_attachments(case, event)
    store.save_case(case)
    return store, workflow, case, paths


def recover(workflow, case, **overrides):
    arguments = dict(case_id=case.id, document_id=case.documents[0].id,
        expected_fingerprint=review_fingerprint(case), actor="Local reviewer",
        reason="Reviewed this specific replacement for the unreadable student letter.")
    arguments.update(overrides)
    return recover_document(workflow, **arguments)


def test_explicit_readable_replacement_resolves_only_its_old_blocker(tmp_path, monkeypatch):
    store, workflow, case, paths = setup(tmp_path, monkeypatch)
    before_bytes = [path.read_bytes() for path in paths]
    old, replacement, unrelated = case.documents
    assert len(case.open_blockers()) == 2
    action = recover(workflow, case, replacement_document_id=replacement.id)
    updated = store.get_case(case.id)
    assert updated.documents[0].status == DocumentStatus.SUPERSEDED
    assert updated.documents[1].supersedes_document_id == old.id
    assert updated.documents[1].status == DocumentStatus.ACCEPTED_FOR_REVIEW
    assert updated.documents[2].status == DocumentStatus.NEEDS_REPLACEMENT
    assert len(updated.open_blockers()) == 1
    assert updated.open_blockers()[0].related_document_ids == [unrelated.id]
    resolved = next(issue for issue in updated.issues if old.id in issue.related_document_ids)
    assert resolved.status == IssueStatus.RESOLVED
    assert action in resolved.resolution and replacement.id in resolved.resolution
    assert not updated.profile_confirmed and not updated.final_summary_confirmed
    assert updated.confirmation_fingerprint is None and updated.delivery_path is None
    assert not store.list_outbox() and not store.list_inbound_queue()
    audit = store.export_case_data(case.id)["review_actions"]
    assert len(audit) == 1 and audit[0]["action_kind"] == "document_replacement"
    assert Case.model_validate_json(audit[0]["before_json"]) == case
    assert Case.model_validate_json(audit[0]["after_json"]) == updated
    assert [path.read_bytes() for path in paths] == before_bytes
    store.close()


def test_audited_same_bytes_retry_reruns_reader_and_preserves_previous_failure(tmp_path, monkeypatch):
    store, workflow, case, _ = setup(tmp_path, monkeypatch)
    calls = []
    def reader(path):
        calls.append(path)
        return DocumentReadResult("student_letter", "en", 1, {}, method="test_reader")
    monkeypatch.setattr(workflow, "document_reader", reader)
    old = case.documents[0]
    action = recover(workflow, case)
    updated = store.get_case(case.id)
    assert len(calls) == 1 and str(calls[0]) == old.path
    assert len(updated.documents) == 4
    new = updated.documents[-1]
    assert new.id != old.id and new.sha256 == old.sha256 and new.path == old.path
    assert new.source_event_id == old.source_event_id
    assert new.source_event_id != action
    assert new.supersedes_document_id == old.id
    assert new.status == DocumentStatus.ACCEPTED_FOR_REVIEW
    assert updated.documents[0].status == DocumentStatus.SUPERSEDED
    assert len(updated.open_blockers()) == 1
    assert not store.list_outbox()
    store.close()


@pytest.mark.parametrize("failure", ["unreadable", "unknown", "identity_warning"])
def test_failed_reread_preserves_old_and_new_blockers_with_audit(tmp_path, monkeypatch, failure):
    store, workflow, case, paths = setup(tmp_path, monkeypatch)
    case.profile_confirmed = case.final_summary_confirmed = True
    case.confirmation_fingerprint = "obsolete"
    case.confirmation_kind = "final"
    case.confirmation_request_event_id = "old-summary"
    store.save_case(case)
    def reader(path):
        if failure == "unreadable":
            raise ValueError("Unreadable again")
        return DocumentReadResult("passport" if failure == "identity_warning" else "unknown",
            "en", 1, {}, requires_review=True, review_reason="Specimen is not an identity document")
    monkeypatch.setattr(workflow, "document_reader", reader)
    action = recover(workflow, case)
    updated = store.get_case(case.id)
    assert updated.documents[0] == case.documents[0]
    assert len(updated.documents) == 4
    assert updated.documents[-1].id != case.documents[0].id
    assert updated.documents[-1].source_event_id == case.documents[0].source_event_id
    assert len(updated.open_blockers()) == 3
    assert all(issue.status == IssueStatus.OPEN for issue in updated.issues)
    assert not updated.profile_confirmed and not updated.final_summary_confirmed
    assert updated.confirmation_fingerprint is None
    assert store.export_case_data(case.id)["review_actions"][0]["id"] == action
    assert paths[0].read_bytes() == b"unclear.pdf"
    assert not store.list_outbox()
    store.close()


def test_unknown_classification_can_be_retried_but_normal_email_hash_dedupe_does_not_change(tmp_path, monkeypatch):
    store, workflow, case, paths = setup(tmp_path, monkeypatch)
    case.documents = []
    case.issues = []
    calls = []
    def reader(path):
        calls.append(path)
        return DocumentReadResult("unknown" if len(calls) == 1 else "student_letter", "en", 1, {},
                                  requires_review=len(calls) == 1)
    monkeypatch.setattr(workflow, "document_reader", reader)
    event = InboundEvent(id="source", external_thread_id="thread", sender=case.applicant_contact,
        channel="gmail", subject="", body="", attachment_paths=[str(paths[0])], received_at=datetime.now(UTC))
    workflow._ingest_attachments(case, event)
    workflow._ingest_attachments(case, event.model_copy(update={"id": "normal-repeat"}))
    assert len(calls) == 1 and len(case.documents) == 1
    assert case.documents[0].kind == "unknown"
    store.save_case(case)
    recover(workflow, case)
    updated = store.get_case(case.id)
    assert len(calls) == 2 and len(updated.documents) == 2
    assert not updated.open_blockers()
    assert updated.documents[0].status == DocumentStatus.SUPERSEDED
    assert updated.documents[1].source_event_id == "source"
    store.close()


@pytest.mark.parametrize("change", ["old_tampered", "new_tampered", "old_missing", "new_missing",
    "stale_fingerprint", "actor", "reason", "missing_document", "missing_replacement", "same_document",
    "replacement_unknown", "replacement_review", "replacement_superseded", "old_known_identity",
    "old_accepted", "human_review", "finalized", "delivered", "other_channel", "sending", "ambiguous"])
def test_invalid_recovery_is_atomic_and_never_calls_reader(tmp_path, monkeypatch, change):
    store, workflow, case, paths = setup(tmp_path, monkeypatch)
    overrides = {"replacement_document_id": case.documents[1].id}
    if change.endswith("tampered"):
        paths[0 if change.startswith("old") else 1].write_bytes(b"changed")
    elif change.endswith("missing"):
        paths[0 if change.startswith("old") else 1].unlink()
    elif change == "stale_fingerprint":
        overrides["expected_fingerprint"] = "stale"
    elif change in {"actor", "reason"}:
        overrides[change] = ""
    elif change == "missing_document":
        overrides["document_id"] = "not-this-case"
    elif change == "missing_replacement":
        overrides["replacement_document_id"] = "not-this-case"
    elif change == "same_document":
        overrides["replacement_document_id"] = case.documents[0].id
    elif change == "replacement_unknown":
        case.documents[1].kind = "unknown"
    elif change == "replacement_review":
        case.documents[1].status = DocumentStatus.HUMAN_REVIEW_REQUIRED
    elif change == "replacement_superseded":
        case.documents[1].status = DocumentStatus.SUPERSEDED
    elif change == "old_known_identity":
        case.documents[0].kind = "passport"
        case.documents[0].status = DocumentStatus.HUMAN_REVIEW_REQUIRED
    elif change == "old_accepted":
        case.documents[0].status = DocumentStatus.ACCEPTED_FOR_REVIEW
    elif change in {"human_review", "finalized", "delivered"}:
        case.status = {"human_review": CaseStatus.HUMAN_REVIEW_REQUIRED,
                       "finalized": CaseStatus.READY_FOR_HUMAN_REVIEW,
                       "delivered": CaseStatus.DELIVERED_AFTER_CONFIRMATION}[change]
    elif change == "other_channel":
        case.primary_channel = "whatsapp"
    elif change in {"sending", "ambiguous"}:
        event = InboundEvent(id="prior", external_thread_id="thread", sender=case.applicant_contact,
            channel="gmail", subject="", body="", received_at=datetime.now(UTC))
        store.commit_event(case, event, "blocked", "Prior message")
        with store.connection:
            store.connection.execute("UPDATE outbox SET status=?", (change.upper(),))
    store.save_case(case)
    before = store.export_case_data(case.id)
    def forbidden(path):
        pytest.fail("Invalid recovery must not invoke any document reader")
    monkeypatch.setattr(workflow, "document_reader", forbidden)
    with pytest.raises(ValueError):
        recover(workflow, case, **overrides)
    assert store.export_case_data(case.id) == before
    store.close()


def test_success_persists_after_reopen_and_preserves_sent_and_held_records(tmp_path, monkeypatch):
    store, workflow, case, _ = setup(tmp_path, monkeypatch)
    case.preparation_paused = True
    case.preparation_control_epoch = 7
    case.profile_confirmed = case.final_summary_confirmed = True
    for identifier, status in (("sent", "SENT"), ("draft", "PENDING"), ("retry", "RETRY")):
        event = InboundEvent(id=identifier, external_thread_id="thread", sender=case.applicant_contact,
            channel="gmail", subject="", body="", received_at=datetime.now(UTC))
        store.commit_event(case, event, "blocked", identifier)
        with store.connection:
            store.connection.execute("UPDATE outbox SET status=?, attempt_count=2, provider_message_id='provider' WHERE event_id=?",
                                     (status, identifier))
    held = event.model_copy(update={"id": "held"})
    store.record_rejected_event(event_id=held.id, case_id=case.id, thread_id="thread",
        reason_code="HUMAN_REVIEW_CASE_NEW_EVENT", detail="held", held_event=held)
    store.save_case(case)
    sent = next(row for row in store.list_outbox() if row["status"] == "SENT")
    recover(workflow, case, replacement_document_id=case.documents[1].id)
    store.close()
    store = SQLiteStore(tmp_path / "case.db")
    updated = store.get_case(case.id)
    assert updated.preparation_paused and updated.preparation_control_epoch == 7
    assert not updated.profile_confirmed and not updated.final_summary_confirmed
    assert store.has_unreviewed_held_updates(case.id)
    assert len(store.list_held_inbound(case.id)) == 1
    assert next(row for row in store.list_outbox() if row["status"] == "SENT") == sent
    assert all(row["status"] == "FAILED" and row["attempt_count"] == 2 and row["provider_message_id"] == "provider"
               for row in store.list_outbox() if row["event_id"] != "sent")
    assert not store.list_inbound_queue()
    store.close()


def test_audit_insert_failure_rolls_back_recovery(tmp_path, monkeypatch):
    import sqlite3
    store, workflow, case, _ = setup(tmp_path, monkeypatch)
    before = store.export_case_data(case.id)
    store.connection.execute("""CREATE TRIGGER fail_document_audit BEFORE INSERT ON review_actions
        BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        recover(workflow, case, replacement_document_id=case.documents[1].id)
    assert store.export_case_data(case.id) == before
    store.close()


def test_source_changed_during_reread_rolls_back_state(tmp_path, monkeypatch):
    store, workflow, case, paths = setup(tmp_path, monkeypatch)
    before = store.export_case_data(case.id)
    def reader(path):
        path.write_bytes(b"changed during processing")
        return DocumentReadResult("student_letter", "en", 1, {})
    monkeypatch.setattr(workflow, "document_reader", reader)
    with pytest.raises(ValueError, match="integrity"):
        recover(workflow, case)
    assert store.export_case_data(case.id) == before
    store.close()


def test_cli_inspect_replace_and_gated_retry_use_only_existing_locked_state(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from review_document import main
    store, workflow, case, _ = setup(tmp_path, monkeypatch)
    store.close()
    (tmp_path / "case.db").rename(tmp_path / "sandbox.db")
    base = ["--state-dir", str(tmp_path), "--case", case.id]
    calls = []
    def reader_factory(model):
        calls.append(model)
        return lambda path: DocumentReadResult("student_letter", "en", 1, {})
    main(["inspect", *base], reader_factory=reader_factory)
    assert review_fingerprint(case) in capsys.readouterr().out
    assert not calls
    args = [*base, "--document", case.documents[0].id, "--fingerprint", review_fingerprint(case),
            "--actor", "Reviewer", "--reason", "Re-read this specific retained ordinary document."]
    with pytest.raises(SystemExit):
        main(["retry", *args], reader_factory=reader_factory)
    assert not calls
    main(["retry", *args, "--allow-model-processing"], reader_factory=reader_factory)
    assert calls == ["deepseek-v4-flash"]
    assert '"outcome": "recovered"' in capsys.readouterr().out
    store = SQLiteStore(tmp_path / "sandbox.db")
    updated = store.get_case(case.id)
    assert not store.list_outbox()
    store.close()
    args = [*base, "--document", updated.documents[2].id, "--fingerprint", review_fingerprint(updated),
            "--actor", "Reviewer", "--reason", "The applicant specified this new replacement attachment.",
            "--replacement", updated.documents[1].id]
    main(["replace", *args], reader_factory=reader_factory)
    assert calls == ["deepseek-v4-flash"]
    assert '"outcome": "recovered"' in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["reread", "replacement"])
def test_repeated_failed_reads_then_recovery_resolve_only_the_audited_lineage(tmp_path, monkeypatch, mode):
    store, workflow, case, _ = setup(tmp_path, monkeypatch)
    original = case.documents[0]
    for _ in range(2):
        recover(workflow, case)
        case = store.get_case(case.id)
    assert len(case.documents) == 5 and len(case.open_blockers()) == 4
    assert all(doc.retry_of_document_id == original.id for doc in case.documents[3:])
    calls = []
    def reader(path):
        calls.append(path)
        return DocumentReadResult("student_letter", "en", 1, {}, method="test_reader")
    monkeypatch.setattr(workflow, "document_reader", reader)
    recover(workflow, case, document_id=case.documents[-1].id,
            **({"replacement_document_id": case.documents[1].id} if mode == "replacement" else {}))
    updated = store.get_case(case.id)
    assert len(calls) == (mode == "reread")
    assert len(updated.open_blockers()) == 1
    assert updated.open_blockers()[0].related_document_ids == [case.documents[2].id]
    assert all(updated.documents[index].status == DocumentStatus.SUPERSEDED for index in (0, 3, 4))
    successful = updated.documents[-1] if mode == "reread" else updated.documents[1]
    assert successful.status == DocumentStatus.ACCEPTED_FOR_REVIEW
    assert successful.supersedes_document_id == original.id
    assert len(store.export_case_data(case.id)["review_actions"]) == 3
    assert not store.list_outbox()
    store.close()


@pytest.mark.parametrize("change", ["missing_parent", "cycle", "different_hash", "different_source",
                                    "no_audit", "identity_warning"])
def test_invalid_or_identity_review_retry_lineage_cannot_be_hidden(tmp_path, monkeypatch, change):
    store, workflow, case, _ = setup(tmp_path, monkeypatch)
    if change == "identity_warning":
        monkeypatch.setattr(workflow, "document_reader", lambda path:
            DocumentReadResult("passport", "en", 1, {}, requires_review=True,
                               review_reason="Specimen is not an identity document"))
    recover(workflow, case)
    case = store.get_case(case.id)
    target = case.documents[-1]
    if change == "missing_parent":
        target.retry_of_document_id = "not-in-this-case"
    elif change == "cycle":
        case.documents[0].retry_of_document_id = target.id
    elif change == "different_hash":
        target.sha256 = "different"
    elif change == "different_source":
        target.source_event_id = "someone-elses-event"
    elif change == "no_audit":
        with store.connection:
            store.connection.execute("DELETE FROM review_actions")
    store.save_case(case)
    before = store.export_case_data(case.id)
    # Asking to recover the unknown root must not hide a classified identity warning.
    with pytest.raises(ValueError):
        recover(workflow, case, document_id=target.id if change == "missing_parent" else case.documents[0].id,
                replacement_document_id=case.documents[1].id)
    assert store.export_case_data(case.id) == before
    store.close()


def test_old_snapshot_defaults_to_no_retry_lineage():
    from visa_agent.domain.models import Document
    document = Document.model_validate(dict(id="old", filename="ordinary.pdf", kind="unknown", sha256="digest",
        mime_type="application/pdf", status="NEEDS_REPLACEMENT", source_event_id="original", path="/not-opened"))
    assert document.retry_of_document_id is None


@pytest.mark.parametrize(("profile_text", "final_text"), [
    ("Everything is correct, please proceed.", "Everything is correct, please proceed."),
    ("I confirm the profile summary", "I confirm the final summary"),
    ("我确认个人资料摘要", "我确认最终资料摘要"),
])
def test_fixture_read_recovery_requires_fresh_sent_confirmations_before_zip_and_one_captured_send(
    tmp_path, monkeypatch, profile_text, final_text,
):
    """Existing explicitly synthetic fixture flow, not ordinary-material live evidence."""
    from datetime import timedelta
    from zipfile import ZipFile

    from visa_agent.channels.email_fixture import parse_eml
    from visa_agent.channels.outbound import OutboxDispatcher
    from visa_agent.delivery.pack import generate_pack
    from visa_agent.documents.natural import read_fixture_pdf
    from visa_agent.documents.samples import generate_sample_documents

    documents = tmp_path / "documents"
    generate_sample_documents(documents)
    store = SQLiteStore(tmp_path / "case.db")
    workflow = WorkflowService(store,
        load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
        OfflineFixtureLLM(), today_provider=lambda: date(2026, 9, 4))
    def failed_student(path):
        if path.name == "student_letter.pdf":
            raise ValueError("Injected read failure in existing synthetic fixture")
        return read_fixture_pdf(path)
    monkeypatch.setattr(workflow, "document_reader", failed_student)
    class Capture:
        def __init__(self):
            self.requests = []
        def send(self, request):
            self.requests.append(request)
            return "captured-" + request.outbox_id
    capture = Capture()
    for path in sorted(Path("samples/emails").glob("*.eml"))[:2]:
        event = parse_eml(path, documents).model_copy(update={"channel": "gmail"})
        case, _, plan = workflow.process(event)
        assert plan == "blocked"
        OutboxDispatcher(store, capture, allowed_message_types=(plan,)).dispatch_due(datetime.now(UTC))
    old = next(doc for doc in case.documents if doc.filename == "student_letter.pdf")
    assert old.status == DocumentStatus.NEEDS_REPLACEMENT
    assert generate_pack(case, workflow.policy, store, tmp_path / "packs", date(2026, 9, 4))[0] is None
    monkeypatch.setattr(workflow, "document_reader", read_fixture_pdf)
    recover_document(workflow, case_id=case.id, document_id=old.id,
        expected_fingerprint=review_fingerprint(case), actor="Synthetic local reviewer",
        reason="Retry the injected read failure with the same deterministic fixture reader.")
    updated = store.get_case(case.id)
    assert not updated.open_blockers()
    assert not updated.profile_confirmed and not updated.final_summary_confirmed
    assert generate_pack(updated, workflow.policy, store, tmp_path / "packs", date(2026, 9, 4))[0] is None
    # Fresh ordinary replies do not retroactively confirm the now-invalid old summary.
    for index, expected in enumerate(("awaiting_profile_confirmation", "awaiting_confirmation", "ready")):
        event = event.model_copy(update={"id": f"fresh-confirmation-{index}",
            "body": profile_text if index < 2 else final_text, "attachment_paths": [],
            "received_at": event.received_at + timedelta(hours=1)})
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == expected
        assert case.profile_confirmed == (index >= 1)
        if expected != "ready":
            assert generate_pack(case, workflow.policy, store, tmp_path / "packs", date(2026, 9, 4))[0] is None
            result = OutboxDispatcher(store, capture, allowed_message_types=(plan,)).dispatch_due(datetime.now(UTC))
            assert len(result) == 1 and result[0].status == "SENT"
    assert workflow.process(event)[1:] == (True, "duplicate_ignored")
    archive, reasons = generate_pack(case, workflow.policy, store, tmp_path / "packs", date(2026, 9, 4))
    assert archive is not None and not reasons
    with ZipFile(archive) as zipped:
        members = [name for name in zipped.namelist() if name.endswith("student_letter.pdf")]
        assert len(members) == 1
        assert zipped.read(members[0]) == (documents / "student_letter.pdf").read_bytes()
    dispatcher = OutboxDispatcher(store, capture, allowed_message_types=("ready",))
    first = dispatcher.dispatch_due(datetime.now(UTC))
    assert len(first) == 1 and first[0].status == "SENT"
    assert dispatcher.dispatch_due(datetime.now(UTC)) == []
    final = [request for request in capture.requests if request.attachment is not None]
    assert len(final) == 1 and final[0].attachment[1] == archive.read_bytes()
    store.close()
