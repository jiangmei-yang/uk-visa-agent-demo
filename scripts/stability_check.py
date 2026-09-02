from __future__ import annotations

import hashlib
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from visa_agent import web
from visa_agent.config import Settings
from visa_agent.demo import run_demo


def main() -> None:
    run_count = int(os.getenv("STABILITY_RUNS", "20"))
    if run_count < 2:
        raise ValueError("STABILITY_RUNS must be at least 2")

    hashes: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="uk-visa-agent-stability-") as raw_dir:
        root = Path(raw_dir)
        latest_settings: Settings | None = None
        for index in range(run_count):
            latest_settings = Settings(
                database_path=root / f"run-{index}.db",
                output_dir=root / f"run-{index}",
                policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
            )
            result = run_demo(latest_settings, reset=True)
            hashes.add(hashlib.sha256(result.package_path.read_bytes()).hexdigest())
            if result.counts != {
                "cases": 1,
                "processed_events": 3,
                "outbox": 3,
                "deliveries": 1,
            }:
                raise RuntimeError(f"Persistent counts drifted on run {index + 1}: {result.counts}")

        if len(hashes) != 1:
            raise RuntimeError(f"Clean runs produced {len(hashes)} distinct package hashes")
        if latest_settings is None:
            raise RuntimeError("No stability run completed")

        web.settings = latest_settings
        with ThreadPoolExecutor(max_workers=12) as executor:
            responses = list(executor.map(lambda _: web.index(), range(100)))
        if any(response.status_code != 200 for response in responses):
            raise RuntimeError("Concurrent review-console reads were not all successful")

    digest = next(iter(hashes))
    print(f"Stability check passed: {run_count} clean runs, one ZIP SHA-256 {digest}")
    print("Concurrent review-console reads passed: 100/100")


if __name__ == "__main__":
    main()
