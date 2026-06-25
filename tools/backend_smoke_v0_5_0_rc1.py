from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from price_parser.api import create_app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    app = create_app(args.database_url, auto_migrate=False)
    client = TestClient(app)

    health = client.get("/health")
    health.raise_for_status()
    stats = client.get("/stats")
    stats.raise_for_status()
    search = client.get(
        "/search",
        params={"profile": "ПРУТОК", "grade": "70С3", "dimensions": "10"},
    )
    search.raise_for_status()
    reviews = client.get("/reviews")
    reviews.raise_for_status()

    search_payload = search.json()
    result = {
        "version": health.json()["version"],
        "health": health.json()["status"],
        "stats": stats.json(),
        "search_top_id": (
            search_payload["results"][0]["item"]["id"]
            if search_payload["results"]
            else None
        ),
        "search_match_type": (
            search_payload["results"][0]["match_type"]
            if search_payload["results"]
            else None
        ),
        "automatic_application_performed": search_payload.get(
            "automatic_application_performed"
        ),
        "open_review_items": reviews.json()["total"],
        "live_llm_e2e": "NOT_VERIFIED",
    }

    if result["search_top_id"] != "offer-ccb686a876cec115":
        raise RuntimeError("Неожиданный Top-1 для контрольного запроса 70С3 Ø10")
    if result["automatic_application_performed"] is not False:
        raise RuntimeError("Автоматическое применение должно быть отключено")
    if result["stats"]["offers"] != 4371:
        raise RuntimeError("Ожидалось 4371 предложения")
    if result["open_review_items"] != 18:
        raise RuntimeError("Ожидалось 18 REVIEW")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
