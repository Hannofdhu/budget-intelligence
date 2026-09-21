# 第一期字段清单：年度预算基线

本清单描述业务字段，不预设数据库列名或接口协议。金额底层按元保存和计算；比例统一保存为数值比例，页面以百分比展示。`必填` 指该对象进入相应确认或发布步骤前必须有值，不表示创建空草稿时必须立即填写。

## 1. 年度预算版本 `AnnualBudgetVersion`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `budget_version_id` | 稳定ID | 是 | 系统 | 全局唯一，不因显示名称变化 |
| `fiscal_year` | 枚举/编码 | 是 | 创建人 | 一个版本只属于一个财年 |
| `parent_version_id` | 稳定ID | 否 | 系统 | 派生草稿指向父版本 |
| `revision_no` | 整数 | 是 | 系统 | 同一版本链单调增加 |
| `version_status` | 枚举 | 是 | 系统 | 草稿、已正式确认、已发布；阻断状态另列校验结果，不覆盖主状态 |
| `parameter_version_id` | 稳定ID | 是 | 系统 | 指向本次完整参数快照 |
| `created_by` / `created_at` | 用户/时间 | 是 | 系统 | 不可改 |
| `updated_by` / `updated_at` | 用户/时间 | 是 | 系统 | 随草稿修订更新 |
| `formal_confirmed_by` / `formal_confirmed_at` | 用户/时间 | 发布前是 | 部门预算负责人 | 输入变化后置空并留历史事件 |
| `published_by` / `published_at` | 用户/时间 | 已发布时是 | 部门预算负责人 | 发布后不可变 |
| `role_snapshot_id` | 稳定ID | 是 | 系统 | 记录关键动作发生时的角色成员 |

## 2. 学校版目标 `BudgetTarget`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `target_id` | 稳定ID | 是 | 系统 | 唯一 |
| `budget_version_id` | 稳定ID | 是 | 系统 | 绑定年度预算版本 |
| `target_level` | 枚举 | 是 | 业务配置 | 部门或赛道 |
| `track_id` | 维度ID | 赛道目标是 | 业务配置 | 部门目标为空 |
| `target_revenue_yuan` | 金额 | 是 | 学校版目标 | FY27：伴学 3,700 万、空间 7,600 万、部门 11,300 万 |
| `tolerance_yuan` | 金额 | 是 | 规则 | 固定 10,000 元 |
| `target_source_evidence` | 文本/附件引用 | 是 | 数据维护人员 | 保留学校版预算任务或正式依据 |
| `target_set_lock_status` | 枚举 | 是 | 系统 | 整套未锁定、整套已锁定、已失效；不允许部分锁定 |
| `target_set_locked_by` / `target_set_locked_at` | 用户/时间 | 已锁定时是 | 系统 | 数据维护人员锁定动作及角色快照 |
| `calculated_revenue_yuan` | 金额 | 是 | 系统 | 只读、完整精度汇总 |
| `variance_yuan` | 金额 | 是 | 系统 | 计算值减目标值 |
| `target_check_status` | 枚举 | 是 | 系统 | 缺口、达标、超额；逐目标独立判断 |

## 3. 数据快照 `ParameterSourceSnapshot`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `snapshot_id` | 稳定ID | 是 | 系统 | 参数版本引用 |
| `roster_dataset_id` / `roster_dataset_name` | ID/文本 | 是 | 系统配置 | 花名册来源 |
| `roster_batch_id` | 文本 | 是 | 数据任务 | 保留实际批次 |
| `summary_dataset_id` / `summary_dataset_name` | ID/文本 | 是 | 系统配置 | 指标汇总宽表来源 |
| `summary_dtdate` | 日期 | 是 | 系统 | 必须等于生成时唯一 `MAX(dtdate)` |
| `summary_batch_id` | 文本 | 是 | 数据任务 | 保留实际批次 |
| `data_cutoff_at` | 时间 | 是 | 系统 | 两源共同截止边界 |
| `refresh_chain_status` | 枚举 | 是 | 系统 | 对齐、等待宽表快照 |
| `reconciliation_status` | 枚举 | 是 | 系统 | 通过、未通过、未执行 |
| `reconciliation_detail` | 结构化明细 | 是 | 系统 | 记录两项比率的分子、分母对账结果 |
| `confirmed_by_data_maintainer` | 用户/时间 | 是 | 数据维护人员 | 表示接受最新分区质量，不替代源间对账 |

## 4. 角色成员 `RoleMembership`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `membership_id` | 稳定ID | 是 | 系统 | 唯一 |
| `user_id` | 用户ID | 是 | 权限管理员 | 不以姓名作主键 |
| `role_type` | 枚举 | 是 | 权限管理员 | 数据维护人员、部门预算负责人、权限管理员 |
| `effective_from` / `effective_to` | 时间 | 是/否 | 系统 | 支持历史角色快照 |
| `change_reason` | 文本 | 是 | 权限管理员 | 所有变更必填 |
| `changed_by` / `changed_at` | 用户/时间 | 是 | 系统 | 审计字段 |

系统始终校验至少一名权限管理员和一名部门预算负责人。

## 5. 预算组合 `BudgetCombination`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `combination_id` | 稳定ID | 是 | 系统 | 用于确定性排序 |
| `budget_version_id` | 稳定ID | 是 | 系统 | 绑定年度预算版本 |
| `track_id` | 维度ID | 是 | 历史识别/人工 | 具体值 |
| `product_id` | 维度ID | 是 | 历史识别/人工 | 具体值 |
| `entry_attribute` | 布尔/枚举 | 是 | 入口范围规则 | 入口、非入口 |
| `grade_id` | 维度ID | 是 | 历史识别/人工 | 具体值 |
| `subject_id` | 维度ID | 是 | 历史识别/人工 | 具体值 |
| `campus_id` | 维度ID | 是 | 历史识别/人工 | 具体值 |
| `origin_type` | 枚举 | 是 | 系统 | 历史组合、新增组合 |
| `enabled` | 布尔 | 是 | 数据维护人员草拟 | 只有确认启用才参与正式计算 |
| `change_reason` | 文本 | 条件必填 | 数据维护人员 | 新增、关闭、重新启用需填写 |
| `confirmation_status` | 枚举 | 是 | 系统 | 待确认、已确认、已失效 |
| `confirmed_by` / `confirmed_at` | 用户/时间 | 已确认时是 | 部门预算负责人 | 与保存动作分开 |
| `history_snapshot_id` | 稳定ID | 历史组合是 | 系统 | 指向成熟季节识别快照和入口规则版本 |

公共维度 `赛道＋产品＋年级＋科目＋校区` 在同一预算版本中必须唯一；若只因入口属性不同而重复，发布阻断。

## 6. 入口范围例外 `EntryScopeException`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `exception_id` | 稳定ID | 是 | 系统 | 唯一 |
| `fiscal_year` | 财年编码 | 是 | 数据维护人员 | 具体值 |
| `track_id` / `campus_id` / `product_id` | 维度ID | 是 | 数据维护人员 | 不支持通配 |
| `grade_id` / `subject_id` | 维度ID | 是 | 数据维护人员 | 不支持通配 |
| `exception_type` | 枚举 | 是 | 数据维护人员 | 人工纳入、人工排除 |
| `reason` | 文本 | 是 | 数据维护人员 | 审计必填 |
| `rule_version_id` | 稳定ID | 是 | 系统 | 随预算版本冻结 |
| `created_by` / `created_at` | 用户/时间 | 是 | 系统 | 审计字段 |

默认规则：财年第一个季节编码且七年级。判定优先级：人工排除 > 人工纳入 > 默认规则。

## 6A. 财年入口总值 `FiscalYearEntryTotal`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `entry_total_id` | 稳定ID | 是 | 系统 | 唯一 |
| `budget_version_id` / `fiscal_year` | 稳定ID/财年编码 | 是 | 系统 | 绑定年度预算草稿 |
| `fiscal_year_entry_total` | 非负整数人次 | 是 | 数据维护人员 | 锁定后才可生成整套校区入口建议 |
| `change_reason` | 文本 | 是 | 数据维护人员 | 首次录入及每次修改均必填 |
| `locked` / `locked_value` | 布尔/非负整数 | 是/锁定时是 | 数据维护人员 | 锁定值随整套校区入口确认，不另设独立审批 |
| `locked_by` / `locked_at` | 用户/时间 | 锁定时是 | 系统 | 保留操作人和时间 |
| `lock_role_snapshot_id` | 稳定ID | 锁定时是 | 系统 | 必须包含数据维护人员角色 |
| `campus_entry_confirmation_id` | 稳定ID | 整套确认后是 | 系统 | 部门预算负责人确认整套入口时绑定当前锁定值 |
| `invalidation_scope` | 结构化结果 | 修改或解锁时是 | 系统 | 整套校区入口、明细入口、人数、收入、班均、候选方案、正式方案确认、折扣解锁 |

## 7. 校区入口分配 `CampusEntryAllocation`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `allocation_id` | 稳定ID | 是 | 系统 | 唯一 |
| `budget_version_id` | 稳定ID | 是 | 系统 | 绑定草稿 |
| `campus_id` | 维度ID | 是 | 组合范围 | 每个校区一行 |
| `prior_year_entry_business_new_students` | 数值 | 有历史时是 | 花名册 | 该校区上一财年入口班业务新生人次 |
| `prior_year_total_entry_business_new_students` | 数值 | 有历史时是 | 花名册 | 全部校区同口径汇总分母 |
| `historical_entry_share` | 比例 | 有历史时是 | 系统 | 两项历史人次先汇总后相除 |
| `history_availability` | 枚举 | 是 | 系统 | 有历史、新校区、无可用历史、历史总分母为零 |
| `classroom_resource` | 数值或结构化说明 | 是 | 业务方 | 承载依据 |
| `sales_resource` | 数值或结构化说明 | 是 | 业务方 | 承载依据 |
| `manual_reserved_suggestion` | 整数人次 | 无历史时是 | 业务方 | 非负整数，先于历史校区分配预留，必须填写原因 |
| `total_manual_reserved_suggestion` | 整数人次 | 是 | 系统 | 不得超过财年入口总值 |
| `remaining_entry_for_historical_campuses` | 整数人次 | 是 | 系统 | 财年入口总值减无历史校区预留合计 |
| `suggested_entry_theoretical` | 小数人次 | 有历史时是 | 系统 | 剩余入口总值乘历史占比；不叠加资源权重 |
| `suggestion_source_type` | 枚举 | 是 | 系统 | 历史公式、业务人工设置 |
| `suggested_entry_floor` | 整数人次 | 有历史时是 | 系统 | 建议理论值的整数部分 |
| `suggested_entry_fraction` | 小数 | 有历史时是 | 系统 | 用于最大余数排序 |
| `suggestion_tail_assigned` | 整数标记 | 有历史时是 | 系统 | 本轮是否获得一人尾差 |
| `suggested_entry_value` | 整数人次 | 是 | 系统/业务方 | 历史校区由最大余数法生成；不自动生效 |
| `manual_suggestion_reason` | 文本 | 人工建议时是 | 业务方 | 新校区、无历史或总分母为零时必填 |
| `confirmed_entry_value` | 整数人次 | 是 | 业务方 | 不得为负 |
| `variance_from_suggestion` | 整数人次 | 是 | 系统 | 确定值减整数建议值 |
| `reason_category` | 枚举 | 条件必填 | 业务方 | 偏离时必填 |
| `reason_note` | 文本 | 条件必填 | 业务方 | 偏离时必填 |
| `set_confirmation_id` | 稳定ID | 已确认时是 | 系统 | 全部校区共享同一次整套确认 |

版本级另存 `campus_suggestion_sum`、`campus_suggestion_variance`、`campus_entry_sum`、`campus_entry_variance`。整套建议值与整套确定值分别严格汇总到已锁定财年入口总值，且全部必填信息完整后，才能完成相应计算或确认。

## 8. 入口明细分配 `EntryDetailAllocation`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `budget_version_id` / `combination_id` | 稳定ID | 是 | 系统 | 仅入口组合 |
| `campus_confirmed_entry` | 整数人次 | 是 | 校区入口分配 | 只读 |
| `historical_business_new_students` | 数值 | 存量组合是 | 花名册 | 上一财年入口班实际 |
| `campus_historical_business_new_students` | 数值 | 存量组合是 | 花名册 | 占比分母 |
| `historical_share` | 比例 | 存量组合是 | 系统 | 分子/分母，先汇总再计算 |
| `entry_initial_value` | 整数人次 | 是 | 系统/业务方 | 自动入口建议的起点 |
| `entry_soft_lower_bound` / `entry_soft_upper_bound` | 整数人次 | 是 | 系统 | 默认初始值的 80% / 120%，仅为常规建议范围 |
| `entry_hard_lower_bound` / `entry_hard_upper_bound` | 整数人次 | 是 | 业务方 | 教室、销售等资源形成的绝对边界 |
| `zero_initial_manual_plan` | 整数人次 | 初始值为0时是 | 业务方 | 必须显式填写计划值和原因 |
| `entry_boundary_reason` | 文本 | 人工边界时是 | 业务方 | 记录资源依据 |
| `new_product_share` | 比例 | 新产品是 | 业务方 | 同校区合计不得超过 100% |
| `reserved_entry_theoretical` | 小数人次 | 新产品是 | 系统 | 校区确定值乘比例 |
| `allocated_entry_theoretical` | 小数人次 | 是 | 系统 | 最大余数法前 |
| `allocated_entry_integer` | 整数人次 | 是 | 系统 | 同校区严格汇总到确定值 |
| `allocation_source_type` | 枚举 | 是 | 系统 | 历史占比、新产品人工比例、人工计划 |
| `source_snapshot_id` | 稳定ID | 是 | 系统 | 来源快照 |

## 9. 比例参数 `RateParameter`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `rate_parameter_id` | 稳定ID | 是 | 系统 | 完整参数键唯一 |
| `parameter_version_id` | 稳定ID | 是 | 系统 | 版本绑定 |
| `rate_type` | 枚举 | 是 | 系统 | 续班率、新生占比 |
| `rate_usage_mode` | 枚举 | 新生占比是 | 系统 | 反推输入、定量人数计算结果 |
| `fiscal_year` / `season_id` | 维度 | 是 | 系统 | 目标财年与季节 |
| `track_id` / `product_id` / `grade_id` / `campus_id` | 维度ID | 是 | 系统 | 历史精确匹配维度 |
| `renewal_relation_id` | 关系ID | 续班率是 | 系统 | 续班率必须保持相同关系 |
| `numerator_value` | 数值 | 历史来源时是 | 宽表/对账结果 | 汇总分子 |
| `denominator_value` | 数值 | 历史来源时是 | 宽表/对账结果 | 汇总分母 |
| `historical_rate` | 比例 | 历史来源时是 | 系统 | 分子/分母 |
| `minimum_sample_size` | 整数 | 是 | 配置 | 默认 30 |
| `source_type` | 枚举 | 是 | 系统 | 精确历史、去校区回退、人工指定 |
| `fallback_reason` | 文本 | 回退时是 | 系统 | 分母为零或样本不足等 |
| `system_value` | 比例 | 是 | 系统 | 默认基线 |
| `current_confirmed_value` | 比例 | 反推输入时是 | 系统/人工 | 0.01 个百分点精度；反推型新生占比不超过 99.99% |
| `confirmed_lower_bound` | 比例 | 自动调平前是 | 数据维护人员录入、负责人确认 | 不得高于当前值 |
| `confirmed_upper_bound` | 比例 | 自动调平前是 | 数据维护人员录入、负责人确认 | 不得低于当前值；反推型新生占比不得超过 99.99% |
| `boundary_confirmation_status` | 枚举 | 是 | 系统 | 草稿、已确认、已失效 |
| `locked` / `locked_value` | 布尔/比例 | 是/条件必填 | 两类角色 | 锁定值继续参与计算 |
| `manual_reason` | 文本 | 人工修改时是 | 操作者 | 必填 |

科目属于预算明细维度，但当前已确认的历史率精确匹配键未把科目列为独立率参数键；实现不得自行增加科目口径，若后续要按科目定参需新增业务决策。

## 9A. 计划新生人次 `PlannedNewStudentCount`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `planned_new_student_id` | 稳定ID | 是 | 系统 | 唯一 |
| `budget_version_id` / `combination_id` / `season_id` | 稳定ID | 是 | 系统 | 绑定完整季节预算明细 |
| `theoretical_old_students` | 数值人次 | 是 | 系统 | 必须为 0 才进入人工计划分支 |
| `new_student_volume_source` | 枚举 | 是 | 系统 | 入口分配、人工计划、新生占比反推 |
| `planned_new_students` | 非负整数人次/空值 | 人工计划时是 | 业务方 | 空值表示未处理；显式 0 表示不招生；负数或小数拒绝保存且不自动取整 |
| `manual_reason` | 文本 | 人工计划时是 | 业务方 | 必填并进入参数版本审计 |
| `calculated_new_student_share` | 比例/不适用 | 是 | 系统 | 新生大于 0 时为 100%；新生为 0 时不适用 |
| `locked` | 布尔 | 是 | 两类角色 | 数据维护人员和部门预算负责人均可锁定或解锁 |
| `locked_value` | 数值人次 | 锁定时是 | 系统 | 锁定后调平引擎不得改写 |
| `locked_by` / `locked_at` | 用户/时间 | 锁定时是 | 系统 | 保留操作时角色快照 |
| `formal_plan_confirmation_id` | 稳定ID | 正式确认时是 | 系统 | 不另设单独审批，纳入完整方案确认 |
| `invalidation_scope` | 结构化结果 | 修改或解锁时是 | 系统 | 人数、收入、班均、候选方案、正式方案确认、折扣率调平解锁 |
| `handling_status` | 枚举 | 是 | 系统 | 未处理空值、已填写待锁定、已锁定零值、已锁定正值 |

## 10. 班均参数 `ClassSizeParameter`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `parameter_version_id` | 稳定ID | 是 | 系统 | 绑定完整资源与财务参数版本 |
| `track_id` / `product_id` | 维度ID | 是 | 数据维护人员 | 联合唯一键 |
| `minimum_class_size` | 整数人次 | 是 | FY27 初值/数据维护人员 | 大于 0；新产品启用前必填 |
| `system_value` / `final_value` | 整数人次 | 是 | 系统/人工 | 保留差异 |
| `change_reason` / `maintained_by` / `maintained_at` | 文本/用户/时间 | 人工修改时是 | 数据维护人员/系统 | 草稿留痕 |
| `approval_status` | 枚举 | 是 | 系统 | 草稿、待确认、已确认、已失效 |
| `confirmed_by` / `confirmed_at` | 用户/时间 | 已确认时是 | 部门预算负责人/系统 | 参数版本内冻结；与保存草稿分开 |

## 11. 学费与结转参数 `RevenueParameter`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `parameter_version_id` | 稳定ID | 是 | 系统 | 与班均共同绑定完整资源与财务参数版本 |
| `track_id` / `product_id` / `season_id` | 维度ID | 是 | 数据维护人员 | 学费原价适用键 |
| `list_price_yuan` | 金额 | 是 | 数据维护人员维护、产品政策提供 | 不参与自动调平 |
| `budget_tax_conversion_factor` | 正数 | 是 | 数据维护人员维护、财务政策提供 | FY27 学校版初值 1.06；参数版本冻结，不参与自动调平 |
| `recognition_parameter_key` | 结构化键 | 是 | 数据维护人员维护、财务政策提供 | 按正式财务粒度 |
| `current_season_recognition_rate` | 比例 | 是 | 数据维护人员维护、财务政策提供 | 0% 至 100%；参数版本冻结 |
| `cross_season_recognition_rate` | 比例 | 是 | 系统 | 由 `1-current_season_recognition_rate` 派生，不允许独立维护 |
| `opening_carryover_revenue_yuan` | 金额 | FY27 Q1 是 | 数据维护人员维护、财务依据提供 | FY26 春季甩入 FY27 Q1 的不含税金额；按正式财务适用键维护 |
| `source_evidence` | 文本/附件引用 | 是 | 数据维护人员 | 保留产品或财务政策依据 |
| `change_reason` / `maintained_by` / `maintained_at` | 文本/用户/时间 | 修改时是 | 数据维护人员/系统 | 修改形成新参数版本草稿 |
| `parameter_status` | 枚举 | 是 | 系统 | 草稿、待确认、已确认、已失效 |
| `confirmed_by` / `confirmed_at` | 用户/时间 | 已确认时是 | 部门预算负责人/系统 | 与保存草稿分开；权限管理员不参与确认 |

## 12. 折扣参数 `DiscountParameter`

| 字段 | 类型 | 必填 | 来源/维护者 | 规则 |
|---|---|---:|---|---|
| `detail_discount_key` | 结构化键 | 是 | 系统配置 | 至少覆盖赛道、产品、年级、季节 |
| `detail_discount_rate` | 比例 | 是 | 参数/候选方案 | 自动调平仅按 0.1 个百分点步长 |
| `composite_target_level` | 枚举 | 是 | 学校版要求 | 部门、赛道；年度、季节 |
| `composite_target_rate` | 比例 | 是 | 学校版要求 | 上层目标，不因解锁而改变 |
| `composite_actual_rate` | 比例 | 是 | 系统 | 原价收入加权、完整精度 |
| `composite_variance_pp` | 百分点 | 是 | 系统 | 绝对值不超过 0.05 才达标 |
| `unlock_status` | 枚举 | 是 | 系统/负责人 | 未具备条件、待确认、已解锁、已失效 |
| `infeasibility_proof_id` | 稳定ID | 解锁时是 | 系统 | 绑定当前草稿和上游边界 |

## 13. 季节预算结果 `SeasonBudgetResult`

| 字段 | 类型 | 必填 | 来源 | 规则 |
|---|---|---:|---|---|
| `budget_version_id` / `combination_id` / `season_id` | 稳定ID | 是 | 系统 | 完整季节预算明细唯一 |
| `theoretical_old_students` / `theoretical_new_students` | 小数人次 | 是 | 系统 | 只读 |
| `integer_old_students` / `integer_new_students` | 整数人次 | 是 | 最大余数法 | 赛道＋校区＋季节分别保持汇总 |
| `new_student_rounding_mode` | 枚举 | 是 | 系统 | 定量整数保留、反推型最大余数分配 |
| `integer_total_students` | 整数人次 | 是 | 系统 | 老生加新生 |
| `realized_retention_rate` / `realized_new_student_share` | 比例 | 是 | 系统 | 取整后重算 |
| `rounding_variance` | 比例/人数 | 是 | 系统 | 展示，不静默补人 |
| `class_size_required` / `class_size_gap` | 整数人次 | 是 | 系统 | 0 人不触发异常 |
| `class_size_status` | 枚举 | 是 | 系统 | 未计划开班、不可成班、通过 |
| `discounted_tuition_yuan` | 金额 | 是 | 系统 | 原价乘折扣率 |
| `business_quarter_revenue_yuan` | 金额 | 是 | 系统 | 含税；整数总人次乘折后学费 |
| `business_quarter_net_of_tax_base_yuan` | 金额 | 是 | 系统 | 业务季度收入除以预算含税转换系数 |
| `current_recognized_revenue_yuan` | 金额 | 是 | 系统 | 本季税后基数乘当季结转比例 |
| `cross_season_revenue_yuan` | 金额 | 是 | 系统 | 本季税后基数乘跨季结转比例，递延至下一财季 |
| `quarter_recognized_revenue_yuan` | 金额 | 是 | 系统 | 当季确认税后收入与上一季跨季结转之和 |
| `next_fiscal_year_opening_carryover_yuan` | 金额 | 春季行是 | 系统 | FY27 春季跨季结转，供 FY28 Q1 引用，不计入 FY27 年度总结转 |

## 14. 候选调平方案 `RebalancingScenario`

| 字段 | 类型 | 必填 | 来源 | 规则 |
|---|---|---:|---|---|
| `scenario_id` | 稳定ID | 是 | 系统 | 唯一 |
| `budget_revision_id` / `parameter_version_id` / `snapshot_id` | 稳定ID | 是 | 系统 | 输入绑定，变化即失效 |
| `scenario_types` | 枚举集合 | 是 | 系统 | 平衡、留存侧、招生侧；收敛可多标签 |
| `target_contribution_share` | 比例组 | 是 | 规则 | 50/50、70/30、30/70 |
| `actual_contribution_amount` / `actual_contribution_share` | 金额/比例组 | 是 | 系统 | 取整后完整复算 |
| `transferred_amount` / `transfer_reason` | 金额/文本 | 条件必填 | 系统 | 一侧触界时透明展示 |
| `parameter_change_set` | 明细集合 | 是 | 系统 | 原值、建议值、边界、锁定、收入影响 |
| `target_result_set` | 明细集合 | 是 | 系统 | 部门与赛道目标分别校验 |
| `class_size_result` / `discount_result` | 结构化结果 | 是 | 系统 | 全部约束结果 |
| `selection_metrics` | 结构化数值 | 是 | 系统 | 贡献偏离、最大调整、总调整、稳定排序 |
| `scenario_status` | 枚举 | 是 | 系统 | 可行、不可行、已应用、已失效 |
| `infeasibility_reasons` | 明细集合 | 不可行时是 | 系统 | 不突破边界或扩大容差 |

## 15. 校验与审计

### `ValidationResult`

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `validation_id` | 稳定ID | 是 | 唯一 |
| `budget_revision_id` | 稳定ID | 是 | 绑定当前修订 |
| `validation_code` | 稳定编码 | 是 | 不以页面文案作主键 |
| `validation_group` | 枚举 | 是 | 目标、快照、组合、入口、参数、人数、班均、收入、折扣、权限、审计 |
| `scope_key` | 结构化键 | 是 | 指向具体目标或明细 |
| `severity` | 枚举 | 是 | 阻断、提示 |
| `status` | 枚举 | 是 | 通过、未通过、不适用 |
| `actual_value` / `expected_rule` | 结构化值/文本 | 是 | 可解释 |
| `repair_route` | 页面与对象定位 | 未通过时是 | 可直接跳转 |

### `AuditEvent`

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `event_id` | 稳定ID | 是 | 唯一、不可改 |
| `event_type` | 枚举 | 是 | 保存、确认、应用、撤销、锁定、解锁、发布、失效等 |
| `object_type` / `object_key` | 枚举/结构化键 | 是 | 指向业务对象 |
| `before_value` / `after_value` | 结构化值 | 条件必填 | 修改事件保留差异 |
| `system_value` / `manual_value` | 结构化值 | 条件必填 | 人工覆盖必须保留 |
| `reason` | 文本 | 条件必填 | 按业务规则 |
| `actor_id` / `acted_at` | 用户/时间 | 是 | 不可改 |
| `role_snapshot` | 角色集合 | 是 | 保留操作时身份 |
| `budget_version_id` / `parameter_version_id` | 稳定ID | 是 | 支持复算追溯 |

## 16. 详细技术设计对应关系

原业务冻结时保留的实现事项已经转入详细技术设计：

| 实现事项 | 设计结论 |
|---|---|
| 稳定 ID | 应用生成 UUIDv7，数据库使用 `uuid`；见[数据模型](../technical/phase-1-data-model.md) |
| 维度主数据与批次 | 只读适配器导入版本化映射，冻结外部编码、来源批次和映射哈希；见[数据模型](../technical/phase-1-data-model.md) |
| 求解器、队列和超时 | CP-SAT 精确适配器、PostgreSQL 作业队列，严格区分不可行与计算未完成；见[计算引擎](../technical/phase-1-calculation-engine.md) |
| 分页、缓存和并发 | 游标分页、不可变修订 ETag、无分布式缓存、`If-Match` 和幂等键；见[API 与状态](../technical/phase-1-api-state-security.md) |
| 审计与归档 | PostgreSQL 追加审计、只读导出和可验证归档包；一期不硬删除，未来保留期另行治理；见[数据模型](../technical/phase-1-data-model.md)与[API 与状态](../technical/phase-1-api-state-security.md) |

这些实现结论不改变本文所列粒度、公式、确认责任、精度和发布硬约束。
