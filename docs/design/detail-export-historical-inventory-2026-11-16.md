# 定版预算及辅助模块：明细导出扫描表

日期：2026-11-16。范围限定为本表列出的 8 个模块；本次不改预算结果、计算、确认、发布、权限与前端表格排版。

## 统一约定

- Excel 使用业务原字段投影，不解析界面中的 `/`、`·`、百分号、换行或格式化文案。
- SKU 身份按 `campus / track / product / grade / subject` 五个字段分别成列，顺序为校区、赛道、产品、年级、科目。字段未提供时保留空值，不猜测或拼造维度。
- 人数、金额、率值、上下限、备注、状态各自成列。API 以 ppm 保存的比例继续导出原 ppm，列标题明确单位；以小数比例提供的聚合率继续使用 Excel 百分比格式。
- `0`、负数、`null`、布尔值及原始时间字段分别保留，界面显示精度不截断导出值。金额小数字符串交由既有 Excel 导出组件按金额列转换。
- 原筛选范围全部导出，分页只影响屏幕显示。原只读与编辑权限、选区复制、保存和确认按钮均保持原行为。
- 聚合表保留实际分组维度，不展开为另一份 SKU 明细。没有提供 SKU 字段的方案参数键不反向解析成 SKU。

## 扫描与处置

| 模块 / 表格 | 原问题或既有状态 | 本次处置与原字段路径 | 范围 / 边界 |
| --- | --- | --- | --- |
| `HistoricalBudgetPage.tsx` / 第 1—4 步逐季表 | 已使用显式字段导出，屏幕身份合并为两格；续班率 `-` 混入数值列 | 通过 `historicalBudgetExport.ts` 共用五维列；续班率有效数值保留，`-` 导出数值空白并在独立 `renewal_status` 列记录“不适用”；未知值分为“未填写 / 待核对” | 使用完整 `detailRows`，不随 25 行分页截断；真实 0 不变为空；保持隐藏季度筛选的既有行为 |
| `HistoricalBudgetPage.tsx` / 全年目标与定版结转收入 | DOM 回退读取格式化金额，会失去原小数精度 | 显式导出 `scope / target / actual / gap`，与屏幕使用同一现有汇总结果 | 全部门、赛道目标范围保持不变；不重新生成预算 |
| `HistoricalBudgetPage.tsx` / 招生与财务跨季度表 | 原 `quarterlyBusinessColumns` 已有完整五维原字段；招生续班率 `-` 需要拆成状态 | 招生表使用 `exportMetrics` 独立定义数值续班率和状态；屏幕 `metrics` 仍显示原 `-`；财务导出保持 | 不重复追加业务列，不改变横排阅读 |
| `historicalBudgetExport.ts` | 五维已拆列但顺序单独定义 | 使用共享 `skuDetailColumns`，其余列和单位不变 | 不增加曾隐藏的 Excel 单元格位置、来源凭证或财务结转分项 |
| `OpeningStudentsPage.tsx` / 冻结来源明细 | 赛道、产品合并；续入结果可能把年级和不续入原因混成文字；比例和理论人数来自格式化 DOM | 五维、`target_grade`、`exclusion`、`enrollment`、`renewal_rate_ppm`、`theoretical_old_students`、`source_batch` 独立列 | 使用当前关键词筛选的全部 `sourceRows`；理论人数沿用屏幕已有投影，无来源人数 / 率值时为 `null`，毕业或学科结束不伪造 0 |
| `OpeningStudentsPage.tsx` / 无来源覆盖明细 | 两个身份复合格与勾选控件依赖 DOM | 五维与 `zero_confirmed` 布尔值独立列 | 使用原 `uncovered` 全集；未勾选为 `false`，不自动确认或保存 |
| `OpeningStudentsPage.tsx` / 来源元信息 | 来源说明把财季、数据集、分区拼在一句中 | 保留说明，同时向既有公共元数据提供 `sourceObject / sourceFiscalSeason / sourceDataset / sourcePartition` | 未从说明猜财年或版本号；来源快照日期、精确来源标识沿用原上下文 |
| `BudgetPeopleBoard.tsx` / 季度招生预算 | DOM 回退会把续班率四舍五入为显示百分比 | 显式导出季度、老生、新生、总人次、续班阶段、原始续班率 | 复用已有季度聚合对象；缺季 `null` 与真实 0 分开 |
| `BudgetPeopleBoard.tsx` / 赛道年级跨季续班率 | 视觉汇总 / 百分比 DOM 回退 | 赛道、年级以及每季原始续班率独立列，合计单独行 | 保持当前筛选事实与现有加权聚合，不平均屏幕百分比 |
| `BudgetQuarterTable.tsx` / 校区、赛道科目季度表 | 已按每个 `rowDimension` 独立列、季度 × 指标独立数值列导出 | 扫描后保持既有实现 | 单位切换只改变已有金额导出单位；不增加虚假的其他维度，合计与缺值保持原语义 |
| `RateTableEditor.tsx` / 比例参数 | 校区与业务、年度与季度、允许区间、两类原因和状态混在显示格内 | 五维、`fiscal_year`、`quarter_name`、`target_fiscal_season`、`rate_type`、参考 / 当前 / 下限 / 上限 ppm、`boundary_reason`、`manual_reason`、`locked`、`boundary_confirmed` 分列 | 使用完整 `filtered`，不从复合文字拆字段；共享比例参数无科目时科目为空；不会修改输入或执行锁定命令 |
| `BusinessForm.tsx` / 通用只读结果表 | 先格式化再导出，金额精度 / 时间 / 原状态被文案覆盖；嵌套对象全部忽略 | `readResultDetailExport` 递归投影原始标量叶字段；嵌套版本时间、序号、状态、备注各列；根维度存在时补齐五维列 | 原搜索与 25 行屏幕分页不变，导出完整 `filtered`；沿用既有隐藏 ID / hash / stable key 与两项财务分项策略，不额外暴露隐藏字段 |
| `RebalancingPage.tsx` / 候选方案参数变更 | DOM 格式化人数和百分比，舍入精度且单位夹在数值中 | `object_type / object_stable_key / original_value / suggested_value / adjustment / unit` 分列 | 对象键是后端精确键，接口未提供五维，不按分隔符猜测；入口为人次，比例和折扣为 ppm；全部候选变更导出 |

## 验证记录

- TypeScript 检查通过。
- `historicalBudgetPage`、`auxiliaryDesign`、`legacyDesign`、`budgetPeopleBoard`、`budgetCampusBoard`、`budgetTrackSubjectBoard`、`budgetExport`、`budgetUiCleanup`、`revenueTerminology` 针对测试共 67 项通过。
- 覆盖 61 行超过一页时全量筛选、维度名称自身含 `/`、独立上下限和原因、布尔状态、原始 ppm、未舍入金额、负数、零、缺值、ISO 时间与嵌套版本字段。
- 既有 952 行 FY27 XLSX 回读与结转收入来源对账通过；校区和赛道科目季度表既有 XLSX 精度 / 空值 / 计数测试通过。
- 补充检查当前 952 行 FY27 续班率导出：数值列只有数字或空白，119 个原 `-` 均在独立状态列为“不适用”，45 个真实数值 0 均保留为数值 0；界面继续显示原 `-`。
- 测试使用本地合成输入或既有只读快照，无业务写入、确认、发布或 GitHub 操作。
