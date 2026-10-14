"""Read models and typed form preparation for the existing budget commands.

No new mutation semantics: the browser submits prepared forms to the original APIs.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from apps.api.routes import versioning as v
from packages.contracts import versioning as c
from packages.persistence.entry_detail import get_entry_detail_set_version
from packages.persistence.entry_planning import _assert_version_access
from packages.persistence.models import (
    AnnualBudget,
    CampusEntryConfirmation,
    CombinationSetConfirmation,
    EntryScopeConfirmation,
)
from packages.persistence.resource_finance import resource_finance_batch_content_hash
from packages.persistence.revenue import discount_parameter_batch_content_hash
from packages.persistence.target_closure import target_set_batch_content_hash
from packages.persistence.versioning import get_source_snapshot

router = APIRouter()

# Section, business label, manifest pointer, read function, request model, command.
FORMS: dict[str, tuple[str, str, str, type[BaseModel], str]] = {
    "targets": (
        "学校版收入目标",
        "target_set_version_id",
        "get_target_set_version",
        c.TargetSetReplaceAndLockRequest,
        "/targets:replace-and-lock",
    ),
    "combinations": (
        "SKU",
        "combination_set_version_id",
        "get_combination_set_version",
        c.CombinationReplaceDraftRequest,
        "/combinations:replace-draft",
    ),
    "entry-total": (
        "年度入口总值",
        "entry_total_version_id",
        "get_entry_total_version",
        c.EntryTotalLockRequest,
        "/entry-total:save-and-lock",
    ),
    "campus": (
        "校区入口确定值",
        "campus_entry_set_version_id",
        "get_campus_entry_set_version",
        c.CampusEntryReplaceDraftRequest,
        "/campus-entry:replace-draft",
    ),
    "entry-scope": (
        "入口范围例外",
        "entry_scope_version_id",
        "get_entry_scope_version",
        c.EntryScopeReplaceDraftRequest,
        "/entry-scope:replace-draft",
    ),
    "rates": (
        "续班率与新生占比",
        "rate_parameter_version_id",
        "get_rate_parameter_version",
        c.RateParameterReplaceDraftRequest,
        "/rate-parameters:replace-draft",
    ),
    "planned": (
        "计划新生人次",
        "planned_new_student_version_id",
        "get_planned_new_student_version",
        c.PlannedNewStudentChangeAndLockRequest,
        "/planned-new-students:change-and-lock",
    ),
    "resources": (
        "班均、价格与结转",
        "resource_finance_parameter_version_id",
        "get_resource_finance_version",
        c.ResourceFinanceReplaceDraftRequest,
        "/resource-finance:replace-draft",
    ),
    "discount": (
        "明细折扣与综合目标",
        "discount_parameter_version_id",
        "get_discount_parameter_version",
        c.DiscountParameterReplaceDraftRequest,
        "/discount-parameters:replace-draft",
    ),
}

CONFIRMS = {
    "combinations": "/combinations:confirm",
    "campus": "/campus-entry:confirm",
    "entry-scope": "/entry-scope:confirm",
    "rates": "/rate-boundaries:confirm",
    "resources": "/resource-finance:confirm",
    "discount": "/discount-parameters:confirm",
}

CONFIRMATION_MODELS = {
    "combinations": (
        CombinationSetConfirmation,
        CombinationSetConfirmation.combination_set_version_id,
        "combination_set_confirmation_id",
    ),
    "campus": (
        CampusEntryConfirmation,
        CampusEntryConfirmation.campus_entry_set_version_id,
        "campus_entry_confirmation_id",
    ),
    "entry-scope": (
        EntryScopeConfirmation,
        EntryScopeConfirmation.entry_scope_version_id,
        "entry_scope_confirmation_id",
    ),
}


def resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return resolve(root["$defs"][schema["$ref"].split("/")[-1]], root)
    choices = schema.get("anyOf", [])
    if choices:
        # Decimal's validation schema permits number or string; forms keep decimal strings.
        return resolve(
            next(
                (s for s in choices if s.get("type") == "string"),
                next(s for s in choices if s.get("type") != "null"),
            ),
            root,
        )
    return schema


def project(value: Any, schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """Only copy request fields from immutable response records, retaining exact decimals."""
    schema = resolve(schema, root)
    if value is None:
        return None
    if schema.get("type") == "object" and "properties" in schema:
        result = {k: project(value[k], s, root) for k, s in schema["properties"].items() if k in value}
        for key, field in schema["properties"].items():
            if key not in result and resolve(field, root).get("type") == "array":
                result[key] = []
        return result
    if schema.get("type") == "array":
        return [project(item, schema["items"], root) for item in value]
    return value


def entry_detail_form_value(data: dict[str, Any]) -> dict[str, Any]:
    """Project persisted manual reservations back into the calculation form."""
    return {
        "manual_reservations": [
            {
                "track": row["track"],
                "product": row["product"],
                "grade": row["grade"],
                "subject": row["subject"],
                "campus": row["campus"],
                "reservation_ratio_ppm": row["manual_reservation_ratio_ppm"],
                "reason": row["manual_reservation_reason"],
            }
            for row in data.get("items", [])
            if row.get("manual_reservation_ratio_ppm") is not None
        ]
    }


def planned_new_student_form_value(data: dict[str, Any]) -> dict[str, Any]:
    """Restore editable fields that have different names in the immutable response."""
    return {
        "items": [
            {
                "fiscal_season": row["fiscal_season"],
                "track": row["track"],
                "product": row["product"],
                "entry_attribute": row["entry_attribute"],
                "grade": row["grade"],
                "subject": row["subject"],
                "campus": row["campus"],
                "planned_new_students": row["planned_new_students"],
                "manual_renewal_rate_ppm": row.get("lock_origin", {}).get("manual_renewal_rate_ppm"),
                "reason": row["manual_reason"],
            }
            for row in data.get("items", [])
            if row.get("locked")
        ]
    }


def resource_finance_form_seed(combinations: list[dict[str, Any]], fiscal_year: int) -> dict[str, Any]:
    """Create dimension-complete blank rows without inventing business parameter values."""
    enabled = [row for row in combinations if row.get("enabled") is True]
    products = sorted({(str(row["track"]), str(row["product"])) for row in enabled})
    first_season = fiscal_year * 10 + 1
    evidence = {"reference": ""}
    common = {"source_evidence": evidence, "change_reason": ""}
    return {
        "budget_tax_conversion_factor": "",
        "tax_factor_source_evidence": evidence,
        "tax_factor_change_reason": "",
        "class_size_items": [
            {
                "track": track,
                "product": product,
                "system_value": "",
                "final_value": "",
                **common,
            }
            for track, product in products
        ],
        "revenue_items": [
            {
                "fiscal_season": fiscal_year * 10 + season,
                "track": track,
                "product": product,
                "list_price_yuan": "",
                "current_season_recognition_rate_ppm": "",
                "opening_carryover_revenue_yuan": "0",
                **common,
            }
            for season in range(1, 5)
            for track, product in products
        ],
        "opening_carryover_items": [
            {
                "fiscal_season": first_season,
                "track": str(row["track"]),
                "product": str(row["product"]),
                "grade": str(row["grade"]),
                "subject": str(row["subject"]),
                "campus": str(row["campus"]),
                "amount_yuan": "",
                **common,
            }
            for row in sorted(
                enabled,
                key=lambda item: (
                    str(item["track"]),
                    str(item["product"]),
                    str(item["grade"]),
                    str(item["subject"]),
                    str(item["campus"]),
                ),
            )
        ],
    }


@router.get("/workspace/budgets")
async def budgets(session: v.Session, actor: v.CurrentActor) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(AnnualBudget)
        .where(
            AnnualBudget.department_id == actor.department_id,
        )
        .order_by(AnnualBudget.fiscal_year.desc())
    )
    return [
        {
            "annual_budget_id": str(b.annual_budget_id),
            "fiscal_year": b.fiscal_year,
            "draft_revision_id": str(b.current_draft_revision_id) if b.current_draft_revision_id else None,
            "published_revision_id": str(b.current_published_revision_id)
            if b.current_published_revision_id
            else None,
        }
        for b in rows
    ]


@router.get("/revisions/{revision_id}/workspace")
async def workspace(revision_id: uuid.UUID, session: v.Session, actor: v.CurrentActor) -> dict[str, Any]:
    revision, budget = await _assert_version_access(session, actor=actor, revision_id=revision_id)
    manifest = revision.component_manifest
    sections = []
    all_data: dict[str, Any] = {}
    for key, (label, pointer, getter, model, path) in FORMS.items():
        data: dict[str, Any] = {}
        if manifest.get(pointer):
            response = await getattr(v, getter)(
                session, actor=actor, **{pointer: uuid.UUID(str(manifest[pointer]))}
            )
            data = response.model_dump(mode="json")
            if key in CONFIRMATION_MODELS:
                model_class, version_column, confirmation_id_field = CONFIRMATION_MODELS[key]
                confirmation_model: Any = model_class
                confirmation: Any = await session.scalar(
                    select(confirmation_model).where(
                        version_column == uuid.UUID(str(manifest[pointer])),
                        confirmation_model.input_fingerprint == data["content_hash"],
                    )
                )
                if confirmation is not None:
                    data["confirmed"] = True
                    data["confirmation_id"] = str(getattr(confirmation, confirmation_id_field))
                    data["confirmed_at"] = confirmation.confirmed_at.isoformat()
        all_data[key] = data
        schema = model.model_json_schema()
        value = project(data, schema, schema)
        if key == "planned":
            value = planned_new_student_form_value(data)
        if key == "resources" and not data:
            value = resource_finance_form_seed(
                all_data.get("combinations", {}).get("items", []), budget.fiscal_year
            )
        confirm: dict[str, Any] | None = (
            {"path": CONFIRMS[key], "body": {pointer: manifest[pointer]}}
            if key in CONFIRMS and manifest.get(pointer)
            else None
        )
        if key == "rates" and confirm:
            confirm["body"]["rate_parameter_stable_keys"] = [
                r["rate_parameter_stable_key"] for r in data.get("items", [])
            ]
        sections.append(
            {
                "key": key,
                "label": label,
                "schema": schema,
                "value": value,
                "data": data,
                "path": path,
                "confirm": confirm,
            }
        )
    # Actions use the same typed forms as the existing commands; no arbitrary URL dispatch.
    # Entry-detail is a calculation action rather than a draft form. Rebuild its editable
    # manual reservations from the immutable result so a successful calculation survives reload.
    entry_detail_data: dict[str, Any] = {}
    entry_detail_value: dict[str, Any] = {"manual_reservations": []}
    if manifest.get("entry_detail_set_version_id"):
        entry_detail_response = await get_entry_detail_set_version(
            session,
            actor=actor,
            entry_detail_set_version_id=uuid.UUID(str(manifest["entry_detail_set_version_id"])),
        )
        entry_detail_data = entry_detail_response.model_dump(mode="json")
        entry_detail_value = entry_detail_form_value(entry_detail_data)
    combos = all_data["combinations"].get("items", [])
    extras: list[tuple[str, str, type[BaseModel], str, dict[str, Any]]] = [
        (
            "campus-suggestions",
            "重新生成校区建议",
            c.CampusSuggestionGenerateRequest,
            "/campus-entry:generate-suggestions",
            {
                "campuses": [
                    {"campus": name, "classroom_resource": {}, "sales_resource": {}}
                    for name in sorted({r["campus"] for r in combos if r["enabled"]})
                ]
            },
        ),
        (
            "entry-detail",
            "分配入口明细",
            c.EntryDetailCalculateRequest,
            "/entry-detail:calculate",
            entry_detail_value,
        ),
        ("rate-lock", "锁定选定比例", c.RateParameterLockRequest, "/rate-parameters:lock", {"items": []}),
        (
            "rate-change-lock",
            "修改并锁定选定比例",
            c.RateParameterChangeAndLockRequest,
            "/rate-parameters:change-and-lock",
            {"items": []},
        ),
        (
            "rate-unlock",
            "解锁选定比例",
            c.RateParameterUnlockRequest,
            "/rate-parameters:unlock",
            {"items": []},
        ),
        (
            "planned-unlock",
            "解锁计划新生",
            c.PlannedNewStudentUnlockRequest,
            "/planned-new-students:unlock",
            {"items": []},
        ),
    ]
    for key, label, model, path, value in extras:
        sections.append(
            {
                "key": key,
                "label": label,
                "schema": model.model_json_schema(),
                "value": value,
                "data": entry_detail_data if key == "entry-detail" else {},
                "path": path,
                "confirm": None,
            }
        )
    assert revision.source_snapshot_id is not None
    source = await get_source_snapshot(session, actor=actor, source_snapshot_id=revision.source_snapshot_id)
    return {
        "revision_id": str(revision_id),
        "revision_no": revision.revision_no,
        "fiscal_year": budget.fiscal_year,
        "is_current_draft": budget.current_draft_revision_id == revision_id,
        "is_published": budget.current_published_revision_id == revision_id,
        "manifest": manifest,
        "source": source.model_dump(mode="json"),
        "sections": sections,
    }


@router.post("/workspace/forms/{section}:prepare")
async def prepare_form(section: str, values: dict[str, Any], actor: v.CurrentActor) -> dict[str, Any]:
    """Validate and hash the three existing batch contracts; this does not save or approve."""
    if section not in {"targets", "resources", "discount"}:
        raise HTTPException(404, "没有此表单")
    values = {**values, "batch_content_hash": "0" * 64}
    try:
        if section == "targets":
            target = c.TargetSetReplaceAndLockRequest.model_validate(values)
            target.batch_content_hash = target_set_batch_content_hash(target)
            return target.model_dump(mode="json")
        if section == "resources":
            resource = c.ResourceFinanceReplaceDraftRequest.model_validate(values)
            resource.batch_content_hash = resource_finance_batch_content_hash(resource)
            return resource.model_dump(mode="json")
        discount = c.DiscountParameterReplaceDraftRequest.model_validate(values)
        discount.batch_content_hash = discount_parameter_batch_content_hash(discount)
        return discount.model_dump(mode="json")
    except ValidationError as error:
        raise HTTPException(
            422,
            {
                "code": "FORM_INVALID",
                "message": "; ".join(f"{' / '.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()),
            },
        ) from error
