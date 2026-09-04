"""Four-call DeepSeek probe over fictional ordinary financial PDFs.

This creates visible fictional evaluation documents, reads each exactly once and
retains every outcome. It does not access Gmail, customer state or real documents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from visa_agent.documents.natural import DocumentProposal, NaturalPDFReader
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.secrets import read_secret

MODEL = "deepseek-v4-flash"
DOCUMENTS = {
    "fictional_employment_letter.pdf": {
        "sha256": "64146476e2b61f5d5305e7436c81b6dfe976a213adacbdf6e6c69edee2e23e80",
        "expected_kind": "employment_letter", "financial_kind": "salary",
        "subject": "Kai Example", "amount": "42000.00", "currency": "GBP",
        "lines": [
            "North Harbour Trading Ltd - Employment Letter",
            "FICTIONAL SPECIMEN FOR SOFTWARE EVALUATION - NO REAL PERSON OR EMPLOYER",
            "Letter date: 2026-08-31",
            "Employee: Kai Example",
            "Kai Example is employed as an analyst. Gross annual salary: GBP 42,000.00.",
        ],
    },
    "fictional_bank_statement_a.pdf": {
        "sha256": "70d27a0e37878bbba55a787ac705066ad27333a07222f29ecacb73302118e1f7",
        "expected_kind": "bank_statement", "financial_kind": "closing_balance",
        "subject": "Kai Example", "amount": "12500.50", "currency": "GBP",
        "lines": [
            "North Harbour Bank - Current Account Statement",
            "FICTIONAL SPECIMEN FOR SOFTWARE EVALUATION - NOT A REAL ACCOUNT",
            "Account holder: Kai Example. Account ending: 1234.",
            "Closing balance GBP 12,500.50 as of 2026-08-31. Account ending: 1234.",
        ],
    },
    "fictional_bank_statement_b.pdf": {
        "sha256": "adae3cdd2a6740f5fac82bac11f0c2e4801e2a25fa24c565356bc35688a77f63",
        "expected_kind": "bank_statement", "financial_kind": "closing_balance",
        "subject": "Kai Example", "amount": "13000.00", "currency": "GBP",
        "lines": [
            "North Harbour Bank - Corrected Current Account Statement",
            "FICTIONAL SPECIMEN FOR SOFTWARE EVALUATION - NOT A REAL ACCOUNT",
            "Account holder: Kai Example. Account ending: 1234.",
            "Closing balance GBP 13,000.00 as of 2026-08-31. Account ending: 1234.",
        ],
    },
    "fictional_sponsor_statement.pdf": {
        "sha256": "e255b307ec708735dfda4ce81f69c3425a61afd871b270d1c4dcc278325dddea",
        "expected_kind": "sponsor_funds", "financial_kind": "closing_balance",
        "subject": "Mina Example", "amount": "88000.00", "currency": "HKD",
        "lines": [
            "Harbour Test Bank - Sponsor Funds Statement",
            "FICTIONAL SPECIMEN FOR SOFTWARE EVALUATION - NOT A REAL ACCOUNT",
            "Account holder: Mina Example. Account ending: 9876.",
            "Closing balance HKD 88,000.00 as of 2026-08-31. Account ending: 9876.",
            "This statement belongs to the fictional sponsor, not the applicant.",
        ],
    },
}

IMPLEMENTATION_FILES = (
    "scripts/financial_document_probe.py",
    "src/visa_agent/documents/natural.py",
    "src/visa_agent/domain/financial_review.py",
    "src/visa_agent/domain/rules.py",
    "src/visa_agent/llm/deepseek_client.py",
    "src/visa_agent/workflow/service.py",
)


def write_pdf(path: Path, lines: list[str]) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pdf.setTitle(path.stem.replace("_", " ").title())
    text = pdf.beginText(54, 780)
    text.setFont("Helvetica", 11)
    text.setLeading(23)
    for line in lines:
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _verify_fictional_pdf(path: Path, expected: dict[str, Any]) -> str:
    """Refuse unknown pre-existing bytes before any provider client is constructed."""
    payload = path.read_bytes()
    digest = _sha256_bytes(payload)
    if digest != expected["sha256"]:
        raise ValueError(f"Refusing non-frozen PDF bytes for {path.name}")
    pages = [page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages]
    text = "\n".join(pages)
    fictional_label = expected["lines"][1]
    if fictional_label not in text or "FICTIONAL SPECIMEN FOR SOFTWARE EVALUATION" not in text:
        raise ValueError(f"Refusing PDF without its frozen fictional label: {path.name}")
    return digest


def _source_manifest(selected: list[str]) -> dict[str, dict[str, Any]]:
    return {
        filename: {
            "sha256": DOCUMENTS[filename]["sha256"],
            "fictional_evaluation_document": True,
            "fictional_label": DOCUMENTS[filename]["lines"][1],
            "fictional_label_sha256": _sha256_bytes(
                DOCUMENTS[filename]["lines"][1].encode()
            ),
        }
        for filename in selected
    }


def _current_sha256(path: Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


class _PromptCapture:
    def __init__(self, content: str, delegate: Any | None = None) -> None:
        self.content = content
        self.delegate = delegate
        self.arguments: dict[str, Any] = {}
        self.response_model: str | None = None

    def create(self, **kwargs: Any) -> Any:
        self.arguments = kwargs
        if self.delegate is None:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
                usage=None,
                model="offline-request-fingerprint",
            )
        response = self.delegate.create(**kwargs)
        response_model = getattr(response, "model", None)
        self.response_model = response_model if isinstance(response_model, str) else None
        return response


def _request_fingerprints() -> dict[str, str]:
    """Hash the effective adapter prompt/config without network or credentials."""
    proposal = DocumentProposal(
        kind="other_supporting_document",
        language="en",
        classification_page=1,
        classification_excerpt="Offline request fingerprint",
        confidence=1,
    )
    capture = _PromptCapture(proposal.model_dump_json())
    adapter = DeepSeekStructuredLLM.__new__(DeepSeekStructuredLLM)
    adapter.client = SimpleNamespace(chat=SimpleNamespace(completions=capture))
    adapter.model = adapter.version = MODEL
    adapter.last_usage = None
    adapter.usage_history = []
    adapter.capture_raw_responses = False
    adapter.last_extraction_content = None
    adapter.extract_document(["Offline request fingerprint"])
    prompt = capture.arguments["messages"][0]["content"]
    request_config = {
        key: capture.arguments[key]
        for key in ("model", "response_format", "temperature", "max_tokens", "extra_body")
    }
    return {
        "document_prompt_sha256": _sha256_bytes(prompt.encode()),
        "request_config_sha256": _canonical_sha256(request_config),
    }


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True,
        text=True, timeout=10,
    )
    return result.stdout.strip()


def _write_checkpoint(path: Path, report: dict[str, Any], *, exclusive: bool = False) -> None:
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        return
    temporary = path.with_name(f".{path.name}.checkpoint")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--only", nargs="*", choices=tuple(DOCUMENTS))
    args = parser.parse_args()
    if not args.allow_model_calls:
        parser.error(
            "--allow-model-calls is required; this probe makes one paid call per selected file"
        )
    if args.report.exists():
        parser.error("Refusing to overwrite an existing evidence report")
    args.directory.mkdir(parents=True, exist_ok=True)
    selected = args.only or list(DOCUMENTS)
    paths: dict[str, Path] = {}
    source_hashes: dict[str, str] = {}
    # Validate the complete selected set before the first external call. A file
    # merely having one of the expected names is never evidence that it is fictional.
    for filename in selected:
        expected = DOCUMENTS[filename]
        path = args.directory / filename
        if not path.exists():
            write_pdf(path, expected["lines"])
        try:
            source_hashes[filename] = _verify_fictional_pdf(path, expected)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        paths[filename] = path
        # Always refresh the preview from the verified bytes; an older same-named
        # PNG is not evidence for the currently selected PDF.
        subprocess.run(["pdftoppm", "-singlefile", "-png", str(path),
                        str(path.with_suffix(""))], check=True, timeout=30,
                       capture_output=True)
    key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=Path(".secrets/deepseek_api_key.txt"))
    if not key:
        parser.error("Missing DeepSeek key")
    root = Path(__file__).resolve().parents[1]
    implementation_hashes = {
        name: _sha256_bytes((root / name).read_bytes()) for name in IMPLEMENTATION_FILES
    }
    request_fingerprints = _request_fingerprints()
    started_at = datetime.now(UTC).isoformat()
    source_manifest = _source_manifest(selected)
    report: dict[str, Any] = {
        "evidence_contract_version": "financial-document-probe-v2",
        "scope": f"{len(selected)} selected fictional financial PDFs; one DeepSeek extraction each",
        "model": MODEL, "maximum_model_calls": len(selected), "mailbox_calls": 0,
        "real_documents": 0, "completed": False, "all_passed": False, "results": [],
        "run_started_at": started_at,
        "git_head": _git_head(root),
        "selected_documents": selected,
        "document_source_manifest": source_manifest,
        "document_source_sha256": source_hashes,
        "document_source_bundle_sha256": _canonical_sha256(source_manifest),
        "implementation_files_sha256": implementation_hashes,
        "implementation_bundle_sha256": _canonical_sha256(implementation_hashes),
        "document_schema_sha256": _canonical_sha256(DocumentProposal.model_json_schema()),
        **request_fingerprints,
    }
    report["run_id"] = _canonical_sha256({
        key: report[key] for key in (
            "run_started_at", "git_head", "document_source_bundle_sha256",
            "implementation_bundle_sha256", "document_schema_sha256",
            "document_prompt_sha256", "request_config_sha256",
        )
    })
    args.report.parent.mkdir(parents=True, exist_ok=True)
    _write_checkpoint(args.report, report, exclusive=True)
    model = DeepSeekStructuredLLM(MODEL, api_key=key, capture_raw_responses=True)
    provider_capture = _PromptCapture("", model.client.chat.completions)
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=provider_capture))
    reader = NaturalPDFReader(model, allow_ocr=False)
    for filename in selected:
        expected = DOCUMENTS[filename]
        path = paths[filename]
        usage_start = len(model.usage_history)
        provider_capture.response_model = None
        model.last_extraction_content = None
        try:
            source_sha256 = _verify_fictional_pdf(path, expected)
            result = reader(path)
            # Refuse to bind a provider result to a path replaced during the call.
            if _verify_fictional_pdf(path, expected) != source_sha256:
                raise ValueError(f"PDF bytes changed while reading {filename}")
        except Exception as error:  # provider failures are retained, never selected away
            report["results"].append({
                "filename": filename,
                "sha256": _current_sha256(path),
                "checks": {"frozen_fictional_source": False},
                "passed": False,
                "failure": {"type": type(error).__name__},
                "raw_model_response": model.last_extraction_content,
                "usage": model.usage_history[usage_start:],
                "provider_response_model": provider_capture.response_model,
                "provider_response_model_sha256": (
                    _sha256_bytes(provider_capture.response_model.encode())
                    if provider_capture.response_model is not None else None
                ),
            })
            _write_checkpoint(args.report, report)
            continue
        observation = (
            result.financial_observations[0]
            if len(result.financial_observations) == 1
            else None
        )
        checks = {
            "frozen_fictional_source": True,
            "expected_kind": result.kind == expected["expected_kind"],
            "accepted_extraction": not result.requires_review,
            "one_financial_observation": observation is not None,
            "expected_financial_kind": bool(observation and observation.kind == expected["financial_kind"]),
            "expected_subject": bool(observation and observation.subject_name == expected["subject"]),
            "expected_amount": bool(observation and observation.amount == expected["amount"]),
            "expected_currency": bool(observation and observation.currency == expected["currency"]),
        }
        report["results"].append({
            "filename": filename, "sha256": source_sha256,
            "checks": checks, "passed": all(checks.values()),
            "read_result": {
                "kind": result.kind, "requires_review": result.requires_review,
                "review_reason": result.review_reason,
                "financial_observations": [item.model_dump(mode="json")
                                           for item in result.financial_observations],
            },
            "raw_model_response": model.last_extraction_content,
            "usage": model.usage_history[usage_start:],
            "provider_response_model": provider_capture.response_model,
            "provider_response_model_sha256": (
                _sha256_bytes(provider_capture.response_model.encode())
                if provider_capture.response_model is not None else None
            ),
        })
        _write_checkpoint(args.report, report)
    report["completed"] = True
    report["all_passed"] = all(item["passed"] for item in report["results"])
    report["provider_response_models"] = sorted({
        item["provider_response_model"]
        for item in report["results"]
        if item["provider_response_model"] is not None
    })
    report["provider_response_models_sha256"] = {
        model_name: _sha256_bytes(model_name.encode())
        for model_name in report["provider_response_models"]
    }
    report["run_completed_at"] = datetime.now(UTC).isoformat()
    _write_checkpoint(args.report, report)


if __name__ == "__main__":
    main()
