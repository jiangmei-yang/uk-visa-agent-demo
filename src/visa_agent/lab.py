from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.config import Settings
from visa_agent.delivery.pack import generate_pack
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import Case, GateResult, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

LAB_FIXTURES = tuple(sorted(Path("samples/emails").glob("*.eml")))
_DEMO_FACTS = re.compile(r"\n?<!-- DEMO_FACTS\n.*?\n-->\n?", re.DOTALL)


@dataclass(frozen=True)
class LabPaths:
    database: Path
    output: Path
    documents: Path


@dataclass(frozen=True)
class VerifiedLabPack:
    filename: str
    content: bytes


def lab_paths(settings: Settings) -> LabPaths:
    output = settings.output_dir / "guided_lab"
    return LabPaths(
        database=settings.database_path.with_name("guided_lab.db"),
        output=output,
        documents=output / "synthetic_documents",
    )


def _visible_body(body: str) -> str:
    match = _DEMO_FACTS.search(body)
    if match is None:
        return body.strip()
    facts = []
    for line in match.group(0).splitlines():
        if "=" not in line:
            continue
        field, value = line.split("=", 1)
        facts.append(f"{field.replace('_', ' ').title()}: {value}")
    summary = "\nProfile summary (synthetic):\n" + "\n".join(facts) + "\n"
    return _DEMO_FACTS.sub(summary, body).strip()


def _events(paths: LabPaths) -> list[InboundEvent]:
    generate_sample_documents(paths.documents)
    return [parse_eml(path, paths.documents) for path in LAB_FIXTURES]


def _processed_count(store: SQLiteStore, events: list[InboundEvent]) -> int:
    processed = [store.event_processed(event.id) for event in events]
    count = 0
    for complete in processed:
        if not complete:
            break
        count += 1
    if any(processed[count:]):
        raise RuntimeError("Guided lab events are not a valid prefix")
    return count


def _conversation(
    store: SQLiteStore,
    events: list[InboundEvent],
    processed_count: int,
) -> list[dict[str, Any]]:
    replies = {str(row["event_id"]): str(row["payload"]) for row in store.list_outbox()}
    result: list[dict[str, Any]] = []
    for index, event in enumerate(events[:processed_count], start=1):
        result.append(
            {
                "step": index,
                "subject": event.subject,
                "body": _visible_body(event.body),
                "attachments": [Path(path).name for path in event.attachment_paths],
                "reply": replies.get(event.id, "No reply was recorded."),
            }
        )
    return result


def _snapshot(
    settings: Settings,
    store: SQLiteStore,
    events: list[InboundEvent],
) -> dict[str, Any]:
    processed_count = _processed_count(store, events)
    cases = store.list_cases()
    case: Case | None = cases[0] if cases else None
    if case is None:
        return {
            "synthetic": True,
            "mode": "deterministic_fixture",
            "processed_steps": 0,
            "total_steps": len(events),
            "next_step": 1,
            "conversation": [],
            "open_blockers": [],
            "gate": None,
            "pack_available": False,
            "case_id": None,
        }
    policy = load_policy(settings.policy_path)
    gate = evaluate_gate(case, policy, DEMO_EVALUATION_DATE)
    return {
        "synthetic": True,
        "mode": "deterministic_fixture",
        "processed_steps": processed_count,
        "total_steps": len(events),
        "next_step": processed_count + 1 if processed_count < len(events) else None,
        "conversation": _conversation(store, events, processed_count),
        "open_blockers": [
            {"code": issue.code, "title": issue.title, "detail": issue.detail}
            for issue in case.open_blockers()
        ],
        "gate": gate.model_dump(mode="json"),
        "pack_available": _verified_lab_pack(settings, store, case, gate) is not None,
        "case_id": case.id,
        "stage": case.stage.value,
        "profile": case.profile.model_dump(mode="json"),
    }


def get_lab_state(settings: Settings) -> dict[str, Any]:
    paths = lab_paths(settings)
    store = SQLiteStore(paths.database)
    try:
        return _snapshot(settings, store, _events(paths))
    finally:
        store.close()


def _verified_lab_pack(
    settings: Settings, store: SQLiteStore, case: Case, gate: GateResult
) -> VerifiedLabPack | None:
    # Match the ordinary case-download boundary, without treating this fixture as live evidence.
    if not gate.allowed or not case.delivery_path or store.has_unreviewed_held_updates(case.id):
        return None
    registered = store.connection.execute(
        "SELECT path, sha256, case_revision FROM deliveries WHERE case_id=?", (case.id,)
    ).fetchone()
    if (
        registered is None
        or registered["path"] != case.delivery_path
        or registered["case_revision"] != case.delivery_revision
    ):
        return None
    try:
        path = Path(case.delivery_path).resolve()
        allowed_root = lab_paths(settings).output.resolve()
        if allowed_root not in path.parents or not path.is_file():
            return None
        content = path.read_bytes()
    except (OSError, RuntimeError):
        return None
    if hashlib.sha256(content).hexdigest() != registered["sha256"]:
        return None
    # Return the checked bytes, not a path which an HTTP file response would read later.
    return VerifiedLabPack(filename=path.name, content=content)


def get_lab_pack(settings: Settings) -> VerifiedLabPack | None:
    paths = lab_paths(settings)
    store = SQLiteStore(paths.database)
    try:
        cases = store.list_cases()
        if not cases:
            return None
        case = cases[0]
        gate = evaluate_gate(case, load_policy(settings.policy_path), DEMO_EVALUATION_DATE)
        return _verified_lab_pack(settings, store, case, gate)
    finally:
        store.close()


def process_lab_step(settings: Settings, step: int) -> dict[str, Any]:
    paths = lab_paths(settings)
    events = _events(paths)
    store = SQLiteStore(paths.database)
    try:
        expected = _processed_count(store, events) + 1
        if step != expected or step > len(events):
            raise ValueError(f"Expected guided step {expected}, received {step}")
        policy = load_policy(settings.policy_path)
        service = WorkflowService(
            store,
            policy,
            OfflineFixtureLLM(),
            today_provider=lambda: DEMO_EVALUATION_DATE,
        )
        case, _, _ = service.process(events[step - 1])
        generate_pack(case, policy, store, paths.output, DEMO_EVALUATION_DATE)
        return _snapshot(settings, store, events)
    finally:
        store.close()


def reset_lab(settings: Settings) -> dict[str, Any]:
    paths = lab_paths(settings)
    if paths.database.is_file():
        paths.database.unlink()
    if paths.output.is_dir():
        shutil.rmtree(paths.output)
    return get_lab_state(settings)
