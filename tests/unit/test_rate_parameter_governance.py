from packages.rate_parameters.governance import (
    RateBoundaryValues,
    RateParameterKey,
    boundary_fingerprint,
    cross_year_match_key,
    rate_parameter_stable_key,
    validate_rate_values,
)


def test_rate_boundaries_require_order_and_hundred_ppm_precision() -> None:
    issues = validate_rate_values(
        rate_type="RENEWAL_RATE",
        usage_mode="RATE_INPUT",
        values=RateBoundaryValues(790_050, 800_000, 780_000),
    )
    assert {item.code for item in issues} == {
        "RATE_PRECISION_INVALID",
        "RATE_BOUNDARY_INVALID",
    }


def test_reverse_new_student_share_cannot_reach_one_hundred_percent() -> None:
    issues = validate_rate_values(
        rate_type="NEW_STUDENT_SHARE",
        usage_mode="REVERSE_INPUT",
        values=RateBoundaryValues(999_900, 0, 1_000_000),
    )
    assert [item.code for item in issues] == ["RATE_BOUNDARY_INVALID"]


def test_cross_year_key_ignores_year_but_stable_key_does_not() -> None:
    first = RateParameterKey("RENEWAL_RATE", 2027, 20271, "初中", "空间", "七年级", "A", "SAME")
    second = RateParameterKey("RENEWAL_RATE", 2028, 20281, "初中", "空间", "七年级", "A", "SAME")
    assert rate_parameter_stable_key(first) != rate_parameter_stable_key(second)
    assert cross_year_match_key(first) == cross_year_match_key(second)


def test_boundary_confirmation_survives_current_value_change_inside_same_bounds() -> None:
    first = RateBoundaryValues(800_000, 700_000, 900_000)
    second = RateBoundaryValues(850_000, 700_000, 900_000)
    assert boundary_fingerprint(stable_key="a" * 64, values=first) == boundary_fingerprint(
        stable_key="a" * 64, values=second
    )
