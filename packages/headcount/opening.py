"""Prepare opening students without reading BI or changing finalized FY27 data."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from packages.headcount.calculation import PPM, HeadcountInvariantError

OPENING_RULE_VERSION = "opening-students-v1"


@dataclass(frozen=True, order=True)
class OpeningKey:
    track: str
    product: str
    grade: str
    subject: str
    campus: str


@dataclass(frozen=True)
class SpringSource:
    key: OpeningKey
    enrollment: int
    renewal_rate_ppm: int
    rate_version_id: str


@dataclass(frozen=True)
class OpeningRow:
    source_key: OpeningKey
    target_key: OpeningKey | None
    enrollment: int
    renewal_rate_ppm: int
    rate_version_id: str
    theoretical_old_students: Fraction
    exclusion: str | None


@dataclass(frozen=True)
class OpeningPreparation:
    fiscal_year: int
    source_fiscal_season: int
    snapshot_date: date
    source_batch: str
    source_snapshot_id: str
    rule_version: str
    rows: tuple[OpeningRow, ...]


def prepare_bi_opening(
    *,
    fiscal_year: int,
    source_fiscal_season: int,
    snapshot_date: date,
    source_batch: str,
    source_snapshot_id: str,
    rows: list[SpringSource],
) -> OpeningPreparation:
    """Input is aggregated actual 招生人次, not attendance-qualified enrollment.

    Missing target rows are deliberately not filled with zero. The persistence
    adapter must validate coverage against the full enabled budget combination set.
    Rates are explicitly supplied; this function never substitutes historical goals.
    """
    if fiscal_year < 2028:
        raise HeadcountInvariantError("FY27_FINALIZED", "FY27 不从 BI 重建期初老生")
    if source_fiscal_season != (fiscal_year - 1) * 10 + 4:
        raise HeadcountInvariantError("OPENING_SOURCE_SEASON_INVALID", "来源必须为上一财年春季")
    if not source_batch.strip() or not source_snapshot_id.strip():
        raise HeadcountInvariantError("OPENING_PROVENANCE_REQUIRED", "必须保留来源快照和批次")
    seen: set[OpeningKey] = set()
    result: list[OpeningRow] = []
    for row in sorted(rows, key=lambda item: item.key):
        key = row.key
        if not all(value.strip() for value in (key.track, key.product, key.grade, key.subject, key.campus)):
            raise HeadcountInvariantError("OPENING_DIMENSION_REQUIRED", "期初老生来源维度必须完整")
        if key in seen:
            raise HeadcountInvariantError("OPENING_DUPLICATE_SOURCE", "同维度来源必须先汇总，不能重复")
        seen.add(key)
        if type(row.enrollment) is not int or row.enrollment < 0:
            raise HeadcountInvariantError("OPENING_ENROLLMENT_INVALID", "来源招生人次必须为非负整数")
        if type(row.renewal_rate_ppm) is not int or not 0 <= row.renewal_rate_ppm <= PPM:
            raise HeadcountInvariantError("OPENING_RATE_INVALID", "续班率必须在 0 到 100% 之间")
        if not row.rate_version_id.strip():
            raise HeadcountInvariantError("OPENING_RATE_VERSION_REQUIRED", "续班率必须绑定参数版本")
        exclusion: str | None = None
        if key.grade == "九年级":
            exclusion = "GRADUATED"
        elif key.grade == "八年级" and key.subject in {"地理", "生物"}:
            exclusion = "SUBJECT_ENDED"
        elif key.grade not in {"七年级", "八年级"}:
            raise HeadcountInvariantError("OPENING_GRADE_UNSUPPORTED", "未确认年级不得自动映射")
        target = (
            OpeningKey(
                key.track,
                key.product,
                "八年级" if key.grade == "七年级" else "九年级",
                key.subject,
                key.campus,
            )
            if exclusion is None
            else None
        )
        result.append(
            OpeningRow(
                key,
                target,
                row.enrollment,
                row.renewal_rate_ppm,
                row.rate_version_id,
                Fraction(row.enrollment * row.renewal_rate_ppm, PPM) if target else Fraction(0),
                exclusion,
            )
        )
    return OpeningPreparation(
        fiscal_year,
        source_fiscal_season,
        snapshot_date,
        source_batch,
        source_snapshot_id,
        OPENING_RULE_VERSION,
        tuple(result),
    )
