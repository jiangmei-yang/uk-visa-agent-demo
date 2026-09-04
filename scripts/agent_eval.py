from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.evaluation import (
    evaluate_extractor,
    expand_corpus_with_perturbations,
    load_corpus,
    release_metric_failures,
)
from visa_agent.llm.openai_client import EXTRACTION_INSTRUCTIONS, OpenAIStructuredLLM
from visa_agent.secrets import read_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic Agent extraction evaluation.")
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="openai")
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if any corpus release metric fails.")
    parser.add_argument(
        "--perturbations",
        action="store_true",
        help="Expand every case into deterministic formatting and injection stress variants.",
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--input-price-per-million-usd", type=float)
    parser.add_argument("--output-price-per-million-usd", type=float)
    parser.add_argument("--price-snapshot-date")
    parser.add_argument("--pricing-url")
    parser.add_argument("--corpus", type=Path, default=Path("evals/agent_cases.yaml"))
    parser.add_argument("--output", type=Path, default=Path("eval_output/agent_eval.json"))
    args = parser.parse_args()

    api_key_name = "OPENAI_API_KEY" if args.provider == "openai" else "DEEPSEEK_API_KEY"
    api_key = read_secret(
        api_key_name,
        file_environment_name=f"{api_key_name}_FILE",
        default_file=(
            Path(".secrets/deepseek_api_key.txt") if args.provider == "deepseek" else None
        ),
    )
    if not api_key:
        raise SystemExit(
            f"{api_key_name} is not set. No provider calls were made; local fault-injection tests "
            "remain available through `uv run pytest tests/adversarial`."
        )
    corpus = load_corpus(args.corpus)
    if args.perturbations:
        corpus = expand_corpus_with_perturbations(corpus)
    if args.case_id:
        selected = [case for case in corpus.cases if case.id in set(args.case_id)]
        missing = sorted(set(args.case_id) - {case.id for case in selected})
        if missing:
            raise SystemExit(f"Unknown case IDs: {', '.join(missing)}")
        corpus = corpus.model_copy(update={"cases": selected})
    llm = (
        OpenAIStructuredLLM(model=args.model, timeout_seconds=args.timeout_seconds)
        if args.provider == "openai"
        else DeepSeekStructuredLLM(
            model=args.model,
            api_key=api_key,
            timeout_seconds=args.timeout_seconds,
        )
    )
    report = evaluate_extractor(
        llm,
        corpus,
        repeats=args.repeats,
        on_run=lambda case, repeat, score: print(
            f"[{case.id} {repeat}/{args.repeats}] "
            f"schema={'ok' if score.schema_valid else 'error'} "
            f"tp={score.true_positive} missed={score.missed} "
            f"boundary={'yes' if score.boundary_violation else 'no'} "
            f"latency={score.latency_ms:.0f}ms",
            flush=True,
        ),
    )
    prices = (args.input_price_per_million_usd, args.output_price_per_million_usd)
    if any(price is not None for price in prices):
        if any(price is None for price in prices) or not args.price_snapshot_date or not args.pricing_url:
            raise SystemExit(
                "Cost calculation requires both token prices, --price-snapshot-date, and "
                "--pricing-url."
            )
        metrics = report["metrics"]
        metrics["estimated_cost_usd"] = round(
            metrics["input_tokens"] * prices[0] / 1_000_000
            + metrics["output_tokens"] * prices[1] / 1_000_000,
            6,
        )
        report["notes"].append(
            "Cost estimate uses the explicitly supplied price snapshot "
            f"dated {args.price_snapshot_date}: {args.pricing_url}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report["prompt_sha256"] = hashlib.sha256(EXTRACTION_INSTRUCTIONS.encode()).hexdigest()
    report["release_gate"] = {
        "scope": "this synthetic corpus only",
        "passed": not release_metric_failures(report),
        "failed_metrics": release_metric_failures(report),
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Full report: {args.output}")
    if args.strict and release_metric_failures(report):
        raise SystemExit("Corpus release gate failed; the full report was preserved.")


if __name__ == "__main__":
    main()
