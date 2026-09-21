-- SQLite JSON1, in-memory only. :source_json is the read-only OOXML extraction.
-- Targets are the user-provided FY27 school budget notice, not group targets.
WITH rolling_cells AS (
  SELECT json_extract(value, '$.cells') AS cells
  FROM json_each(:source_json, '$.sheets.滚动表.rows')
  WHERE json_extract(value, '$.row') >= 2
    AND json_extract(value, '$.cells.A.value') IS NOT NULL
), seasons AS (
  SELECT 1 AS sequence, '暑假' AS period,
    SUM(json_extract(cells,'$.AS.value')) AS numerator_yuan,
    SUM(json_extract(cells,'$.AR.value')) AS denominator_yuan FROM rolling_cells
  UNION ALL
  SELECT 2, '秋季', SUM(json_extract(cells,'$.BJ.value')), SUM(json_extract(cells,'$.BI.value')) FROM rolling_cells
  UNION ALL
  SELECT 3, '寒假', SUM(json_extract(cells,'$.CA.value')), SUM(json_extract(cells,'$.BZ.value')) FROM rolling_cells
  UNION ALL
  SELECT 4, '春季', SUM(json_extract(cells,'$.CR.value')), SUM(json_extract(cells,'$.CQ.value')) FROM rolling_cells
), periods AS (
  SELECT * FROM seasons
  UNION ALL
  SELECT 0, '全年', SUM(numerator_yuan), SUM(denominator_yuan) FROM seasons
), targets(sequence,target_percent) AS (
  VALUES (0,86.0), (1,71.0), (2,89.0), (3,95.0), (4,95.0)
)
SELECT period, numerator_yuan, denominator_yuan, target_percent,
  numerator_yuan * 100.0 / NULLIF(denominator_yuan,0) AS actual_percent,
  numerator_yuan * 100.0 / NULLIF(denominator_yuan,0) - target_percent AS gap_pp,
  0.05 AS tolerance_pp
FROM periods JOIN targets USING(sequence)
ORDER BY sequence;
