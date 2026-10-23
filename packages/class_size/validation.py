from dataclasses import dataclass

CLASS_SIZE_RULE_VERSION = "phase1-class-size-v1"


@dataclass(frozen=True)
class ClassSizeInput:
    parameter_id: str
    track: str
    product: str
    minimum_class_size: int


@dataclass(frozen=True)
class HeadcountInput:
    result_id: str
    combination_stable_key: str
    fiscal_season: int
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    calculation_status: str
    integer_total_students: int | None


@dataclass(frozen=True)
class ClassSizeResult:
    headcount: HeadcountInput
    parameter_id: str | None
    minimum_class_size: int | None
    class_size_gap: int | None
    status: str


@dataclass(frozen=True)
class ClassSizeValidation:
    fiscal_season: int
    combination_stable_key: str
    code: str
    message: str
    detail: dict[str, object]


@dataclass(frozen=True)
class ClassSizeCalculation:
    rows: tuple[ClassSizeResult, ...]
    validations: tuple[ClassSizeValidation, ...]

    @property
    def publishable(self) -> bool:
        return not self.validations


class ClassSizeInvariantError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_class_sizes(
    *, headcounts: list[HeadcountInput], parameters: list[ClassSizeInput]
) -> ClassSizeCalculation:
    parameter_by_key: dict[tuple[str, str], ClassSizeInput] = {}
    for parameter_item in parameters:
        if parameter_item.minimum_class_size <= 0:
            raise ClassSizeInvariantError("INVALID_MINIMUM_CLASS_SIZE", "班均必须为正整数")
        key = parameter_item.track, parameter_item.product
        if key in parameter_by_key:
            raise ClassSizeInvariantError("DUPLICATE_CLASS_SIZE_PARAMETER", "班均参数键不得重复")
        parameter_by_key[key] = parameter_item

    ordered = sorted(
        headcounts,
        key=lambda item: (
            item.fiscal_season,
            item.track,
            item.product,
            item.entry_attribute,
            item.grade,
            item.subject,
            item.campus,
            item.combination_stable_key,
        ),
    )
    rows: list[ClassSizeResult] = []
    validations: list[ClassSizeValidation] = []
    totals_by_combination: dict[str, list[int | None]] = {}

    for headcount_item in ordered:
        parameter = parameter_by_key.get((headcount_item.track, headcount_item.product))
        totals_by_combination.setdefault(headcount_item.combination_stable_key, []).append(
            headcount_item.integer_total_students
        )
        if headcount_item.calculation_status != "CALCULATED" or headcount_item.integer_total_students is None:
            rows.append(ClassSizeResult(headcount_item, None, None, None, "HEADCOUNT_UNAVAILABLE"))
            validations.append(
                ClassSizeValidation(
                    headcount_item.fiscal_season,
                    headcount_item.combination_stable_key,
                    "HEADCOUNT_UNAVAILABLE_FOR_CLASS_SIZE",
                    "人数结果未完成，不能执行班均判定",
                    {"headcount_result_id": headcount_item.result_id},
                )
            )
            continue
        if parameter is None:
            rows.append(ClassSizeResult(headcount_item, None, None, None, "PARAMETER_MISSING"))
            validations.append(
                ClassSizeValidation(
                    headcount_item.fiscal_season,
                    headcount_item.combination_stable_key,
                    "CLASS_SIZE_PARAMETER_MISSING",
                    "当前赛道和产品缺少班均参数",
                    {"track": headcount_item.track, "product": headcount_item.product},
                )
            )
            continue
        total = headcount_item.integer_total_students
        if total == 0:
            status = "NO_PLANNED_CLASS"
            gap = 0
        elif total < parameter.minimum_class_size:
            status = "BELOW_MINIMUM_CLASS_SIZE"
            gap = parameter.minimum_class_size - total
            validations.append(
                ClassSizeValidation(
                    headcount_item.fiscal_season,
                    headcount_item.combination_stable_key,
                    status,
                    "取整后计划人次低于班均",
                    {
                        "integer_total_students": total,
                        "minimum_class_size": parameter.minimum_class_size,
                        "class_size_gap": gap,
                    },
                )
            )
        else:
            status = "PASS"
            gap = 0
        rows.append(
            ClassSizeResult(
                headcount_item,
                parameter.parameter_id,
                parameter.minimum_class_size,
                gap,
                status,
            )
        )

    for stable_key, totals in sorted(totals_by_combination.items()):
        if len(totals) == 4 and all(value == 0 for value in totals):
            validations.append(
                ClassSizeValidation(
                    min(item.fiscal_season for item in ordered if item.combination_stable_key == stable_key),
                    stable_key,
                    "ENABLED_COMBINATION_ALL_SEASONS_ZERO",
                    "已启用组合四季均为零，必须由部门预算负责人关闭后才能发布",
                    {"season_count": 4},
                )
            )

    return ClassSizeCalculation(tuple(rows), tuple(validations))
