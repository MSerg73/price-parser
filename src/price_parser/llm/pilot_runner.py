from __future__ import annotations

from ..normalization import grade_match_key, normalize_grade

import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Protocol

from .pilot_provider import PilotProviderResult


PRICING_SNAPSHOT_DATE = "2026-06-18"
MODEL_PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5-mini": (0.25, 2.00),
}


class PilotProvider(Protocol):
    def parse(self, payload: dict[str, Any]) -> PilotProviderResult:
        ...


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    records: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}: некорректный JSONL, строка {line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{source}: строка {line_number} должна быть JSON-объектом")
            records.append(value)
    return records


def validate_cases(cases: list[dict[str, Any]], max_cases: int) -> dict[str, int]:
    if not cases:
        raise ValueError("Пилотная выборка пуста")
    if len(cases) > max_cases:
        raise ValueError(
            f"В выборке {len(cases)} строк, лимит запуска — {max_cases}. "
            "Увеличьте --max-cases осознанно."
        )

    required = {
        "case_id",
        "case_type",
        "source_id",
        "supplier",
        "description",
        "current_profile",
        "current_grade",
        "current_dimensions",
        "domain_policy",
    }
    seen_case_ids: set[str] = set()
    seen_sources: set[str] = set()
    counters = {"control": 0, "candidate": 0}

    for index, case in enumerate(cases, start=1):
        missing = sorted(required - set(case))
        if missing:
            raise ValueError(f"Строка {index}: отсутствуют поля {', '.join(missing)}")
        case_id = str(case["case_id"])
        source_id = str(case["source_id"])
        case_type = str(case["case_type"])
        if case_type not in counters:
            raise ValueError(f"{case_id}: case_type должен быть control или candidate")
        if case_id in seen_case_ids:
            raise ValueError(f"Дублирующий case_id: {case_id}")
        if source_id in seen_sources:
            raise ValueError(f"Дублирующий source_id: {source_id}")
        dims = case["current_dimensions"]
        if not isinstance(dims, list) or len(dims) != 3:
            raise ValueError(f"{case_id}: current_dimensions должен содержать 3 элемента")
        policy = case["domain_policy"]
        if not isinstance(policy, dict) or not policy.get("domain"):
            raise ValueError(f"{case_id}: не задан domain_policy.domain")
        seen_case_ids.add(case_id)
        seen_sources.add(source_id)
        counters[case_type] += 1

    return counters


def build_prompt_payload(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    # Gold labels and case_type are deliberately excluded from the model input.
    return {
        "rows": [
            {
                "source_id": case["source_id"],
                "supplier": case["supplier"],
                "description": case["description"],
                "current_profile": case["current_profile"],
                "current_grade": case["current_grade"],
                "current_dimensions": case["current_dimensions"],
                "domain_policy": case["domain_policy"],
                "requires_review": bool(case.get("requires_review", False)),
                "review_reasons": list(case.get("review_reasons", [])),
            }
            for case in cases
        ]
    }


def run_pilot(
    *,
    cases: list[dict[str, Any]],
    provider: PilotProvider,
    output_dir: str | Path,
    gold: list[dict[str, Any]] | None = None,
    batch_size: int = 5,
    max_cases: int = 25,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 10:
        raise ValueError("batch_size должен быть от 1 до 10")
    counters = validate_cases(cases, max_cases=max_cases)
    gold = gold or []
    _validate_gold(gold, cases)

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    results_path = target_dir / "pilot_results.jsonl"
    metrics_path = target_dir / "pilot_metrics.json"

    by_source = {str(case["source_id"]): case for case in cases}
    all_results: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency = 0.0
    model_names: list[str] = []
    response_ids: list[str] = []

    for batch_index, start in enumerate(range(0, len(cases), batch_size), start=1):
        batch = cases[start : start + batch_size]
        payload = build_prompt_payload(batch)
        started = time.perf_counter()
        provider_result = provider.parse(payload)
        latency = time.perf_counter() - started
        total_latency += latency
        total_input_tokens += provider_result.input_tokens
        total_output_tokens += provider_result.output_tokens
        model_names.append(provider_result.model)
        if provider_result.response_id:
            response_ids.append(provider_result.response_id)

        expected_sources = {str(case["source_id"]) for case in batch}
        returned_sources = [str(row.get("source_id", "")) for row in provider_result.rows]
        if len(returned_sources) != len(set(returned_sources)):
            raise RuntimeError(f"Пакет {batch_index}: модель вернула дубли source_id")
        if set(returned_sources) != expected_sources:
            missing = sorted(expected_sources - set(returned_sources))
            unexpected = sorted(set(returned_sources) - expected_sources)
            raise RuntimeError(
                f"Пакет {batch_index}: нарушен состав source_id; "
                f"missing={missing}, unexpected={unexpected}"
            )

        for row in provider_result.rows:
            source_id = str(row["source_id"])
            case = by_source[source_id]
            record = {
                "case_id": case["case_id"],
                "case_type": case["case_type"],
                "source_id": source_id,
                "supplier": case["supplier"],
                "description": case["description"],
                "current_profile": case["current_profile"],
                "current_grade": case["current_grade"],
                "current_dimensions": case["current_dimensions"],
                "batch_index": batch_index,
                "model": provider_result.model,
                **row,
            }
            all_results.append(record)

    with results_path.open("w", encoding="utf-8") as stream:
        for record in all_results:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    model = _single_or_mixed(model_names)
    evaluation = evaluate_results(all_results, gold)
    pricing = estimate_cost(model, total_input_tokens, total_output_tokens)
    acceptance = evaluate_pilot_acceptance(
        evaluation=evaluation,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        latency_seconds=total_latency,
        estimated_cost=pricing,
    )
    metrics = {
        "cases_total": len(cases),
        "control_cases": counters["control"],
        "candidate_cases": counters["candidate"],
        "batch_size": batch_size,
        "api_calls": (len(cases) + batch_size - 1) // batch_size,
        "model": model,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "latency_seconds": round(total_latency, 3),
        "estimated_cost": pricing,
        "evaluation": evaluation,
        "acceptance": acceptance,
        "response_ids": response_ids,
        "results_file": str(results_path.resolve()),
    }
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metrics


def evaluate_results(
    results: list[dict[str, Any]],
    gold: list[dict[str, Any]],
) -> dict[str, Any]:
    gold_by_case = {str(row["case_id"]): row for row in gold}
    result_by_case = {str(row["case_id"]): row for row in results}
    field_names = ("profile", "grade", "dim1", "dim2", "dim3")
    field_correct = {field: 0 for field in field_names}
    field_total = {field: 0 for field in field_names}
    full_correct = 0
    evaluated = 0
    unnecessary_review = 0

    for case_id, expected in gold_by_case.items():
        actual = result_by_case.get(case_id)
        if actual is None:
            continue
        evaluated += 1
        case_ok = True
        for field in field_names:
            field_total[field] += 1
            if _field_equal(field, actual.get(field), expected.get(field)):
                field_correct[field] += 1
            else:
                case_ok = False
        if case_ok:
            full_correct += 1
        if actual.get("decision") == "REVIEW":
            unnecessary_review += 1

    candidate_results = [
        row for row in results if row.get("case_type") == "candidate"
    ]
    candidate_review = sum(
        1 for row in candidate_results if row.get("decision") == "REVIEW"
    )
    proposed_new_values = 0
    for row in candidate_results:
        current = {
            "profile": row.get("current_profile"),
            "grade": row.get("current_grade"),
            "dim1": _dimension_at(row.get("current_dimensions"), 0),
            "dim2": _dimension_at(row.get("current_dimensions"), 1),
            "dim3": _dimension_at(row.get("current_dimensions"), 2),
        }
        for field in field_names:
            if current[field] in (None, "") and row.get(field) not in (None, ""):
                proposed_new_values += 1

    field_accuracy = {
        field: round(field_correct[field] / field_total[field], 4)
        if field_total[field]
        else None
        for field in field_names
    }
    return {
        "controls_expected": len(gold),
        "controls_evaluated": evaluated,
        "fully_correct_controls": full_correct,
        "fully_correct_rate": round(full_correct / evaluated, 4) if evaluated else None,
        "field_accuracy": field_accuracy,
        "unnecessary_review_controls": unnecessary_review,
        "candidate_review_count": candidate_review,
        "candidate_review_rate": (
            round(candidate_review / len(candidate_results), 4)
            if candidate_results
            else None
        ),
        "candidate_new_values_requiring_human_check": proposed_new_values,
        "automatic_application_performed": False,
    }



def evaluate_pilot_acceptance(
    *,
    evaluation: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    latency_seconds: float,
    estimated_cost: dict[str, Any],
) -> dict[str, Any]:
    field_accuracy = evaluation.get("field_accuracy") or {}
    field_checks = {
        field: value is not None and float(value) >= 0.95
        for field, value in field_accuracy.items()
    }
    criteria = {
        "all_controls_evaluated": (
            evaluation.get("controls_expected", 0) > 0
            and evaluation.get("controls_evaluated")
            == evaluation.get("controls_expected")
        ),
        "fully_correct_rate_at_least_0_90": (
            evaluation.get("fully_correct_rate") is not None
            and float(evaluation["fully_correct_rate"]) >= 0.90
        ),
        "each_field_accuracy_at_least_0_95": bool(field_checks)
        and all(field_checks.values()),
        "automatic_application_disabled": (
            evaluation.get("automatic_application_performed") is False
        ),
        "tokens_recorded": input_tokens > 0 and output_tokens > 0,
        "latency_recorded": latency_seconds > 0,
        "cost_recorded": estimated_cost.get("usd") is not None,
    }
    return {
        "status": "PASSED" if all(criteria.values()) else "FAILED",
        "criteria": criteria,
        "field_checks": field_checks,
        "live_verification_required": True,
        "note": (
            "Прохождение порогов не заменяет live-подтверждение модели "
            "и проверку неизвестного прайса."
        ),
    }


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    pricing = _find_pricing(model)
    if pricing is None:
        return {
            "usd": None,
            "reason": "Для модели нет зафиксированной ставки",
            "pricing_snapshot_date": PRICING_SNAPSHOT_DATE,
        }
    input_rate, output_rate = pricing
    usd = input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000
    return {
        "usd": round(usd, 6),
        "input_usd_per_million": input_rate,
        "output_usd_per_million": output_rate,
        "pricing_snapshot_date": PRICING_SNAPSHOT_DATE,
        "excludes": ["regional processing uplift", "налоги", "внешние tools"],
    }


def dry_run_summary(
    cases: list[dict[str, Any]],
    *,
    batch_size: int,
    max_cases: int,
) -> dict[str, Any]:
    counters = validate_cases(cases, max_cases=max_cases)
    if batch_size < 1 or batch_size > 10:
        raise ValueError("batch_size должен быть от 1 до 10")
    domains: dict[str, int] = {}
    for case in cases:
        domain = str(case["domain_policy"]["domain"])
        domains[domain] = domains.get(domain, 0) + 1
    return {
        "dry_run": True,
        "cases_total": len(cases),
        "control_cases": counters["control"],
        "candidate_cases": counters["candidate"],
        "batch_size": batch_size,
        "planned_api_calls": (len(cases) + batch_size - 1) // batch_size,
        "domains": domains,
        "gold_not_sent_to_model": True,
        "automatic_application": False,
    }


def _validate_gold(
    gold: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> None:
    case_ids = {str(case["case_id"]) for case in cases}
    control_ids = {
        str(case["case_id"])
        for case in cases
        if case.get("case_type") == "control"
    }
    seen: set[str] = set()
    for row in gold:
        case_id = str(row.get("case_id", ""))
        if case_id in seen:
            raise ValueError(f"Дублирующий case_id в gold: {case_id}")
        if case_id not in case_ids:
            raise ValueError(f"Gold содержит неизвестный case_id: {case_id}")
        if case_id not in control_ids:
            raise ValueError(f"Gold допустим только для control: {case_id}")
        seen.add(case_id)
    missing = sorted(control_ids - seen)
    if missing:
        raise ValueError(f"Для control отсутствует gold: {', '.join(missing)}")


def _field_equal(field: str, actual: Any, expected: Any) -> bool:
    if field.startswith("dim"):
        return _decimal_equal(actual, expected)
    if field == "grade":
        if actual in (None, "") or expected in (None, ""):
            return actual in (None, "") and expected in (None, "")
        actual_grade = normalize_grade(str(actual))[0]
        expected_grade = normalize_grade(str(expected))[0]
        return grade_match_key(actual_grade) == grade_match_key(expected_grade)
    return _text_normalize(actual) == _text_normalize(expected)


def _decimal_equal(left: Any, right: Any) -> bool:
    if left in (None, "") and right in (None, ""):
        return True
    if left in (None, "") or right in (None, ""):
        return False
    try:
        return Decimal(str(left).replace(",", ".")) == Decimal(
            str(right).replace(",", ".")
        )
    except InvalidOperation:
        return str(left).strip() == str(right).strip()


def _text_normalize(value: Any) -> str:
    if value in (None, ""):
        return ""
    return " ".join(str(value).upper().replace("Ё", "Е").split())


def _dimension_at(values: Any, index: int) -> Any:
    if isinstance(values, list) and len(values) > index:
        return values[index]
    return None


def _single_or_mixed(values: list[str]) -> str:
    unique = list(dict.fromkeys(values))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return "mixed:" + ",".join(unique)


def _find_pricing(model: str) -> tuple[float, float] | None:
    if model in MODEL_PRICING_USD_PER_MILLION:
        return MODEL_PRICING_USD_PER_MILLION[model]
    for prefix, pricing in MODEL_PRICING_USD_PER_MILLION.items():
        if model.startswith(prefix + "-"):
            return pricing
    return None
