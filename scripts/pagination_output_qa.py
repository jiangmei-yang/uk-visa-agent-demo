"""Keep separate fictional pagination evidence without overwriting old PDFs."""

from pathlib import Path

from visa_agent.delivery.pack import _pdf


def main() -> None:
    root = Path("output/pdf/pagination-repair")
    for count in (25, 26):
        target = root / f"rows-{count}.pdf"
        if target.exists():
            raise SystemExit("Refusing to overwrite previous visual evidence")
    root.mkdir(parents=True, exist_ok=True)
    for count in (25, 26):
        target = root / f"rows-{count}.pdf"
        _pdf(target, "Sponsor output states",
             [f"Fictional row {number:03d}" for number in range(count)],
             "FICTIONAL QA - NOT A CUSTOMER PACK", compact=True)
        print(target)


if __name__ == "__main__":
    main()
