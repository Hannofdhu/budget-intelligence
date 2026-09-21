import argparse
import asyncio
import json

from packages.config.settings import get_settings
from packages.source_snapshot.adapter import GuanCliSourceAdapter
from packages.source_snapshot.aggregation import aggregate_source_rows, mismatch_count


async def main() -> None:
    parser = argparse.ArgumentParser(description="只读验证观远来源适配器")
    parser.add_argument("--full", action="store_true", help="抽取 FY27 参考期并只输出聚合摘要")
    args = parser.parse_args()
    settings = get_settings()
    adapter = GuanCliSourceAdapter(settings)
    roster, summary = await asyncio.gather(
        adapter._metadata(settings.roster_dataset_id),
        adapter._metadata(settings.summary_dataset_id),
    )
    partition = await adapter._preview(
        settings.summary_dataset_id,
        columns=("dtdate",),
        limit=1,
        sort_desc="dtdate",
    )
    filters = ("季节财年 BT 20261,20264",)
    filtered_count = await adapter._preview_count(settings.roster_dataset_id, filters)
    filtered = await adapter._preview(
        settings.roster_dataset_id, columns=("季节财年",), filters=filters, limit=1
    )
    result: dict[str, object] = {
        "roster_id": roster.dataset_id,
        "roster_version": roster.version,
        "roster_batch": roster.last_execution_at.isoformat(),
        "summary_id": summary.dataset_id,
        "summary_version": summary.version,
        "summary_batch": summary.last_execution_at.isoformat(),
        "latest_dtdate": partition[0]["dtdate"],
        "filtered_row_count": filtered_count,
        "season_filter_ok": len(filtered) == 1,
    }
    result["season_row_counts"] = {
        str(season): {
            "roster": await adapter._preview_count(settings.roster_dataset_id, (f"季节财年 EQ {season}",)),
            "summary": await adapter._preview_count(
                settings.summary_dataset_id,
                (f"季节财年 EQ {season}", f"dtdate EQ {partition[0]['dtdate']}"),
            ),
        }
        for season in range(20261, 20265)
    }
    if args.full:
        extract = await adapter.extract(2027)
        aggregation = aggregate_source_rows(
            roster_rows=extract.roster_rows,
            summary_rows=extract.summary_rows,
            entry_fiscal_season=extract.fiscal_season_start,
        )
        result["full_extract"] = {
            "roster_rows": len(extract.roster_rows),
            "summary_rows": len(extract.summary_rows),
            "entry_history_keys": len(aggregation.entry_history),
            "excluded_zero_incomplete_rows": aggregation.excluded_zero_incomplete_rows,
            "metric_key_counts": {code: len(values) for code, values in aggregation.roster_metrics.items()},
            "exact_key_mismatch_counts": {
                code: mismatch_count(values, aggregation.summary_metrics[code])
                for code, values in aggregation.roster_metrics.items()
            },
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
