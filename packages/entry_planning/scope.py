from dataclasses import dataclass


@dataclass(frozen=True)
class EntryScopeDecision:
    default_match: bool
    manual_include_match: bool
    manual_exclude_match: bool
    effective_entry_attribute: str
    effective_source: str


def evaluate_entry_scope(
    *, grade: str, manual_include_match: bool, manual_exclude_match: bool
) -> EntryScopeDecision:
    default_match = grade == "七年级"
    if manual_exclude_match:
        effective_attribute = "NON_ENTRY"
        effective_source = "MANUAL_EXCLUDE"
    elif manual_include_match:
        effective_attribute = "ENTRY"
        effective_source = "MANUAL_INCLUDE"
    elif default_match:
        effective_attribute = "ENTRY"
        effective_source = "DEFAULT_RULE"
    else:
        effective_attribute = "NON_ENTRY"
        effective_source = "NOT_ENTRY"
    return EntryScopeDecision(
        default_match=default_match,
        manual_include_match=manual_include_match,
        manual_exclude_match=manual_exclude_match,
        effective_entry_attribute=effective_attribute,
        effective_source=effective_source,
    )
