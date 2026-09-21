# 第一期详细技术设计

当前交付状态（2026-09-09）：停止扩展新功能，按 [正式版收口计划](./release-readiness.md) 关闭上线阻断。以下内容是技术设计基线，不代表已全部生产上线。

状态：技术设计基线 v1  
业务依据：[第一期年度预算基线 PRD](../product/phase-1-annual-budget-baseline-prd.md)  
范围：只实现年度预算基线；自然滚动预测和滚动补缺继续留在后续阶段。

## 参考技术栈

- 前端：React + TypeScript；
- API 与计算 Worker：Python + FastAPI，使用同一领域与计算内核；
- 数据库：PostgreSQL；
- ORM 与迁移：SQLAlchemy + Alembic；
- 精确优化：OR-Tools CP-SAT，经独立计算内核复算；
- 作业队列：PostgreSQL 作业表，Worker 使用 `FOR UPDATE SKIP LOCKED` 领取；
- 文件导入导出：对象存储接口；本地开发可以文件系统实现替代。

具体依赖版本在建立代码仓库时由锁文件和兼容性测试冻结，不把易变化的版本号写进业务规格。

## 设计文档

1. [系统架构](./phase-1-architecture.md)
2. [数据模型与版本化](./phase-1-data-model.md)
3. [计算与调平引擎](./phase-1-calculation-engine.md)
4. [API、状态机、权限与审计](./phase-1-api-state-security.md)
5. [测试、验收追踪与交付计划](./phase-1-test-delivery.md)

## 不可破坏的实现约束

- 发布结果必须绑定不可变预算修订、参数版本和数据快照；
- 所有人次、比例和金额计算禁止二进制浮点；
- 中间结果只读，人工只能修改已开放的业务输入；
- 赛道和部门目标分别校验，不允许跨赛道抵消；
- 求解超时或异常是“计算未完成”，不能冒充“不可行”；
- 所有保存、锁定、确认、应用和发布均为独立审计事件；
- 新扩召构成和联报率不进入一期数据模型、页面、接口或求解器。

## 技术 ADR

- [ADR-0143：模块化单体与 PostgreSQL 作业队列](../adr/0143-use-a-modular-monolith-with-a-postgresql-job-queue.md)
- [ADR-0144：不可变修订清单](../adr/0144-version-budget-state-with-immutable-revision-manifests.md)
- [ADR-0145：精确业务数值单位](../adr/0145-use-exact-decimal-and-integer-business-units.md)
- [ADR-0146：确定性精确求解适配器](../adr/0146-use-a-deterministic-exact-optimization-adapter.md)
- [ADR-0147：并列比例杠杆的对称收入归因](../adr/0147-attribute-parallel-rate-contributions-symmetrically.md)
- [ADR-0148：折扣阶段采用精确整数收入模型](../adr/0148-compile-discount-with-exact-integer-revenue-arithmetic.md)
- [ADR-0149：候选应用创建差异组件版本并保留非杠杆输入](../adr/0149-apply-scenarios-as-immutable-delta-revisions.md)
- [ADR-0150：完整计算作业采用固定顺序和阶段级幂等重放](../adr/0150-orchestrate-full-calculation-with-stage-idempotency.md)
- [ADR-0151：正式方案在完整复算通过后落确认，发布保持独立](../adr/0151-confirm-only-after-recalculation-and-publish-separately.md)
