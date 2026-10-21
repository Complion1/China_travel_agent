# Module 11：持久执行与恢复 Vibe Coding 执行契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次仅接入M09的planning.model_invocation=17；通用预算SQL无需新列。阶段计数存M09新版Artifact，专用诊断记录不是恢复证据，模型节点non_recoverable不变。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 适用对象：后续正式实施、修复、续执行、审查和收尾模型
>
> 权威设计：[`11_durable_execution_and_recovery.md`](11_durable_execution_and_recovery.md)
>
> 共享规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)

本文只规定“如何实施和验收”，不重新定义业务与持久化语义。实现者必须按“文档路径 + 精确章节/AC/阶段”读取，不得把本文摘要当成主设计替代品。

## 1. 权威引用与开工前置

### 1.1 每份实施提示词必须引用

每份实施、修复、续执行、审查或下一阶段提示词必须完整放在一个 Markdown 代码框中，并至少引用：

1. `docs/11_durable_execution_and_recovery.md`：本阶段消费的精确章节；
2. `docs/11_durable_execution_and_recovery_vibe_coding.md`：本阶段编号；
3. `docs/SHARED_IMPLEMENTATION_RULES.md`：第 1～9 节；
4. `docs/00_agent_master_design.md`：第 5～10 节；
5. `docs/IMPLEMENTATION_ROADMAP.md`：第 2、3、6、8 节；
6. `docs/IMPLEMENTATION_STATUS.md`：只核验当前事实，不作为设计真源；
7. 本阶段实际消费的直接依赖章节。

禁止在提示词中重复粘贴主设计已写明的大段状态机、表结构或恢复矩阵。引用之后，只补充本阶段目标、前置条件、分支、精确白名单、允许动作、测试矩阵、停止条件和报告格式。

### 1.2 直接依赖必读路由

| 依赖 | 实施前必须读取 |
|---|---|
| Module 04 | `04_workflow_state_and_artifacts.md` 第 2～4、6.6～6.7、10～13、15、17～19、21～26 节 |
| Module 05 | `05_requirement_subgraph.md` 第 2～3、6～16、17～23 节 |
| Module 08 | `08_candidate_research_loop.md` 第 3、13～18、20～29、30.5 节 |
| Module 09 | `09_itinerary_planning_loop.md` 第 4～5、12～14、16～23 节及附录 A.1～A.2、B.1、B.4～B.6、C、D |
| Module 10 | `10_artifact_persistence.md` 第 2～5、7～14、16～22 节 |

还必须核对主设计任务列出的对应代码和合同测试。代码只证明当前事实，不能反向覆盖冻结设计。

补充但不属于路线图 `depends_on`：涉及 M05 initial adapter 时，只读 `01_requirement_and_dialogue.md` 第 3、5、7、13、15～16 节，确认 driver 最终验证的 `RequirementTurnInput`；涉及 D-11-67 的既有酒店外调恢复分类时，只读 `07_hotel_candidate_selection.md` 第 4、9～11、14～18、20～21、23～27 节。Module 11 生产代码不得导入 01/07，实施提示词必须把二者标为“传递合同/只读审计”，不得称为直接依赖或修改 Roadmap。

### 1.3 实施分支与 Git 边界

正式实现必须在包含已批准 Module 11 两份文档的最新本地 `main` 上创建：

```text
feature/module-11-durable-execution-implementation
```

若该名称已存在、基线不是最新干净 `main`、HEAD 与用户给定实施基线不一致、工作区 dirty 或 staged 非空，`STOP`。不得 reset、checkout 覆盖、clean、stash 或删除未知文件。

所有阶段默认：

- 实施模型不得 `git add`、stage 或 commit；
- 不得 merge、push、建 PR 或 deploy；
- 是否 commit 只能由对应阶段完成、独立审查通过后，用户另行明确授权；
- push、PR、deploy 始终需要单独授权；
- 两份 Module 11 设计文档在实施期间只读，禁止为迁就代码而修改。

### 1.4 全局文件边界

普通实现阶段只允许：

```text
app/durable_execution/**
tests/unit/durable_execution/**
tests/contract/durable_execution/**
tests/integration/durable_execution/**
tests/support/durable_execution_fakes.py
tests/support/durable_execution_worker.py
tests/support/run_module11_coverage.py
```

以下默认冻结：

```text
app/workflow_contracts/**
app/requirement_subgraph/**
app/requirements/**
app/hotel_selection/**
app/candidate_research/**
app/itinerary_planning/**
app/artifact_persistence/**
docs/00_agent_master_design.md
docs/IMPLEMENTATION_ROADMAP.md
docs/SHARED_IMPLEMENTATION_RULES.md
docs/01_requirement_and_dialogue.md
docs/04_workflow_state_and_artifacts.md
docs/05_requirement_subgraph.md
docs/07_hotel_candidate_selection.md
docs/08_candidate_research_loop.md
docs/09_itinerary_planning_loop.md
docs/10_artifact_persistence.md
docs/11_durable_execution_and_recovery.md
docs/11_durable_execution_and_recovery_vibe_coding.md
pyproject.toml
requirements.lock
```

`Dockerfile`、`compose.yaml`、`.env.example`、`README.md`、`docs/IMPLEMENTATION_STATUS.md` 只在 `DE-T09` 白名单中。当前依赖已经足够；若实现要求修改依赖或任何冻结文件，立即 `STOP` 并请求用户扩大范围。

### 1.5 全局安全规则

- 不读取根目录 `.env`，不输出、散列或验证真实 Secret；
- 不运行真实 DeepSeek、百度地图或其他收费/联网 API；
- 数据库集成只使用本项目唯一前缀的临时 PostgreSQL 资源；
- 不触碰 `process_simulation-platform-dev`；
- 不公开 Saver、pool、SQL、内部 Repository、journal/history/delete；
- 不用 skip、xfail、自动 rerun、私有状态访问、弱化断言或 sleep 碰运气；
- 小范围且明确的缺陷可在当前阶段白名单内修复；改变设计、扩大仓库范围或大改必须先征求用户同意。

## 2. 主设计到实现阶段映射

| 主设计章节 | 实施阶段 | 主要文件 | Gate |
|---|---|---|---|
| §5.2～5.7、§13 | DE-T01 | `__init__.py`、`errors.py`、`models.py`、`protocols.py`、`settings.py` | exact public/import/unit |
| §6～7 | DE-T02 | `migrate.py`、Alembic 文件、migration tests | 真实 PostgreSQL migration safety |
| §6.2、§9、§10、§13 | DE-T03 | `repository.py`、`budget.py` | coordination/budget/journal DB Gate |
| §9.3、§12.1～12.2 | DE-T04 | `serializer.py`、`checkpointer.py` | official Saver/fencing/checkpoint Gate |
| §8、§12.3～12.4、§12.6～12.7 | DE-T05 | `runtime.py` | start/resume/get/terminal/lifecycle Gate |
| §11、§12.5、§13 | DE-T06 | `runtime.py`、`repository.py`、`checkpointer.py` | recovery/artifact/fault Gate |
| §15～16、§18 | DE-T07 | 全模块测试与 support harness | full PostgreSQL/cross-process/coverage |
| §18～19、本文全部 | DE-T08 | 只读审查；确认缺陷的白名单内修复 | independent review + re-review |
| 共享规则 §7、主设计 §18 | DE-T09 | README/状态/Docker/Compose/示例环境 | MODULE-CLOSEOUT |

阶段执行组合固定：

- `DE-T00` 单独执行，只读；
- `DE-T01 + DE-T02` 一次实施，但 `DE-T02` migration Gate 独立判定，失败后不得继续；
- `DE-T03 + DE-T04` 一次实施并统一运行相应测试；
- `DE-T05 + DE-T06` 一次实施并统一运行相应测试；
- `DE-T07`、`DE-T08`、`DE-T09` 各自单独执行。

不得为了凑相邻两个阶段跳过迁移、安全、真实数据库、独立审查或 closeout Gate。

## 3. 预期实现树

### 3.1 生产文件

```text
app/durable_execution/
├── __init__.py
├── errors.py
├── models.py
├── protocols.py
├── settings.py
├── serializer.py
├── budget.py
├── repository.py
├── checkpointer.py
├── runtime.py
├── migrate.py
├── alembic.ini
└── migrations/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── m11_0001_create_durable_execution.py
```

### 3.2 测试与 support 文件

```text
tests/unit/durable_execution/
├── test_settings.py
├── test_models_and_protocols.py
├── test_serializer.py
├── test_budget.py
├── test_repository.py
├── test_checkpointer.py
├── test_runtime.py
├── test_recovery.py
└── test_lifecycle.py

tests/contract/durable_execution/
├── test_public_api.py
├── test_import_boundaries.py
├── test_execution_budget_manager_contract.py
├── test_requirement_subgraph_integration.py
└── test_security_contract.py

tests/integration/durable_execution/
├── conftest.py
├── test_migrations.py
├── test_bootstrap_and_restart.py
├── test_lease_and_fencing.py
├── test_budget_and_journal.py
├── test_artifact_reconciliation.py
├── test_checkpoint_and_lineage.py
├── test_cross_process_recovery.py
├── test_capacity_and_lifecycle.py
├── test_failures_and_cancellation.py
└── test_security.py

tests/support/
├── durable_execution_fakes.py
├── durable_execution_worker.py
└── run_module11_coverage.py
```

确实不需要的预期文件可以不创建；需要新增不同文件时先 `STOP`，说明职责、所属阶段和最小白名单扩展。禁止用文件拆分规避阶段范围。

## 4. 共用测试与判定规则

### 4.1 测试顺序

每个代码阶段按顺序运行：

1. 本阶段新增 unit；
2. 本阶段 contract；
3. 本阶段真实 PostgreSQL integration（若适用）；
4. Module 04/05/08/09/10 直接依赖受影响回归，以及 Module 01 传递合同/Module 07 只读审计回归；
5. 到 `DE-T07` 才执行 Module 11 完整 Gate、coverage、全部离线 unit/contract；
6. `DE-T09` 同步后重新构建并执行共享规则 §7 要求的最终 Gate。

前一层失败时，后一层 `NOT_RUN`，并注明阻断项。不得反复重跑外部/竞态测试直到偶然变绿。

### 4.2 pytest 通用参数

所有 pytest 命令必须保留：

```text
-q -p no:cacheprovider --import-mode=importlib
```

`collected 0 items`、collection error、命令非零退出均为失败。测试报告必须分别列出 passed、failed、skipped、xfail、rerun；后四项均必须为 0。

### 4.3 真实 PostgreSQL 编排

宿主编排器负责创建唯一命名的 PostgreSQL 16 container、network、volume 和 database，并注入：

```text
MODULE11_TEST_IDENTITY_TOKEN
MODULE11_TEST_PREFIX
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
```

`tests/integration/durable_execution/conftest.py` 只能验证 identity/prefix 并消费数据库，不能创建、停止或删除 Docker 资源。测试结束时宿主编排器只删除本次唯一前缀资源；不得执行广域 prune、不得复用普通 `postgres_data`，不得触碰其他项目。

迁移顺序必须是：空库 → `python -m app.durable_execution.migrate` → 测试。Runtime 启动前不得暗中迁移。跨进程 helper 只传安全 case ID、run UUID 和同步信号，不打印连接串、token 或正文。

### 4.4 coverage

`tests/support/run_module11_coverage.py` 必须单进程收集 `source=app.durable_execution` 的 line/branch 数据，并输出机器可读摘要。三项门槛分别为：

```text
line >= 70.00%
branch >= 70.00%
combined >= 70.00%
```

combined 公式引用主设计 §16.3。必须用未舍入分子/分母判定；不得 omit 源码、排除低覆盖文件、合并不同代码版本的 coverage 数据或用早期采集替代代码最终版。

### 4.5 review 发现格式

独立审查每条问题必须使用：

```text
文件：
章节或行号：
等级：P0 / P1 / P2 / P3
问题：
后果：
最小修复建议：
```

P0/P1/P2 未解决不得进入下一 Gate。P3 必须修复或记录接受理由。审查必须逐条对照两份 Module 11 文档和直接依赖，不能只写“测试通过”。

## 5. 分阶段执行契约

## DE-T00：只读预检

### 目标

证明实施基线、直接依赖、锁定包、目标路径、Docker 能力和测试入口真实可用，不产生文件修改。

### 前置条件

- 两份 Module 11 文档已经用户批准并位于最新本地 `main`；
- 用户已明确授权开始实施；
- 提供预期起始 HEAD 与分支名。

### 分支

只在干净 `main` 上核验；通过后才创建 `feature/module-11-durable-execution-implementation`。创建分支是本阶段唯一允许的状态变化。

### 文件白名单

无写入白名单。

### 允许动作

- `git rev-parse`、`git status --short`、`git diff --cached --name-only`；
- `rg`、`Get-Content`、包 metadata/公开源码签名读取；
- 检查 Docker/Compose 可用性，但不启动容器；
- 验证 §1.2 引用和 §3 目标路径状态；
- 核对锁定版本、MIT license、Saver 四表 migration 和公开 async API。

### 禁止动作

任何文件写入、依赖安装、测试、Docker 运行、数据库连接、stage/commit/merge/push/PR/deploy。

### 验证矩阵

| ID | 检查 |
|---|---|
| T00-GIT | root、main、HEAD、subject、dirty、staged |
| T00-REF | 所有权威引用与精确章节存在 |
| T00-DEPS | 锁定 LangGraph/Saver/psycopg/SQLAlchemy/Alembic/Pydantic 版本 |
| T00-SAVER | `AsyncPostgresSaver` 公开签名、四表 migration、license |
| T00-PATH | Module 11 实现目录尚未存在或与授权基线一致 |
| T00-CONFIG | Dockerfile、Compose、示例环境和现有 PostgreSQL 入口存在 |

### PASS

全部只读事实与实施授权一致，分支创建后 HEAD 不变、工作区干净、staged 为空。

### STOP

任一引用缺失；基线变化；已有未知 Module 11 文件；依赖真实 API 与主设计冲突；需要修改上位合同。

### 报告格式

使用 §6 通用报告，并追加：实际 Git root、创建前/后分支、HEAD/subject、依赖版本、Saver 方法/四表证据、缺失引用、目标路径状态、是否创建分支。

## DE-T01：公共合同、配置与安全错误

### 目标

实现主设计 §5.2～5.7、§13 的精确公共合同层，不连接数据库、不编译 Graph；包括 encoded command、start/phase provider、动态 task-context capabilities、可跨 operation 保存的 run-bound manager、Module 05 driver 注册和 async factory 签名。

### 前置条件

`DE-T00 PASS`；实施分支干净；两份 Module 11 文档未变化。

### 分支

`feature/module-11-durable-execution-implementation`。

### 文件白名单

```text
app/durable_execution/__init__.py
app/durable_execution/errors.py
app/durable_execution/models.py
app/durable_execution/protocols.py
app/durable_execution/settings.py
app/durable_execution/runtime.py
tests/unit/durable_execution/test_settings.py
tests/unit/durable_execution/test_models_and_protocols.py
tests/contract/durable_execution/test_public_api.py
tests/contract/durable_execution/test_import_boundaries.py
tests/contract/durable_execution/test_security_contract.py
tests/support/durable_execution_fakes.py
```

### 允许动作

- strict/frozen/extra-forbid DTO、枚举、Protocol、settings loader、零参数 error；
- `runtime.py` 在本阶段只定义主设计 §5.7 的精确 Runtime/factory 签名与无 I/O 合同骨架；不得连接数据库、编译 Graph 或宣称入口可用，完整行为必须在 DE-T05 替换完成；
- canonical bytes/digest helper 可放在拥有该合同的 `models.py`，不得新增万能 helper；
- 使用 fake 做 codec、start/phase provider、graph callback/capability/driver-registration 协议测试；
- 仅运行本阶段 unit/contract。

### 禁止动作

数据库 I/O、pool、Saver、migration、Graph compile/invoke、Artifact Repository 实现、Docker、依赖修改。

### 测试矩阵

| ID | 必测 |
|---|---|
| T01-DTO | 主设计 §5.4/§5.7 每个字段、顺序、annotation、strict/frozen/extra、budgeted_phases 顺序/唯一、enum、版本、canonical/digest、1 MiB 硬边界；以含 Unicode node 的同一合同做 Module 13 producer/Module 11 validator 固定 fixture，证明不同输入 dict key 顺序得到完全相同 UTF-8 bytes/digest，且 tuple/enum、无 BOM/空白/换行、无 Unicode normalization 规则一致 |
| T01-CODEC | 每个方法签名、exact type、registry、round-trip、node/generation；M01 turn adapter 的协议返回 `object` 且 M11 不导入 M01，真实 M05 driver 在副作用前 exact/fresh 拒绝错型；M05 `RequirementResume` fresh strict adapter、非 M05 context 拒绝、dynamic import/pickle 拒绝 |
| T01-PLAN | start 无 command 参数且必须非空/至少一个 requirements key/不得含其他 phase，phase 需要 state、纯同步、strict copy、run/key/phase mismatch 拒绝 |
| T01-CAP | rule.phase、动态 task context 越界、run-bound manager 跨 operation 复用、未知 node、ensure_phase_budget 签名、M05 limits=max_stored_runs、durable 组合 factory 调用省略 signal_sink 并使用默认 NoOp、driver 单次注册、raw coordinator/错配 limits/错配 driver 拒绝 |
| T01-ERR | 精确继承、零参数、无 cause/secret |
| T01-PUBLIC | `__all__` 精确，禁止 Saver/pool/Repository/SQL/history/delete |
| T01-IMPORT | 不导入业务私有模块、MCP、HTTP、供应商 SDK |

### PASS

本阶段测试全绿，公共导出与主设计 §5.2 精确一致，无 I/O side effect，diff 仅白名单。

### STOP

需要改变公共名称/签名、引入任意 JSON/动态 import、修改依赖或上游 DTO。

### 报告格式

使用 §6，并追加：公共导出清单、DTO/Protocol 清单、settings 环境键、error hierarchy、测试命令/计数、未实现边界。

## DE-T02：七表迁移、官方 setup 与结构 Gate

### 目标

实现主设计 §6～7 的独立 schema、七张业务表精确列/类型/null/default/check/PK/unique/FK/index、迁移锁、官方 Saver setup 和 Runtime 可复用的只读结构检查。

### 前置条件

`DE-T01 PASS`；锁定 dependency 事实仍为 Saver 四表；已准备唯一隔离空 PostgreSQL。

### 分支

同 `DE-T01`。

### 文件白名单

```text
app/durable_execution/migrate.py
app/durable_execution/alembic.ini
app/durable_execution/migrations/env.py
app/durable_execution/migrations/script.py.mako
app/durable_execution/migrations/versions/m11_0001_create_durable_execution.py
app/durable_execution/repository.py
tests/unit/durable_execution/test_repository.py
tests/integration/durable_execution/conftest.py
tests/integration/durable_execution/test_migrations.py
```

`repository.py` 此阶段只允许结构检查与 migration 共用的私有固定 metadata，不实现 T03 运行事务。

### 允许动作

- 手写 Alembic revision 与明确拒绝 downgrade；
- 实现 direct advisory migration lock 和显式 module entry；
- 以固定 search path 调官方 setup；
- 校验主设计 §6.2 的七张业务表每一列/类型/null/default/check/PK/unique/FK、§6.4 四个显式 index、`alembic_version`、四张 Saver 表及其版本；
- 在隔离空库、半迁移库、结构漂移库运行测试。

### 禁止动作

- Runtime 启动自动 migration；
- 复制或改写 Saver migration/SQL；
- 修改 Module 10 schema/迁移；
- 对日常数据库做测试性 DROP/TRUNCATE；
- 在 pytest fixture 中控制 Docker。

### 测试矩阵

| ID | 必测 |
|---|---|
| T02-EMPTY | 空库一次迁移得到精确 7+1+4 表；capacity 表为空，migration 不猜 Runtime limits |
| T02-REENTRY | 重复迁移安全且版本不漂移 |
| T02-LOCK | 两迁移者竞争，第二个 5 秒边界安全失败；两个独立 Python worker 对同一 migration target 计算完全相同 signed-bigint key，且与 capacity/run domain 隔离，禁止内建 `hash()`/进程随机算法 |
| T02-FAIL | Alembic 后/Saver setup 中断可重入，不假装完成 |
| T02-DRIFT | 每表逐一注入缺列、多列、type/null/default/check/PK/unique/FK/index/version/limits 漂移并拒绝；lineage 0/257、非法 blocked/journal reason、自由错误文本均拒绝，闭集每个合法值可写 |
| T02-NOAUTO | 未迁移库的普通检查拒绝且不创建任何对象 |
| T02-SCOPE | 所有对象只在 `durable_execution`，`artifact_store` 未变化 |
| T02-SEC | 日志/错误不含 DSN/password/SQL 参数 |

### PASS

真实 PostgreSQL migration Gate 全绿；失败案例无越界对象；Module 10 schema hash/结构未变化。只有 `DE-T02 PASS` 才可继续 T03。

### STOP

官方 setup 需要私有 API/SQL 修改；迁移无法在 advisory lock 下可重入；必须增加第八张业务表或第五张 Saver 表；隔离身份无法证明。

### 报告格式

使用 §6，并追加：临时资源前缀、migration 命令、revision、实际表清单、Saver migration 版本、并发锁结果、失败重入、结构漂移案例、资源清理结果。不得报告 Secret。

## DE-T03：协调 Repository、持久预算与 journal

### 目标

实现主设计 §6.2、§9、§10、§13 的容量、run、lease、pending、预算和 append-only journal 事务。

### 前置条件

`DE-T02 PASS`；隔离数据库已用当前源码迁移；T01/T02 diff 已复核。

### 分支

实施分支不变。

### 文件白名单

```text
app/durable_execution/repository.py
app/durable_execution/budget.py
app/durable_execution/models.py
app/durable_execution/errors.py
tests/unit/durable_execution/test_budget.py
tests/unit/durable_execution/test_repository.py
tests/contract/durable_execution/test_execution_budget_manager_contract.py
tests/integration/durable_execution/test_lease_and_fencing.py
tests/integration/durable_execution/test_budget_and_journal.py
tests/integration/durable_execution/test_capacity_and_lifecycle.py
tests/integration/durable_execution/test_failures_and_cancellation.py
tests/support/durable_execution_fakes.py
```

### 允许动作

- coordination pool、schema-qualified parameterized SQL、短事务；
- capacity singleton 并发首次初始化、全 limits 固化与逐项漂移拒绝；
- per-run advisory lock、lease acquire/renew/release/CAS；
- active/stored capacity 与 run lifecycle；
- start/phase budget activation、绑定 checkpoint、persistent BudgetManager 三方法；
- 主设计 §10.3 完整 journal FSM（全部允许边与全部拒绝边）和 commit-unknown reconciliation；
- deterministic barrier/fault seam。

### 禁止动作

Saver、Graph、业务 checkpoint 读写、Module 10 put、自动 recover、后台 scan、公开 Repository/SQL、持连接跨业务 await。唯一允许的 checkpoint table 访问是：为主设计 §6.2.1 的 capacity 首次初始化，对 schema-qualified 官方 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 三表分别执行只读 `EXISTS`/空库检查；不得读取 payload/metadata、列举历史或写入这些表。

### 测试矩阵

| ID | 必测 |
|---|---|
| T03-BUDGET | Module 04 三方法共享合同、applied/rejected/replayed/conflict |
| T03-ACT | start 静态计划必须非空/至少一个 requirements key/不得含其他 phase；requirements 首节点只重算/比较 ordinal-0 start activation，绝不调用 `plan_for_phase` 或插入第二份 activation；其后 budgeted phase 首次激活、confirmed-days 公式、旧 activation 重算、无预算 phase 无占位、future 缺席、越过安全门 |
| T03-ATOMIC | budget operation + journal 同提交/同回滚 |
| T03-CAP | 七张业务表及 Saver 三张业务数据表全空时并发初始化唯一获胜；单例缺失但任一 run/从表/checkpoint/blob/write 已存在时全局拒绝且不插 0/不重算；固定 capacity lock + 一致性读取下 stored/active 计数与全部/open runs 精确相等，范围内漂移也全局拒绝且不修复；limits 漂移拒绝、默认及 custom-low active/stored/operation/journal 边界与并发不超售 |
| T03-JFSM | 六状态、四条完整路径、每条合法边、状态字段约束、所有其余边、完整 history、exact retry；non_recoverable budgeted 正常成功走无 Artifact 字段的 permitted→checkpoint→completed，checkpoint 前 crash 不得走该边 |
| T03-LEASE | DB time、60/15、owner+token CAS、start 始终 AlreadyExists；除同一 Runtime local-driver poison 先 StateError 外，其余入口遇有效租约 busy；expiry takeover；两个独立 worker 对同一 canonical UUID 得到相同 run lock key，不同 UUID/domain 隔离；recover 新 lease/fence 必须先提交，随后才允许 operation scope/dispatch CAS/Graph/Saver/预算/journal/Artifact 写 |
| T03-COMMIT | unknown commit fresh read、一次 retry、final read、无第三写 |
| T03-PENDING | awaiting_input→accepted→dispatch_started→checkpointed/cleared 合法转换、时间/正文约束、CAS 冲突、marker 必须先于 Graph |
| T03-BLOCK | 未 checkpointed pending 原子转 `cleared`+`cleared_at`+清两类正文并保留 digest/type/size/既有时间，禁止正文为空却保留 accepted/dispatch_started；invalidate lease、release active、保留证据、主设计 §6.2 的 blocked reason 闭集逐值/优先级映射且不写错误文本；journal indeterminate 四个 reason 及 lease→cancel→artifact-missing→generic 优先级 |
| T03-SEC | 零参数错误、无内部 lease/token/SQL 泄露 |

### PASS

unit/contract/真实数据库测试全绿；事务失败无部分记录；manager 可作为 `ExecutionBudgetManager` 使用；diff 仅白名单。

### STOP

需要修改 Module 04 budget 协议、用轮询/sleep 保证 lease、需要跨 schema transaction/FK 或公开内部 Repository。

### 报告格式

使用 §6，并追加：每个事务 commit point、capacity 并发结果、lease/fence 观察结果、journal FSM 覆盖、commit-unknown fault 点、连接借用检查。

## DE-T04：serializer、官方 Saver 与 fencing checkpointer

### 目标

实现主设计 §9.3、§12.1～12.2：bounded serializer、官方 Saver 唯一存储、写 fencing、latest pointer 和 bounded lineage。

### 前置条件

`DE-T03 PASS`；官方公开 API 再核验未变化；checkpoint pool 参数可由公开构造满足。

### 分支

实施分支不变。

### 文件白名单

```text
app/durable_execution/serializer.py
app/durable_execution/checkpointer.py
app/durable_execution/repository.py
tests/unit/durable_execution/test_serializer.py
tests/unit/durable_execution/test_checkpointer.py
tests/contract/durable_execution/test_requirement_subgraph_integration.py
tests/contract/durable_execution/test_security_contract.py
tests/integration/durable_execution/test_checkpoint_and_lineage.py
tests/integration/durable_execution/test_lease_and_fencing.py
tests/support/durable_execution_fakes.py
```

### 允许动作

- 包装 Module 05 serializer，读写受实例 `limits.max_payload_bytes` 限制，1 MiB 仅为绝对硬上限；
- 构造私有 `AsyncPostgresSaver` 与 max-size 1 pool；
- 实现 BaseCheckpointSaver 公开 surface 的私有 decorator；
- 对 `aput/aput_writes` 做同一 per-run advisory lock fencing；
- 对内部 get/list 限制 canonical thread、root 及 root 实际引用的已注册 descendant、累计最多实例 `limits.max_lineage_checkpoints` 且绝不超过 256；
- root/descendant pointer、safe digest、lineage 与 metadata closed allowlist 校验。

### 禁止动作

- 自建 Saver 表或 persistence algorithm；
- 复制/修改官方 SQL；
- 调用官方私有 helper；
- 公开 `alist`、history、delete、checkpoint ID；
- 旧 checkpoint fallback；
- 锁内执行模型、地图或 Module 10 await。

### 测试矩阵

| ID | 必测 |
|---|---|
| T04-SERDE | delegate 类型、round-trip、默认 1 MiB-1/等于/超过、custom-low `max_payload_bytes` 的等于/超过、read oversize、contract/command/context 同一实例上限、pickle 拒绝 |
| T04-SAVER | 四表实际写读、sync durability 可观察、root+Module 05 descendant namespace/thread ID |
| T04-FENCE | current token 写成功；expired/stale/lost token 的 put/writes 均拒绝 |
| T04-RACE | old writer 持锁时 takeover 不能完成；释放后新 token 生效 |
| T04-LINE | `StateSnapshot.tasks`+child config/checkpoint_map+metadata parent、root/descendant latest、pointer repair、默认累计 256 与 custom-low `max_lineage_checkpoints` 的等于/超过边界、跨 thread/孤立/fork/divergence/missing/corrupt；动态 namespace 必须按主设计 §5.4 的 `/` 分段算法映射 |
| T04-NODES | root/child/nested qualified name、父 node 与 child 分别覆盖、原始 key 的 `/|:`/控制字符/空段/首尾空白/总长超限拒绝、锁定 LangGraph namespace `|`/task suffix `:` 精确解析、框架虚拟 node 排除、名称漂移拒绝 |
| T04-META | configurable 仅 thread/ns/可选 id/map，JSONB 仅 `source/step/parents`；caller canary、自定义 key、run_id、delta、update/fork 全拒绝 |
| T04-NOAPI | delete/history/raw Saver 无公共路径 |
| T04-M05 | 只用公共 factory/driver 的实际 Module 05 child 可 checkpoint/interrupt/resume；不访问 coordinator 私有状态 |

### PASS

官方 Saver 是唯一 checkpoint 存储实现；stale writer 无写入；lineage 无旧版恢复；所有 T03+T04 测试统一全绿。

### STOP

必须修改 Saver 私有 API/SQL；公开 saver surface 无法装饰；Module 05 serializer 无法承载根 Graph 合法值；fencing 与 takeover 无法用同一锁证明。

### 报告格式

使用 §6，并追加：delegate/Saver 类型、pool 参数、被装饰方法、fence race 证据、lineage case、官方表写入证明、未公开 surface 检查。

## DE-T05：Runtime 的 start/resume/get_state/terminal/close

### 目标

实现主设计 §8、§12.3～12.4、§12.6～12.7 的门面主路径，不实现一般 crash recover。

### 前置条件

`DE-T04 PASS`；T03+T04 统一回归全绿；Graph callback 和 capabilities 合同已冻结。

### 分支

实施分支不变。

### 文件白名单

```text
app/durable_execution/runtime.py
app/durable_execution/checkpointer.py
app/durable_execution/repository.py
app/durable_execution/budget.py
app/durable_execution/models.py
app/durable_execution/protocols.py
app/durable_execution/__init__.py
tests/unit/durable_execution/test_runtime.py
tests/unit/durable_execution/test_lifecycle.py
tests/contract/durable_execution/test_public_api.py
tests/contract/durable_execution/test_requirement_subgraph_integration.py
tests/integration/durable_execution/test_bootstrap_and_restart.py
tests/integration/durable_execution/test_capacity_and_lifecycle.py
tests/integration/durable_execution/test_failures_and_cancellation.py
tests/support/durable_execution_fakes.py
```

### 允许动作

- 工厂 startup check、两 pool ownership、one-time Graph callback/compile；
- node-bound controlled capabilities、phase 首 node 的 `ensure_phase_budget`、`requirement_subgraph_limits=max_stored_runs` 与 Module 05 embedded driver limits 精确注册；
- start/phase activation、bootstrap、两类 pause context/token、resume CAS、get_state、terminal repair；
- 强制 sync durability/recursion 256；
- close/context manager、local task/renewal 管理。

### 禁止动作

- Module 13 生产主图或业务 command；
- 自动启动恢复、lease wait/poll；
- 返回 checkpoint/interrupt internals；
- Runtime 创建/关闭 Module 10；
- 一般 `recover` 续跑逻辑（留 T06）。

### 测试矩阵

| ID | 必测 |
|---|---|
| T05-FACTORY | startup 顺序、graph callback 恰好一次、精确 capability surface、M05 factory spy 证明 signal_sink 参数省略/默认 NoOp 且 scope-before-marker 零信号、phase guard 必须先于外调/consume、requirements guard 不调用 `plan_for_phase` 且不重复插 activation、失败清 M11 不清 M10 |
| T05-BOOT | generation 0 decode+M01 turn adapter、M05 start scope 覆盖完整父 Graph 与全部 child result 解析、accepted 可安全重提、dispatch_started 无 checkpoint 阻断、checkpoint/marker gap；外调证据只注入 M11 可达的 pointer/journal/budget/已保存 ArtifactRef，不扫描 M10 任意行；scope 已进入后 marker 明确失败/commit unknown 使本 Runtime 该 run local_driver_poisoned，同 Runtime StateError 且不重入，新 Runtime 对 accepted/dispatch_started 分别恢复/阻断 |
| T05-PAUSE | M05/native 两类 token domain、context canonical、token 仅一次、DB 仅 digest、正文 checkpoint 后清；fault injection 覆盖 waiting checkpoint 已提交但 awaiting_input pending/context/token-digest 尚未插入，重启后不得生成 token/context、不得 Graph/driver 重入并必须 pending_command_invalid 阻断 |
| T05-RESUME | pending row lock、constant-time verify、codec→fresh M05 resume adapter、driver scope preflight、awaiting→accepted 与 accepted→dispatch_started 两次 CAS、marker 先于 Graph、scope 覆盖完整父 Graph/父 router/全部 child result 解析、node/generation/branch/hash/contract/activated-plan 精确匹配；scope 后 marker 失败同 Runtime 不重入、新 Runtime 唯一恢复 |
| T05-STATE | start existing 始终优先 AlreadyExists；同一 Runtime local-driver poison 的 resume/recover/get_state 在自持或遗留 lease 仍有效时也先 StateError，fresh Runtime 对同一 lease Busy；其他 active busy；bootstrap/no-checkpoint 只 StateError；waiting 无 token；无租约合法中间 checkpoint 只返回 fresh state 且不分类/续跑；closed 稳定、blocked 禁读；两段锁校验与 lease/pointer 竞态线性化 |
| T05-TERM | checkpoint→validate→close 顺序和 crash repair |
| T05-CLOSE | 幂等、共同 5 秒收束期限、cancel、无后台遗留 task、异常优先、pool 顺序、M10 未关闭 |
| T05-CONFIG | caller 无法覆盖 durability/recursion/config/thread/namespace/checkpoint ID |
| T05-RECUR | 确定性真实循环命中 256 后阻断；重复 recover 不能分段绕过 |

### PASS

除 crash recovery 外四公开入口的适用行为全部符合主设计；start/resume/get_state/close 重启集成全绿。

### STOP

需要公开 Graph/Saver、需要 Module 13 占位、首业务 node 前 checkpoint 无法证明、token 只能靠明文持久化恢复。

### 报告格式

使用 §6，并追加：factory/close ownership、bootstrap fault 点、token 持久化扫描、terminal repair、强制 config 证据、未实施 recover 分支。

## DE-T06：recover、Artifact 对账与阻断

### 目标

实现主设计 §11、§12.5、§13 的显式 crash recovery、三种 node 分类、Artifact 对账、失租例外和 blocked 语义。

### 前置条件

`DE-T05 PASS`；最小私有 Graph 可覆盖三种 recovery class；真实 M10 Repository 可供隔离测试。

### 分支

实施分支不变。

### 文件白名单

```text
app/durable_execution/runtime.py
app/durable_execution/repository.py
app/durable_execution/checkpointer.py
app/durable_execution/budget.py
app/durable_execution/models.py
app/durable_execution/errors.py
tests/unit/durable_execution/test_recovery.py
tests/unit/durable_execution/test_lifecycle.py
tests/contract/durable_execution/test_execution_budget_manager_contract.py
tests/contract/durable_execution/test_requirement_subgraph_integration.py
tests/contract/durable_execution/test_security_contract.py
tests/integration/durable_execution/test_artifact_reconciliation.py
tests/integration/durable_execution/test_cross_process_recovery.py
tests/integration/durable_execution/test_checkpoint_and_lineage.py
tests/integration/durable_execution/test_failures_and_cancellation.py
tests/support/durable_execution_fakes.py
tests/support/durable_execution_worker.py
```

### 允许动作

- 显式 recover 的完整决策顺序；
- replay_safe/journal_guarded/non_recoverable 判定；M05 注册父段下三个 qualified node `process_initial_turn/await_clarification/await_confirmation` 及既有 07/08/09 外调 service node 默认 non_recoverable；
- Module 10 wrapper 与 §11.2 全矩阵；
- journal_guarded put 必须有同 op permission；non_recoverable 上游 Artifact 无论同 op 是否已有 budget permission 都只透传验证且不得追加 artifact_confirmed，既有 budget journal 仍计入受管容量；
- 仅 accepted（dispatch marker 未提交）pending command 可精确重提；dispatch_started 无 checkpoint 时按 node 分类阻断/对账；checkpoint 已前进只清残留；
- 只收束失租前已进入 critical section 的 Artifact put；
- block transaction 与首次/后续异常映射；
- 确定性子进程测试。

### 禁止动作

- startup scan、后台 worker、自动轮询或自动多次 recover；
- 结果不确定时外部重调；
- 旧 checkpoint fallback；
- 对 blocked run 返回旧 state；
- 跨 schema FK/事务；
- 测试使用真实模型/地图。

### 测试矩阵

| ID | 必测 |
|---|---|
| T06-MATRIX | 主设计 §11.2 每格正反测试；先应用 node class，managed/unmanaged Artifact 不得混淆；07/08/09 non_recoverable 分别覆盖 permission 后、无 post-node checkpoint 的无 journal Artifact、`execution_permitted`+合法 Artifact、完整 journal+合法 Artifact，均阻断且不补 artifact journal/不继续；唯一关联且停在 execution_permitted 的受管 op 必须按四级 reason 优先级原子追加 indeterminate，无受管 op/无法唯一关联/非 permitted/已终态不得伪造；另覆盖 uninterrupted 合法 post-node checkpoint 后无 Artifact 字段的 checkpoint_confirmed/completed、可证明 checkpoint crash-gap 先独立提交 repair、再重新只读分类和第二次锁定取得 lease、只从 node 之后继续，以及合法 terminal checkpoint close repair |
| T06-CLASS | 三种 recovery class、M05 三个 qualified 真实 node 与 07/08/09 外调 node 默认 non_recoverable、unlisted/mismatch/parallel pending |
| T06-PENDING | generation 0 accepted 仅按 bootstrap 的 decode_initial→initial_state/turn→start scope 重提且 context 必须为空；generation>0 accepted 仅按 decode_resume→M05 resume adapter→resume scope 重提；两者不得混路由；dispatch_started 未前进阻断、已前进清残留；waiting 仅在 exact/完整 awaiting_input row 存在时返回 AwaitingInput，checkpoint→pending insert crash gap 必须 pending_command_invalid 阻断、零 token 生成/Graph 重入并释放 active；无需 token 明文 |
| T06-NORECALL | M05 `process_initial_turn/await_clarification/await_confirmation`、酒店选择、候选 action/judgment/map、规划 action/map 在 dispatch_started crash 后调用计数不增加；任意 Artifact 都不得反向重建 state，只有合法 terminal checkpoint 可修复关闭 |
| T06-LOST | 失租前已开始的原 op put 可在 M10 timeout 内收束；显式 close 只给共同 5 秒且超时取消、后续按原 op 对账；失租后尝试新 put 拒绝；所有 M11/新外调写禁止 |
| T06-BLOCK | 首次具体错误、后续 RunBlocked、active 释放、evidence 保留、plaintext 清除；block 前后断言唯一 permitted 受管 op 与 indeterminate 同事务封口，故障回滚时 lifecycle/journal/pending/lease/capacity 均不部分提交；多个/无法唯一关联的 permitted 负例保留原 journal 并以 classification/journal invalid 阻断，不伪造 transition |
| T06-CANCEL | 可证明外调前释放；permission/non_recoverable 开始后等 expiry；仅已开始 put shield 且不越过 close 共同期限；CancelledError 原样 |
| T06-CROSS | 进程崩溃、有效 lease Busy、受控 expiry 后唯一接管；barrier 证明可续跑分支的新 lease/fence commit 先于 scope/dispatch CAS/Graph；repair-only 在无有效租约+per-run advisory lock 下只修 pointer/pending/`checkpoint_confirmed` journal/close/capacity，不取得执行 lease、不调用 Graph且不产生其他写；repair 后继续必须先提交/释放第一把锁，再重跑只读分类并第二次锁定/复验/取 lease，并发竞态只有一方获胜、其余 Busy |
| T06-TERM | closed recover 无 Graph/预算/journal/外调 |

### PASS

T05+T06 统一测试全绿；任何不确定路径均无第二次外部调用；blocked 与容量一致。

### STOP

无法从公开 checkpoint/journal/Artifact 证明安全；需要保存私有模型上下文；需要自动恢复或扩大公共 API；发现已确认矩阵缺口必须改变设计。

### 报告格式

使用 §6，并追加：恢复决策表 case ID、外部调用计数、Artifact/journal/budget 前后状态、block reason 类别、子进程退出与接管证据、取消路径。

## DE-T07：真实 PostgreSQL 完整资格与 coverage

### 目标

对代码最终候选执行 Module 11 完整 durable-workflow Gate、跨进程/重启/fault matrix、coverage 和受影响回归。

### 前置条件

`DE-T06 PASS`；实现 tree 完整；无未解释 dirty/staged；隔离 PostgreSQL 编排已复核。

### 分支

实施分支不变。

### 文件白名单

资格本身不修改生产代码。若发现小范围明确缺陷，可修改：

```text
app/durable_execution/**
tests/unit/durable_execution/**
tests/contract/durable_execution/**
tests/integration/durable_execution/**
tests/support/durable_execution_fakes.py
tests/support/durable_execution_worker.py
tests/support/run_module11_coverage.py
```

修复若改变设计、迁移或公共 API，`STOP`。

### 允许动作

- 从空库执行 migration 和完整测试；
- 重启 Runtime、Python worker、PostgreSQL container但保留 test volume；
- 运行 coverage harness；
- 运行 Module 04/05/08/09/10 直接依赖受影响回归、Module 01 传递合同/Module 07 只读审计回归和全量离线 unit/contract；
- 对确定小缺陷修复后从最早受影响 Gate 重跑。

### 禁止动作

- 真实模型/地图/API；
- 普通开发数据库或日常 volume；
- skip/xfail/rerun/弱化断言；
- 用宿主同进程对象替代跨进程证据；
- coverage 排除源码或舍入过线；
- 资格过程中修改文档/Docker/README/状态。

### 测试矩阵

| ID | 必测 |
|---|---|
| T07-UNIT | 全部 Module 11 unit |
| T07-CONTRACT | exact public/import/budget activation/M05 driver/security 全部 contract |
| T07-PG | 全部 Module 11 integration，空库迁移开始 |
| T07-RESTART | Runtime/worker/PG restart 后数据与恢复一致 |
| T07-XPROC | lease/fence/capacity/相同请求跨进程并发；独立 worker 的 migration/capacity/run advisory key 同目标一致、跨 domain 隔离，禁止 `hash()`/随机种子导致互斥失效 |
| T07-FAULT | bootstrap accepted/dispatch marker、phase activation、commit unknown、Artifact gap、root/descendant checkpoint gap、terminal gap、renew/cancel |
| T07-COV | line/branch/combined 各 70%，0 skip/xfail/rerun |
| T07-UPSTREAM | Module 04/05/08/09/10 直接依赖受影响回归；Module 01 传递合同与 Module 07 只读审计回归 |
| T07-FULL | 全项目离线 unit/contract |
| T07-STATIC | compileall、import boundary、Secret pattern、pycache/异常文件 |

### PASS

全部 Gate 对同一代码最终版通过；coverage 三项达标；无残留测试资源；生产/测试文件 hash 在 coverage 后未变化。

### STOP

任一 Gate 失败且修复需改设计/迁移/白名单；隔离身份不可靠；Docker/数据库/网络不可用；coverage 不达标；出现 skipped/xfail/rerun。

### 报告格式

使用 §6，并追加每条精确命令、测试计数、coverage 原始分子/分母、容器/network/volume 前缀、重启和跨进程 case、资源清理、代码最终版 hash。

## DE-T08：独立代码审查、修复与复审

### 目标

由独立审查者逐条对照主设计、本文和直接依赖检查实现，不以 T07 绿灯替代审查；修复确认问题后复审。

### 前置条件

`DE-T07 PASS`；提供当前 diff、全部测试证据和代码最终版 hash。

### 分支

实施分支不变。

### 文件白名单

第一轮审查只读。确认缺陷的修复白名单与 `DE-T07` 相同；两份 Module 11 文档和上游文件仍只读。

### 允许动作

- 独立审查全部 production、migration、tests 与报告；
- 逐条检查 §18 AC 和每个阶段 PASS；
- 复现实质问题的定向测试；
- 主实施者只修确认问题；
- 独立审查者执行第二轮复审。

### 禁止动作

- 审查者直接修改文件；
- 用“测试通过”作为无问题结论；
- 为消除意见改变已确认设计；
- 未复核即照单全收；
- P2 未解决即进入 closeout。

### 审查矩阵

| Review | 重点 |
|---|---|
| A | 精确公共 API、依赖方向、七表逐列/四表、事务、分阶段预算/完整 journal FSM、直接依赖 04/05/08/09/10 一致性及 01 传递合同/07 只读审计边界 |
| B | lease/fence、root+descendant checkpoint、crash window、non_recoverable 外调、Artifact 对账、pending plaintext/metadata、安全、Module 12–15 保留空间、弱模型可执行性 |

第一轮 A/B 后，主实施者独立复核并修复；所有受影响测试从最早 Gate 重跑；第二轮 A/B 必须确认 P0/P1/P2 为 0。P3 修复或记录接受依据。

### PASS

第二轮无未解决 P0/P1/P2；P3 有处置；修复后的 T07 适用 Gate 仍通过；diff 仍在白名单。

### STOP

意见要求改变主设计/上位合同、扩大白名单或大改；审查者无法独立复核；修复后 Gate 失败。

### 报告格式

使用 §6，并逐条附第一轮 Review A/B、主模型判定、修复 commit 状态（仍应 NOT_RUN）、重跑 Gate、第二轮 A/B、P0/P1/P2/P3 汇总。

## DE-T09：`MODULE-CLOSEOUT`

### 目标

严格执行 `docs/SHARED_IMPLEMENTATION_RULES.md` 第 7 节：同步真实完成事实、更新 Docker/Compose 用户路径，并对同步后交付物重新构建和回归。

### 前置条件

`DE-T08 PASS`；production/test 最终版冻结；用户已明确授权进入 closeout，但尚未授权 commit/merge/push/PR/deploy。

### 分支

实施分支不变。

### 文件白名单

```text
README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile
compose.yaml
.env.example
```

两份 Module 11 设计文档、生产代码、测试、迁移和依赖均只读。只修改真实需要同步的文件；无需修改时必须给出精确现状证据，不能写 `N/A`。

### 允许动作

- README 只同步真实可执行 migration/Runtime/Docker 路径；
- 状态页只记录实际分支/HEAD/命令/计数/coverage/限制；
- Dockerfile 将 `app.durable_execution` 纳入 COPY/runtime import self-check/test target；
- Compose 添加显式、一次性的 Module 11 migration 服务和必要依赖顺序，不自动迁移应用；
- `.env.example` 只在确需说明既有五个 PostgreSQL 变量时修改，不新增真实值；
- 从同步后源码重建 runtime/test image，运行默认 CMD、Module 11 Gate、受影响回归和完整离线回归；
- closeout 完成后安排独立只读 closeout 审查。

### 禁止动作

- 修改 production/test/migration/设计来让 closeout 通过；
- 写入易变事实到长期 Module 11 设计；
- 把 Module 11 宣称为完整网页应用/Module 13/14 已完成；
- 自动 migration、真实 API、普通数据库清理；
- stage/commit/merge/push/PR/deploy。

### 测试矩阵

| ID | 必测 |
|---|---|
| T09-DOC | README/状态只含真实事实，无过期 Module 10-only 声明 |
| T09-DOCKER | 同步后 runtime/test image 从当前源码重建 |
| T09-CMD | runtime 默认 CMD 导入 Module 01–11 且不连接/迁移数据库 |
| T09-MIGRATE | Compose migration 显式执行，应用启动不自动迁移 |
| T09-M11 | 同步后 Module 11 完整 Gate |
| T09-UPSTREAM | 受影响 Module 04/05/08/09/10 Gate，加 Module 01 传递合同/Module 07 只读审计 Gate |
| T09-FULL | 同步后完整离线 unit/contract |
| T09-FREEZE | production/test 相对 T07/T08 最终版逐字节未变 |
| T09-REVIEW | 独立 closeout 审查逐条对照共享规则 §7 |

coverage 不因 closeout 自动重跑；只有 `T09-FREEZE` 证明 production/test 未变时，才可引用紧邻 closeout 前的 T07 coverage。若 production/test 变化，closeout 失败并返回相应代码阶段重做 coverage/审查。

### PASS

共享规则 §7 全部满足；同步后镜像和回归通过；独立 closeout 审查无未解决 P0/P1/P2；工作区只含白名单预期变更；staged 为空。

### STOP

需要修改冻结设计/代码/测试/依赖；同步事实无法由命令证明；镜像/回归失败；出现白名单外文件；准备未经授权提交或合并。

### 报告格式

使用 §6 全量格式，并额外逐项报告：README、状态页、Dockerfile COPY/CMD、Compose migration 服务、`.env.example`、同步后镜像 ID、runtime CMD、Module 11/上游/全量回归、coverage 引用资格、独立 closeout 审查、commit/merge/push/PR/deploy 授权状态。

## 6. 每阶段统一报告格式

每个阶段或配对阶段报告不得省略：

```text
任务/阶段：
最终状态：PASS / FAIL / STOP / NOT_RUN

Git：
- 分支：
- 开始 HEAD：
- 结束 HEAD：
- 工作区开始/结束状态：
- staged：

前置与引用：
- 主设计精确章节：
- Vibe 阶段：
- 共享规则：
- 直接依赖章节：
- 冲突：

文件：
- 白名单：
- 创建：
- 修改：
- 白名单外异常：

数据库与迁移：
- schema/revision：
- 七张业务表：
- 四张 Saver 表：
- migration：
- 数据迁移：
- 临时资源与清理：

测试：
- unit：
- contract：
- integration：
- restart/cross-process/fault：
- 上游回归：
- 全量离线回归：
- failed/skipped/xfail/rerun：

coverage：
- statements/covered lines：
- branches/covered branches：
- line：
- branch：
- combined：

安全与边界：
- Secret/raw payload 扫描：
- 自动迁移/内存 fallback：
- 公开 Saver/history/delete：
- 其他项目是否触碰：

审查与验收：
- 主设计逐条对照：
- Vibe 逐条对照：
- P0/P1/P2/P3：
- 有意偏离：

外部操作：
- commit：
- commit hash：
- merge：
- push：
- PR：
- deploy：

剩余问题：
最终结论：
```

阶段没有涉及的项目必须写 `NOT_RUN — 原因`，不得写成 PASS。最终报告必须明确分支、前后 HEAD、修改文件、migration、测试、异常文件、commit、push、PR 和最终状态。

## 7. 实施完成与提交边界

只有 `DE-T00..DE-T09` 全部 PASS、两轮独立代码审查及 closeout 审查无未解决 P0/P1/P2、静态检查与 staged 检查通过，才可报告：

```text
MODULE11_IMPLEMENTATION_READY_FOR_USER_COMMIT_DECISION
```

这仍不授权 commit 或 merge。用户另行授权后，独立执行者才可按授权提交；合并到本地 `main` 仍需单独确认。push、PR、deploy 从不因本状态自动获得授权。

不得提前报告 `MODULE11_COMPLETE`、完整 Demo 已完成或 Module 13/14 可用。

## 8. 网页控制扩展实施合同 DE-W00..DE-W05

本节消费权威设计 §22.1–22.9，属于后续独立授权实施任务；不修改旧 DE-T00..T09 的历史结论，不授权现在实施。先完成本节，再开放M13接线阶段。报告使用 §6，增加逐项 §22 对照及旧数据兼容证据。各阶段不得stage/commit/merge/push/PR/deploy；实施结束状态为 READY_FOR_INDEPENDENT_REVIEW，提交资格 NOT AUTHORIZED；等待独立审查。

共同前置：八份设计文档已获用户最终批准；当次提示词明确授权阶段并记录真实HEAD/dirty；读取主设计§6/9、本文§4、权威设计§22。分支固定 feature/module-11-web-controls，从明确已批准设计基线创建，不自动接管同名分支。M13设计分支不当作实施分支。文件路径未存在且标“新建”者为允许创建目标，不能当既有必读引用。没有 filesystem AGENT.md/AGENTS.md，按会话指令执行。

### DE-W00：只读预检（独立）

目标：核对旧schema、公开方法、原生resume、迁移链和DE-W文件是否存在；记录冻结设计哈希、已有dirty归属与前后HEAD。白名单空；允许只读，不运行迁移/测试/业务。逐条核对§22.1–22.9与真实实现差距、列出必须新增目标，不猜表名。PASS为引用存在、基线可解释、已批准阶段清楚；任何设计缺口或未知dirty STOP。报告§6加差异矩阵。

### DE-W01：前向迁移和结构安全门（独立）

前置W00通过，旧库备份/隔离测试数据库准备；禁止对用户运行库自动迁移。
精确白名单：
- 新建 app/durable_execution/migrations/versions/m11_0002_web_controls.py；
- 修改 app/durable_execution/migrate.py、app/durable_execution/repository.py（仅schema结构检查部分）；
- 修改 tests/integration/durable_execution/test_migrations.py。
允许：只实现§22.2列/CHECK/索引及结构检查；保留m11_0001字节、原七表和官方Saver表，不建新任务索引表。若结构检查实际委托其他未列文件，STOP申请最小白名单扩展，不借机修改runtime。

测试矩阵：空库upgrade；m11_0001有open/closed/blocked与pending/journal数据的旧库upgrade；二次upgrade幂等；新组合约束拒绝；旧数据/contract/digest不变；缺列/错误CHECK启动检查失败；禁止数据删除式downgrade。真实PostgreSQL按§4.3隔离；使用当前源码新Docker容器。PASS全部行为通过且旧数据保留。不能用删库重建替代迁移验证。失败STOP，不配对后续阶段。

### DE-W02：公共DTO、查询与凭证端口

前置W01 Gate通过。可与W03相邻配对，二者完成后统一相应测试。
精确白名单：
- app/durable_execution/__init__.py、models.py、protocols.py、errors.py、repository.py、runtime.py；
- tests/unit/durable_execution/test_models_and_protocols.py、test_repository.py；
- 新建 tests/unit/durable_execution/test_runtime.py；
- tests/contract/durable_execution/test_public_api.py、test_security_contract.py；
- 新建 tests/integration/durable_execution/test_web_queries.py；
- 修改 tests/integration/durable_execution/test_artifact_reconciliation.py；
- 新建 tests/integration/durable_execution/test_pause_credential_handoff.py。
允许：§22.1/4/5/8/9，含节点只读Artifact视图及终态预算读取；只扩展受控公开方法与factory可选参数，旧签名调用兼容；sink是Protocol，真实加密/M14表和密钥不在白名单。列表不读取应用私有索引、不泄露lease/token/正文。测试fake sink只能内存保留测试token，不写明文文件。

测试矩阵：DTO strict/fresh/未知字段/有限error；精确__all__更新；页大小1/20/100与重复时间的keyset；分页新增run不重返旧页；执行/等待/重启/终态/blocked/旧contract查询；与状态变化竞争；两个暂停类型的store失败、store成功pending失败、pending成功响应丢失及token错代。补充测试：§22.8的无journal上游读/跨day读/拒绝只读put/写许可不放宽/跨run与失效绑定；§22.9同进程安全取消后全部终态预算、排序/activation/ledger一致性以及非终态和blocked拒绝。PASS为§22.1/4/5/8/9逐条证据齐全；缺API、非canonical数据、token泄露或直接跳过校验STOP。

### DE-W03：持久取消与原生恢复

前置W02合同代码就绪；配对统一测试后才能宣称W02/W03通过。
精确白名单：
- app/durable_execution/runtime.py、repository.py、budget.py、checkpointer.py、errors.py；
- tests/unit/durable_execution/test_recovery.py；
- 拟新建（W02已创建则修改）tests/unit/durable_execution/test_runtime.py；新建 tests/unit/durable_execution/test_lifecycle.py；
- tests/integration/durable_execution/test_failures_and_cancellation.py、test_budget_and_journal.py、test_bootstrap_and_restart.py、test_lease_and_fencing.py、test_checkpoint_and_lineage.py；
- 新建 tests/integration/durable_execution/test_web_cancellation.py；
- 新建 tests/integration/durable_execution/test_native_hotel_resume.py；
- tests/support/durable_execution_fakes.py、durable_execution_worker.py。
允许：§22.2/3/7线性化、只取消对应graph task、cancel terminal原子写和读入口统一、native Command恢复；不得修改M05或第三方Saver，不用私有Graph update来补终态。

测试矩阵：request_cancel与正常close/接受resume/permission双向竞争（屏障同步不用sleep）；重复取消/响应丢失；有效lease仍requested；测试控制层模拟一次显式取消后重复纯维护，在执行退出/lease到期后safe取消，且不重放业务；实际M14调度器不在本阶段实现；未知外部结果blocked且不新增外调；budget/Artifact不回退；取消bytes损坏/基checkpoint不符；仅关闭单run；无意图raw CancelledError回归；原生hotel已激活phase跨进程恢复、恰一次选择且不重跑需求/搜索；M05 old path回归；receipt字段与中文展示语义。
PASS：W02/W03测试联合全部通过，§22所有取消写入口都受同一run事务裁决。任何“先普通完成再覆盖取消”、自动重放未知调用、native重跑根图、close整个Runtime或无法逐条说明fence均STOP。

### DE-W04：独立审查、扩展Gate与延期触发核验（独立）

前置W02/W03通过。默认只读白名单；小修仅回到W01–03原精确文件且重跑受影响Gate。按权威设计§18/22、本文§4审查实际diff，不能只看测试绿灯。
必须运行完整M11 unit/contract/integration和04/05/08/09/10受影响回归；记录line/branch/combined真实值。D-13-09不自动取消旧M11 Demo资格例外，未触发债务继续明确列出；新增扩展路径不得skip/xfail或借历史例外漏测。逐条对照UF-11-003/004，报告已补哪些路径、未触发哪些路径，不能笼统“延期已清零”。
PASS：无P0/P1/P2，新增合同与实际用到恢复路径全部通过；历史未触发资格缺口在报告保持可见。变更设计、扩大范围或实质重构STOP并求用户批准。

### DE-W05：扩展 MODULE-CLOSEOUT（独立）

前置W04通过；冻结设计哈希不变；仍需独立授权此阶段。
白名单仅README.md、docs/IMPLEMENTATION_STATUS.md、Dockerfile。逐条执行SHARED_IMPLEMENTATION_RULES.md §7和本文DE-T09，不改八份冻结设计。Dockerfile若包/命令未变可以不修改，但必须指出实际CMD仍覆盖M11并运行自检。
从同步后源码重建runtime/test，运行默认CMD、完整M11测试/coverage、compileall、截至M11及当前仓库已实现模块的全量离线unit/contract、相关数据库integration；默认离线隔离，PG按§4.3专用网络。此前四份文档dirty若仍存在须在提示词明确归属；不能自动混入closeout。
PASS：同步事实准确、当前源码证据齐全、独立审查通过、未解决问题按D-13-09边界如实报告；得到“可供M13消费”的扩展资格。无commit/merge授权不得执行；不把资格通过等同于已提交或整库历史严格资格PASS。
