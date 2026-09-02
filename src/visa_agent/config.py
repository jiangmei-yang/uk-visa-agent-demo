from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    database_path: Path = Path("data/visa_agent.db")
    output_dir: Path = Path("demo_output")
    policy_path: Path = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_path=Path(os.getenv("VISA_AGENT_DATABASE", "data/visa_agent.db")),
            output_dir=Path(os.getenv("VISA_AGENT_OUTPUT_DIR", "demo_output")),
            policy_path=Path(
                os.getenv(
                    "VISA_AGENT_POLICY_PATH", "knowledge/uk_standard_visitor_2026-02-25.yaml"
                )
            ),
        )
