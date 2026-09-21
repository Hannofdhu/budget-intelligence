from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from packages.domain.versioning.hashing import content_hash
from packages.source_snapshot.adapter import DIMENSION_FIELDS, SourceAdapterError

RENEWAL_RATE_REFERENCE = "RENEWAL_RATE_REFERENCE"
NEW_STUDENT_SHARE_REFERENCE = "NEW_STUDENT_SHARE_REFERENCE"
OPENING_ENROLLMENT = "OPENING_ACTUAL_ENROLLMENT_V1"


@dataclass(frozen=True, order=True)
class AggregateKey:
    fiscal_season: int
    quarter_name: str
    track: str
    product: str
    grade: str
    subject: str
    campus: str

    def as_dict(self) -> dict[str, object]:
        return {
            "fiscal_season": self.fiscal_season,
            "quarter_name": self.quarter_name,
            "track": self.track,
            "product": self.product,
            "grade": self.grade,
            "subject": self.subject,
            "campus": self.campus,
        }


@dataclass(frozen=True)
class MetricValue:
    numerator: Decimal
    denominator: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return {"numerator": self.numerator, "denominator": self.denominator}


@dataclass(frozen=True)
class AggregationResult:
    roster_metrics: dict[str, dict[AggregateKey, MetricValue]]
    summary_metrics: dict[str, dict[AggregateKey, MetricValue]]
    entry_history: dict[AggregateKey, int]
    excluded_zero_incomplete_rows: dict[str, int]


def aggregate_source_rows(
    *,
    roster_rows: list[dict[str, object]],
    summary_rows: list[dict[str, object]],
    entry_fiscal_season: int,
    opening_fiscal_season: int | None = None,
) -> AggregationResult:
    roster, roster_excluded = _aggregate_rows(roster_rows, source="花名册", new_student_field="业务新生人次")
    summary, summary_excluded = _aggregate_rows(
        summary_rows, source="指标汇总宽表", new_student_field="新生人次"
    )
    entry_history: dict[AggregateKey, int] = {}
    for row_index, row in enumerate(roster_rows, start=1):
        if _zero_incomplete_row(row, source="花名册", row_index=row_index, new_student_field="业务新生人次"):
            continue
        key = _key(row, source="花名册", row_index=row_index)
        if key.fiscal_season != entry_fiscal_season or key.grade != "七年级":
            continue
        count = _decimal(row, "业务新生人次", source="花名册", row_index=row_index)
        if count != count.to_integral_value():
            raise SourceAdapterError("SOURCE_COUNT_NOT_INTEGER", "入口历史业务新生人次必须为整数")
        entry_history[key] = entry_history.get(key, 0) + int(count)
    if opening_fiscal_season is not None:
        roster[OPENING_ENROLLMENT] = aggregate_opening_enrollment(roster_rows, opening_fiscal_season)
    return AggregationResult(
        roster_metrics=roster,
        summary_metrics=summary,
        entry_history=entry_history,
        excluded_zero_incomplete_rows={
            "roster": roster_excluded,
            "summary": summary_excluded,
        },
    )


def aggregate_opening_enrollment(
    rows: list[dict[str, object]],
    fiscal_season: int,
) -> dict[AggregateKey, MetricValue]:
    result: dict[AggregateKey, MetricValue] = {}
    for index, row in enumerate(rows, 1):
        if str(row.get("季节财年")) != str(fiscal_season):
            continue
        marker = row.get("目标标识(目标,数据)")
        if marker == "目标":
            continue
        if marker != "数据":
            raise SourceAdapterError("OPENING_ACTUAL_MARKER_REQUIRED", "春季来源必须明确标记为实际数据")
        key = _key(row, source="期初老生来源", row_index=index)
        if row.get("招生人次") is None or not str(row["招生人次"]).strip():
            raise SourceAdapterError("OPENING_ENROLLMENT_MISSING", "普通口径招生人次缺失，不能默认为零")
        count = _decimal(row, "招生人次", source="期初老生来源", row_index=index)
        if count != count.to_integral_value():
            raise SourceAdapterError("OPENING_ENROLLMENT_INVALID", "招生人次必须为非负整数")
        _add_metric(result, key, count, Decimal(0))
    return result


def metric_map_hash(values: dict[AggregateKey, MetricValue]) -> str:
    return content_hash(
        [
            {**key.as_dict(), **value.as_dict()}
            for key, value in sorted(values.items(), key=lambda item: item[0])
        ]
    )


def entry_map_hash(values: dict[AggregateKey, int]) -> str:
    return content_hash(
        [
            {**key.as_dict(), "business_new_student_count": count}
            for key, count in sorted(values.items(), key=lambda item: item[0])
        ]
    )


def metric_totals(values: dict[AggregateKey, MetricValue]) -> MetricValue:
    return MetricValue(
        numerator=sum((item.numerator for item in values.values()), start=Decimal(0)),
        denominator=sum((item.denominator for item in values.values()), start=Decimal(0)),
    )


def mismatch_count(left: dict[AggregateKey, MetricValue], right: dict[AggregateKey, MetricValue]) -> int:
    keys = set(left) | set(right)
    zero = MetricValue(Decimal(0), Decimal(0))
    return sum(left.get(key, zero) != right.get(key, zero) for key in keys)


def _aggregate_rows(
    rows: list[dict[str, object]], *, source: str, new_student_field: str
) -> tuple[dict[str, dict[AggregateKey, MetricValue]], int]:
    result: dict[str, dict[AggregateKey, MetricValue]] = {
        RENEWAL_RATE_REFERENCE: {},
        NEW_STUDENT_SHARE_REFERENCE: {},
    }
    excluded = 0
    for row_index, row in enumerate(rows, start=1):
        if _zero_incomplete_row(
            row,
            source=source,
            row_index=row_index,
            new_student_field=new_student_field,
        ):
            excluded += 1
            continue
        key = _key(row, source=source, row_index=row_index)
        renewal = _decimal(row, "续班人次", source=source, row_index=row_index) - _decimal(
            row, "专项班续班人次", source=source, row_index=row_index
        )
        if renewal < 0:
            raise SourceAdapterError("SOURCE_RENEWAL_NEGATIVE", f"{source}续班分子不能为负数")
        _add_metric(
            result[RENEWAL_RATE_REFERENCE],
            key,
            renewal,
            _decimal(row, "原班人次", source=source, row_index=row_index),
        )
        _add_metric(
            result[NEW_STUDENT_SHARE_REFERENCE],
            key,
            _decimal(row, new_student_field, source=source, row_index=row_index),
            _decimal(
                row,
                "招生人次(三分之二出勤口径)",
                source=source,
                row_index=row_index,
            ),
        )
    return result, excluded


def _zero_incomplete_row(
    row: dict[str, object], *, source: str, row_index: int, new_student_field: str
) -> bool:
    missing = [
        field for field in DIMENSION_FIELDS if row.get(field) is None or not str(row.get(field)).strip()
    ]
    if not missing:
        return False
    metric_fields = (
        new_student_field,
        "招生人次(三分之二出勤口径)",
        "原班人次",
        "续班人次",
        "专项班续班人次",
    )
    if all(_decimal(row, field, source=source, row_index=row_index) == 0 for field in metric_fields):
        return True
    raise SourceAdapterError(
        "SOURCE_DIMENSION_MISSING",
        f"{source}第 {row_index} 行维度 {missing[0]} 缺失且存在有效预算人次",
    )


def _add_metric(
    target: dict[AggregateKey, MetricValue],
    key: AggregateKey,
    numerator: Decimal,
    denominator: Decimal,
) -> None:
    current = target.get(key, MetricValue(Decimal(0), Decimal(0)))
    target[key] = MetricValue(
        numerator=current.numerator + numerator,
        denominator=current.denominator + denominator,
    )


def _key(row: dict[str, object], *, source: str, row_index: int) -> AggregateKey:
    values: list[str] = []
    for field in DIMENSION_FIELDS:
        value = row.get(field)
        if value is None or not str(value).strip():
            raise SourceAdapterError(
                "SOURCE_DIMENSION_MISSING",
                f"{source}第 {row_index} 行维度 {field} 缺失",
            )
        values.append(str(value).strip())
    try:
        fiscal_season = int(values[0])
    except ValueError as exc:
        raise SourceAdapterError(
            "SOURCE_DIMENSION_INVALID", f"{source}第 {row_index} 行季节财年无效"
        ) from exc
    return AggregateKey(
        fiscal_season=fiscal_season,
        quarter_name=values[1],
        track=values[2],
        product=values[3],
        grade=values[4],
        subject=values[5],
        campus=values[6],
    )


def _decimal(row: dict[str, object], field: str, *, source: str, row_index: int) -> Decimal:
    value = row.get(field)
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, bool):
        raise SourceAdapterError("SOURCE_METRIC_INVALID", f"{source}第 {row_index} 行字段 {field} 无效")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SourceAdapterError(
            "SOURCE_METRIC_INVALID", f"{source}第 {row_index} 行字段 {field} 无效"
        ) from exc
    if not number.is_finite() or number < 0:
        raise SourceAdapterError(
            "SOURCE_METRIC_INVALID", f"{source}第 {row_index} 行字段 {field} 必须为非负有限数"
        )
    return number
