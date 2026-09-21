from dataclasses import dataclass

from packages.domain.versioning.hashing import content_hash

RATE_STEP_PPM = 100
MAX_RATE_PPM = 1_000_000
MAX_REVERSE_NEW_STUDENT_SHARE_PPM = 999_900


@dataclass(frozen=True)
class RateParameterKey:
    rate_type: str
    fiscal_year: int
    target_fiscal_season: int
    track: str
    product: str
    grade: str
    campus: str
    renewal_relation_code: str | None


@dataclass(frozen=True)
class RateBoundaryValues:
    current_value_ppm: int
    lower_bound_ppm: int
    upper_bound_ppm: int


@dataclass(frozen=True)
class RateValidationIssue:
    code: str
    message: str


def rate_parameter_stable_key(key: RateParameterKey) -> str:
    return content_hash({"schema_version": "1.0", **key.__dict__})


def cross_year_match_key(key: RateParameterKey) -> str:
    return content_hash(
        {
            "schema_version": "1.0",
            "rate_type": key.rate_type,
            "quarter_index": key.target_fiscal_season % 10,
            "track": key.track,
            "product": key.product,
            "grade": key.grade,
            "campus": key.campus,
            "renewal_relation_code": key.renewal_relation_code,
        }
    )


def boundary_fingerprint(*, stable_key: str, values: RateBoundaryValues) -> str:
    return content_hash(
        {
            "schema_version": "1.0",
            "rate_parameter_stable_key": stable_key,
            "lower_bound_ppm": values.lower_bound_ppm,
            "upper_bound_ppm": values.upper_bound_ppm,
        }
    )


def validate_rate_values(
    *, rate_type: str, usage_mode: str, values: RateBoundaryValues
) -> list[RateValidationIssue]:
    issues: list[RateValidationIssue] = []
    named_values = {
        "current_value_ppm": values.current_value_ppm,
        "lower_bound_ppm": values.lower_bound_ppm,
        "upper_bound_ppm": values.upper_bound_ppm,
    }
    for name, value in named_values.items():
        if value % RATE_STEP_PPM != 0:
            issues.append(
                RateValidationIssue(
                    "RATE_PRECISION_INVALID",
                    f"{name} 必须为 100 ppm（0.01 个百分点）的整数倍",
                )
            )
    maximum = (
        MAX_REVERSE_NEW_STUDENT_SHARE_PPM
        if rate_type == "NEW_STUDENT_SHARE" and usage_mode == "REVERSE_INPUT"
        else MAX_RATE_PPM
    )
    if not (0 <= values.lower_bound_ppm <= values.current_value_ppm <= values.upper_bound_ppm <= maximum):
        issues.append(
            RateValidationIssue(
                "RATE_BOUNDARY_INVALID",
                f"必须满足 0 ≤ 下限 ≤ 当前值 ≤ 上限 ≤ {maximum} ppm",
            )
        )
    return issues
