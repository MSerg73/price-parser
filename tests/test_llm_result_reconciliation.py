from price_parser.llm.result_reconciliation import (
    reconcile_result,
    reconcile_results,
)


def row(**overrides):
    value = {
        "case_id": "CTRL-X",
        "decision": "PROPOSE_CHANGE",
        "current_profile": "ПРУТОК",
        "current_grade": "А75",
        "current_dimensions": ["11", None, None],
        "profile": "ПРУТОК",
        "grade": "A75",
        "dim1": "11.0",
        "dim2": None,
        "dim3": None,
        "warnings": ["current_grade conflicts with source text"],
        "research_required": False,
    }
    value.update(overrides)
    return value


def test_false_grade_conflict_is_removed_and_keep_is_computed() -> None:
    reconciled, audit = reconcile_result(row())

    assert reconciled["decision"] == "KEEP"
    assert reconciled["warnings"] == []
    assert reconciled["model_decision"] == "PROPOSE_CHANGE"
    assert audit["changed_fields"] == []
    assert len(audit["removed_false_warnings"]) == 1


def test_real_change_is_propose_change() -> None:
    reconciled, audit = reconcile_result(
        row(
            grade="Х12М",
            warnings=[],
            decision="KEEP",
        )
    )

    assert reconciled["decision"] == "PROPOSE_CHANGE"
    assert audit["changed_fields"] == ["grade"]


def test_research_required_forces_review() -> None:
    reconciled, _audit = reconcile_result(
        row(
            warnings=[],
            research_required=True,
        )
    )

    assert reconciled["decision"] == "REVIEW"


def test_existing_model_review_is_not_silently_downgraded() -> None:
    reconciled, _audit = reconcile_result(
        row(
            decision="REVIEW",
            warnings=[],
        )
    )

    assert reconciled["decision"] == "REVIEW"


def test_unrelated_warning_is_preserved_and_forces_review() -> None:
    warning = "dimension requires human check"
    reconciled, _audit = reconcile_result(
        row(
            warnings=[warning],
        )
    )

    assert reconciled["warnings"] == [warning]
    assert reconciled["decision"] == "REVIEW"


def test_summary_reports_full_decision_consistency() -> None:
    reconciled, summary = reconcile_results(
        [
            row(),
            row(
                case_id="CTRL-Y",
                warnings=[],
                grade="Х12М",
            ),
        ]
    )

    assert len(reconciled) == 2
    assert summary["decision_consistency_rate"] == 1.0
    assert summary["automatic_application_performed"] is False


def test_av_t1_control_is_canonicalized_without_review() -> None:
    value = row(
        current_profile="ТРУБА",
        current_grade="АВ",
        current_dimensions=["70", "17.5", None],
        profile="ТРУБА",
        grade="АВ.Т1",
        dim1="70",
        dim2="17.5",
        dim3=None,
        warnings=[
            "Марка в строке может быть записана как АВ.Т1; "
            "не разделять без источника."
        ],
        additional_info=["сплав алюм.", "1340"],
        decision="KEEP",
    )

    reconciled, audit = reconcile_result(value)

    assert reconciled["grade"] == "АВ"
    assert reconciled["decision"] == "KEEP"
    assert reconciled["warnings"] == []
    assert "Состояние поставки: Т1" in reconciled["additional_info"]
    assert audit["changed_fields"] == []
    assert audit["normalized_fields"]["grade"] == {
        "before": "АВ.Т1",
        "after": "АВ",
    }
    assert len(audit["removed_false_warnings"]) == 1



def test_reconciliation_is_idempotent() -> None:
    first, first_audit = reconcile_result(row())
    second, second_audit = reconcile_result(first)

    assert second == first
    assert second_audit == first_audit


def test_reconciliation_of_raw_and_previously_reconciled_rows_matches() -> None:
    raw_rows = [
        row(),
        row(case_id="CTRL-Y", warnings=[], grade="Х12М", decision="KEEP"),
    ]
    once, _ = reconcile_results(raw_rows)
    twice, _ = reconcile_results(once)
    replay, _ = reconcile_results(raw_rows)

    assert twice == replay
