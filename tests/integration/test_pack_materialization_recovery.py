from __future__ import annotations

import hashlib
import json
import zipfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.delivery import pack
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import DocumentStatus
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")


def _ready_unmaterialized(directory: Path):
    output = directory / "output"
    documents = output / "synthetic_documents"
    generate_sample_documents(documents)
    policy = load_policy(POLICY_PATH)
    store = SQLiteStore(directory / "case.db")
    workflow = WorkflowService(
        store, policy, OfflineFixtureLLM(), today_provider=lambda: DEMO_EVALUATION_DATE
    )
    for message in sorted(Path("samples/emails").glob("*.eml")):
        case, _, _ = workflow.process(parse_eml(message, documents))
    assert case.delivery_path is None
    assert case.final_summary_confirmed
    assert evaluate_gate(case, policy, DEMO_EVALUATION_DATE).allowed
    return store, case, policy, output


@pytest.mark.parametrize(
    "change",
    ["missing_file", "modified_bytes", "wrong_hash", "missing_registry", "wrong_path",
     "wrong_revision", "outside_root", "lost_case_path"],
)
def test_registered_pack_mismatch_is_withheld_without_rebuilding_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    try:
        archive, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is not None and reasons == []
        original = archive.read_bytes()
        if change == "missing_file":
            archive.unlink()
        elif change == "modified_bytes":
            archive.write_bytes(b"damaged isolated archive")
        elif change == "lost_case_path":
            case.delivery_path = None
            store.save_case(case)
        elif change == "outside_root":
            outside = tmp_path / "outside-output.zip"
            outside.write_bytes(original)
            case.delivery_path = str(outside)
            store.save_case(case)
            with store.connection:
                store.connection.execute("UPDATE deliveries SET path=? WHERE case_id=?",
                                         (str(outside), case.id))
        else:
            with store.connection:
                if change == "missing_registry":
                    store.connection.execute("DELETE FROM deliveries WHERE case_id=?", (case.id,))
                elif change == "wrong_hash":
                    store.connection.execute("UPDATE deliveries SET sha256=? WHERE case_id=?",
                                             ("0" * 64, case.id))
                elif change == "wrong_path":
                    store.connection.execute("UPDATE deliveries SET path=? WHERE case_id=?",
                                             (str(output / "different.zip"), case.id))
                else:
                    store.connection.execute("UPDATE deliveries SET case_revision=99 WHERE case_id=?",
                                             (case.id,))
        before_case = store.get_case(case.id).model_dump_json()
        before_rows = [dict(row) for row in store.connection.execute("SELECT * FROM deliveries")]
        before_bytes = archive.read_bytes() if archive.exists() else None

        def must_not_render(*args, **kwargs):
            pytest.fail("A registered archive must not be silently rebuilt")

        monkeypatch.setattr(pack, "_pdf", must_not_render)
        result, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert result is None and reasons
        assert store.get_case(case.id).model_dump_json() == before_case
        assert [dict(row) for row in store.connection.execute("SELECT * FROM deliveries")] == before_rows
        assert (archive.read_bytes() if archive.exists() else None) == before_bytes
    finally:
        store.close()


def test_failed_materialization_retry_excludes_stale_and_changed_supporting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    try:
        original = next(doc for doc in case.documents if doc.status == DocumentStatus.SUPERSEDED)
        current = next(doc for doc in case.documents if doc.filename == "conference_invitation_corrected.pdf")
        original_bytes = Path(original.path).read_bytes()
        source_bytes = Path(current.path).read_bytes()

        def fail_with_legacy_partial_files(source: Path, target: Path) -> None:
            support = source / "supporting_documents"
            (support / original.filename).write_bytes(original_bytes)
            (support / current.filename).write_bytes(original_bytes)
            (source / "obsolete-generated-note.txt").write_text("unregistered failed attempt")
            target.write_bytes(b"partial unregistered zip")
            raise OSError("synthetic archive write failure")

        with monkeypatch.context() as injected:
            injected.setattr(pack, "_write_zip", fail_with_legacy_partial_files)
            with pytest.raises(OSError, match="synthetic archive write failure"):
                pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert case.delivery_path is None
        assert store.get_case(case.id).delivery_path is None
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
        assert Path(current.path).read_bytes() == source_bytes
        store.close()
        store = SQLiteStore(tmp_path / "case.db")
        case = store.get_case(case.id)
        assert case is not None
        archive, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is not None and reasons == []
        accepted = {doc.filename: Path(doc.path).read_bytes() for doc in case.documents
                    if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW}
        with zipfile.ZipFile(archive) as zipped:
            actual_support = {name.removeprefix("supporting_documents/") for name in zipped.namelist()
                              if name.startswith("supporting_documents/")}
            assert actual_support == set(accepted)
            assert "obsolete-generated-note.txt" not in zipped.namelist()
            for filename, content in accepted.items():
                assert zipped.read("supporting_documents/" + filename) == content
        assert {path.name for path in (output / case.id / "pack" / "supporting_documents").iterdir()} == set(accepted)
    finally:
        store.close()


@pytest.mark.parametrize(
    "failure_point",
    ["pdf", "copy", "zip", "registration", "case_save", "publish_case", "publish_zip", "outer_commit"],
)
def test_failure_does_not_commit_and_fresh_retry_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    baseline_store, baseline_case, policy, baseline_output = _ready_unmaterialized(tmp_path / "baseline")
    try:
        baseline_zip, _ = pack.generate_pack(
            baseline_case, policy, baseline_store, baseline_output, DEMO_EVALUATION_DATE
        )
        assert baseline_zip is not None
        expected = baseline_zip.read_bytes()
    finally:
        baseline_store.close()
    store, case, policy, output = _ready_unmaterialized(tmp_path / "retry")
    before = case.model_copy(deep=True)
    try:
        def fail(*args, **kwargs):
            raise OSError("synthetic materialization failure")

        with monkeypatch.context() as injected:
            if failure_point == "pdf":
                injected.setattr(pack, "_pdf", fail)
            elif failure_point == "copy":
                injected.setattr(pack.shutil, "copy2", fail)
            elif failure_point == "zip":
                injected.setattr(pack, "_write_zip", fail)
            elif failure_point == "registration":
                injected.setattr(store, "save_delivery", fail)
            elif failure_point == "case_save":
                injected.setattr(store, "save_case", fail)
            elif failure_point in {"publish_case", "publish_zip"}:
                original_replace = pack.os.replace
                failing_target = (output / case.id if failure_point == "publish_case" else
                                  output / f"visa_application_pack_{case.id}.zip")

                def fail_publication(source, destination):
                    if Path(destination) == failing_target:
                        raise OSError("synthetic materialization failure")
                    return original_replace(source, destination)

                injected.setattr(pack.os, "replace", fail_publication)
            else:
                original_atomic = store.atomic_write

                @contextmanager
                def fail_before_outer_commit():
                    outer = store._atomic_write_depth == 0
                    with original_atomic():
                        yield
                        if outer:
                            raise OSError("synthetic materialization failure")

                injected.setattr(store, "atomic_write", fail_before_outer_commit)
            with pytest.raises(OSError, match="synthetic materialization failure"):
                pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert case == before
        assert store.get_case(case.id) == before
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
        assert not store.connection.in_transaction
        store.close()
        store = SQLiteStore(tmp_path / "retry" / "case.db")
        case = store.get_case(case.id)
        assert case is not None
        archive, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is not None and reasons == []
        assert archive.read_bytes() == expected
        record = store.connection.execute("SELECT sha256 FROM deliveries WHERE case_id=?", (case.id,)).fetchone()
        assert record["sha256"] == hashlib.sha256(expected).hexdigest()
        snapshot = json.loads((output / case.id / "audit" / "case_snapshot.json").read_text())
        assert snapshot["delivery_path"] == str(archive)
    finally:
        store.close()


def test_legacy_unregistered_partials_are_quarantined_not_merged_or_deleted(tmp_path: Path) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    case_dir = output / case.id
    support = case_dir / "pack" / "supporting_documents"
    support.mkdir(parents=True)
    audit = case_dir / "audit"
    audit.mkdir()
    stale_bytes = b"Unregistered synthetic supporting file from an interrupted attempt"
    (support / "obsolete-support.pdf").write_bytes(stale_bytes)
    (case_dir / "pack" / "obsolete-note.txt").write_text("unregistered old note")
    (audit / "old-attempt.json").write_text('{"synthetic": true}')
    legacy_zip = output / f"visa_application_pack_{case.id}.zip"
    legacy_zip.write_bytes(b"unregistered incomplete archive")
    unrelated = output / "unrelated-owner-file.txt"
    unrelated.write_text("Keep this separate fixture")
    try:
        archive, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is not None and reasons == []
        with zipfile.ZipFile(archive) as zipped:
            assert "supporting_documents/obsolete-support.pdf" not in zipped.namelist()
            assert "obsolete-note.txt" not in zipped.namelist()
        quarantines = list(output.glob(".unregistered-pack-*"))
        assert len(quarantines) == 1
        retained = quarantines[0]
        assert (retained / "case-tree" / "pack" / "supporting_documents" / "obsolete-support.pdf").read_bytes() == stale_bytes
        assert (retained / "case-tree" / "audit" / "old-attempt.json").is_file()
        assert (retained / "archive.zip").read_bytes() == b"unregistered incomplete archive"
        assert not (support / "obsolete-support.pdf").exists()
        assert not (audit / "old-attempt.json").exists()
        assert unrelated.read_text() == "Keep this separate fixture"
        assert not list(output.glob(".pack-stage-*"))
    finally:
        store.close()


@pytest.mark.parametrize("escape", ["case_id", "case_tree", "audit", "support", "zip"])
def test_materialization_rejects_paths_outside_output_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape: str
) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "do-not-touch.txt"
    marker.write_text("Unrelated synthetic owner file")
    case_dir = output / case.id
    if escape == "case_id":
        case.id = "../outside"
    elif escape == "case_tree":
        case_dir.symlink_to(outside, target_is_directory=True)
    elif escape == "audit":
        case_dir.mkdir()
        (case_dir / "audit").symlink_to(outside, target_is_directory=True)
    elif escape == "support":
        (case_dir / "pack").mkdir(parents=True)
        (case_dir / "pack" / "supporting_documents").symlink_to(outside, target_is_directory=True)
    else:
        (output / f"visa_application_pack_{case.id}.zip").symlink_to(marker)

    def must_not_render(*args, **kwargs):
        pytest.fail("Path escape must be rejected before rendering")

    monkeypatch.setattr(pack, "_pdf", must_not_render)
    try:
        archive, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is None and reasons
        assert list(outside.iterdir()) == [marker]
        assert marker.read_text() == "Unrelated synthetic owner file"
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
        assert not list(output.glob(".pack-stage-*"))
        assert not list(output.glob(".unregistered-pack-*"))
    finally:
        store.close()


@pytest.mark.parametrize("collision", ["target_zip", "target_case", "case_subtree"])
def test_materialization_preserves_other_registered_archive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collision: str,
) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    if collision == "target_zip":
        archive = output / f"visa_application_pack_{case.id}.zip"
    elif collision == "target_case":
        archive = output / case.id
    else:
        archive = output / case.id / "audit" / "historical-archive.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    historical_bytes = b"Registered synthetic historical archive, never relocate or replace"
    archive.write_bytes(historical_bytes)
    with store.connection:
        store.connection.execute(
            "INSERT INTO delivery_versions(case_id, path, sha256, case_revision) VALUES (?, ?, ?, ?)",
            ("separate-historical-case", str(archive), hashlib.sha256(historical_bytes).hexdigest(), 1),
        )

    def must_not_render(*args, **kwargs):
        pytest.fail("A registered archive path must not be replaced or relocated")

    monkeypatch.setattr(pack, "_pdf", must_not_render)
    try:
        result, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert result is None and reasons
        assert archive.read_bytes() == historical_bytes
        assert store.get_case(case.id).delivery_path is None
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
        assert not list(output.glob(".pack-stage-*"))
        assert not list(output.glob(".unregistered-pack-*"))
    finally:
        store.close()


@pytest.mark.parametrize("change", ["missing_source", "changed_source", "changed_staged_copy"])
def test_accepted_supporting_file_integrity_failure_cannot_publish_or_rewrite_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    document = next(doc for doc in case.documents if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW)
    source = Path(document.path)
    accepted_bytes = source.read_bytes()
    accepted_case = case.model_copy(deep=True)
    try:
        if change == "missing_source":
            source.unlink()
        elif change == "changed_source":
            source.write_bytes(b"Different synthetic bytes after acceptance")
        else:
            original_copy = pack.shutil.copy2

            def damaged_copy(source_path, destination):
                result = original_copy(source_path, destination)
                if Path(source_path) == source:
                    Path(destination).write_bytes(b"Different synthetic bytes in the staged copy")
                return result

            monkeypatch.setattr(pack.shutil, "copy2", damaged_copy)
        with pytest.raises((OSError, ValueError)):
            pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert case == accepted_case
        assert store.get_case(case.id) == accepted_case
        assert document.sha256 == hashlib.sha256(accepted_bytes).hexdigest()
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
        assert not store.connection.execute("SELECT 1 FROM delivery_versions").fetchone()
        assert not (output / case.id).exists()
        assert not (output / f"visa_application_pack_{case.id}.zip").exists()
        assert not list(output.glob(".pack-stage-*"))
        assert not list(output.glob(".unregistered-pack-*"))
        if change == "changed_staged_copy":
            assert source.read_bytes() == accepted_bytes
    finally:
        store.close()


def test_every_packed_supporting_file_matches_accepted_and_indexed_sha256(tmp_path: Path) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    try:
        archive, reasons = pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is not None and reasons == []
        accepted = {doc.filename: doc for doc in case.documents
                    if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW}
        with zipfile.ZipFile(archive) as zipped:
            actual_support = {name.removeprefix("supporting_documents/") for name in zipped.namelist()
                              if name.startswith("supporting_documents/")}
            assert actual_support == set(accepted)
            index = PdfReader(BytesIO(zipped.read("03_document_index.pdf")))
            index_text = "".join("".join(page.extract_text().split()) for page in index.pages)
            for filename, document in accepted.items():
                digest = hashlib.sha256(zipped.read("supporting_documents/" + filename)).hexdigest()
                assert digest == document.sha256
                assert "".join(filename.split()) in index_text
                assert digest in index_text
    finally:
        store.close()


@pytest.mark.parametrize(
    "filename_kind", ["empty", "whitespace", "dot", "dotdot", "traversal", "nested", "backslash", "absolute"],
)
def test_all_accepted_filenames_are_validated_before_any_support_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename_kind: str,
) -> None:
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    outside = tmp_path / "outside-staging.txt"
    outside.write_bytes(b"Keep this unrelated temporary fixture unchanged")
    names = {"empty": "", "whitespace": "   ", "dot": ".", "dotdot": "..",
             "traversal": "../00_READ_ME_FIRST.pdf", "nested": "subdir/document.pdf",
             "backslash": "..\\outside-staging.txt", "absolute": str(outside)}
    accepted = [doc for doc in case.documents if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW]
    # Put the invalid name last: no earlier support file may be copied before validation.
    accepted[-1].filename = names[filename_kind]
    store.save_case(case)
    before = case.model_copy(deep=True)
    sources = {doc.path: Path(doc.path).read_bytes() for doc in accepted}
    copy_attempts = []

    def must_not_copy(*args, **kwargs):
        copy_attempts.append(args)
        pytest.fail("Validate every accepted filename before any supporting-file copy")

    monkeypatch.setattr(pack.shutil, "copy2", must_not_copy)
    try:
        assert evaluate_gate(case, policy, DEMO_EVALUATION_DATE).allowed
        with pytest.raises(ValueError, match="filename"):
            pack.generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert copy_attempts == []
        assert case == before and store.get_case(case.id) == before
        assert outside.read_bytes() == b"Keep this unrelated temporary fixture unchanged"
        assert all(Path(path).read_bytes() == content for path, content in sources.items())
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
        assert not store.connection.execute("SELECT 1 FROM delivery_versions").fetchone()
        assert not (output / case.id).exists()
        assert not (output / f"visa_application_pack_{case.id}.zip").exists()
        assert not list(output.glob(".pack-stage-*"))
        assert not list(output.glob(".unregistered-pack-*"))
    finally:
        store.close()
