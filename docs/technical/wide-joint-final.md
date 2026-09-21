# 宽边界联合调平收口

日期：2026-09-09。状态：指定合成规模成功求解已验收；不等于全部生产场景上线。

## 验收结果

| 场景 | 规模及边界 | 结果 |
| --- | --- | --- |
| 单赛道联合调平 | 238 组合、952 季度行、三个校区、续班率 80%—81%、新生占比 20%—21% | 约 97 秒，三类 profile 均 OPTIMAL，3 个候选全部复算达标 |
| 两赛道联合调平 | 同等规模和边界，第二赛道价格为第一赛道的 1.1 倍 | 约 101 秒，三类 profile 均 OPTIMAL，候选全部复算达标 |
| 规模任务进程超时 | 实际两赛道宽边界计算，2 秒进程期限 | TimeoutError，事件循环持续响应，子进程全部回收 |
| 数据库集成回归 | 本地隔离库，19 项，包括原金额和 1.1256 亿元期初黄金链路 | 全部通过，包含应用、撤销、重新应用、正式确认及隔离发布 |

求解使用现有默认的单 profile 300 秒预算，未调整业务边界、100 ppm 比例步长、折扣步长、收入和综合折扣容差。最后一个季度收入也经过正式内核重算；不是仅凭求解器返回状态判定达标。

规模测试进程累计峰值 RSS 约 308 MiB（第二个场景记录为 323,141,632 字节）；这是测试进程连同依赖和前序场景的累计峰值，不是单场景增量内存、生产承诺或任意规模上限。验收期间部分回归并行进行，耗时为本机观测值，不是独占压测 SLA。

## 修改范围

1. 各比例类型的档位先独立计算，再组合，消除重复水位计算。不同决策状态不丢弃。
2. 同一运行三个偏好复用已完成的状态表，仅重新构建各自的贡献目标。与独立编译模型逐项对照一致。
3. 保留前轮的局部内核缓存、建模与求解共同计时、状态数量保护和进程级硬超时。超时继续返回未完成，不冒充不可行。
4. 缓存不跨运行；上下文、调节方向、状态限制变化时重编译，避免旧版本或旧边界结果复用。

## 可复现验证

最终回归汇总：237 项单元/性质测试、3 项规模测试、19 项数据库集成测试，共 **259 项通过，0 失败、0 错误、0 跳过**。Ruff、格式检查、mypy 和前端生产构建通过。最终窄边界联合比例用例为 18.33 秒（本机观测）；宽边界数字来自上表独立规模报告。

```sh
.venv/bin/pytest tests/unit tests/property -q --junitxml=output/business-scale-tests.xml
.venv/bin/pytest tests/scale -q -o junit_family=xunit1 --junitxml=output/wide-joint-scale-tests.xml
```

规模测试：[tests/scale/test_wide_joint_budget.py](../../tests/scale/test_wide_joint_budget.py)。模型等价与缓存失效验证：[test_business_profile_reuse.py](../../tests/unit/test_business_profile_reuse.py)。档位完整性：[test_rate_state_factorization.py](../../tests/unit/test_rate_state_factorization.py)。

证据：`output/business-scale-tests.xml`、`output/wide-joint-scale-tests.xml`、`output/wide-joint-integration-tests.xml`。本次规模报告包含耗时、人数规模和 RSS 属性；生成时 pytest 对 xunit2 属性兼容性提出警告，但 3 项均通过且属性已保存，复跑命令显式使用 xunit1 避免该格式警告。

数据库使用 `budget_acceptance_20260905_real`，迁移 `20260907_0026`。本轮启动的 PostgreSQL 已停止，测试卷保留；未连接线上 BI、未修改 FY27、未发布真实预算。前端生产构建通过。

## 不应混淆的剩余工作

- 后续已补充 238 组合、952 季度记录的单赛道 API 全链路验收，见 [238 明细 API 验收](scale-api-238-final.md)。该 API 用例固定入口结构，比例边界 0.1 个百分点；不可替代本文两赛道宽边界求解器验收。
- 不代表无限参数范围、全部校区共同解锁或所有复杂产品组合都能在 300 秒内求解；超限和超时仍需如实报告。
- 期初图形化维护入口和业务人员试用仍需推进。本轮只收口上述宽边界计算问题，不将自然滚动预测提前引入一期。
