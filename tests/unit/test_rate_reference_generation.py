from decimal import Decimal

import pytest

from packages.rate_reference.generation import (
    CAMPUS_REMOVED_FALLBACK,
    EXACT_HISTORY,
    MANUAL_REQUIRED,
    RateReferenceGenerationError,
    SourceRateKey,
    SourceRateValue,
    generate_rate_references,
)


def _value(
    *,
    season: int,
    campus: str,
    numerator: str,
    denominator: str,
    subject: str = "数学",
    metric_code: str = "RENEWAL_RATE_REFERENCE",
) -> SourceRateValue:
    return SourceRateValue(
        key=SourceRateKey(
            metric_code=metric_code,
            fiscal_season=season,
            quarter_name={1: "暑假", 2: "秋季", 3: "寒假", 4: "春季"}[season % 10],
            track="初中",
            product="智慧空间",
            grade="七年级",
            subject=subject,
            campus=campus,
        ),
        numerator=Decimal(numerator),
        denominator=Decimal(denominator),
    )


def test_exact_and_campus_removed_fallback_sum_before_division() -> None:
    values: list[SourceRateValue] = []
    for season in range(20261, 20265):
        values.extend(
            [
                _value(season=season, campus="甲", numerator="9", denominator="18"),
                _value(
                    season=season,
                    campus="甲",
                    numerator="1",
                    denominator="2",
                    subject="英语",
                ),
                _value(season=season, campus="乙", numerator="30", denominator="40"),
                _value(season=season, campus="丙", numerator="0", denominator="0"),
            ]
        )

    references = generate_rate_references(values, target_fiscal_year=2027, minimum_sample_size=30)
    first_season = [item for item in references if item.key.source_fiscal_season == 20261]
    by_campus = {item.key.campus: item for item in first_season}

    assert by_campus["甲"].source_type == CAMPUS_REMOVED_FALLBACK
    assert by_campus["甲"].exact_numerator == Decimal(10)
    assert by_campus["甲"].exact_denominator == Decimal(20)
    assert by_campus["甲"].reference_numerator == Decimal(40)
    assert by_campus["甲"].reference_denominator == Decimal(60)
    assert by_campus["甲"].system_value_ppm == 666_700
    assert by_campus["乙"].source_type == EXACT_HISTORY
    assert by_campus["乙"].system_value_ppm == 750_000
    assert by_campus["丙"].source_type == CAMPUS_REMOVED_FALLBACK
    assert by_campus["丙"].fallback_reason == "ZERO_DENOMINATOR"


def test_manual_required_when_exact_and_peer_samples_are_insufficient() -> None:
    values = [
        _value(season=season, campus="甲", numerator="5", denominator="10") for season in range(20261, 20265)
    ]
    references = generate_rate_references(values, target_fiscal_year=2027, minimum_sample_size=30)
    assert all(item.source_type == MANUAL_REQUIRED for item in references)
    assert all(item.system_value_ppm is None for item in references)
    assert all(item.fallback_reason == "BELOW_MINIMUM_SAMPLE" for item in references)


def test_new_student_share_at_input_ceiling_requires_manual_value() -> None:
    values = [
        _value(
            season=season,
            campus="甲",
            numerator="100",
            denominator="100",
            metric_code="NEW_STUDENT_SHARE_REFERENCE",
        )
        for season in range(20261, 20265)
    ]
    references = generate_rate_references(values, target_fiscal_year=2027, minimum_sample_size=30)
    assert all(item.source_type == MANUAL_REQUIRED for item in references)
    assert all(item.exact_rate_ppm == 1_000_000 for item in references)
    assert all(item.system_value_ppm is None for item in references)


def test_previous_fiscal_year_must_cover_all_four_seasons() -> None:
    with pytest.raises(RateReferenceGenerationError) as caught:
        generate_rate_references(
            [_value(season=20261, campus="甲", numerator="10", denominator="30")],
            target_fiscal_year=2027,
            minimum_sample_size=30,
        )
    assert caught.value.code == "SOURCE_SEASONS_INCOMPLETE"
