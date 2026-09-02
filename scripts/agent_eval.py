from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.evaluation import evaluate_extractor, load_corpus
from visa_agent.llm.openai_client import OpenAIStructuredLLM


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic Agent extraction evaluation.")
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="openai")
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--corpus", type=Path, default=Path("evals/agent_cases.yaml"))
    parser.add_argument("--output", type=Path, default=Path("eval_output/agent_eval.json"))
    args = parser.parse_args()

    api_key_name = "OPENAI_API_KEY" if args.provider == "openai" else "DEEPSEEK_API_KEY"
    api_key = os.environ.get(api_key_name, "")
    if not api_key:
        raise SystemExit(
            f"{api_key_name} is not set. No provider calls were made; local fault-injection tests "
            "remain available through `uv run pytest tests/adversarial`."
        )
    llm = (
        OpenAIStructuredLLM(model=args.model)
        if args.provider == "openai"
        else DeepSeekStructuredLLM(model=args.model, api_key=api_key)
    )
    report = evaluate_extractor(
        llm,
        load_corpus(args.corpus),
        repeats=args.repeats,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Full report: {args.output}")


if __name__ == "__main__":
    main()
