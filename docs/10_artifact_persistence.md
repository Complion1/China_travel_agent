# 10 PostgreSQL Artifact 持久化权威设计

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次专用诊断表、独立容量、复用连接池及m10_0002直接写入 [M10设计§23](10_artifact_persistence.md)，新增对M02固定记录类型的依赖；业务ArtifactRepository三方法/正文限制继续适用。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档状态：已批准并冻结
> 模块状态：仅完成需求澄清与设计，不代表 Module 10 已实现
> 目标分支：`feature/module-10-artifact-persistence`

## 1. 文档目的

本文定义 Module 10 的唯一实现边界：把 Module 04 已确认的 `ArtifactRepository`（Artifact 仓库接口）接到 PostgreSQL，使结构化 Artifact 在应用正常重启和 PostgreSQL 容器重启后仍可读取。

这里的 **Artifact** 是各工作流阶段产生的、已经过类型校验的结构化结果，例如酒店候选、研究结果或行程方案；**Repository** 是业务代码读写这些结果时面对的接口；**PostgreSQL** 是实际保存数据的数据库。业务模块只依赖 Repository 接口，不直接写 SQL，也不需要知道数据表细节。

本文是设计文档，不授权修改代码、运行迁移、创建日常数据库、部署或删除数据。精确实施顺序见 [10 Artifact 持久化 Vibe Coding 执行规范](10_artifact_persistence_vibe_coding.md)。

## 2. 规范依据与优先级

实施、审查和验收必须按以下顺序解决冲突：

1. 当前用户明确指令与已经确认的 Module 10 决策；
2. [Agent 总体设计](00_agent_master_design.md)；
3. 本权威设计文档；
4. [Module 04 工作流状态与 Artifact 合同](04_workflow_state_and_artifacts.md)，尤其第 8～11、18～19、22～25 节；
5. Module 04～09 已发布的公共合同和注册项；
6. [共享实施规则](SHARED_IMPLEMENTATION_RULES.md)；
7. [实施路线图](IMPLEMENTATION_ROADMAP.md)与[实施状态](VALIDATION.md)；
8. 测试与当前代码事实；
9. 本模块的执行规范。

如果上位文档、当前代码与本文出现无法在 Module 10 白名单内解决的实质冲突，必须停止，报告冲突，不得自行改写 Module 04～09 的合同来迁就实现。

外部库的官方资料只用于确认 API 用法，不改变仓库内规范的优先级：

- [Psycopg 异步连接池](https://www.psycopg.org/psycopg3/docs/api/pool.html)
- [SQLAlchemy PostgreSQL psycopg 方言](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
- [Alembic Runtime API](https://alembic.sqlalchemy.org/en/latest/api/runtime.html)
- [Alembic 配置教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)

## 3. 目标、成功标准与非目标

### 3.1 当前目标

Module 10 必须交付：

- 一个实现 Module 04 `ArtifactRepository` Protocol 的 PostgreSQL 版本；
- 一个明确拥有连接池生命周期的运行时对象和工厂；
- 一套由 Alembic 管理、可检查版本的数据库结构；
- 可重复执行且并发安全的 `put`、`lookup`、`get` 行为；
- 与内存版相同的公开 Repository 语义和现有异常语义；
- 独立真实 PostgreSQL 测试环境、分层测试矩阵与覆盖率门槛；
- 严格的凭据脱敏、无隐式降级和无自动迁移边界。

### 3.2 成功标准

只有同时满足以下条件，Module 10 的实现才可被称为完成：

- Module 04～09 的公共接口未被擅自改变；
- PostgreSQL Repository 通过共享合同测试和真实 PostgreSQL 集成测试；
- 同一个 `OperationId` 的相同内容重放得到原始 `ArtifactRef`，不同内容得到冲突；
- 普通应用重启、连接重建和 PostgreSQL 测试容器重启后，已提交 Artifact 可读取；
- 迁移版本与数据库结构检查是显式、可失败且不自动修复的；
- 容量上限在多个应用实例并发写入时仍不超额；
- 日志、异常、测试输出和最终报告不泄露密码或完整连接串；
- `app.artifact_persistence` 的 line、branch、combined coverage 均不低于 70%；
- 审查逐条对照本文和执行规范，而不是仅以“测试通过”为结论。

### 3.3 明确非目标

Module 10 不包含：

- 工作流 checkpoint、run 协调、执行预算持久化、操作日志或跨进程恢复；这些属于 Module 11；
- 最终输出组合与主应用完整装配；分别属于 Module 12、Module 13；
- Artifact 删除、清理、TTL、自动淘汰、保留策略、静态加密或密钥生命周期；属于 Module 19；
- 生产审计、指标、Tracing 和生产组合；属于 Module 20～21；
- 生产备份、灾难恢复、长期运行验证；留待后续 Production 工作和 Module 22；
- 把已有内存数据或测试数据复制到日常数据库；
- 对任意 JSON、提示词、模型原始响应、推理过程或密钥做通用存储；
- 为了 PostgreSQL 修改 Module 04 `ArtifactRepository` Protocol；
- 新增 `exists`、`list`、`delete`、`clear`、批量操作、TTL、`close` 或 `aclose` Repository 方法。

## 4. 初学者术语表

| 名词 | 本模块中的含义 |
|---|---|
| 数据库 | 长期保存数据的软件。应用退出后，已提交数据仍存在。 |
| PostgreSQL | 本模块选用的关系型数据库。数据放在有约束的表中。 |
| Repository | 业务代码使用的“存取接口”。它把 SQL 和连接管理藏在实现内部。 |
| 内存版 Repository | 数据只存在 Python 进程内，进程退出即消失；适合快速单元测试。 |
| PostgreSQL 版 Repository | 通过同一公共接口把 Artifact 保存到 PostgreSQL；用于真实持久化。 |
| Schema（命名空间） | 数据库中给表分组的名字。本模块固定为 `artifact_store`。 |
| 表 | 有固定列和约束的数据集合。类似规则严格的表格，但由数据库并发管理。 |
| 事务 | 一组操作要么全部成功、要么全部回滚，避免只写了一半。 |
| 迁移 | 用版本化脚本创建或改变数据库结构。迁移改变“表的形状”，不是业务数据。 |
| Alembic | 管理迁移版本和按顺序执行迁移的工具。它不代替 PostgreSQL。 |
| 连接池 | 复用少量数据库连接的组件，避免每次操作都重新连接。 |
| 幂等 | 同一操作重复执行，结果与只成功执行一次相同。 |
| Canonical JSON | 键顺序和编码规则固定的 JSON 字节；相同内容必得相同字节和 hash。 |
| hash | 对字节计算的指纹。本模块使用 `sha256:` 加 64 位小写十六进制。 |
| `BYTEA` | PostgreSQL 保存原始字节的列类型。这里保存 Canonical JSON 的唯一正文。 |
| `READ COMMITTED` | 每条 SQL 读取当时已经提交的数据的事务隔离级别；本模块再配合行锁保证容量。 |
| 行锁 | 事务临时锁住特定的一行，让竞争写入按顺序核对容量，不锁整张 Artifact 表。 |

## 5. 模块边界与依赖方向

### 5.1 包所有权

新增包固定为 `app.artifact_persistence`。它拥有：

- PostgreSQL 配置模型和环境变量加载；
- 持久化启动异常；
- 连接池、迁移版本检查、容量状态检查和关闭流程；
- PostgreSQL `ArtifactRepository` 私有实现；
- Alembic 环境与 Module 10 迁移。

`app.workflow_contracts` 继续拥有：

- `ArtifactRepository` Protocol；
- `ArtifactRef`、`ArtifactWriteRequest`、读取与 lookup DTO；
- `ArtifactSchemaRegistry` 与 Canonical JSON 规则；
- `ArtifactRepositoryLimits`；
- Repository 操作异常。

Module 07～09 继续拥有当前已经发布的 Artifact DTO 与注册项。Module 10 的业务 Repository 直接依赖 Module 04；本次专用诊断新增依赖 M02 的固定记录类型/端口（§23），Module 07～09 只作为兼容性验证材料。Module 10 不自动扫描这些包、不在生产代码中组合这些注册项，也不把业务 DTO 移入数据库包。正式跨模块 Registry 组合仍属于 Module 13。

### 5.2 允许依赖

依赖方向固定为：

```text
Module 05～09 业务代码
          │ 仅依赖
          ▼
app.workflow_contracts.ArtifactRepository
          ▲
          │ 实现
app.artifact_persistence ──► psycopg / psycopg_pool / PostgreSQL
          │
          └──► 显式注入的 ArtifactSchemaRegistry
```

禁止形成以下方向：

- `app.workflow_contracts` 导入 `app.artifact_persistence`；
- Module 05～09 直接导入 psycopg、SQLAlchemy、Alembic 或 SQL；
- Module 10 导入每个业务 Artifact 模块以自动组成 Registry；
- 数据库表或 SQLAlchemy 模型成为业务公共 DTO；
- 发生数据库错误时自动换用 `InMemoryArtifactRepository`。

## 6. 沿用的 Module 04 公共合同

### 6.1 Repository 方法不变

PostgreSQL 实现必须结构化符合现有 Protocol，仅实现以下三个异步方法：

```python
async def put(self, request: ArtifactWriteRequest) -> ArtifactRef: ...

async def lookup(
    self,
    *,
    operation_id: OperationId,
) -> ArtifactLookupOutcome: ...

async def get(
    self,
    ref: ArtifactRef,
    *,
    expected_model: type[TArtifact],
) -> ArtifactReadResult[TArtifact]: ...
```

不得把数据库连接、事务、session、cursor 或 SQL 参数暴露到这些方法中。

### 6.2 标识与正文规则不变

- `OperationId = (run_id, phase, logical_step)`，是一次逻辑写入的唯一身份；
- `ArtifactRef` 的七个字段全部保留：`artifact_id`、`artifact_type`、`schema_version`、`content_hash`、`operation_id`、`created_at`、`size_bytes`；
- Canonical JSON 必须继续调用 Module 04 `ArtifactSchemaRegistry.prepare()` 产生；
- 读取必须继续调用 `ArtifactSchemaRegistry.decode()` 完成类型、版本、hash、长度和规范字节验证；
- Module 10 不复制一套 Canonical JSON 算法，也不放宽模型校验；
- 单个 Artifact 仍受 `ArtifactRepositoryLimits` 约束。

### 6.3 两种 Repository 的关系

PostgreSQL 版完成后，内存版仍有用途：

- 单元测试和不需要持久化的合同测试使用内存版，速度快、故障定位简单；
- 显式的临时本地实验可以使用内存版；
- 真实运行要持久保存时显式选择 PostgreSQL 版；
- 任何环境都不得在 PostgreSQL 故障后自动回退到内存版，因为那会让调用者误以为数据已持久保存；
- 两种实现不做双写、同步或自动迁移数据。

## 7. 新增公共 API

### 7.1 包根唯一公共导出

`app.artifact_persistence.__all__` 固定只导出：

```python
[
    "ArtifactPersistenceSettings",
    "ArtifactPersistenceRuntime",
    "ArtifactPersistenceError",
    "ArtifactPersistenceConfigurationError",
    "ArtifactPersistenceDependencyError",
    "create_artifact_persistence_runtime",
    "load_artifact_persistence_settings",
]
```

PostgreSQL Repository 具体类、连接池、SQL、重试帮助函数和迁移帮助函数均为包内私有实现。业务组合层只能从 `ArtifactPersistenceRuntime.repository` 取得一个按 `ArtifactRepository` 使用的对象。

### 7.2 `ArtifactPersistenceSettings`

这是严格、不可变、禁止额外字段的 Pydantic 模型。字段固定为：

| 字段 | 类型与规则 | 来源 |
|---|---|---|
| `host` | 非空 `str` | `POSTGRES_HOST` |
| `port` | `int`，1～65535 | `POSTGRES_PORT` |
| `database` | 非空 `str` | `POSTGRES_DB` |
| `user` | 非空 `str` | `POSTGRES_USER` |
| `password` | `SecretStr`，非空 | `POSTGRES_PASSWORD` |

不接受完整 DSN 环境变量，不在模型中保存可打印的密码明文，不提供包含凭据的 `repr`、`str` 或 `safe_display`。

函数合同固定为：

```python
def load_artifact_persistence_settings(
    *,
    environ: Mapping[str, str] | None = None,
) -> ArtifactPersistenceSettings: ...
```

`environ=None` 时只读取当前进程中上述五个环境变量；显式 `environ` 只用于受控测试。缺失、空值、端口格式错误或范围错误统一抛出 `ArtifactPersistenceConfigurationError()`，不把原值放入异常。

### 7.3 `ArtifactPersistenceRuntime`

运行时对象拥有：

- 一个私有 `AsyncConnectionPool`；
- 一个只读 `repository: ArtifactRepository` 属性；
- `async close() -> None`；
- `async __aenter__()` 与 `async __aexit__()`，便于应用生命周期托管。

`close()` 必须幂等；关闭后调用 Repository 或跨 event loop 使用运行时，抛出 `ArtifactRepositoryUsageError()`。运行时不公开池对象，也不提供让业务代码借用裸连接的方法。

包根 import 只能导入符号：不得读取环境变量、打开连接、检查迁移或产生其他 I/O。`ArtifactPersistenceRuntime` 的直接构造器不构成公共合同，调用方必须使用工厂。`__aenter__()` 在已关闭 Runtime 上抛 `ArtifactRepositoryUsageError()`，否则返回自身；`__aexit__()` 总是尝试关闭并返回 `False`/`None`，不得吞掉调用方异常。若业务异常与关闭异常同时发生，业务异常保持原样；无业务异常时，关闭依赖失败安全映射为 `ArtifactPersistenceDependencyError()`。

工厂合同固定为：

```python
async def create_artifact_persistence_runtime(
    *,
    settings: ArtifactPersistenceSettings,
    registry: ArtifactSchemaRegistry,
    limits: ArtifactRepositoryLimits = DEMO_ARTIFACT_REPOSITORY_LIMITS,
) -> ArtifactPersistenceRuntime: ...
```

这里的默认限制必须直接复用 Module 04 的 `DEMO_ARTIFACT_REPOSITORY_LIMITS`：单个 1 MiB、最多 2048 个、正文总量 64 MiB。工厂在返回前完成配置复制校验、连接池打开与可用性等待、迁移 head 检查、数据库结构检查和容量配置检查。传入 `ArtifactRepositoryLimits` 必须按 Module 04 的两段式严格复制规则生成 fresh value；伪造或非法 limits 抛 `ArtifactRepositoryUsageError()`。只有一个合法 limits 与数据库已经固化的限制不一致，才抛 `ArtifactPersistenceConfigurationError()`。任何步骤失败都先关闭已创建资源，再抛出安全异常；不得返回“半初始化”运行时。

### 7.4 启动异常

新增三个零参数、安全 marker exception：

```text
ArtifactPersistenceError(Exception)
├── ArtifactPersistenceConfigurationError
└── ArtifactPersistenceDependencyError
```

要求与 Module 04 异常风格一致：构造器不接收消息，`str(error)` 只返回类名，不设置 `__cause__` 暴露底层异常。含义：

- `ArtifactPersistenceConfigurationError`：环境变量、迁移版本、结构、容量限制等启动配置不符合设计；
- `ArtifactPersistenceDependencyError`：启动时 PostgreSQL、DNS、网络、连接池或数据库服务不可用。

Repository 三个方法运行中的错误继续使用 Module 04 已有异常，不用启动异常替代。

## 8. 数据库命名空间与表结构

### 8.1 固定 Schema

Module 10 只拥有 PostgreSQL schema `artifact_store`。它包含：

1. `artifact_store.artifacts`：Artifact 元数据和正文；
2. `artifact_store.capacity_state`：全库容量限制和当前计数；
3. `artifact_store.alembic_version`：Alembic 记录的当前迁移版本。

Module 11 将来必须使用自己的命名空间或独立表，不得借用 `artifacts` 表存 checkpoint、预算或操作日志。

### 8.2 `artifacts` 表

| 列 | PostgreSQL 类型 | 约束与来源 |
|---|---|---|
| `artifact_id` | `UUID` | 主键，默认 `gen_random_uuid()`，由数据库生成 |
| `run_id` | `UUID` | 非空，来自 `OperationId.run_id` |
| `phase` | `TEXT` | 非空，只允许七个当前 `WorkflowPhase` 值 |
| `logical_step` | `VARCHAR(64)` | 非空，1～64 字符且匹配 `^[a-z][a-z0-9_]*$` |
| `artifact_type` | `VARCHAR(65)` | 非空，匹配 Module 04 `ArtifactType` 规则 |
| `schema_version` | `VARCHAR(16)` | 非空，匹配 Module 04 `ArtifactSchemaVersion` 规则 |
| `content_hash` | `CHAR(71)` | 非空，匹配 `^sha256:[0-9a-f]{64}$` |
| `canonical_bytes` | `BYTEA` | 非空，Canonical JSON 的唯一权威正文 |
| `size_bytes` | `BIGINT` | 非空，1～`2**53-1`，且等于 `octet_length(canonical_bytes)` |
| `created_at` | `TIMESTAMPTZ` | 非空，默认 `CURRENT_TIMESTAMP`，数据库生成并以 UTC 返回 |

额外约束：

- 主键唯一保证 `artifact_id` 唯一；
- `UNIQUE (run_id, phase, logical_step)` 保证一个 `OperationId` 只有一个第一胜者；
- `phase` 固定允许：`requirements`、`hotel_selection`、`candidate_research`、`itinerary_planning`、`validation`、`budget_evaluation`、`output_formatting`；
- 表不包含 `updated_at`，应用也没有 UPDATE/DELETE 权限路径；
- 不增加 JSONB 正文、URI、提示词、原始响应、reasoning、业务专用列、每种 Artifact 一张表或按 hash 去重表；
- 不为当前公共接口不存在的 list/run 查询预建索引。

Canonical JSON 只保存一份 `BYTEA`，避免“JSONB 和字节谁才是真的”这一双重真源问题。`artifact_type` 和其他标量列用于定位与校验，不是第二份正文。

### 8.3 `capacity_state` 表

该表只有一行，固定 `singleton_id = 1`：

| 列 | PostgreSQL 类型 | 约束 |
|---|---|---|
| `singleton_id` | `SMALLINT` | 主键且必须等于 1 |
| `max_artifact_bytes` | `BIGINT` | 1～`2**53-1` |
| `max_artifacts` | `BIGINT` | 1～`2**53-1` |
| `max_total_content_bytes` | `BIGINT` | 1～`2**53-1`，且不小于单件上限 |
| `artifact_count` | `BIGINT` | 0～`max_artifacts` |
| `total_content_bytes` | `BIGINT` | 0～`max_total_content_bytes` |

它既保存首次初始化时确认的限制，也保存已提交计数。新增 Artifact 的 INSERT 和计数 UPDATE 必须在同一事务中；事务回滚时两者一起回滚。

### 8.4 容量行初始化与限制漂移

运行时启动时执行：

1. 在短事务中读取容量行；
2. 若容量行存在，传入的三个限制必须逐项完全相同，否则配置错误；
3. 若容量行不存在，先确认 `artifacts` 表为空，再用并发安全的 `INSERT ... ON CONFLICT DO NOTHING` 尝试建立行；
4. 随后再次读取并逐项比较；
5. 若“容量行缺失但 Artifact 已存在”，视为结构/数据不一致，配置错误，不扫描后自动重建计数。

自定义小容量只允许在完全隔离的测试数据库中使用。日常数据库的限制不能因一次启动参数变化而被静默改写。

## 9. Alembic 迁移设计

### 9.1 Alembic 的职责

Alembic 只负责数据库结构版本：创建 `artifact_store`、两张表、约束和版本记录。它不运行应用，不写业务 Artifact，也不创建测试数据。

初始 revision 固定标识为 m10_0001，历史迁移保持只读；本次目标 head 升至 m10_0002（§23），现有代码仍为旧head，不能把设计当已迁移。Alembic 版本表必须放在 `artifact_store.alembic_version`，不能使用默认 `public.alembic_version`。

空库存在一个必须显式解决的引导顺序：Alembic 在执行首个 revision 前需要建立版本表，但版本表所在 schema 此时还不存在。因此在线 `env.py` 必须使用同一个连接和同一个最外层 PostgreSQL 事务，先执行固定标识符的 `CREATE SCHEMA IF NOT EXISTS artifact_store`，再以 `version_table_schema="artifact_store"`、事务性 DDL 和该连接运行 Alembic。随后 Alembic 建立版本表并执行 `m10_0001`；`m10_0001` 只创建两张业务表及其约束，不再负责创建 schema。schema、版本表、业务表和 revision 记录必须同提交或同回滚。离线迁移模式不在 Module 10 支持范围，必须安全拒绝。

### 9.2 驱动与配置

- 迁移进程使用 SQLAlchemy 2.x 的同步 `postgresql+psycopg` 方言；
- 应用运行时使用 psycopg3 原生异步连接和 `psycopg_pool.AsyncConnectionPool`；
- 两者从同一个 `ArtifactPersistenceSettings` 来源构造连接参数；
- `alembic.ini` 不写 URL、用户名或密码；`env.py` 在内存中用 SQLAlchemy `URL.create()` 组装；
- 日志、异常和报告不得打印 URL 对象、连接信息字典或底层异常文本。

### 9.3 前进式迁移与失败行为

- 空库 schema 引导、版本表、初始迁移和 revision 写入在同一最外层 PostgreSQL 事务中执行；未完成时全部回滚；
- 已成功迁移的数据库永不自动 downgrade；
- revision 的 `downgrade()` 必须显式拒绝，而不是删除表；
- 设计修正必须新增向前 revision，不改写已经发布且使用过的 revision；
- 应用启动不执行 `upgrade head`，只核对数据库当前 revision 是否等于代码 head；
- 未迁移、落后、超前、多 head、版本表位置不正确或表/约束缺失时，应用都在业务写入前拒绝启动；
- 自动生成迁移不是本模块的实现路径；revision 应手写、审查固定标识符与约束。

### 9.4 Docker Compose 使用方式

Compose 新增一次性服务 `artifact-migrate`，它与 PostgreSQL 健康检查配合，显式运行 `alembic upgrade head` 后退出。应用服务只检查，不自动迁移。

本地正确顺序是：

```text
启动 PostgreSQL → 显式运行 artifact-migrate → 启动应用/测试检查者
```

日常数据库只能在真实测试门通过后，另行使用同一迁移初始化为空库。真实集成测试必须使用独立临时容器和独立测试数据卷，绝不能复用或删除普通 `postgres_data`。

## 10. 连接池、生命周期与事务

### 10.1 生命周期所有者

应用组合层创建一个 `ArtifactPersistenceRuntime`，整个应用共享它，并在应用退出时关闭。Repository 的每次方法调用从池中短暂借一个连接，用完归还；Repository 自己不创建长期独占连接。

连接池固定行为：

- `AsyncConnectionPool(open=False)`，由异步工厂显式 `open()` 和 `wait()`；
- 最小连接数 1，最大连接数 4；
- 连接/打开/借用超时 10 秒，语句超时 10 秒，锁等待超时 5 秒，关闭等待 5 秒；
- 每个连接会话时区设置为 UTC；
- 应用事务隔离级别使用 PostgreSQL `READ COMMITTED`；
- 值一律参数化绑定；schema、表、列等标识符来自代码固定常量，不接收用户输入；
- 工厂在任何 `BaseException` 路径关闭池，同时保留取消语义。

这些是 Demo 范围内固定的小型池参数，不扩展为用户可调配置。生产调优留待 Production 组合阶段。

### 10.2 时间与 UUID

与内存版不同，PostgreSQL 版的 `artifact_id` 和 `created_at` 由数据库默认值生成，并通过 `INSERT ... RETURNING` 返回。这是持久化适配器允许使用数据库身份和时间源的明确选择，不向工厂注入 `UtcClock` 或 `UUIDFactory`。

数据库返回的 `created_at` 必须转换并验证为 UTC aware `datetime`。任意不符合 `ArtifactRef` 的结果按依赖失败处理，不能猜测或修正。

### 10.3 `put` 事务算法

`put` 在公共方法入口先完成 lifecycle guard 和一次 `registry.prepare(request)`，形成不可变 `PreparedArtifact` 快照；后续首次尝试与唯一一次重试必须复用这一个快照，不能重新读取调用方可能被嵌套修改的 `request.content`。固定顺序如下：

1. 检查运行时未关闭且在创建它的 event loop 使用；
2. 在事务外仅调用一次 `registry.prepare(request)`，得到严格、不可变的 Canonical JSON、hash 和长度快照；
3. 开始 `READ COMMITTED` 事务，按 `OperationId` 查询现有行；
4. 若已存在，比较类型、版本、hash、长度和 Canonical bytes：完全相同返回数据库中的原始 `ArtifactRef`；任一不同抛 `ArtifactConflictError()`；
5. 若不存在，用 `SELECT ... FOR UPDATE` 锁住 `capacity_state` 唯一行；
6. 获得容量行锁后再次查询同一个 `OperationId`，处理等待期间产生的第一胜者；仍须先返回重放或冲突；
7. 第二次仍不存在时才检查单件大小；超过 `max_artifact_bytes` 抛 `ArtifactTooLargeError()`；
8. 再检查 Artifact 数量和正文总量；超限抛 `ArtifactCapacityExceededError()`；
9. INSERT Artifact，让数据库生成 UUID/时间，并 `RETURNING` 全部 ref 元数据；
10. 同事务更新 `artifact_count` 与 `total_content_bytes`；
11. 构造严格 `ArtifactRef` 后提交并返回。

不能交换“重新查询”和“任何容量检查”的顺序；已有相同重放即使库已满或本次内容会超过单件上限，也必须返回原 ref；已有不同内容必须优先得到冲突，而不是单件/总量错误。

唯一约束冲突会使当前 PostgreSQL 事务进入失败状态。实现必须先退出并回滚原事务，再开启一个新的短读事务按 `OperationId` 查询：找到则按同一不可变快照得出重放或冲突；找不到说明是 `artifact_id` 主键碰撞或数据库结构异常，安全抛 `ArtifactRepositoryDependencyError()`，不得生成新 UUID 后重试。不能在 aborted transaction 中查询，也不能盲目再次 INSERT。

### 10.4 为什么一行锁足够

所有新 Artifact 在写入前都锁同一容量行，所以“读取计数 → 检查上限 → 增加计数”按顺序发生。这样多实例并发也不会都看到旧计数后一起超额。已有 `OperationId` 的读取重放不需要这把锁；整个 Artifact 表也不被锁住。

### 10.5 `lookup` 算法

1. 验证运行时，然后对 `OperationId` 执行 Module 04 的两段式 fresh copy：从 `model_dump(mode="python")` 开始严格 `validate_python`，再经 JSON dump/`validate_json` 重建；失败抛 `ArtifactRequestValidationError()`；该不可变快照只生成一次并供重试复用；
2. 只按三列身份查询元数据，不取 `canonical_bytes`；
3. 找不到返回严格 `ArtifactAbsent`；
4. 找到后构造严格 `ArtifactRef` 并返回 `ArtifactFound`。

`lookup` 只回答“该逻辑操作是否已经有 ref”，不做完整正文读取和完整性扫描。完整正文验证由 `get` 按需完成。

### 10.6 `get` 算法

1. 验证运行时，然后对输入 `ArtifactRef` 执行同样的两段式 fresh copy；失败抛 `ArtifactRequestValidationError()`；该不可变快照只生成一次并供重试复用；
2. 按 `artifact_id` 读取一行及 Canonical bytes；
3. 不存在抛 `ArtifactNotFoundError()`；
4. 数据库中七个 ref 字段与调用方 ref 任一不一致，抛 `ArtifactIntegrityError()`；
5. 调用 `registry.decode(ref=stored_ref, canonical_bytes=..., expected_model=...)`；
6. 返回严格 `ArtifactReadResult`。

这会验证注册类型/版本、expected model、hash、长度、JSON 模型和重新规范化字节。发现未知版本或损坏数据时不得自动删除、覆盖或修复。

## 11. 幂等、并发与不确定提交结果

### 11.1 幂等矩阵

| 状态 | 相同 `OperationId` | 内容 | 结果 |
|---|---|---|---|
| 首次写入 | 无现有行 | 合法且未超限 | 写入并返回新 ref |
| 顺序重放 | 已有行 | 类型、版本、hash、长度、字节全同 | 返回原始 ref |
| 顺序冲突 | 已有行 | 任一不同 | `ArtifactConflictError` |
| 并发相同 | 两请求竞争 | 全同 | 第一条写入，两者最终得到同一 ref |
| 并发不同 | 两请求竞争 | 不同 | 第一条写入，另一条得到冲突 |
| 库已满时重放 | 已有行 | 全同 | 仍返回原 ref |
| 新写单件超限 | 无现有行 | 正文超过单件上限 | `ArtifactTooLargeError`，优先于数量/总字节容量 |
| 库已满时新写 | 无现有行 | 正文未超单件上限，但数量或总字节已满 | `ArtifactCapacityExceededError` |

“第一胜者”由 PostgreSQL 实际提交结果决定，不按任务创建顺序、协程调度顺序或 UUID 排序决定。

### 11.2 重试边界

`put`、`lookup`、`get` 对短暂连接/连接池故障最多自动重试一次，总尝试次数最多两次。公共入口的严格输入复验只执行一次，重试循环只能消费该次生成的不可变快照。可重试范围只包括：

- `psycopg_pool.PoolTimeout`；
- psycopg `OperationalError` 中 SQLSTATE 为连接类 `08`，或明确没有 SQLSTATE 的连接中断。

不可重试：

- 请求/Registry/模型/版本校验错误；
- 冲突、容量、单件过大、找不到和完整性错误；
- 配置错误、迁移错误、语句超时、锁超时、权限错误；
- `CancelledError`、`KeyboardInterrupt`、`SystemExit` 或其他取消/退出信号。

Repository 中所有未被 Module 04 业务异常或数据完整性异常覆盖的 psycopg/pool/数据库失败，都安全映射为 `ArtifactRepositoryDependencyError()`。只有上述短暂连接类会执行第二次尝试；语句超时、锁超时、认证/权限和其他数据库失败只尝试一次。第二次短暂连接失败同样抛 `ArtifactRepositoryDependencyError()`。不得把原 SQL、DSN、host、user、password、正文或底层消息串进异常。

### 11.3 `put` 重试为什么安全

连接中断时，客户端可能不知道 COMMIT 是否已被数据库接受。`put` 重试必须复用公共入口第一次且仅一次生成的同一个不可变 `PreparedArtifact` 快照；第二次尝试首先按该快照中的 `OperationId` 查询：若第一次已提交，相同内容返回原 ref；若未提交，再执行一次写入。因此不会创建两个逻辑结果，也不能重新读取调用方 request，或在不查询的情况下盲目再次 INSERT。

## 12. 启动检查与失败映射

### 12.1 启动检查顺序

工厂必须按以下逻辑完成后才发布 Repository：

1. 严格复制 settings、registry 和 limits 引用/值；
2. 构造但不隐式打开连接池；
3. 显式打开并等待至少一个连接可用；
4. 验证服务器会话和 UTC 行为；
5. 读取代码期望的 Alembic 单一 head；
6. 读取 `artifact_store.alembic_version` 当前 revision 并要求精确相等；
7. 检查 `artifacts`、`capacity_state` 及关键列、约束存在；
8. 初始化或核对容量行；
9. 创建私有 Repository 并返回 Runtime。

启动不扫描、解析或 hash 全表 Artifact。损坏数据在 `get` 时按需发现。

### 12.2 错误映射

| 场景 | 对外异常 |
|---|---|
| 环境变量缺失/非法 | `ArtifactPersistenceConfigurationError` |
| 伪造或非法 `ArtifactRepositoryLimits` 工厂输入 | `ArtifactRepositoryUsageError` |
| 数据库不可连接、DNS/网络/池启动失败、数据库不存在、认证或权限不足 | `ArtifactPersistenceDependencyError` |
| 数据库未迁移、behind/ahead、多 head、结构缺失 | `ArtifactPersistenceConfigurationError` |
| 容量行与传入 limits 不同或计数结构异常 | `ArtifactPersistenceConfigurationError` |
| Repository 方法短暂连接故障重试后仍失败 | `ArtifactRepositoryDependencyError` |
| Repository 方法语句/锁超时、认证/权限或其他非业务数据库失败 | `ArtifactRepositoryDependencyError`，只尝试一次 |
| Repository 已关闭/跨 loop 使用 | `ArtifactRepositoryUsageError` |
| 请求、类型、版本、expected model 错误 | 沿用对应 Module 04 异常 |
| 行不存在、冲突、损坏、容量超限 | 沿用对应 Module 04 异常 |

不要把任意 psycopg、SQLAlchemy、Alembic 或 Pydantic 原始异常直接穿透公共边界。

## 13. 安全、隐私与数据最小化

### 13.1 凭据规则

- `.env` 必须被 Git 忽略；仓库只提交 `.env.example` 占位说明；
- Python 源码、迁移脚本、`alembic.ini`、Compose、测试快照和文档不得写真实密码；
- 连接参数使用分字段配置，不接受或记录完整 DSN；
- 公开异常只有类名，日志只允许安全操作标识和分类，不记录连接详情；
- 测试必须用哨兵秘密验证异常、日志和报告中没有该值及 URL 编码形式；
- SQL 值全部参数化，禁止字符串拼接用户输入；数据库标识符必须是代码常量。

### 13.2 Artifact 内容规则

只允许显式注入 Registry 中已经注册且满足 Module 04 安全模型规则的 DTO。不得保存：

- API key、数据库密码、cookie、token；
- 完整提示词、system prompt、模型原始响应；
- chain-of-thought、隐藏推理或调试堆栈；
- 未经筛选的 provider payload；
- 与当前 Artifact DTO 无关的自由文本转储。

若某业务 Artifact 未来需要新字段，应在其所属模块先完成 DTO 与版本设计，不能由 Module 10 绕过 Registry 直接写 JSON。

### 13.3 数据库暴露边界

本地 Compose PostgreSQL 默认只在 Compose 内部网络可访问，不因 Module 10 自动发布宿主机端口。Module 10 本地可暂用同一数据库账号执行迁移和运行时访问；生产环境分离迁移账号与只写/只读运行账号、传输加密、静态加密和密钥轮换均为后续 Production 工作。在这些生产要求被提出但尚未设计时，应 STOP，而不是伪称已满足。

## 14. Docker 与数据库环境隔离

必须区分三类数据库：

| 环境 | 用途 | 数据来源 | 可否清空 |
|---|---|---|---|
| 临时真实测试库 | 合同、迁移、并发、重启测试 | 测试自行创建 | 只可销毁明确命名的测试容器与测试卷 |
| 日常本地库 | 开发者正常使用 | 测试门通过后由同一迁移初始化为空库 | 本模块不提供删除/重建命令 |
| 未来生产库 | 正式运行 | 后续部署流程 | Module 10 不部署、不清空 |

测试必须使用独立的临时 PostgreSQL 容器与测试卷，不得把应用普通 `postgres_data` 当测试夹具。任何删除动作都必须先解析并验证目标确实是测试资源；不得使用宽泛 volume prune、通配符删除或清空工作区。

## 15. 测试设计

### 15.1 分层原则

数据库代码必须有真实 PostgreSQL 测试，但不是所有测试都依赖数据库：

- 纯配置、错误、重试分类和公共导出测试不需要数据库；
- 业务 Artifact 的模型和 Registry 合同继续用内存版快速验证；
- PostgreSQL Repository 的 SQL、事务、迁移、并发和重启行为必须用真实 PostgreSQL；
- fake/mock 只能辅助命中特定分支，不能代替真实 PostgreSQL 资格门。

### 15.2 必测矩阵

| 类别 | 必须验证 |
|---|---|
| 配置 | 五个变量、严格端口、缺失/空值、SecretStr、无 DSN 泄露 |
| 公共 API | 包根 `__all__` 精确、私有 PostgreSQL 类不外泄、Protocol 结构兼容 |
| 生命周期 | import 无 I/O、显式 open/wait、幂等 close、上下文不吞异常、失败清理、关闭后/跨 loop 拒绝 |
| 输入复验 | forged/mutated request、OperationId、ArtifactRef、limits 均按 Module 04 两段式规则失败；重试不重读输入 |
| 迁移 | 空库同事务 schema 引导并升到 m10_0002，另验证已有m10_0001的增量升级、失败全部回滚、版本表 schema、表列约束、重复 upgrade、拒绝 downgrade |
| 启动 | head 精确、未迁移/behind/ahead/结构缺失拒绝、无自动 upgrade |
| 基本合同 | `put`、`lookup`、`get` 对当前 Module 07～09 注册 Artifact 工作；Module 04 三方法和 17 项语义不变 |
| Canonical bytes | Unicode、键顺序、hash、长度、按需 decode 与重规范化一致 |
| 幂等 | 顺序相同重放原 ref、不同内容冲突、满库相同重放仍成功；既有冲突/重放优先于单件超限；unique violation 先回滚再重查 |
| 并发 | 相同内容第一胜者、不同内容第一胜者、容量边界不超额 |
| 事务 | Artifact 与计数同成败；INSERT/计数故障均不留半提交 |
| 容量 | 单件、数量、总字节、限制漂移、缺失容量行与非空表失败 |
| 故障 | 一次可重试故障恢复、二次失败映射、超时/认证/权限等不可重试错误一次映射、取消透传 |
| 完整性 | 不存在、ref 元数据篡改、正文/hash/size/type/version 损坏均安全失败 |
| 持久性 | 应用 runtime 关闭重建后读取；测试 PostgreSQL 容器 restart 后读取 |
| 安全 | SQL 参数化、秘密哨兵不出现在异常/log/captured output、无 prompt/raw payload |
| 隔离 | 测试只操作独立容器和卷，普通 `postgres_data` 未被删除或写入测试数据 |
| 回归 | Module 01～09 既有测试全部通过，现有内存版行为不变 |

### 15.3 真实 PostgreSQL 资格门

真实门由宿主阶段编排和 test 镜像内 pytest 分工完成，test 镜像不得挂载 Docker daemon socket，也不要求在容器内安装 Docker CLI：

1. 宿主侧阶段编排器创建并保存独立临时 PostgreSQL 16 容器、隔离 network 和独立测试卷的准确 ID；
2. 宿主等待健康，不复用普通应用卷，并用临时 env-file 把连接字段和测试身份传给同一隔离 network 中的 test 镜像；
3. 宿主在迁移容器中显式运行 Alembic 到 head；
4. test 镜像内 pytest 运行真实 Repository 合同、事务、并发、故障和安全测试；其 `conftest.py` 只消费并验证注入的测试身份，不创建、restart 或删除 Docker 资源；
5. pytest 在同一数据库上关闭并重建应用 Runtime，验证数据仍在；
6. 容器 restart 是独立的两段 Gate：第一段 test 镜像写入固定测试 Artifact 后退出；宿主 restart 精确 PostgreSQL 容器并等待健康；第二段全新 test 镜像按固定 `OperationId` 执行 `lookup/get`；
7. coverage harness 在一个 test 容器/pytest 进程中运行不需要控制 Docker 生命周期的 Module 10 unit、contract 和真实数据库 integration；restart 两段 Gate 独立报告，不合并 coverage 数据，也不能被 coverage 冒充；
8. 无论 Gate 成败，宿主都只在名称、label 和保存的 ID 三重核验后销毁本次测试容器、network、测试卷和临时 env-file；
9. 报告宿主与容器内命令、exit code、测试数量和跳过项；真实门不得因 Docker 不可用而静默 skip 并宣称 PASS。

### 15.4 覆盖率

覆盖范围固定为 `app.artifact_persistence`，门槛同时满足：

- line coverage ≥ 70%；
- branch coverage ≥ 70%；
- combined coverage ≥ 70%。

覆盖率只是一个门，不替代真实 PostgreSQL 测试、文档逐条审查和安全审查。

## 16. 可观测性与日志边界

Module 10 不新增生产审计系统或指标系统。若当前应用日志设施需要记录事件，只允许记录：

- 操作类别：startup、put、lookup、get、close；
- 安全的结果分类：success、configuration_error、dependency_error、conflict 等；
- `OperationId.safe_display()` 或 artifact UUID（仅在已有安全日志规范允许时）；
- 尝试次数，不记录异常原文。

不得记录 canonical bytes、反序列化内容、完整 ref dump、settings dump、SQL 参数、DSN、host/user/password 或底层 traceback 到用户可见结果。Module 20 再统一设计正式审计、metrics 和 tracing。

## 17. 文件所有权与预期实现树

Module 10 生产文件预期为：

```text
app/artifact_persistence/
├── __init__.py
├── errors.py
├── settings.py
├── runtime.py
├── repository.py
├── alembic.ini
└── migrations/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── m10_0001_create_artifact_store.py
```

测试预期为：

```text
tests/unit/artifact_persistence/
├── test_settings.py
├── test_runtime.py
└── test_retry_policy.py
tests/contract/artifact_persistence/
├── test_public_api.py
├── test_import_boundaries.py
└── test_coverage_gate.py
tests/integration/artifact_persistence/
├── conftest.py
├── test_migrations.py
├── test_repository_contract.py
├── test_concurrency_and_capacity.py
├── test_failures_and_replay.py
├── test_restart_persistence.py
└── test_security.py
tests/support/run_module10_coverage.py
```

依赖锁、Compose、Dockerfile、`.env.example`、README 和实施状态只在执行规范明确阶段与白名单中修改。Module 04～09 生产文件和现有测试默认冻结。

## 18. 对下游模块的影响

| 模块 | Module 10 提供什么 | Module 10 不替它做什么 |
|---|---|---|
| 04 | 现有 `ArtifactRepository` 的持久化实现 | 不修改公共 Protocol/DTO/Registry |
| 05～06 | 保持上游合同与回归兼容 | 当前无 Artifact 注册项，不制造虚假直接依赖 |
| 07～09 | 当前已注册业务 Artifact 可跨正常重启读取 | 只在测试中组合注册项；生产 Registry 组合延期 Module 13 |
| 11 | 可复用持久化配置思想和数据库服务 | 不保存 checkpoint、budget、operation journal |
| 12 | 将来可读取最终格式化所需 Artifact | 不生成最终输出 |
| 13 | 提供可由组合根显式创建的 Runtime | 不完成全应用 Registry/Repository 接线 |
| 14～15 | 可作为应用/网页流程及其验收的 Artifact 存储 | 不改变其状态机或交互合同 |
| 19 | 提供未来保留/加密设计的存储事实 | 不实现删除、TTL、加密、密钥管理 |
| 20～22 | 提供后续审计、生产组合、长测的基础 | 不宣称生产就绪或灾备完成 |

## 19. 验收清单

实现验收必须逐条提供证据：

- [ ] 公共 API 与第 6～7 节精确一致；
- [ ] schema、表、列、约束与第 8 节精确一致；
- [ ] Alembic 前进式、版本表位置和应用不自动迁移符合第 9 节；
- [ ] Runtime 生命周期、池参数与事务隔离符合第 10 节；
- [ ] `put`/`lookup`/`get` 算法和异常符合第 10～12 节；
- [ ] 同内容重放、不同内容冲突、错误优先级和第一胜者并发语义符合第 11 节；
- [ ] 无自动 fallback、双写、同步、删除、TTL 或全表启动扫描；
- [ ] 安全与数据最小化符合第 13 节；
- [ ] 测试资源与日常资源隔离符合第 14 节；
- [ ] 第 15 节测试矩阵全部通过，非适用项有明确理由；
- [ ] line/branch/combined coverage 各 ≥ 70%；
- [ ] Module 01～09 全量回归通过；
- [ ] 独立代码审查逐条对照设计与执行规范，无 P0/P1/P2 未解决项；
- [ ] `MODULE-CLOSEOUT` 按共享规则完成；
- [ ] 没有未经授权的 commit、push、PR 或部署。

## 20. 停止条件

出现任一情况必须 STOP 并向用户报告，不得扩大范围自行解决：

- 需要修改 Module 04 已确认公共接口、Module 05～09 既有业务合同或 Module 07～09 当前 Artifact 注册合同；
- 需要增加删除、TTL、清理、加密、审计、checkpoint 或完整应用组合；
- 发现当前 PostgreSQL 数据库含真实/日常数据但操作可能清空或重建；
- 测试无法证明目标容器/卷是隔离测试资源；
- 迁移 head、schema 所有权或数据库结构与本文冲突；
- 需要把秘密写入仓库、日志、命令行参数或报告；
- Docker/真实 PostgreSQL 门无法运行，却准备把 skip 当作通过；
- 需要改变已确认设计、扩大文件白名单或进行大改；
- 工作树出现来源不明且与白名单重叠的修改；
- 要执行 push、PR、部署，或阶段未明确授权却要 commit；
- 无法把底层异常安全映射到本文定义的公共异常；
- 独立审查仍有未解决的 P0/P1/P2。

## 21. 已确认决策记录

| 决策 | 结论 |
|---|---|
| 数据库 | PostgreSQL |
| 数据库 schema | `artifact_store` |
| 正文真源 | 单一 `BYTEA` Canonical JSON |
| Repository 接口 | 沿用 Module 04 三方法，不扩展 |
| 内存版 | 保留给快速测试/显式临时开发 |
| PostgreSQL 故障 | 失败关闭，不自动回退内存 |
| Registry | 显式注入，不自动发现业务类型 |
| UUID/时间 | PostgreSQL 生成 |
| 写入模型 | append-only、第一胜者、无覆盖 |
| 隔离与并发 | `READ COMMITTED` + 单行容量锁 + 唯一约束 |
| 容量 | 同事务计数，首次空库固化 limits，后续精确匹配 |
| 迁移 | Alembic，显式执行，应用只检查 |
| 迁移方向 | 只向前；成功后不自动 downgrade |
| 重试 | 仅短暂连接故障，最多一次 |
| 删除 | 本模块放弃，延期 Module 19 |
| 测试 | 快速测试 + 独立真实 PostgreSQL 资格门 |
| 日常库 | 测试通过后另行迁移初始化为空库 |
| 备份灾备 | 不在 Module 10 |
| 最终状态 | 文档评审通过只可标记 `READY_FOR_DESIGN_REVIEW`，不能标记已实现 |

## 22. 延期清单

以下事项被有意延期，后续模块必须重新设计并取得授权：

- Module 11：checkpoint、run 协调、execution budget、operation journal、跨进程工作流恢复；
- Module 12～13：输出组合、主组合根、跨模块 Registry 组装和实际 Repository 选择；
- Module 19：删除 API、TTL、保留、清理、静态加密、密钥生命周期；
- Module 20～21：审计、metrics、tracing、生产组合和生产账号权限分离；
- Module 22/Production：备份、灾难恢复、长时间稳定性和容量运营；
- Web/公开部署、长期语义记忆和过程记忆。

延期不等于默认同意，Module 10 实现不得提前加入占位公共 API。


## 23. 专用交互诊断存储（2026-09-30）

记录规范归 [M02 §19](02_llm_runtime_and_model_profiles.md)，阶段与验收归 [M09 §12/25](09_itinerary_planning_loop.md)，实施白名单归 [M09执行规范§29/W01/W03](09_itinerary_planning_loop_vibe_coding.md)。本节是同库专用诊断例外，ArtifactRepository三方法/容量/安全合同不变；不另设业务真源。

### 23.1 一张专用表

新增 artifact_store.interaction_records；复用 M10 的连接设置/连接池，不建立外键或第二个数据库。列：record_id UUID主键、run_id UUID、trace_id UUID、call_id UUID可空、record_kind受限TEXT、captured_at TIMESTAMPTZ、content_hash CHAR(71)、content_bytes BYTEA、size_bytes BIGINT。正文是 InteractionRecord 的规范 UTF-8 JSON，包含原始正文快照；读取时复验 hash、size、DTO及列/正文身份一致。索引为(run_id,captured_at,record_id)，用于导出；没有 UPDATE/DELETE 路径。

相同 record_id/相同规范正文写重试返回已保存；不同正文冲突报告诊断失败，不覆盖。record_id 只生成一次；未知 commit 的本地兜底沿用相同 ID，导出按 ID/hash 去重，不能把重复保存算成重复 API 调用。call_id 仅是诊断关联，不是执行幂等凭据。

诊断独立限制：单条规范记录最多 8 MiB、全库诊断正文最多 1 GiB。通过诊断专用短事务 advisory lock、元数据 SUM(size_bytes)、INSERT 在同一事务检查，不新增容量表，不修改 Artifact 1 MiB/2048/64 MiB 额度。限额是存储限额，不是累计 Token 限额。达到上限按 §23.2 兜底，不自动删数据或扩大限额。

迁移 m10_0002 只建表、索引和约束；不修改旧业务表、清空测试结果、转换旧 Artifact 或回填交互。M10 revision/业务结构验证同步新 head；诊断表单独可用性失败不冒充业务 Artifact 损坏。迁移必须由独立 Gate 验证，不能让应用启动自动执行 Alembic。

### 23.2 写入失败

诊断一次写入总等待上限 2 秒，包含池等待/SQL/至多一次同 ID 重试，避免借用 M10 默认较长超时拖住每次模型调用。内部诊断超时/容量/表不可用转为记录失败，尝试已配置目录的 run 专属 interactions.jsonl；目录不存在或不可写也明确记录缺失。不得把外部 CancelledError、KeyboardInterrupt 或业务异常吞掉。

本地内容遵守相同凭证规则、record_id/规范格式和完整性标记，不进入 Git/普通日志。没有后台同步、重发 API、自动更换数据库或业务恢复。PostgreSQL 的业务 Artifact、预算、检查点写失败仍按既有 fail-closed；“诊断不阻断”只适用于独立诊断写入。

### 23.3 导出

提供 scripts/export_interactions.py，参数至少 --run-id、--output-dir，可选 --fallback-file。只读查询专用表；显式文件可补充同 run 的兜底记录，校验/去重后输出 interactions.jsonl 和 summary.md。路径必须显式指定，不能覆盖既有输出；不输出凭证、不自动打开网页、不写业务数据库。

summary.md 按模块、M08 action/judgment、M09 发起阶段/预算分类及逐调用列出逻辑次数、物理尝试、缓存命中/未命中输入、输出、思考及非思考输出、结束原因、工具和缺失标志，并能定位原始记录。completion 含 reasoning，不能相加重复收费；非思考=completion-reasoning 只在两者已知且合法时计算。只汇总 actual usage；不将 bytes 当 tokens，也不估算 unknown。

可复用 M09 §12.4 业务指纹标出完全重复的提交及重复字节；供应商没提供每工具/每字段 token 用量时，不能给重复片段标“精确 Token 成本”。价格与费用计算不在本次冻结范围；原始 usage 足够后续按当时价格离线核算。

### 23.4 公共装配和启动边界

新增公共导出 InteractionStoreLimits、DEMO_INTERACTION_STORE_LIMITS、InteractionStoreReadError；ArtifactPersistenceRuntime 暴露 interaction_recorder:InteractionRecorder（只消费M02公共类型）。create_artifact_persistence_runtime新增可选interaction_limits和interaction_fallback_dir（显式路径或None），未传limits使用本节默认值；诊断实现负责本地兜底，现有业务参数不变；连接池仍由Runtime独占生命周期，recorder借用但不创建/关闭池。M13只拿此属性，不取私有pool/SQL。诊断表read_run按捕获时间/ID稳定返回指定run的记录，完整strict校验；读取失败返回安全专用错误，不能伪称空run。导出脚本显式提供数据库设置，不向stdout打印DSN。

业务启动head检查要求m10_0002，artifacts/capacity_state/alembic结构严格校验；interaction_records不再被算作意外业务表。诊断表单独缺失/不完整/权限失败只将recorder标为不可用，业务Repository仍可用，append走兜底。若整个head错误或业务表损坏，启动仍fail-closed；不能把这些错误当仅诊断缺失。本设计不自动执行迁移或重建表。

只允许固定SELECT/INSERT，记录模型验证、时间UTC、非空身份、size/hash与正文一致性必须可证明。global诊断容量锁使用独立常量，与业务capacity锁互不混用；达到8MiB/1GiB时不扩大、不清理，转本地或缺失。scope/fallback目录由生产组合根明确配置，不硬编码宿主路径。验收AC-09～11见M09§25.3。

## 24. M14 新云 profile 与只读容量查询（设计增量，尚未实施）

用户已确认腾讯云上海2核2GB/40GBSSD/3Mbps/200GB月流量，云端使用独立新数据库；M14明确传入ArtifactRepositoryLimits(max_artifact_bytes=1048576,max_artifacts=200000,max_total_content_bytes=4294967296)。通用默认1MiB/2048/64MiB和§8.4首次初始化/完全匹配规则不变；禁止静默扩大已存在的capacity行，禁止迁移或删除本地开发库。交互诊断仍8MiB/1GiB，云fallback_dir=None。4GiB和1GiB只计正文，不包含checkpoint/WAL/索引和系统开销；M14另对实际数据盘free<5GiB拒绝新任务。资源/装配真源见 [M14 §3.2、§12](14_web_application.md)。

未来包根新增ArtifactCapacitySnapshot，严格frozen/extra=forbid，字段schema_version="1.0"、max_artifact_bytes、max_artifacts、max_total_content_bytes、artifact_count、total_content_bytes；整数非负，限制为正，均<=2**53-1，总限额>=单份，实际计数/总正文不超过对应限额。新增异步ArtifactPersistenceRuntime.get_capacity_snapshot()->ArtifactCapacitySnapshot；固定SELECT读取artifact_store.capacity_state singleton_id=1，复验与runtime配置限制一致，遵守既有loop-owner/closed/typed error/池生命周期。无缓存、无写入或capacity自动修复，缺行/坏行/漂移失败关闭；查询失败不能伪称容量为零或可接纳。

新文件capacity.py只实现该DTO；runtime.py只加公开查询方法，__init__.py只加导出。M04 ArtifactRepository三方法/DTO、M10 put事务/容量、原迁移head和交互记录表全部不变。M14只能消费该公共查询，不获取私有pool或SQL。M14最后一次预检不能代替业务put时的事务容量裁判；任务执行过程中容量用尽仍按原typed失败，不截断攻略。

执行阶段和精确测试归 [M14执行规范 §3.2–3.3、W01+W02、C02/C16](14_web_application_vibe_coding.md)。本节只冻结设计增量，不声明公共查询已经存在，也不授权部署/迁移/扩容。
