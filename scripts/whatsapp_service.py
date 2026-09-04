"""Run the existing signed-webhook queue continuously. Does not start a public gateway."""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.channels.whatsapp_service import CurrentWhatsAppSender, run_cycle
from visa_agent.config import Settings
from visa_agent.documents.natural import NaturalPDFReader
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("Interval must be at least 5 seconds")
    names = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM",
             "TWILIO_STATUS_CALLBACK_PUBLIC_URL")
    config = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in config.items() if not value]
    if missing:
        parser.error("Set " + ", ".join(missing))
    key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=Path(".secrets/deepseek_api_key.txt"))
    if not key:
        parser.error("Missing DeepSeek key")
    settings = Settings.from_env()
    lock_dir = settings.database_path.parent / (settings.database_path.name + ".whatsapp-worker")
    with exclusive_state(lock_dir):
        client = import_module("twilio.rest").Client(config["TWILIO_ACCOUNT_SID"], config["TWILIO_AUTH_TOKEN"])
        model = DeepSeekStructuredLLM(args.model, api_key=key)
        store = SQLiteStore(settings.database_path)
        try:
            workflow = WorkflowService(store, load_policy(settings.policy_path), model,
                                       document_reader=NaturalPDFReader(model))
            sender = CurrentWhatsAppSender(client, config["TWILIO_WHATSAPP_FROM"],
                                           config["TWILIO_STATUS_CALLBACK_PUBLIC_URL"], store)
            while True:
                try:
                    result = run_cycle(store, workflow, sender, datetime.now(UTC))
                except Exception as error:
                    print(json.dumps({"phase": "error", "error_type": type(error).__name__}), flush=True)
                    if args.once:
                        raise
                else:
                    print(json.dumps({"at": datetime.now(UTC).isoformat(), **result}), flush=True)
                if args.once:
                    return
                time.sleep(args.interval)
        finally:
            store.close()


if __name__ == "__main__":
    main()
