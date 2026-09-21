from packages.entry_planning.scope import evaluate_entry_scope


def test_manual_exclusion_precedes_inclusion_and_default_rule() -> None:
    decision = evaluate_entry_scope(
        grade="七年级",
        manual_include_match=True,
        manual_exclude_match=True,
    )

    assert decision.default_match is True
    assert decision.manual_include_match is True
    assert decision.manual_exclude_match is True
    assert decision.effective_entry_attribute == "NON_ENTRY"
    assert decision.effective_source == "MANUAL_EXCLUDE"


def test_manual_inclusion_precedes_non_matching_default_rule() -> None:
    decision = evaluate_entry_scope(
        grade="八年级",
        manual_include_match=True,
        manual_exclude_match=False,
    )

    assert decision.default_match is False
    assert decision.effective_entry_attribute == "ENTRY"
    assert decision.effective_source == "MANUAL_INCLUDE"


def test_default_rule_only_includes_seventh_grade() -> None:
    seventh_grade = evaluate_entry_scope(
        grade="七年级",
        manual_include_match=False,
        manual_exclude_match=False,
    )
    eighth_grade = evaluate_entry_scope(
        grade="八年级",
        manual_include_match=False,
        manual_exclude_match=False,
    )

    assert seventh_grade.effective_source == "DEFAULT_RULE"
    assert seventh_grade.effective_entry_attribute == "ENTRY"
    assert eighth_grade.effective_source == "NOT_ENTRY"
    assert eighth_grade.effective_entry_attribute == "NON_ENTRY"
