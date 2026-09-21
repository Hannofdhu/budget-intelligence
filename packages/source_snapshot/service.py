import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import Settings
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.hashing import content_hash
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    AuditEvent,
    EntryHistoryAggregate,
    SourceMappingVerification,
    SourceMetricAggregate,
    SourceReconciliation,
    SourceSnapshot,
    SourceSnapshotComponent,
)
from packages.source_snapshot.adapter import SourceAdapter, SourceExtract
from packages.source_snapshot.aggregation import (
    NEW_STUDENT_SHARE_REFERENCE,
    RENEWAL_RATE_REFERENCE,
    aggregate_source_rows,
    entry_map_hash,
    metric_map_hash,
    metric_totals,
    mismatch_count,
)


async def build_source_snapshot(
    session: AsyncSession,
    *,
    adapter: SourceAdapter,
    settings: Settings,
    actor: Actor,
    fiscal_year: int,
    build_job_id: uuid.UUID,
    request_id: str,
) -> dict[str, object]:
    async with session.begin():
        replay = await session.scalar(
            select(SourceSnapshot).where(SourceSnapshot.build_job_id == build_job_id)
        )
        if replay is not None:
            return _build_result(replay, replay.status == "FROZEN", {})

    extract = await adapter.extract(fiscal_year)
    aligned = _chain_is_aligned(extract, settings.source_chain_max_lag_minutes)
    aggregation = None
    mismatch_counts: dict[str, int] = {}
    if aligned:
        aggregation = aggregate_source_rows(
            roster_rows=extract.roster_rows,
            summary_rows=extract.summary_rows,
            entry_fiscal_season=extract.fiscal_season_start,
            opening_fiscal_season=(fiscal_year - 1) * 10 + 4 if fiscal_year >= 2028 else None,
        )
        mismatch_counts = {
            metric_code: mismatch_count(
                aggregation.roster_metrics[metric_code],
                aggregation.summary_metrics[metric_code],
            )
            for metric_code in (RENEWAL_RATE_REFERENCE, NEW_STUDENT_SHARE_REFERENCE)
        }

    occurred_at = datetime.now(UTC)
    component_hashes = _component_hashes(extract, settings)
    snapshot_hash = content_hash(
        {
            "schema_version": "1.0",
            "department_id": actor.department_id,
            "fiscal_year": fiscal_year,
            "field_mapping_version": settings.source_field_mapping_version,
            "roster_component_hash": component_hashes["roster"],
            "summary_component_hash": component_hashes["summary"],
            "aggregate_hashes": _aggregate_hashes(aggregation) if aggregation else None,
        }
    )
    request_hash = content_hash(
        {
            "schema_version": "1.0",
            "department_id": actor.department_id,
            "fiscal_year": fiscal_year,
            "build_job_id": str(build_job_id),
        }
    )
    snapshot = SourceSnapshot(
        source_snapshot_id=uuid7(),
        build_job_id=build_job_id,
        department_id=actor.department_id,
        cutoff_at=max(extract.roster.last_execution_at, extract.summary.last_execution_at),
        status="DRAFT" if aligned else "WAITING_FOR_SUMMARY",
        content_hash=snapshot_hash,
        request_hash=request_hash,
        created_by=actor.user_id,
        created_at=occurred_at,
        reconciled_at=None,
        frozen_at=None,
    )
    components = _components(snapshot.source_snapshot_id, extract, settings, component_hashes, occurred_at)

    async with session.begin():
        replay = await session.scalar(
            select(SourceSnapshot).where(SourceSnapshot.build_job_id == build_job_id).with_for_update()
        )
        if replay is not None:
            return _build_result(replay, replay.status == "FROZEN", {})
        session.add(snapshot)
        session.add_all(components)
        await session.flush()
        if aggregation is not None:
            session.add_all(
                _metric_models(snapshot.source_snapshot_id, extract.roster.dataset_id, aggregation)
            )
            session.add_all(
                _entry_models(snapshot.source_snapshot_id, extract.roster.dataset_id, aggregation)
            )
            session.add_all(_mapping_models(snapshot.source_snapshot_id, settings, actor, occurred_at))
            reconciliations = _reconciliation_models(
                snapshot.source_snapshot_id, extract, aggregation, mismatch_counts, occurred_at
            )
            session.add_all(reconciliations)
            await session.flush()

        passed = aggregation is not None and all(value == 0 for value in mismatch_counts.values())
        if passed:
            snapshot.status = "RECONCILED"
            snapshot.reconciled_at = occurred_at
            await session.flush()
            snapshot.status = "FROZEN"
            snapshot.frozen_at = occurred_at
            await session.flush()

        session.add(
            AuditEvent(
                event_id=uuid7(),
                event_type="SOURCE_SNAPSHOT_BUILT",
                actor_id=actor.user_id,
                acting_role=Role.DATA_MAINTAINER.value,
                role_snapshot={
                    "schema_version": "1.0",
                    "user_id": actor.user_id,
                    "department_id": actor.department_id,
                    "roles": sorted(role.value for role in actor.roles),
                },
                object_type="SOURCE_SNAPSHOT",
                object_key={"source_snapshot_id": str(snapshot.source_snapshot_id)},
                detail={
                    "fiscal_year": fiscal_year,
                    "status": snapshot.status,
                    "field_mapping_version": settings.source_field_mapping_version,
                    "mismatch_counts": mismatch_counts,
                    "excluded_zero_incomplete_rows": (
                        aggregation.excluded_zero_incomplete_rows if aggregation else {}
                    ),
                },
                request_id=request_id,
                occurred_at=occurred_at,
                annual_budget_id=None,
                budget_revision_id=None,
            )
        )
    return _build_result(snapshot, passed, mismatch_counts)


def _chain_is_aligned(extract: SourceExtract, max_lag_minutes: int) -> bool:
    lag = extract.summary.last_execution_at - extract.roster.last_execution_at
    return timedelta(0) <= lag <= timedelta(minutes=max_lag_minutes)


def _component_hashes(extract: SourceExtract, settings: Settings) -> dict[str, str]:
    common = {
        "schema_version": "1.0",
        "field_mapping_version": settings.source_field_mapping_version,
        "season_start": extract.fiscal_season_start,
        "season_end": extract.fiscal_season_end,
    }
    return {
        "roster": content_hash(
            {
                **common,
                "dataset_id": extract.roster.dataset_id,
                "version": extract.roster.version,
                "last_execution_at": extract.roster.last_execution_at.isoformat(),
                "selected_row_count": len(extract.roster_rows),
            }
        ),
        "summary": content_hash(
            {
                **common,
                "dataset_id": extract.summary.dataset_id,
                "version": extract.summary.version,
                "last_execution_at": extract.summary.last_execution_at.isoformat(),
                "latest_partition": extract.latest_summary_partition,
                "selected_row_count": len(extract.summary_rows),
            }
        ),
    }


def _aggregate_hashes(aggregation: object) -> dict[str, object]:
    from packages.source_snapshot.aggregation import AggregationResult

    assert isinstance(aggregation, AggregationResult)
    return {
        "roster": {
            code: metric_map_hash(values) for code, values in sorted(aggregation.roster_metrics.items())
        },
        "summary": {
            code: metric_map_hash(values) for code, values in sorted(aggregation.summary_metrics.items())
        },
        "entry_history": entry_map_hash(aggregation.entry_history),
        "excluded_zero_incomplete_rows": aggregation.excluded_zero_incomplete_rows,
    }


def _components(
    snapshot_id: uuid.UUID,
    extract: SourceExtract,
    settings: Settings,
    hashes: dict[str, str],
    occurred_at: datetime,
) -> list[SourceSnapshotComponent]:
    return [
        SourceSnapshotComponent(
            source_snapshot_component_id=uuid7(),
            source_snapshot_id=snapshot_id,
            dataset_id=extract.roster.dataset_id,
            dataset_name=extract.roster.name,
            partition_value=(f"season={extract.fiscal_season_start}..{extract.fiscal_season_end}"),
            task_batch_id=extract.roster.last_execution_at.isoformat(),
            extracted_at=occurred_at,
            row_count=len(extract.roster_rows),
            field_mapping_version=settings.source_field_mapping_version,
            content_hash=hashes["roster"],
        ),
        SourceSnapshotComponent(
            source_snapshot_component_id=uuid7(),
            source_snapshot_id=snapshot_id,
            dataset_id=extract.summary.dataset_id,
            dataset_name=extract.summary.name,
            partition_value=(
                f"dtdate={extract.latest_summary_partition};"
                f"season={extract.fiscal_season_start}..{extract.fiscal_season_end}"
            ),
            task_batch_id=extract.summary.last_execution_at.isoformat(),
            extracted_at=occurred_at,
            row_count=len(extract.summary_rows),
            field_mapping_version=settings.source_field_mapping_version,
            content_hash=hashes["summary"],
        ),
    ]


def _metric_models(
    snapshot_id: uuid.UUID, dataset_id: str, aggregation: object
) -> list[SourceMetricAggregate]:
    from packages.source_snapshot.aggregation import AggregationResult

    assert isinstance(aggregation, AggregationResult)
    result: list[SourceMetricAggregate] = []
    for metric_code, values in sorted(aggregation.roster_metrics.items()):
        for key, value in sorted(values.items(), key=lambda item: item[0]):
            record = {**key.as_dict(), **value.as_dict(), "metric_code": metric_code}
            result.append(
                SourceMetricAggregate(
                    source_metric_aggregate_id=uuid7(),
                    source_snapshot_id=snapshot_id,
                    source_dataset_id=dataset_id,
                    content_hash=content_hash(record),
                    **record,
                )
            )
    return result


def _entry_models(
    snapshot_id: uuid.UUID, dataset_id: str, aggregation: object
) -> list[EntryHistoryAggregate]:
    from packages.source_snapshot.aggregation import AggregationResult

    assert isinstance(aggregation, AggregationResult)
    return [
        EntryHistoryAggregate(
            entry_history_aggregate_id=uuid7(),
            source_snapshot_id=snapshot_id,
            source_dataset_id=dataset_id,
            content_hash=content_hash({**key.as_dict(), "business_new_student_count": count}),
            business_new_student_count=count,
            **key.as_dict(),
        )
        for key, count in sorted(aggregation.entry_history.items(), key=lambda item: item[0])
    ]


def _mapping_models(
    snapshot_id: uuid.UUID, settings: Settings, actor: Actor, occurred_at: datetime
) -> list[SourceMappingVerification]:
    definitions = (
        (
            "RENEWAL_NUMERATOR",
            ["续班人次", "专项班续班人次"],
            "SUM(续班人次)-SUM(专项班续班人次)",
        ),
        ("RENEWAL_DENOMINATOR", ["原班人次"], "SUM(原班人次)"),
        ("NEW_STUDENT_SHARE_NUMERATOR", ["业务新生人次"], "SUM(业务新生人次)"),
        (
            "NEW_STUDENT_SHARE_DENOMINATOR",
            ["招生人次(三分之二出勤口径)"],
            "SUM(招生人次(三分之二出勤口径))",
        ),
    )
    return [
        SourceMappingVerification(
            source_mapping_verification_id=uuid7(),
            source_snapshot_id=snapshot_id,
            business_metric_code=code,
            dataset_id=settings.roster_dataset_id,
            physical_fields=fields,
            business_definition=definition,
            etl_resource_id=settings.roster_etl_id,
            field_mapping_version=settings.source_field_mapping_version,
            verified_by=actor.user_id,
            verified_at=occurred_at,
            evidence_reference=(
                f"guancli:etl:{settings.roster_etl_id};mapping:{settings.source_field_mapping_version}"
            ),
        )
        for code, fields, definition in definitions
    ]


def _reconciliation_models(
    snapshot_id: uuid.UUID,
    extract: SourceExtract,
    aggregation: object,
    mismatch_counts: dict[str, int],
    occurred_at: datetime,
) -> list[SourceReconciliation]:
    from packages.source_snapshot.aggregation import AggregationResult

    assert isinstance(aggregation, AggregationResult)
    result: list[SourceReconciliation] = []
    for metric_code in (RENEWAL_RATE_REFERENCE, NEW_STUDENT_SHARE_REFERENCE):
        left_values = aggregation.roster_metrics[metric_code]
        right_values = aggregation.summary_metrics[metric_code]
        left = metric_totals(left_values)
        right = metric_totals(right_values)
        result.append(
            SourceReconciliation(
                source_reconciliation_id=uuid7(),
                source_snapshot_id=snapshot_id,
                metric_code=metric_code,
                left_source=extract.roster.dataset_id,
                right_source=extract.summary.dataset_id,
                left_numerator=left.numerator,
                left_denominator=left.denominator,
                right_numerator=right.numerator,
                right_denominator=right.denominator,
                passed=mismatch_counts[metric_code] == 0,
                evidence={
                    "schema_version": "1.0",
                    "comparison_grain": "季节财年+季度+赛道+产品+年级+科目+校区",
                    "exact_key_mismatch_count": mismatch_counts[metric_code],
                    "left_aggregate_hash": metric_map_hash(left_values),
                    "right_aggregate_hash": metric_map_hash(right_values),
                },
                checked_at=occurred_at,
            )
        )
    return result


def _build_result(
    snapshot: SourceSnapshot, passed: bool, mismatch_counts: dict[str, int]
) -> dict[str, object]:
    return {
        "source_snapshot_id": str(snapshot.source_snapshot_id),
        "status": snapshot.status,
        "reconciliation_passed": passed,
        "mismatch_counts": mismatch_counts,
        "links": {"source_snapshot": f"/api/v1/source-snapshots/{snapshot.source_snapshot_id}"},
    }
