from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path


def write_outbound_eml(
    output_dir: Path,
    sequence: int,
    applicant_email: str,
    thread_id: str,
    source_event_id: str,
    body: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    message = EmailMessage()
    message["From"] = "UK Visa Preparation Demo <visa-agent@example.test>"
    message["To"] = applicant_email
    message["Subject"] = "Re: Standard Visitor application materials"
    message["Message-ID"] = f"<demo-outbound-{sequence:03d}@example.test>"
    message["In-Reply-To"] = f"<{source_event_id}>"
    message["References"] = f"<{source_event_id}>"
    message["X-Demo-Thread-ID"] = thread_id
    message.set_content(body)
    path = output_dir / f"reply_{sequence:02d}.eml"
    path.write_bytes(message.as_bytes())
    return path
