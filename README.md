# 预算智解

面向学校业务的预算工具：查看已定版预算，预测当财年达成与缺口，再以已确认预测为基础拆解下一财年预算。

## 当前产品（2026-10-12）

当前为本机可用的开发版本，顶部统一切换三个模块。不是已部署的公网或内网生产服务，也不是下载后即可完整复刻业务数据的安装包。

| 模块 | 当前可用内容 |
| --- | --- |
| 查看预算 | FY27 学校版定版预算只读回看；收入、老生与新生人次、续班率看板；多维筛选、透视、明细复制与 XLSX 导出。 |
| 达成预测 | 只读获取观远数据，计算 FY27 全年及季度预计收入、达成率和缺口；支持参数调整、负责人确认及历史快照回看。成功更新保存新版本，失败保留原结果。 |
| 下一年预算 | 以已确认 FY27 预测准备 FY28 草稿；经营目标手工指定，支持多个入口年级、跨维度批量参数编辑、班量测算、新校区与新产品、看板与透视、手动调节、修改影响对比及版本回看。 |

下一年预算支持按经营目标自动调平，以及成班问题的整体调整建议。调平候选须经过现有锁定、参数边界、成班和目标校验；有限搜索不保证任意目标有解。成班建议可在未设置目标时预览，不表示收入已达标，也不代填经营目标；用户保存才追加草稿版本。此模块目前是独立草稿准备流程，不新增正式发布入口，不修改已定版数据或绕过正式发布规则。

### 在已配置的本机打开

- [查看 FY27 预算](http://127.0.0.1:5188/budget/workspace?archive=fy27&step=5)
- [FY27 达成预测](http://127.0.0.1:5188/budget/forecast?archive=fy27)
- [FY28 下一年预算](http://127.0.0.1:5188/budget/next-year)

以上链接只访问打开浏览器的那台电脑；需要该电脑已启动服务并具备对应数据和本机试用身份，不是在线演示地址。`启动本机试用.command` 用于已有环境，**不是新电脑的自动安装器**。现有启动与停止操作见 [本机试用说明](./本机试用说明.md)；其中 2026-09-10 的合成流程和旧验收记录属于历史说明，不代表当前三个模块的全部能力。

### 下载仓库能获得什么

仓库包含前后端源码、数据库迁移、依赖锁文件、自动测试和产品文档。真实预算、业务快照、访问凭据和本机配置不随源码分发：

| 数据或状态 | 当前保存位置 | 是否随 GitHub 下载 |
| --- | --- | --- |
| FY27 定版预算档案 | PostgreSQL `historical_budget_archive` | 否 |
| 预测输入快照、参数、结果及确认历史 | PostgreSQL `forecast_ledger_event` | 否 |
| 下一年预算草稿及版本历史 | 本机 `output/next-year-ledger/` | 否 |
| 原始 Excel、提取文件、本机配置与观远凭据 | 本机文件及独立授权配置 | 否 |

因此，**代码构建和 CI 通过，不等于已验证同事可以完整复刻当前产品**。完整迁移需要受控交付同一时点的数据库、下一年预算文件账本及对应部门配置，并在隔离环境校验；不能只复制 Excel 或只恢复数据库。更新预测还需要单独配置获授权的观远连接。不要将真实预算、学员数据或凭据上传公开仓库。

当前尚未完成全新电脑的一键安装、三个模块完整示例数据初始化、受控数据恢复交付及生产部署验收。本次说明不将这些待办描述为已上线能力。

### 产品说明

- [FY27 定版预算回看与数据库档案](./docs/product/fy27-excel-review.md)
- [预算看板与多维透视](./docs/product/budget-view-analysis-mvp.md)
- [FY27 达成预测与快照机制](./docs/FY27-达成预测-MVP.md)
- [下一年预算：功能、成班方案与验收边界](./docs/product/next-year-planning-v2.md)

这些文档保留历次实现和验收记录；旧版本的结果或限制不代表当前状态。年度预算基线原始规格见 [产品文档](./docs/product/README.md)，生产收口历史记录见 [正式版收口计划](./docs/technical/release-readiness.md)。

## 年度预算基线引擎

以下是已有年度预算后端能力，区别于“下一年预算”的独立草稿准备流程。功能清单不表示所有能力已完成生产部署或具备对应用户页面。

<details>
<summary>展开后端能力清单</summary>

- 来源组件批次、分区、抽取时间、行数、字段映射版本和内容哈希登记；
- 通过 `guancli` 只读抽取花名册与最新 `dtdate` 指标汇总宽表，不修改线上数据集或 ETL；
- 按“季节财年＋季度＋赛道＋产品＋年级＋科目＋校区”先汇总分子分母再逐键对账；
- 固化续班率和新生占比字段映射，生成七年级首季度入口历史汇总；
- 对账一致自动进入 `FROZEN`，明细键不一致保留 `DRAFT`，宽表批次未跟上时进入 `WAITING_FOR_SUMMARY`；
- 抽取按季节及维度值动态分块，每块先获取服务端行数再校验返回行数；超过 60,000 行单次上限或任一块不完整即明确失败，绝不使用样本；
- 维度不完整且五个预算相关分子分母均为 0 的行可计数排除；任一有效人次行维度缺失则阻断快照；
- 从冻结快照生成不可变比例参考版本：先跨科目汇总，再按“精确历史→去校区同类汇总→人工必填”回退；
- 续班率和新生占比共用可配置最小样本门槛（默认 30），保留精确分子分母、实际参考分子分母、回退原因和 ppm 系统初值；
- 生成正式历史参考前必须完整声明上一财年四季成熟，并把成熟证据引用写入版本内容哈希和审计；
- 由四类明确组件引用构成的不可变参数版本；
- 部门财年预算链、首个修订及基于 `If-Match` 的新修订派生；
- 从历史招生汇总生成不可变预算组合，七年级按默认入口范围标记，并由部门预算负责人确认；
- 数据维护人员可整套维护组合草稿；人工新增组合必须填写原因和班均参数引用，关闭或重新启用既有组合必须填写原因；
- 数据维护人员录入、说明并锁定非负整数财年入口总值；
- 在组合已确认后，以校区为第一分配维度，先预留无历史校区，再按历史业务新生人次占比分配并用稳定键执行最大余数取整；
- 校区确定值允许保存未调平草稿但不自动补偿；只有全量依据完整、偏离建议有原因且合计严格调平时才能由部门预算负责人整套确认；
- 现有校区内部组合变化保留已确认校区总值，新增或关闭整个校区则从新修订清单移除校区确认输入；
- 入口范围例外必须填写财年和全部五个业务维度，按“人工排除＞人工纳入＞七年级默认规则”形成并保存每个组合的判定证据；
- 入口明细只读取已确认校区入口确定值；新产品人工比例先预留，剩余额度按上一财年业务新生人次占比下拆，两段均用稳定组合键执行最大余数取整；
- 入口初始值大于 0 时生成 80%–120% 整数软边界；初始值为 0 时不伪造边界，并阻止其进入后续候选调平；
- 续班率、新生占比按 0.01 个百分点保存和校验；反推型新生占比最高为 99.99%；
- 比例边界必须由部门预算负责人按明确参数键确认，确认只绑定稳定键及上下界，不把当前值误当成边界本身；
- 数据维护人员和部门预算负责人均可整批锁定、修改并锁定或解锁比例；整批先校验后入库，任一明细失败时不创建新版本或新修订；
- 同财年派生精确继承锁定值和边界；跨财年只按去财年的完整键复制已锁定值，不做模糊匹配，未匹配或越界项进入不可发布的复制例外；
- 计划新生人次按“季节＋赛道＋产品＋入口属性＋年级＋科目＋校区”完整键维护，只接受非负整数和必填原因；
- 数据维护人员和部门预算负责人均可修改并锁定或解锁；显式锁定 0 与空值严格区分，正数只读派生为 100% 新生占比结果；
- 已有大于 0 的入口新生绝对量时整批拒绝人工计划；计划新生不参与自动调平或最大余数法，修改或解锁会失效人数起的全部下游结果；
- 按财年四个季节顺序推演人数：第一季从入口绝对量或锁定人工计划起步，后续季使用上一季取整总人次乘续班率得到老生，并用新生占比反推新生理论值；
- 老生和新生分别在“赛道＋校区＋季节”内使用精确分数和最大余数法取整；入口及锁定人工计划先保留，只有反推型新生参与尾差排序；
- 结果保存理论分数、九位解释值、整数值、实际续班率/新生占比和完整取整轨迹；缺少人工计划或比例时保存稳定阻断而不默认补零，结果不可发布；
- 班均、学费原价、预算含税转换系数和结转节奏作为一个完整不可变参数版本保存；数据维护人员整套替换草稿，部门预算负责人独立确认；
- 参数完整性从当前已启用组合反推：班均覆盖每个“赛道＋产品”，价格与结转覆盖对应四季，来源证据、变更原因和客户端整批哈希缺一即整批拒绝；
- 班均只在完整季节明细上消费取整后总人次：0 人为未计划开班，1 至班均减 1 为不可成班，达标为通过；不跨科目或校区合并，不自动补人；
- 已启用组合四季全零会生成发布阻断；人数未完成时不把空值当 0，而是保存“人数不可用”校验证据；
- 期初结转按“财季＋赛道＋产品＋年级＋科目＋校区”完整财务适用键逐条必填；旧的产品级期初结转不再被计算消费；
- 明细折扣按 0.1 个百分点步长保存，综合折扣在部门/赛道的年度/季度层级以原价收入加权；完整折扣版本必须由部门预算负责人独立确认，分母为 0 时为 N/A，绝对差超过 0.05 个百分点则阻止发布；
- 明细预算收入先按版本化含税转换系数转为不含税基数，再按当季/跨季比例结转；已是不含税的实际值使用独立类型，不做二次除税；
- 第一季加上上财年期初结转，第 2 至 4 季加上一季跨季结转；第四季甩出只作为下财年期初，不重复计入本财年；
- 学校版目标由数据维护人员按“一个部门合计＋全部具体赛道”整套导入并锁定，目标、来源证据和变更原因缺一即整批拒绝；目标范围与当前预算组合或年度收入汇总不一致时阻断计算；
- 部门及各赛道分别用完整元级精度计算“收入－目标”，绝对差恰好等于 10,000 元通过，超过一分钱即失败；赛道缺口与超额不得跨赛道抵消；
- 目标闭合状态区分容差内、缺口待调平、超目标待调平和混合待调平；目标闭合与整体可发布分开保存，上游班均或综合折扣阻断不会被目标达标掩盖；
- 组合、入口总值、校区建议、入口范围和入口明细均绑定新预算修订，可按精确版本 ID 查询完整内容；
- 命令幂等、部门隔离、角色校验、审计事件和数据库不可变触发器。

</details>

业务规格见 [产品文档](./docs/product/README.md)，实现约束见[详细技术设计](./docs/technical/README.md)。

## 开发环境启动

要求：Python 3.12、uv、Node 24+、npm、Docker。现有 `.command` 启动器面向 macOS；尚未完成 Windows 或 Linux 新机器的安装验收。

以下命令仅启动开发基础服务，不会导入真实预算、恢复预测历史或初始化当前产品的试用身份。默认配置下，依赖本机试用会话的页面不能直接使用；它们不是完整产品的首次安装指引。仅在全新开发目录执行配置复制，已有 `.env` 不要覆盖。

```bash
cp .env.example .env
UV_CACHE_DIR=.cache/uv uv sync --extra dev --frozen
cd apps/web && npm ci --registry=https://registry.npmjs.org && cd ../..
docker compose up -d postgres
UV_CACHE_DIR=.cache/uv uv run alembic upgrade head
```

API、Worker 和前端是三个常驻进程，分别在独立终端运行。以下命令均从项目根目录开始。

终端一：

```bash
UV_CACHE_DIR=.cache/uv uv run budget-api
```

终端二：

```bash
UV_CACHE_DIR=.cache/uv uv run budget-worker
```

终端三：

```bash
cd apps/web && npm run dev
```

开发服务地址（与上述本机产品的 5188 / 8018 端口不同）：

- Web：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/api/docs`
- 存活检查：`http://127.0.0.1:8000/api/v1/health/live`
- 就绪检查：`http://127.0.0.1:8000/api/v1/health/ready`

版本化 API：

- `POST /api/v1/source-snapshots:build`（异步读取真实来源）
- `GET /api/v1/jobs/{id}`
- `POST /api/v1/source-snapshots`
- `POST /api/v1/source-snapshots/{id}:reconcile`
- `POST /api/v1/source-snapshots/{id}:freeze`
- `POST /api/v1/source-snapshots/{id}/rate-reference-versions`
- `GET /api/v1/rate-reference-versions/{id}`
- `GET /api/v1/rate-reference-versions/{id}/references?limit=&cursor=`
- `POST /api/v1/parameter-versions`
- `POST /api/v1/annual-budgets`
- `POST /api/v1/revisions/{id}:derive`
- `POST /api/v1/revisions/{id}/targets:replace-and-lock`
- `GET /api/v1/target-set-versions/{id}`
- `POST /api/v1/revisions/{id}/combinations:generate-draft`
- `GET /api/v1/combination-set-versions/{id}`
- `POST /api/v1/revisions/{id}/combinations:replace-draft`
- `POST /api/v1/revisions/{id}/combinations:confirm`
- `POST /api/v1/revisions/{id}/entry-total:save-and-lock`
- `GET /api/v1/entry-total-versions/{id}`
- `POST /api/v1/revisions/{id}/campus-entry:generate-suggestions`
- `GET /api/v1/campus-entry-suggestion-versions/{id}`
- `POST /api/v1/revisions/{id}/campus-entry:replace-draft`
- `GET /api/v1/campus-entry-set-versions/{id}`
- `POST /api/v1/revisions/{id}/campus-entry:confirm`
- `POST /api/v1/revisions/{id}/entry-scope:replace-draft`
- `GET /api/v1/entry-scope-versions/{id}`
- `POST /api/v1/revisions/{id}/entry-scope:confirm`
- `POST /api/v1/revisions/{id}/entry-detail:calculate`
- `GET /api/v1/entry-detail-set-versions/{id}`
- `POST /api/v1/revisions/{id}/rate-parameters:replace-draft`
- `GET /api/v1/rate-parameter-versions/{id}`
- `POST /api/v1/revisions/{id}/rate-boundaries:confirm`
- `POST /api/v1/revisions/{id}/rate-parameters:lock`
- `POST /api/v1/revisions/{id}/rate-parameters:change-and-lock`
- `POST /api/v1/revisions/{id}/rate-parameters:unlock`
- `POST /api/v1/revisions/{id}/rate-parameters:copy-cross-year-locks`
- `POST /api/v1/revisions/{id}/planned-new-students:change-and-lock`
- `POST /api/v1/revisions/{id}/planned-new-students:unlock`
- `GET /api/v1/planned-new-student-versions/{id}`
- `POST /api/v1/revisions/{id}/headcounts:calculate`
- `GET /api/v1/headcount-result-versions/{id}`
- `POST /api/v1/revisions/{id}/resource-finance:replace-draft`
- `POST /api/v1/revisions/{id}/resource-finance:confirm`
- `GET /api/v1/resource-finance-parameter-versions/{id}`
- `POST /api/v1/revisions/{id}/class-sizes:validate`
- `GET /api/v1/class-size-result-versions/{id}`
- `POST /api/v1/revisions/{id}/discount-parameters:replace-draft`
- `POST /api/v1/revisions/{id}/discount-parameters:confirm`
- `GET /api/v1/discount-parameter-versions/{id}`
- `POST /api/v1/revisions/{id}/revenues:calculate`
- `GET /api/v1/revenue-result-versions/{id}`
- `POST /api/v1/revisions/{id}/target-closures:validate`
- `GET /api/v1/target-closure-result-versions/{id}`
- 对应来源快照、参数版本、预算链和修订的精确 ID 查询接口

所有写命令必须携带 `Idempotency-Key`。修订派生还必须携带值为父修订 UUID 的 `If-Match`。本地开发身份头为 `X-User-ID`、`X-Department-ID` 和 `X-Roles`。

也可以构建并启动容器基础服务：

```bash
make container-build
docker compose --profile app up --no-build
```

此路径不自动执行数据库迁移、业务数据导入或本机试用身份初始化，不能作为完整产品的一键部署指引。容器构建命令会使用临时英文路径作为构建上下文，以兼容 macOS 上 Docker/OrbStack 对中文项目路径的限制；源文件仍只从当前项目读取。

Worker 镜像固定包含 `@guandata/guanskill@0.1.14`（其固定的 `guancli` 组件为 `1.0.40`）。观远 profile 和访问凭据不进入镜像、作业载荷、审计或日志，部署时必须以只读密钥挂载到 Worker 运行环境。

## 验证

```bash
make check
make migrate-check
make test-db
make audit
node analysis/validate_documentation.mjs
```

`make check` 与 GitHub CI 均检查完整的 `apps packages tests` 类型，并运行前端测试；不以部分文件检查替代全量验收。CI 将格式、类型、运行测试、构建、依赖审计和文档校验分别展示，任何一项失败仍会使整个检查失败。只有实际生成 JUnit 后才派生并上传验收报告，未执行的测试不会显示通过。

`make migrate-check` 会回退数据库结构，`make test-db` 会写入合成测试数据；**只可对全新隔离测试库执行**。先显式设置 `BUDGET_ENVIRONMENT=test`、`BUDGET_AUTH_MODE=dev_headers` 和指向隔离库的 `BUDGET_DATABASE_URL`，不得使用产品库或已定版预算所在数据库。

前端安装及审计固定使用 npm 官方仓库，避免本机镜像缺失安全审计接口；依赖版本及完整性由锁文件固定，审计失败不得忽略。

AB-050～AB-061 与候选撤销的执行验收（先迁移独立测试库，并设置 `BUDGET_DATABASE_URL`）：

```bash
BUDGET_RUN_DB_TESTS=1 .venv/bin/pytest --junitxml=output/acceptance/pytest.xml -o junit_family=xunit1
.venv/bin/python analysis/report_acceptance.py --junit output/acceptance/pytest.xml
```

追踪清单只保存测试绑定；报告由真实执行结果生成，缺失、跳过或失败不得显示通过。真实合成预算的端到端测试位于 `tests/integration/test_rebalancing_real_journey.py`；不代表已完成 FY27 Excel 影子核验或全部 96 项业务验收。

本地 `dev_headers` 身份模式仅供开发。生产环境启动时会拒绝该模式，必须接入统一 OIDC 身份适配器。
