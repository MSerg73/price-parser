from __future__ import annotations

import argparse
import json
from pathlib import Path

from price_parser.llm.pilot_runner import evaluate_results


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"Invalid JSONL in {path}, line {line_number}: {exc}"
                ) from exc
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    args = parser.parse_args()
    metrics = evaluate_results(load_jsonl(args.results), load_jsonl(args.gold))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
