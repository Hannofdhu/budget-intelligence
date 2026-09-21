from dataclasses import dataclass

from packages.domain.versioning.hashing import content_hash

QUARTER_NAMES = {1: "暑假", 2: "秋季", 3: "寒假", 4: "春季"}


@dataclass(frozen=True)
class PlannedNewStudentKey:
    fiscal_year: int
    fiscal_season: int
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str


def planned_new_student_stable_key(key: PlannedNewStudentKey) -> str:
    return content_hash({"schema_version": "1.0", **key.__dict__})


def quarter_name(fiscal_year: int, fiscal_season: int) -> str | None:
    if fiscal_season // 10 != fiscal_year:
        return None
    return QUARTER_NAMES.get(fiscal_season % 10)


def handling_status(*, planned_new_students: int, locked: bool) -> str:
    if not locked:
        return "FILLED_PENDING_LOCK"
    return "LOCKED_ZERO" if planned_new_students == 0 else "LOCKED_POSITIVE"
