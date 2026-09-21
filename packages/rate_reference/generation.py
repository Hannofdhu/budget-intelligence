from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from packages.domain.versioning.hashing import content_hash

RENEWAL_RATE = "RENEWAL_RATE"
NEW_STUDENT_SHARE = "NEW_STUDENT_SHARE"
EXACT_HISTORY = "EXACT_HISTORY"
CAMPUS_REMOVED_FALLBACK = "CAMPUS_REMOVED_FALLBACK"
MANUAL_REQUIRED = "MANUAL_REQUIRED"


class RateReferenceGenerationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, order=True)
class SourceRateKey:
    metric_code: str
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    subject: str
    campus: str


@dataclass(frozen=True)
class SourceRateValue:
    key: SourceRateKey
    numerator: Decimal
    denominator: Decimal


@dataclass(frozen=True, order=True)
class ReferenceKey:
    rate_type: str
    source_fiscal_season: int
    target_fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    campus: str


@dataclass(frozen=True)
class GeneratedRateReference:
    key: ReferenceKey
    renewal_relation_code: str | None
    exact_numerator: Decimal
    exact_denominator: Decimal
    reference_numerator: Decimal
    reference_denominator: Decimal
    exact_rate_ppm: int | None
    reference_rate_ppm: int | None
    minimum_sample_size: int
    source_type: str
    fallback_reason: str | None
    system_value_ppm: int | None
    content_hash: str

    def content(self) -> dict[str, object]:
        return {
            "rate_type": self.key.rate_type,
            "source_fiscal_season": self.key.source_fiscal_season,
            "target_fiscal_season": self.key.target_fiscal_season,
            "quarter_name": self.key.quarter_name,
            "track": self.key.track,
            "product": self.key.product,
            "grade": self.key.grade,
            "campus": self.key.campus,
            "renewal_relation_code": self.renewal_relation_code,
            "exact_numerator": self.exact_numerator,
            "exact_denominator": self.exact_denominator,
            "reference_numerator": self.reference_numerator,
            "reference_denominator": self.reference_denominator,
            "exact_rate_ppm": self.exact_rate_ppm,
            "reference_rate_ppm": self.reference_rate_ppm,
            "minimum_sample_size": self.minimum_sample_size,
            "source_type": self.source_type,
            "fallback_reason": self.fallback_reason,
            "system_value_ppm": self.system_value_ppm,
        }


def generate_rate_references(
    values: list[SourceRateValue], *, target_fiscal_year: int, minimum_sample_size: int
) -> list[GeneratedRateReference]:
    if minimum_sample_size <= 0:
        raise RateReferenceGenerationError("MINIMUM_SAMPLE_INVALID", "最小样本门槛必须大于 0")
    source_year = target_fiscal_year - 1
    expected_seasons = {source_year * 10 + quarter for quarter in range(1, 5)}
    actual_seasons = {item.key.fiscal_season for item in values}
    if actual_seasons != expected_seasons:
        raise RateReferenceGenerationError(
            "SOURCE_SEASONS_INCOMPLETE",
            f"来源季节必须完整覆盖上一财年四季；期望={sorted(expected_seasons)}，实际={sorted(actual_seasons)}",
        )

    exact = _aggregate_without_subject(values, target_fiscal_year=target_fiscal_year)
    peers = _aggregate_without_campus(exact)
    result: list[GeneratedRateReference] = []
    for key, exact_pair in sorted(exact.items(), key=lambda item: item[0]):
        peer_key = (
            key.rate_type,
            key.source_fiscal_season,
            key.target_fiscal_season,
            key.quarter_name,
            key.track,
            key.product,
            key.grade,
        )
        peer_pair = peers[peer_key]
        source_type, fallback_reason, reference_pair = _select_reference(
            rate_type=key.rate_type,
            exact_pair=exact_pair,
            peer_pair=peer_pair,
            minimum_sample_size=minimum_sample_size,
        )
        exact_rate_ppm = _rate_ppm(*exact_pair)
        reference_rate_ppm = _rate_ppm(*reference_pair) if source_type != MANUAL_REQUIRED else None
        system_value_ppm = _system_ppm(reference_rate_ppm) if reference_rate_ppm is not None else None
        renewal_relation_code = "SAME_BUDGET_ROW" if key.rate_type == RENEWAL_RATE else None
        content = {
            "rate_type": key.rate_type,
            "source_fiscal_season": key.source_fiscal_season,
            "target_fiscal_season": key.target_fiscal_season,
            "quarter_name": key.quarter_name,
            "track": key.track,
            "product": key.product,
            "grade": key.grade,
            "campus": key.campus,
            "renewal_relation_code": renewal_relation_code,
            "exact_numerator": exact_pair[0],
            "exact_denominator": exact_pair[1],
            "reference_numerator": reference_pair[0],
            "reference_denominator": reference_pair[1],
            "exact_rate_ppm": exact_rate_ppm,
            "reference_rate_ppm": reference_rate_ppm,
            "minimum_sample_size": minimum_sample_size,
            "source_type": source_type,
            "fallback_reason": fallback_reason,
            "system_value_ppm": system_value_ppm,
        }
        result.append(
            GeneratedRateReference(
                key=key,
                renewal_relation_code=renewal_relation_code,
                exact_numerator=exact_pair[0],
                exact_denominator=exact_pair[1],
                reference_numerator=reference_pair[0],
                reference_denominator=reference_pair[1],
                exact_rate_ppm=exact_rate_ppm,
                reference_rate_ppm=reference_rate_ppm,
                minimum_sample_size=minimum_sample_size,
                source_type=source_type,
                fallback_reason=fallback_reason,
                system_value_ppm=system_value_ppm,
                content_hash=content_hash(content),
            )
        )
    return result


def _aggregate_without_subject(
    values: list[SourceRateValue], *, target_fiscal_year: int
) -> dict[ReferenceKey, tuple[Decimal, Decimal]]:
    result: dict[ReferenceKey, tuple[Decimal, Decimal]] = {}
    for item in values:
        rate_type = _rate_type(item.key.metric_code)
        quarter = item.key.fiscal_season % 10
        key = ReferenceKey(
            rate_type=rate_type,
            source_fiscal_season=item.key.fiscal_season,
            target_fiscal_season=target_fiscal_year * 10 + quarter,
            quarter_name=item.key.quarter_name,
            track=item.key.track,
            product=item.key.product,
            grade=item.key.grade,
            campus=item.key.campus,
        )
        current = result.get(key, (Decimal(0), Decimal(0)))
        result[key] = (current[0] + item.numerator, current[1] + item.denominator)
    return result


def _aggregate_without_campus(
    exact: dict[ReferenceKey, tuple[Decimal, Decimal]],
) -> dict[tuple[str, int, int, str, str, str, str], tuple[Decimal, Decimal]]:
    result: dict[tuple[str, int, int, str, str, str, str], tuple[Decimal, Decimal]] = {}
    for key, pair in exact.items():
        peer_key = (
            key.rate_type,
            key.source_fiscal_season,
            key.target_fiscal_season,
            key.quarter_name,
            key.track,
            key.product,
            key.grade,
        )
        current = result.get(peer_key, (Decimal(0), Decimal(0)))
        result[peer_key] = (current[0] + pair[0], current[1] + pair[1])
    return result


def _select_reference(
    *,
    rate_type: str,
    exact_pair: tuple[Decimal, Decimal],
    peer_pair: tuple[Decimal, Decimal],
    minimum_sample_size: int,
) -> tuple[str, str | None, tuple[Decimal, Decimal]]:
    minimum = Decimal(minimum_sample_size)
    if exact_pair[1] >= minimum:
        if _usable_as_input(rate_type, exact_pair):
            return EXACT_HISTORY, None, exact_pair
        return MANUAL_REQUIRED, "RATE_OUTSIDE_INPUT_RANGE", exact_pair
    fallback_reason = "ZERO_DENOMINATOR" if exact_pair[1] == 0 else "BELOW_MINIMUM_SAMPLE"
    if peer_pair[1] >= minimum and _usable_as_input(rate_type, peer_pair):
        return CAMPUS_REMOVED_FALLBACK, fallback_reason, peer_pair
    if peer_pair[1] >= minimum:
        fallback_reason = "RATE_OUTSIDE_INPUT_RANGE"
    return MANUAL_REQUIRED, fallback_reason, peer_pair


def _usable_as_input(rate_type: str, pair: tuple[Decimal, Decimal]) -> bool:
    numerator, denominator = pair
    if denominator <= 0 or numerator > denominator:
        return False
    if rate_type == NEW_STUDENT_SHARE and numerator * 10_000 >= denominator * 9_999:
        return False
    return True


def _rate_ppm(numerator: Decimal, denominator: Decimal) -> int | None:
    if denominator <= 0 or numerator > denominator:
        return None
    return int(((numerator / denominator) * Decimal(1_000_000)).quantize(Decimal(1), ROUND_HALF_UP))


def _system_ppm(reference_rate_ppm: int) -> int:
    return int(
        (Decimal(reference_rate_ppm) / Decimal(100)).quantize(Decimal(1), ROUND_HALF_UP) * Decimal(100)
    )


def _rate_type(metric_code: str) -> str:
    mapping = {
        "RENEWAL_RATE_REFERENCE": RENEWAL_RATE,
        "NEW_STUDENT_SHARE_REFERENCE": NEW_STUDENT_SHARE,
    }
    try:
        return mapping[metric_code]
    except KeyError as exc:
        raise RateReferenceGenerationError(
            "SOURCE_METRIC_UNSUPPORTED", f"不支持的来源指标 {metric_code}"
        ) from exc
