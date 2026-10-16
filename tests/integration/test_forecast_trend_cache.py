"""Independent trend-cache verification in fresh, exact PostgreSQL test schemas.

The shared helper drops only the generated test schema in ``finally``. These
tests never query BI, edit a real budget, or update an immutable ledger record.
"""

import os
from datetime import UTC, datetime, tzinfo
from typing import Any, Self

import pytest
from sqlalchemy.exc import SQLAlchemyError

from packages.forecast import database_store as database
from packages.forecast.store import ForecastStoreError, fingerprint
from tests.integration.test_forecast_database_store import inputs, isolated_store

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires isolated PostgreSQL"
)

Json = dict[str, Any]
BASELINE_HASH = "b" * 64
SEASONS = ("暑假", "秋季", "寒假", "春季")
RAW_SOURCE_MARKER = "synthetic-raw-source-must-not-remain-in-the-trend-cache"


class Clock(datetime):
    instant = datetime(2026, 10, 16, 1, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(cls.instant.timestamp(), tz)


def trend_inputs(
    department: str, request_id: str, parent_id: str | None = None, *, amount: str = "100.125"
) -> Json:
    values = inputs(department, request_id, parent_id)
    values["snapshot"].update(
        actual_totals={season: {"actual_revenue": amount} for season in SEASONS},
        source_data={"summary_rows": [{"marker": RAW_SOURCE_MARKER, "value": amount}]},
    )
    values["calculation"].update(
        annual={"forecast_revenue": amount},
        quarters=[{"season": season, "forecast_revenue": amount} for season in SEASONS],
    )
    return values


def count_full_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    original = database.read_events

    async def counted(session: Any, department: str) -> list[Json]:
        reads.append(department)
        return await original(session, department)

    monkeypatch.setattr(database, "read_events", counted)
    return reads


async def test_hot_ranges_and_historical_cutoff_reuse_one_validated_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with isolated_store() as (store, _factory):
        department = "synthetic-trend-hot"
        monkeypatch.setattr(database, "datetime", Clock)
        Clock.instant = datetime(2026, 9, 1, 1, 30, tzinfo=UTC)
        first = await store.append_version(**trend_inputs(department, "first"))
        Clock.instant = datetime(2026, 10, 16, 1, 30, tzinfo=UTC)
        second = await store.append_version(
            **trend_inputs(department, "second", first["version"]["id"], amount="200.25")
        )
        reads = count_full_reads(monkeypatch)

        recent = await store.trend(department, baseline_hash=BASELINE_HASH, days="30")
        assert [point["sequence"] for point in recent["points"]] == [2]
        assert reads == [department]
        older_range = await store.trend(department, baseline_hash=BASELINE_HASH, days="90")
        assert [point["sequence"] for point in older_range["points"]] == [1, 2]
        historical = await store.trend(
            department,
            baseline_hash=BASELINE_HASH,
            days="all",
            through_version=first["version"]["id"],
        )
        assert [point["version_id"] for point in historical["points"]] == [first["version"]["id"]]
        assert historical["points"][0]["date"] == "2026-09-01"
        assert second["version"]["id"] not in str(historical)
        assert reads == [department], "changing only range/cutoff must not reread full BI snapshots"


async def test_returned_values_are_deep_copies_and_cache_contains_no_raw_source_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with isolated_store() as (store, factory):
        department = "synthetic-trend-copy"
        await store.append_version(**trend_inputs(department, "first"))
        reads = count_full_reads(monkeypatch)
        original = await store.trend(department, baseline_hash=BASELINE_HASH)
        original["points"][0]["periods"]["暑假"]["actual_revenue"] = "tampered"
        original["points"][0]["generation"] = "tampered"
        original["points"].append({"version_id": "injected"})

        fresh = await store.trend(department, baseline_hash=BASELINE_HASH)
        assert len(fresh["points"]) == 1
        assert fresh["points"][0]["periods"]["暑假"]["actual_revenue"] == "100.125"
        assert fresh["points"][0]["generation"] == "MANUAL"
        assert reads == [department]
        cached = store._trend_cache[(department, id(factory))][2]
        assert RAW_SOURCE_MARKER not in str(cached)
        assert all("source_data" not in point and "snapshot" not in point for point in cached)
        assert "rows" not in str(cached)


async def test_new_versions_and_confirmation_events_invalidate_without_losing_exact_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with isolated_store() as (store, _factory):
        department = "synthetic-trend-head"
        first = await store.append_version(**trend_inputs(department, "first"))
        reads = count_full_reads(monkeypatch)
        await store.trend(department, baseline_hash=BASELINE_HASH)
        assert len(reads) == 1

        second = await store.append_version(
            **trend_inputs(department, "second", first["version"]["id"], amount="200.25")
        )
        before_new_trend = len(reads)  # Appending itself validates the ledger too.
        current = await store.trend(department, baseline_hash=BASELINE_HASH)
        assert current["points"][-1]["version_id"] == second["version"]["id"]
        assert current["points"][-1]["status"] == "DRAFT"
        assert len(reads) == before_new_trend + 1
        before_cutoff = len(reads)
        historical = await store.trend(
            department, baseline_hash=BASELINE_HASH, through_version=first["version"]["id"]
        )
        assert historical["points"][-1]["version_id"] == first["version"]["id"]
        assert len(reads) == before_cutoff

        await store.confirm(
            department=department,
            actor="synthetic-reviewer",
            version_id=second["version"]["id"],
            request_id="confirm-second",
            request_hash=fingerprint("confirm-second"),
            reason="synthetic cache invalidation test",
        )
        before_confirmation_trend = len(reads)
        confirmed = await store.trend(department, baseline_hash=BASELINE_HASH)
        assert confirmed["points"][-1]["status"] == "CONFIRMED"
        assert len(reads) == before_confirmation_trend + 1
        prior = await store.trend(
            department, baseline_hash=BASELINE_HASH, through_version=first["version"]["id"]
        )
        assert prior["points"][-1]["status"] == "DRAFT"
        assert len(reads) == before_confirmation_trend + 1


async def test_wrong_baseline_never_uses_a_previously_warmed_projection() -> None:
    async with isolated_store() as (store, _factory):
        department = "synthetic-trend-baseline"
        await store.append_version(**trend_inputs(department, "first"))
        valid = await store.trend(department, baseline_hash=BASELINE_HASH)
        with pytest.raises(ForecastStoreError, match="基准不一致"):
            await store.trend(department, baseline_hash="different-baseline")
        assert await store.trend(department, baseline_hash=BASELINE_HASH) == valid


@pytest.mark.parametrize("failure", ["integrity", "database"])
async def test_failed_cold_validation_never_falls_back_to_the_old_head(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async with isolated_store() as (store, factory):
        department = "synthetic-trend-fail-closed"
        first = await store.append_version(**trend_inputs(department, "first"))
        await store.trend(department, baseline_hash=BASELINE_HASH)
        old_token = store._trend_cache[(department, id(factory))][0]
        second = await store.append_version(
            **trend_inputs(department, "second", first["version"]["id"], amount="200.25")
        )
        original = database.read_events

        async def failed(session: Any, requested_department: str) -> list[Json]:
            if failure == "database":
                raise SQLAlchemyError("synthetic cold-read outage")
            events = await original(session, requested_department)
            events[0]["version"]["snapshot"]["actual_totals"]["暑假"]["actual_revenue"] = "tampered"
            return events

        monkeypatch.setattr(database, "read_events", failed)
        expected = SQLAlchemyError if failure == "database" else ForecastStoreError
        with pytest.raises(expected):
            await store.trend(department, baseline_hash=BASELINE_HASH)
        assert store._trend_cache[(department, id(factory))][0] == old_token
        monkeypatch.setattr(database, "read_events", original)
        recovered = await store.trend(department, baseline_hash=BASELINE_HASH)
        assert recovered["points"][-1]["version_id"] == second["version"]["id"]
        assert recovered["points"][-1]["periods"]["暑假"]["actual_revenue"] == "200.25"


async def test_departments_and_changed_session_factories_cannot_share_a_cached_projection() -> None:
    async with isolated_store() as (store, first_factory), isolated_store() as (_other_store, other_factory):
        first_department, second_department = "synthetic-trend-a", "synthetic-trend-b"
        await store.append_version(**trend_inputs(first_department, "first", amount="101"))
        await store.append_version(**trend_inputs(second_department, "first", amount="202"))
        first = await store.trend(first_department, baseline_hash=BASELINE_HASH)
        second = await store.trend(second_department, baseline_hash=BASELINE_HASH)
        assert first["points"][0]["periods"]["暑假"]["actual_revenue"] == "101"
        assert second["points"][0]["periods"]["暑假"]["actual_revenue"] == "202"
        assert first["points"][0]["version_id"] != second["points"][0]["version_id"]

        store.session_factory = other_factory
        empty_other_schema = await store.trend(first_department, baseline_hash=BASELINE_HASH)
        assert empty_other_schema["points"] == []
        await store.append_version(**trend_inputs(first_department, "first", amount="303"))
        different_source = await store.trend(first_department, baseline_hash=BASELINE_HASH)
        assert different_source["points"][0]["periods"]["暑假"]["actual_revenue"] == "303"
        assert (first_department, id(first_factory)) in store._trend_cache
        assert (first_department, id(other_factory)) in store._trend_cache
        store.session_factory = first_factory
        assert await store.trend(first_department, baseline_hash=BASELINE_HASH) == first


async def test_projection_cache_is_bounded_even_for_many_empty_department_reads() -> None:
    async with isolated_store() as (store, _factory):
        for index in range(10):
            assert (await store.trend(f"synthetic-empty-{index}", baseline_hash=BASELINE_HASH))[
                "points"
            ] == []
        assert len(store._trend_cache) == 8
