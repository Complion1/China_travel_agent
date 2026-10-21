# 10 PostgreSQL Artifact 持久化 Vibe Coding 精确执行规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次专用诊断表、独立容量、复用连接池及m10_0002直接写入 [M10设计§23](10_artifact_persistence.md)，新增对M02固定记录类型的依赖；业务ArtifactRepository三方法/正文限制继续适用。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档状态：已批准并冻结
> 适用任务：`MODULE10-ARTIFACT-PERSISTENCE-IMPLEMENTATION`
> 当前用途：为后续实施、代码审查与验收提供可逐条执行的合同；本文自身不授权现在实施

## 1. 契约强度与使用方式

本规范把 [Module 10 权威设计](10_artifact_persistence.md)转换为实施阶段、文件白名单、测试矩阵、PASS/STOP 条件和报告格式。

任何后续实施提示词必须同时引用并实际核对：

- [Module 10 权威设计](10_artifact_persistence.md)；
- 本执行规范；
- [共享实施规则](SHARED_IMPLEMENTATION_RULES.md)；
- [Module 04 权威设计](04_workflow_state_and_artifacts.md)；
- [Module 04 Vibe Coding 契约](04_workflow_state_and_artifacts_vibe_coding.md)；
- [实施路线图](IMPLEMENTATION_ROADMAP.md)与[实施状态](VALIDATION.md)；
- 当前 Module 07～09 Artifact 文档和注册测试（仅作兼容性验证）；Module 10 的唯一直接模块依赖仍是 Module 04。

提示词不得重复粘贴上述文档已有的大段内容，但必须保留当期目标、前置条件、分支、开始 HEAD、文件白名单、允许操作、禁止操作、测试矩阵、PASS、STOP 和最终报告格式。

代码审查与验收必须逐条对照权威设计和本文，不得只看 pytest 是否通过。测试通过但设计不符，仍然失败。

## 2. 当前设计状态与实施授权边界

当前两份 Module 10 文档被批准，只表示“设计可进入评审/后续实施准备”，不表示：

- Module 10 已实现；
- 可以立即运行数据库迁移；
- 可以创建或清空日常数据库；
- 可以 commit、merge、push、建 PR 或部署；
- 可以修改 Module 04～09；
- 可以实现删除、TTL、加密、checkpoint 或主应用组合。

后续每次实施必须由用户对具体阶段提示词授权。本文所有阶段默认：

- 允许在白名单内创建/修改文件和运行明确测试；
- 不允许 stage 或 commit，除非用户对该阶段明确授权且提示词写明；
- 不允许 merge、push、PR、部署；这些动作即使 commit 获授权也仍分别需要明确授权；
- 不允许操作日常/生产数据库；只有隔离测试数据库可由测试自动创建和销毁。

## 3. 开始状态与通用前置条件

每个实施组开始前都必须记录：

```text
branch=<实际分支>
start_head=<40位 SHA>
start_subject=<提交主题>
git_status=<完整 --short 输出或 CLEAN>
staged_files=<完整列表或 NONE>
approved_stage=<阶段编号>
approved_docs=<两份 Module 10 文档路径和当前 hash>
```

预期分支为 `feature/module-10-artifact-persistence`。若文档已经合并且用户要求另开实现分支，必须使用用户批准的准确分支；不能自行换分支、rebase、reset 或覆盖现有更改。

通用前置：

1. 两份 Module 10 文档均存在、已批准且互相链接；
2. 当前 HEAD、分支和 dirty/staged 文件来源已知；
3. `app.workflow_contracts` 的 `ArtifactRepository`、DTO、Registry、异常和 17 项合同基线已读；
4. Module 07～09 当前 Artifact 注册项和公共导出已读，且明确其生产 Registry 组合仍延期到 Module 13；
5. `pyproject.toml`、`requirements.lock`、`Dockerfile`、`.dockerignore`、`.env.example`、`compose.yaml` 当前事实已读；
6. 真实 Docker 可用性只在需要真实 PostgreSQL 的阶段检查；不得在只读预检时擅自启动普通 `postgres`；
7. 工作树中与当期白名单重叠的用户修改必须先停止并报告。

## 4. 通用禁止项

所有阶段都禁止：

- 修改 `app/workflow_contracts/**` 或 Module 01～09 生产代码；
- 修改现有 Module 01～09 合同测试来迁就 Module 10；
- 扩展 `ArtifactRepository` 为 `exists/list/delete/clear/batch/TTL/close/aclose`；
- 自动发现 Artifact Registry、导入所有业务包或用数据库表充当公共 DTO；
- PostgreSQL 失败时自动使用内存版、双写或同步两种 Repository；
- JSONB 第二正文、每种 Artifact 单独表、原始 prompt/provider payload/reasoning 存储；
- 应用启动自动 `alembic upgrade head` 或成功迁移后自动 downgrade；
- 对 Artifact 执行 UPDATE/DELETE，提供清表、volume prune 或普通卷删除能力；
- 记录密码、完整 DSN、SQL 参数、Canonical bytes 或底层异常文本；
- 使用 skip、xfail、rerun、弱化断言、排除难测源码或 fake 替代真实 PostgreSQL 来“通过”门槛；
- 运行 live/provider 网络调用；依赖下载和基础镜像获取仅在对应依赖/构建阶段按现有仓库规范允许；
- 使用破坏性 Git 操作或清理非本任务文件；
- 未经用户分别授权执行 commit、merge、push、PR、部署。

## 5. 总文件白名单

### 5.1 生产与迁移文件

整个 Module 10 实施最多允许修改：

```text
app/artifact_persistence/__init__.py
app/artifact_persistence/errors.py
app/artifact_persistence/settings.py
app/artifact_persistence/runtime.py
app/artifact_persistence/repository.py
app/artifact_persistence/alembic.ini
app/artifact_persistence/migrations/env.py
app/artifact_persistence/migrations/script.py.mako
app/artifact_persistence/migrations/versions/m10_0001_create_artifact_store.py
pyproject.toml
requirements.lock
compose.yaml
.env.example
Dockerfile
README.md
docs/IMPLEMENTATION_STATUS.md
```

两份批准的 Module 10 文档在实施阶段冻结，不在白名单：

```text
docs/10_artifact_persistence.md
docs/10_artifact_persistence_vibe_coding.md
```

若实现发现必须改变设计，STOP，先取得用户批准并单独修订/复审文档，不能在代码阶段顺手改。

### 5.2 新增测试文件

```text
tests/unit/artifact_persistence/test_settings.py
tests/unit/artifact_persistence/test_runtime.py
tests/unit/artifact_persistence/test_retry_policy.py
tests/contract/artifact_persistence/test_public_api.py
tests/contract/artifact_persistence/test_import_boundaries.py
tests/contract/artifact_persistence/test_coverage_gate.py
tests/integration/artifact_persistence/conftest.py
tests/integration/artifact_persistence/test_migrations.py
tests/integration/artifact_persistence/test_repository_contract.py
tests/integration/artifact_persistence/test_concurrency_and_capacity.py
tests/integration/artifact_persistence/test_failures_and_replay.py
tests/integration/artifact_persistence/test_restart_persistence.py
tests/integration/artifact_persistence/test_security.py
tests/support/run_module10_coverage.py
```

不得修改现有 `tests/contract/workflow_contracts/test_artifact_repository_contract.py`。PostgreSQL 集成测试必须逐项镜像其 17 项公共语义，并继续运行原测试保证内存版不回归。

### 5.3 白名单解释

- 新目录中只创建上述文件，不增加 convenience helper、SQL 文件、脚本或快照；
- `pyproject.toml`、`requirements.lock` 只用于声明/锁定 Module 10 必需的 Alembic、SQLAlchemy、psycopg pool 依赖；
- `compose.yaml` 只用于一次性 `artifact-migrate` 服务及必要依赖关系，不发布 PostgreSQL 宿主机端口；
- `.env.example` 保留既有 `POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_IMAGE` 说明，并新增非秘密的 `POSTGRES_HOST`、`POSTGRES_PORT` 示例；Runtime loader 只读取设计规定的五个连接字段，不读取 `POSTGRES_IMAGE`；
- `Dockerfile` 只在当前复制/测试结构不能包含迁移或 Module 10 测试时做最小同步；
- `README.md`、`docs/IMPLEMENTATION_STATUS.md` 只允许在 `MODULE-CLOSEOUT` 更新真实完成事实；
- 任何额外文件都必须 STOP 并取得扩白名单授权。

## 6. 固定依赖与配置合同

### 6.1 依赖选择

实现使用：

- `psycopg` 3 的 async API：运行时 SQL；
- `psycopg_pool.AsyncConnectionPool`：连接池；
- SQLAlchemy 2.x 同步 `postgresql+psycopg` 方言：Alembic 迁移连接；
- Alembic 1.x：迁移版本管理；
- 现有 Pydantic 2：严格 settings 和公共 DTO。

不得引入 ORM model、asyncpg 实现、第二迁移框架、重试库或 secrets 管理库。版本范围先依据当前 Python 3.12 与官方兼容性作最小声明，再由项目既有锁定方式生成 `requirements.lock`；禁止手写虚假的 lock 条目或无关升级。

### 6.2 配置精确规则

只读取：

```text
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
```

公共 settings、加载函数、错误和 runtime/factory 签名必须与权威设计第 7 节一致。禁止 `DATABASE_URL`、`POSTGRES_DSN`、隐式 localhost、默认密码或把缺失密码替换为空串。

`alembic.ini` 必须使用可移植的相对脚本位置，例如基于 `%(here)s`，且不得出现 `sqlalchemy.url` 的真实值。`env.py` 必须调用 Module 10 的同一 settings loader，并用 `URL.create()` 在内存中构造连接 URL。

## 7. 固定数据库与 SQL 合同

### 7.1 对象清单

Module 10 只创建：

```text
artifact_store schema
artifact_store.alembic_version
artifact_store.artifacts
artifact_store.capacity_state
```

初始 Alembic revision 精确为 `m10_0001`。表、列、类型、CHECK、主键、唯一约束和默认值必须逐项对照权威设计第 8 节。

首次空库迁移顺序固定为：在线 `env.py` 使用同一个连接开启最外层 PostgreSQL 事务 → 用固定标识符执行 `CREATE SCHEMA IF NOT EXISTS artifact_store` → 配置 `version_table_schema="artifact_store"` 与事务性 DDL → 运行 Alembic，由其创建版本表并执行只负责两张业务表的 `m10_0001` → schema、版本表、业务表和 revision 记录同提交。任一步失败全部回滚。离线迁移必须安全拒绝。

### 7.2 SQL 安全规则

- schema/table/column 标识符使用模块内固定常量或迁移字面量；
- 所有运行时值使用 psycopg 参数绑定；
- 运行时不得设置 `search_path` 后省略 schema，SQL 必须显式写 `artifact_store.<table>`；
- 所有连接设置 UTC、`statement_timeout=10s`、`lock_timeout=5s`；
- 所有写入使用显式事务；
- `capacity_state` 行锁只锁 `singleton_id=1`；
- 不执行 Artifact UPDATE/DELETE，唯一 UPDATE 是同一事务递增容量计数；
- 测试篡改数据只能在隔离测试库中用测试专用连接直接 SQL 完成。

### 7.3 结构检查最小集合

启动检查不只读 Alembic revision，还至少验证：

- `artifact_store.artifacts` 和 `capacity_state` 是普通表；
- 所有规定列存在且基本类型、nullable 与默认值符合设计；
- Artifact 主键及 `(run_id, phase, logical_step)` 唯一约束存在；
- Canonical bytes 长度、字段格式、phase、容量范围和 singleton CHECK 存在；
- `artifact_store.alembic_version` 只有一个值且精确等于代码单一 head。

禁止启动时读取/解码/hash 全部 Artifact，也禁止发现差异后自动建表、改表或改 revision。

## 8. 固定公共符号与错误映射

### 8.1 公共符号

包根 `__all__` 必须与权威设计第 7.1 节逐字一致，精确为：

```text
ArtifactPersistenceSettings
ArtifactPersistenceRuntime
ArtifactPersistenceError
ArtifactPersistenceConfigurationError
ArtifactPersistenceDependencyError
create_artifact_persistence_runtime
load_artifact_persistence_settings
```

测试必须证明：

- 没有 PostgreSQL repository 具体类、pool、SQL helper、Alembic helper 外泄；
- import 包根不读取环境、不连接数据库、不检查迁移且无其他 I/O；
- `runtime.repository` 可按 Module 04 `ArtifactRepository` 使用；
- `app.workflow_contracts` 没有反向导入；
- Module 05～09 无需依赖新包即可导入和通过原合同；其中只有当前 Module 07～09 的注册项进入兼容性测试，且只在测试中组合。

### 8.2 异常矩阵

| 层级 | 场景 | 精确异常 |
|---|---|---|
| startup | settings 非法、head/结构不符、合法 limits 与数据库固化值不符 | `ArtifactPersistenceConfigurationError()` |
| startup | 伪造或非法 `ArtifactRepositoryLimits` 输入 | `ArtifactRepositoryUsageError()` |
| startup | PostgreSQL/池不可用、数据库不存在、认证或权限不足 | `ArtifactPersistenceDependencyError()` |
| repository | 短暂连接故障第二次仍失败 | `ArtifactRepositoryDependencyError()` |
| repository | 语句/锁超时、认证/权限或其他非业务 psycopg/pool 失败 | `ArtifactRepositoryDependencyError()`，一次尝试 |
| repository | closed/cross-loop | `ArtifactRepositoryUsageError()` |
| repository | request/registry/type/version/model | 对应 Module 04 异常 |
| repository | not found/conflict/integrity/size/capacity | 对应 Module 04 异常 |

新增异常必须零参数、`__slots__=()`、`str()` 仅类名且 `__cause__ is None`。只有明确的短暂连接类才重试一次；其他非业务数据库失败在第一次后映射。取消和进程退出类 `BaseException` 保持原样传播，并执行资源清理。

## 9. 测试夹具与真实 PostgreSQL 安全合同

### 9.1 宿主编排与临时资源身份

Docker 生命周期只由宿主侧阶段编排器控制。这里的“编排器”是当期已批准实施提示词中的一组可审计宿主 Docker 命令，不新增仓库脚本。test 镜像不安装 Docker CLI、不挂载 Docker daemon socket，也不创建、restart 或删除 Docker 对象。

宿主编排器必须为每次 Gate 生成不可碰撞的前缀，例如：

```text
cta-m10-test-<随机UUID>
```

对应容器、network、volume 必须同时带固定 label：

```text
com.china-travel-agent.module10-test=true
```

宿主销毁前必须通过同一宿主 shell 的 Docker inspect 同时确认：

- 精确对象 ID 是本次夹具创建并保存的 ID；
- 名称带本次随机前缀；
- 固定 test label 存在；
- volume 名不等于且不包含普通 `postgres_data` 的解析目标。

任一核验失败，停止清理并让测试失败。禁止 `docker system prune`、`docker volume prune`、按通配符批量删除或枚举后交给另一 shell 删除。

宿主职责固定为：保存对象 ID、创建随机隔离 network/volume/PostgreSQL、等待健康、启动同网络的迁移/test 容器、在两个指定测试步骤之间 restart 精确 PostgreSQL 容器、最后三重核验并清理。`tests/integration/artifact_persistence/conftest.py` 只消费五个连接字段和不含秘密的测试身份 token；它必须拒绝缺少测试标记、数据库名不带本次随机前缀、未迁移或版本不对的目标，但不调用 Docker。若这组宿主命令无法在提示词中安全维护而需要持久编排脚本，必须先 STOP 并申请扩展白名单。

### 9.2 测试凭据

- 每次 Gate 生成测试专用密码；
- 通过权限受限的临时 env-file 交给 Docker，不把密码拼进命令字符串或输出；
- 测试结束删除该临时文件；
- captured stdout/stderr、异常、日志和报告中不得出现明文或 URL 编码密码；
- 临时路径、对象 ID 可以报告，密码不能报告。

### 9.3 真实门不可跳过

`tests/integration/artifact_persistence` 未纳入当前 pytest 默认 `testpaths` 时，阶段命令必须显式指定它。若 Docker daemon、镜像或 PostgreSQL 不可用，真实资格门状态是 FAIL/STOP，不是 skip 或 PASS。

普通 integration 和 coverage 在已由宿主启动、完成迁移的隔离 PostgreSQL 上运行。真实容器 restart 使用两个精确 pytest node：第一节点写入固定 Artifact 后退出；宿主 restart 并等待健康；第二节点从全新 test 容器按固定 `OperationId` 执行 `lookup/get`。coverage harness 不收集这两个外部编排节点，不合并两次 coverage，也不得把 Runtime 重建冒充 PostgreSQL 容器 restart。

## 10. AP-T00——只读预检

### 目标

证明实施基础、文档、Git、依赖和白名单真实可用，不产生修改或启动数据库。

### 前置条件

- 用户已批准执行 AP-T00；
- 两份 Module 10 文档已通过设计评审；
- 当前工作树来源可解释。

### 文件白名单

无。只读。

### 允许操作

- `git status --short`、`git diff --name-only`、`git diff --cached --name-only`、branch/HEAD 查询；
- `rg --files`、`rg`、读取所有规范和直接依赖代码；
- 检查 Python/Docker/Compose 版本，但不拉镜像、不启动服务；
- 解析现有 lock、Compose、Dockerfile、ignore 与环境变量名；
- 记录现有 `postgres_data` 等普通资源的只读身份，供后续防误删。

### 测试矩阵

不运行 pytest、Docker build、迁移或数据库测试。只做引用存在性、Markdown 链接和 Git 状态检查。

### PASS

- branch/HEAD/dirty/staged 完整记录；
- 所有依据文件存在且无实质冲突；
- Module 04 Protocol 仍精确只有 `put/lookup/get`；
- 总白名单足以实现，不需要改上游；
- 普通 PostgreSQL 容器/卷未被启动、修改或删除。

### STOP

- 设计未批准或两份文档冲突；
- 需要修改上游公共合同；
- 白名单与现有用户改动重叠；
- 当前数据库资源身份不清或后续隔离策略不可执行；
- 依赖要求与 Python 3.12/当前构建方式根本冲突。

### 报告

使用第 19 节通用报告，额外列出：规范引用、普通数据库资源只读基线、依赖差异和下一实施组建议。最终状态只能为 `AP_T00_PASSED_AWAITING_AUTHORIZATION` 或 `STOP`。

## 11. AP-T01——配置、异常与依赖基础

### 目标

建立最小依赖、严格 settings loader 和安全启动异常，不创建数据库代码或公共半成品 Runtime。

### 前置条件

- AP-T00 PASS；
- 用户批准联合实施 AP-T01 + AP-T02；
- 提示词写明两个阶段完成后统一执行联合测试；
- AP-T02 迁移安全门仍需单独 PASS 才能继续。

### 文件白名单

```text
app/artifact_persistence/errors.py
app/artifact_persistence/settings.py
pyproject.toml
requirements.lock
tests/unit/artifact_persistence/test_settings.py
```

### 允许操作

- 添加 Alembic、SQLAlchemy、psycopg-pool 的最小直接依赖并更新锁文件；
- 实现五字段严格 settings 和 loader；
- 实现三个零参数启动异常；
- 测试严格类型、缺失/空值、端口、SecretStr、异常字符串和无 secret 泄露；
- 在本阶段白名单内修复明确缺陷。

### 禁止操作

- 创建 `__init__.py` 公共导出、Runtime、Repository、迁移或 Compose 服务；
- 用 `.env` 真实值跑测试；
- 升级无关依赖。

### 测试矩阵

本阶段先运行 settings 定向测试；AP-T02 完成后统一运行 AP-T01/AP-T02 全部测试、依赖一致性和 build import 检查。

### PASS

- settings 与权威设计第 7.2 节精确一致；
- 新异常安全且无 cause/message 泄露；
- lock 仅含必需依赖变化且可重建；
- 无公共半实现 API。

### STOP

- 需要完整 DSN 或真实密码进入仓库；
- 依赖解析要求无关大升级；
- 必须改变 settings 公共字段或异常层级；
- 定向测试失败或出现白名单外文件。

### 报告

按第 19 节，额外报告依赖新增/版本、lock 变化原因、秘密哨兵验证。阶段状态为 `AP_T01_IMPLEMENTED_PENDING_T02_JOINT_GATE` 或 `STOP`。

## 12. AP-T02——Alembic、数据库结构与 Compose 迁移服务

### 目标

创建前进式 `m10_0001` 迁移和显式一次性迁移入口；不实现应用 Repository。

### 前置条件

- AP-T01 实现完成；
- 用户已批准 AP-T01 + AP-T02；
- Docker 真实测试资源安全策略可执行；
- 已记录普通 `postgres_data` 等资源基线。

### 文件白名单

```text
app/artifact_persistence/alembic.ini
app/artifact_persistence/migrations/env.py
app/artifact_persistence/migrations/script.py.mako
app/artifact_persistence/migrations/versions/m10_0001_create_artifact_store.py
compose.yaml
.env.example
Dockerfile
tests/integration/artifact_persistence/conftest.py
tests/integration/artifact_persistence/test_migrations.py
```

加上 AP-T01 白名单，仅用于联合缺陷修复；不得扩到 Runtime/Repository。

### 允许操作

- 实现 `artifact_store`、Alembic version table、`artifacts` 和 `capacity_state`；
- 实现 sync SQLAlchemy + psycopg 的在线 Alembic `env.py`：同一连接/最外层事务先创建 schema，再配置版本表 schema 并运行 revision；离线模式安全拒绝；
- 增加一次性 `artifact-migrate` Compose 服务；
- 由宿主阶段编排器建立/清理独立 Docker PostgreSQL 测试资源；`conftest.py` 只验证并消费注入的隔离数据库身份；
- 测试空库 upgrade、重复 upgrade、表约束、版本表位置、失败回滚和 downgrade 拒绝；
- 运行 AP-T01/AP-T02 联合测试和最小 Docker 构建检查。

### 禁止操作

- 运行普通日常 `postgres` 迁移；
- 删除任何既有容器/卷；
- 在 `alembic.ini`/Compose/日志中写凭据；
- 应用自动 migration、autogenerate、成功迁移后的 downgrade 或 Artifact 测试数据；
- 创建 Runtime/Repository。

### 测试矩阵

至少包括：

1. AP-T01 settings 全测；
2. Alembic 配置静态安全测试；
3. 独立空库 `upgrade head` 到 `m10_0001`；
4. 再次 `upgrade head` 无变化；
5. 精确 schema/table/column/default/constraint introspection；
6. 真正空库中人为制造 schema 引导后、revision 完成前的异常，证明 schema、版本表、业务表和 revision 记录全部回滚且均不存在；
7. `downgrade` 安全拒绝且表仍在；
8. 测试资源 label/ID/名称三重核验与普通卷未动证明；
9. secret sentinel 不出现在 captured output；
10. Compose config 渲染不包含真实秘密或宿主机 PostgreSQL 端口发布。

### PASS

- AP-T01/AP-T02 联合测试全通过；
- `m10_0001` 与权威设计第 8～9 节逐项一致；
- 空库迁移失败后 schema/版本表/业务表/revision 均不存在，成功后不自动回退；
- 仅隔离测试资源被创建/销毁；
- 普通数据库资源前后身份一致。

### STOP

- 迁移需要改写已确认 schema；
- 无法安全区分测试卷与普通卷；
- downgrade 删除成功、迁移非事务或版本表落入 `public`；
- Compose 发布数据库端口或需要真实 secret 入仓；
- 任何测试/构建失败。

### 报告

按第 19 节，额外报告 revision、upgrade 前后版本、创建对象、downgrade 结果、容器/network/volume ID（不含秘密）、普通资源前后核验。最终状态为 `AP_T02_MIGRATION_GATE_PASSED_AWAITING_AUTHORIZATION` 或 `STOP`。

## 13. AP-T03——PostgreSQL `lookup`、`get` 与完整性读取

### 目标

实现私有 PostgreSQL Repository 的读取路径和严格 DTO 重建，不建立公共 Runtime。

### 前置条件

- AP-T02 迁移安全门 PASS；
- 用户批准联合实施 AP-T03 + AP-T04；
- 每个测试场景使用已迁移的隔离数据库；
- AP-T03/AP-T04 完成后统一执行完整 Repository 联合测试。

### 文件白名单

```text
app/artifact_persistence/repository.py
tests/integration/artifact_persistence/test_repository_contract.py
tests/integration/artifact_persistence/test_failures_and_replay.py
tests/integration/artifact_persistence/test_security.py
```

AP-T02 的隔离数据库连接/身份验证夹具只允许为本阶段需要做最小增量修改；Docker 生命周期仍只在宿主编排。

### 允许操作

- 实现私有 Repository 构造、lifecycle guard、`lookup` 和 `get`；每个公共调用只执行一次 Module 04 两段式 fresh copy，重试只复用该不可变快照；
- 显式 schema SQL、参数绑定和严格行到 DTO 转换；
- 调用 Module 04 Registry `decode()`；
- 测试 absent/found、not found、七字段篡改、正文/hash/size/type/version 损坏、expected model，以及 `model_construct()`/嵌套 mutation 伪造输入；
- 测试 lookup 不取正文、启动不全表扫描的可验证边界；
- 在白名单内修复明确缺陷。

### 禁止操作

- 修改 Module 04 helper；
- 复制 Canonical JSON 算法；
- 对损坏行自动修复或删除；
- 实现 `put`、Runtime、公共导出或重试。

### 测试矩阵

先运行读取定向真实 PostgreSQL 测试；联合 Gate 等 AP-T04 完成后执行。测试可以用测试专用 SQL 预置/篡改行，但只能操作本次隔离库。

### PASS

- lookup 精确返回 `ArtifactAbsent/ArtifactFound`，伪造/变异 envelope 按 Module 04 优先得到 `ArtifactRequestValidationError`；
- get 严格核对 ref 七字段并委托 Registry decode；
- 所有 public 错误映射正确且无底层泄露；
- 不复制 canonicalization，不自动修复。

### STOP

- 需要改 Module 04 DTO/Registry/异常；
- 需要新增 Repository 方法；
- SQL 不能参数化或需要动态用户标识符；
- 定向测试失败、秘密泄露或出现白名单外文件。

### 报告

按第 19 节，状态为 `AP_T03_IMPLEMENTED_PENDING_T04_JOINT_GATE` 或 `STOP`。

## 14. AP-T04——`put`、幂等、容量、并发与事务

### 目标

实现完整写入算法，并在真实 PostgreSQL 中证明第一胜者、容量行锁和事务原子性。

### 前置条件

- AP-T03 实现完成；
- 用户已批准 AP-T03 + AP-T04；
- 迁移结构和隔离数据库连接/身份验证夹具仍通过。

### 文件白名单

```text
app/artifact_persistence/repository.py
tests/integration/artifact_persistence/test_repository_contract.py
tests/integration/artifact_persistence/test_concurrency_and_capacity.py
tests/integration/artifact_persistence/test_failures_and_replay.py
tests/integration/artifact_persistence/test_security.py
```

允许对 AP-T03 文件做同组缺陷修复；迁移文件冻结，除非确认是 AP-T02 未发现的 P0/P1 且先 STOP 报告。

### 允许操作

- 实现 `put` 的一次 prepare 快照、先重放/冲突、容量行锁、锁后再查询、再做单件/数量/总量门槛、INSERT RETURNING、计数更新和异常映射；
- 测试顺序/并发同内容重放、不同内容冲突和原值不变；
- 测试满库重放优先、新写超限、单件/数量/总字节；
- 测试已有不同内容时冲突优先于本次单件超限，以及等待容量锁期间出现第一胜者后仍先判重放/冲突；
- 用测试故障注入证明 INSERT 与计数同提交/同回滚；
- 证明不同 `OperationId` 的相同正文获得不同 ref、可共享 hash；
- 将现有 Module 04 合同 helper 的 17 项逐项映射到 PostgreSQL 测试用例。
- 测试唯一约束失败后先回滚、再开短事务查询：OperationId 第一胜者映射 replay/conflict；查询不到映射 dependency error，不能在 aborted transaction 中查询或重生成 UUID。

### 禁止操作

- `SERIALIZABLE`、表锁、advisory 全局锁或跳过容量锁；
- 在锁后第二次 OperationId 查询前检查任意单件/数量/总量门槛；
- UPDATE/覆盖原 Artifact；
- 按 hash 去重不同 OperationId；
- 修改迁移来绕开测试失败而不重新过 AP-T02 门。

### 测试矩阵

联合 Gate 必须包括：

1. `tests/integration/artifact_persistence/test_repository_contract.py` 镜像 M04 17 项；
2. 至少两个独立连接/Runtime 的相同内容并发第一胜者；
3. 至少两个独立连接/Runtime 的不同内容并发冲突；
4. 多连接竞争最后一个数量/字节容量，提交后计数不超限；
5. 等待容量锁期间另一请求提交，第二次 OperationId 查询正确；
6. 既有不同内容且本次正文超单件上限时得到 conflict；容量锁等待后发现第一胜者时错误优先级不变；
7. 插入后计数失败与计数前插入失败均整事务回滚；
8. unique violation 原事务回滚后新事务重查，并区分 OperationId 第一胜者与 UUID/结构异常；
9. exact replay 在库满时不改计数且返回七字段原 ref；
10. conflict 不改正文或计数；
11. 同一公共调用的 request/PreparedArtifact 快照只生成一次，连接重试不重读嵌套 content；
12. M04 原内存合同测试继续通过；
13. AP-T02 迁移与 AP-T03 安全/完整性测试继续通过。

### PASS

- AP-T03/AP-T04 联合真实门全部通过；
- M04 17 项有一一映射表，无遗漏、无弱化；
- 并发后数据库唯一性、计数和总字节精确；
- `READ COMMITTED` + 单容量行锁事实可审计，且重放/冲突优先于所有容量错误；
- 无 UPDATE/DELETE、表锁、fallback 或双写。

### STOP

- 任何竞争测试偶发失败；
- 容量可能超限或计数与实际行数不一致；
- 必须改变 schema/迁移/上游合同；
- 事务故障留下半提交；
- 用 sleep 时序或单连接伪装并发正确；
- 任何测试失败或有未解释异常文件。

### 报告

按第 19 节，额外附 M04 17 项映射表、并发参与者数量、第一胜者结果、容量前后计数和事务故障证据。状态为 `AP_T04_REPOSITORY_GATE_PASSED_AWAITING_AUTHORIZATION` 或 `STOP`。

## 15. AP-T05——Runtime、连接池、启动检查、重试与公共 API

### 目标

把私有 Repository 装入可安全创建/关闭的 Runtime，完成迁移/结构/容量启动检查、精确一次重试和包根公共导出。

### 前置条件

- AP-T04 Repository Gate PASS；
- 用户批准联合实施 AP-T05 + AP-T06；
- 当前 schema 和 API 设计无变更；
- AP-T05/AP-T06 后统一执行最终代码 Gate。

### 文件白名单

```text
app/artifact_persistence/__init__.py
app/artifact_persistence/runtime.py
app/artifact_persistence/repository.py
tests/unit/artifact_persistence/test_runtime.py
tests/unit/artifact_persistence/test_retry_policy.py
tests/contract/artifact_persistence/test_public_api.py
tests/contract/artifact_persistence/test_import_boundaries.py
tests/integration/artifact_persistence/test_failures_and_replay.py
tests/integration/artifact_persistence/test_restart_persistence.py
tests/integration/artifact_persistence/test_security.py
```

### 允许操作

- 实现 `AsyncConnectionPool(open=False)` 的显式 open/wait/close；
- 实现迁移 head、结构和容量初始化/精确匹配检查；
- 实现 Runtime async context manager、幂等 close、同 loop 约束；
- 实现只对 PoolTimeout/连接类 OperationalError 的一次重试；重试循环只接收公共入口已经生成的不可变快照；
- 实现精确七项包根 `__all__`；
- 测试 factory 任意 `BaseException` 路径清理、取消透传和安全映射；
- 测试 runtime 重建读取同一数据。

### 禁止操作

- 公开具体 Repository/pool；
- 启动时 upgrade、扫描全文或修复容量；
- 重试业务错误、语句/锁超时、权限/配置/完整性错误；
- 重试超过一次或在取消后重试；
- 运行时失败自动 fallback 到内存版。

### 测试矩阵

本阶段定向测试至少覆盖：

1. pool `open=False`、显式 open/wait、1/4 大小及固定超时；
2. UTC/READ COMMITTED 会话；
3. factory 成功和每个启动步骤失败时关闭；
4. 未迁移、behind、ahead、多 head、表/约束缺失拒绝；数据库不存在、认证和权限失败精确映射 dependency error；
5. 空库容量初始化、并发初始化、limits 精确匹配/漂移、非空表缺行拒绝；伪造/非法 limits 得到 `ArtifactRepositoryUsageError`，合法漂移才得到 persistence configuration error；
6. Runtime context manager、重复 close、close 后和 cross-loop 使用；已关闭后 `__aenter__` 拒绝，`__aexit__` 返回 False/None 且不吞业务异常；
7. 三个 Repository 方法一次短暂故障后成功；
8. 第二次短暂故障映射 dependency error；
9. 所有不可重试错误尝试次数恰为 1，并安全映射为既定 Module 04 业务异常或 `ArtifactRepositoryDependencyError`；
10. CancelledError 等 `BaseException` 原样传播且资源收敛；
11. 包根导出、签名、Protocol 结构兼容和 import boundary；仅 import 时无环境读取、连接或迁移 I/O；
12. Runtime 关闭重建后 ref/正文仍可读。

### PASS

- Runtime 与权威设计第 7、10、12 节逐项一致；
- public API 精确，无私有实现外泄；
- 重试集合和次数精确，每次公共调用的严格输入快照只生成一次；
- 所有失败路径关闭连接池且无 secret 泄露；
- 启动绝不自动改数据库结构或 Artifact 内容。

### STOP

- 需要暴露 pool/connection 或改变 factory 签名；
- head/结构检查只能靠自动修复；
- 取消被包装、吞掉或重试；
- 失败资源不能确定已关闭；
- 定向测试失败或白名单不足。

### 报告

按第 19 节，额外报告 pool 参数、启动检查矩阵、重试分类/尝试次数、资源关闭证据和公共 `__all__`。状态为 `AP_T05_IMPLEMENTED_PENDING_T06_JOINT_GATE` 或 `STOP`。

## 16. AP-T06——真实重启、安全、覆盖率与完整回归 Gate

### 目标

在代码最终候选版上完成真实 PostgreSQL、容器重启、完整安全、覆盖率、Docker 和 Module 01～09 回归；随后进入独立代码审查。

### 前置条件

- AP-T05 实现完成；
- 用户已批准 AP-T05 + AP-T06；
- 生产/迁移代码不再有已知 TODO、pass、NotImplemented 或临时分支；
- 普通数据库资源只读基线仍可核对。

### 文件白名单

```text
tests/contract/artifact_persistence/test_coverage_gate.py
tests/integration/artifact_persistence/conftest.py
tests/integration/artifact_persistence/test_migrations.py
tests/integration/artifact_persistence/test_repository_contract.py
tests/integration/artifact_persistence/test_concurrency_and_capacity.py
tests/integration/artifact_persistence/test_failures_and_replay.py
tests/integration/artifact_persistence/test_restart_persistence.py
tests/integration/artifact_persistence/test_security.py
tests/support/run_module10_coverage.py
```

允许在第 5 节生产/迁移总白名单内修复本 Gate 真实复现的小范围明确缺陷。若需要改设计、迁移形状或上游合同，STOP。

### 允许操作

- 补齐真实重启、安全和 coverage harness；
- 从当前工作树重建 test/runtime 镜像；
- 宿主按第 9 节创建/核验隔离资源，在同一隔离 network 的 test 镜像中执行 Module 10 门；
- 宿主在两个精确 pytest node 之间 restart 保存 ID 对应的 PostgreSQL 容器并等待健康；
- 运行 Module 01～09 离线 unit/contract/integration 回归和 compileall；
- 执行静态秘密扫描、依赖/导入/白名单检查；
- 对最终候选代码做只读独立代码审查。

### 禁止操作

- 使用普通 `postgres_data`、普通应用容器或日常数据库做测试；
- 向 test 镜像安装 Docker CLI、挂载 Docker daemon socket，或让 pytest 控制 Docker 生命周期；
- 因 Docker 不可用而 skip；
- 覆盖率合并多个数据文件、改变 source、排除生产文件或只报告 line；
- 在代码审查前开始 `MODULE-CLOSEOUT`；
- 为通过回归修改上游测试。

### 16.1 coverage harness 固定算法

`tests/support/run_module10_coverage.py` 必须在一个 Python 进程中：

1. 使用 coverage API，`branch=True`，source 精确为 `app.artifact_persistence`；
2. 通过 pytest API 一次运行 Module 10 全部 unit、contract，以及不负责 Docker 生命周期的真实数据库 integration；精确排除 `test_restart_persistence.py` 中由宿主分两段调用的 seed/verify 节点；
3. 将 JSON 报告写入测试临时目录；
4. 用未舍入的覆盖分子/分母分别计算 line、branch、combined；
5. statements 为 0 或 combined 分母为 0 时失败；branches 为 0 时 branch 可记 100%，但 combined 仍用真实分母；
6. line、branch、combined 任一低于 70.00% 时非零退出；
7. pytest 非零、零收集、skip、xfail、rerun、采集错误时非零退出；
8. 打印 statements、covered lines、branches、covered branches、三项百分比、missing lines/branches 和 exit code；
9. 不打印 secrets、DSN 或测试数据库密码。

### 16.2 完整测试矩阵

必须逐项运行并记录准确命令、镜像 ID、测试数、skip/xfail/rerun、exit code：

1. Module 10 unit tests；
2. Module 10 public/import/coverage harness contract tests；
3. Module 10 普通真实 PostgreSQL integration tests；不得收集 restart 的两个外部编排节点；
4. Alembic 空库/重复/失败/downgrade/版本检查；
5. M04 17 项 PostgreSQL 映射、并发与容量；
6. Runtime disconnect/reconnect；
7. restart 两段 Gate：精确 seed node PASS → 宿主 restart 已保存 ID 的容器并等待健康 → 全新 test 容器中的精确 verify node PASS；两个节点均不得 skip；
8. secret sentinel、SQL 参数化、日志/异常/captured output；
9. `python -m pytest -q -p no:cacheprovider tests/unit tests/contract`；
10. 仓库已有且与 Module 10 相关的离线 integration Gate；
11. `python -m compileall -q app tests`；
12. Module 10 单进程 coverage harness；不含两个 restart 编排节点，且不得合并 restart 两次运行的数据；
13. `git diff --check`、白名单、staged 空和异常缓存检查；
14. test/runtime 镜像 smoke，确保 Alembic 文件和新包在镜像内可导入；
15. 普通数据库容器/卷前后 ID 与状态未被测试改变。

具体宿主 Docker 命令要依据实施时 Compose/Dockerfile 事实写入当期提示词，不得从旧模块机械复制 `--network none` 到需要连接测试 PostgreSQL 的容器。测试网络必须是当次随机命名的隔离 network，只连接当次测试应用容器和 PostgreSQL；不连接外网或普通项目网络。宿主必须以 finally 等价流程在成功/失败两条路径上执行三重身份核验和定向清理；身份核验失败时保留对象并 STOP，不得扩大删除。

### 16.3 独立代码审查

完整行为 Gate PASS 后，安排至少两份只读审查：

- Review A：PostgreSQL schema、SQL、事务、锁、幂等、重试、迁移和安全；
- Review B：公共 Protocol、Module 04～09 兼容、生命周期、测试真实性、延期边界和白名单。

每条发现必须含：文件、行号、P0～P3、问题、后果、最小修复。P0/P1/P2 必须在总白名单内修复后重跑受影响定向门、完整 Module 10 门和必要回归，并由同一审查方向复审。P3 必须明确“接受/延期及理由”，不能静默忽略。

### PASS

- AP-T05/AP-T06 联合全部 Gate 通过；
- 真实 PostgreSQL 无 skip，容器 restart 持久性成立；
- line/branch/combined 均 ≥70%；
- Module 01～09 回归、compileall、Docker smoke、安全和白名单均通过；
- 两个审查方向均 PASS，无 P0/P1/P2；
- 普通数据库资源未改变；
- 未开始 closeout。

### STOP

- 任一测试、覆盖率、构建、smoke、安全或审查失败；
- integration 被 skip/xfail/rerun；
- 普通数据库资源被触碰或测试清理身份不确定；
- 需要改变设计、迁移、上游合同或扩大白名单；
- 有 P0/P1/P2 未解决；
- 生产/测试文件在最终 coverage 后发生未重新资格的变化。

### 报告

按第 19 节，额外附完整测试表、coverage 原始分子/分母、Docker 镜像/资源 ID、重启证据、安全扫描、审查发现与复审结果。状态为 `AP_T06_CODE_AND_REVIEW_PASSED_AWAITING_CLOSEOUT_AUTHORIZATION` 或 `STOP`。

## 17. AP-T07——`MODULE-CLOSEOUT`

### 目标

按共享规则同步真实文档/构建入口，在结束工作树上重建并复验，不增加新设计或新功能。

### 前置条件

- AP-T06 全部 PASS；
- 用户单独批准 AP-T07；
- 代码、测试、迁移和独立复审最终版 hash 已记录；
- 没有未解决 P0/P1/P2；
- closeout 开始前 staged 为空。

### 文件白名单

```text
README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile
compose.yaml
.env.example
```

只在当前事实确有不一致时修改。Module 10 设计文档、生产代码、迁移和测试在本阶段冻结。

### 允许操作

- 按 [共享实施规则第 7 节](SHARED_IMPLEMENTATION_RULES.md)核对并同步 README、状态、镜像入口和示例配置；
- 只写已实际验证的 Module 10 状态、命令和边界；
- 从结束工作树重建镜像；
- 重跑 Module 10 全门、受影响回归、compileall、runtime/migration smoke 和安全检查；
- 证明代码/迁移/测试 hash 与 AP-T06 最终资格版一致。

### 禁止操作

- 增加或修复生产功能；
- 修改设计结论或把延期能力写成已完成；
- 创建/迁移日常数据库；
- 自动执行 commit、merge、push、PR 或部署；
- 复用 closeout 前旧镜像替代重建。

### 测试矩阵

- 结束工作树 test/runtime 镜像重建；
- Module 10 unit/contract/普通真实 PostgreSQL integration，并由宿主重新执行独立的 restart 两段 Gate；
- Module 01～09 全量离线回归；
- compileall；
- migration service 空测试库 smoke 与应用 head check；
- runtime package import smoke；
- secret、白名单、staged、diff-check、异常文件检查；
- coverage 可引用 AP-T06 最终结果的唯一条件：生产代码、迁移和全部 Module 10 测试逐字节未变；任一变化必须重跑 coverage。

### PASS

- README/状态与真实实现一致，未声称 Module 11/19/20 能力；
- 结束工作树全部 Gate 通过；
- 新镜像证据可审计；
- 只有 closeout 白名单内必要变化；
- staged 为空，commit/push/PR/deploy 状态准确报告。

### STOP

- closeout 暴露生产缺陷；
- 文档同步需要改变权威设计；
- 生产/迁移/测试 hash 改变却未重新资格；
- 任一 Gate 失败；
- 需要操作日常数据库或扩大白名单；
- 存在未解释 dirty/staged 文件。

### 报告

必须使用第 19 节完整报告。最终状态只能为 `MODULE10_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE` 或 `STOP`，不能写“已合并”“已部署”或“生产就绪”，除非对应动作另有真实授权和证据。

## 18. 实施提示词生成规则

后续每份阶段提示词必须放在一个 Markdown 代码块中，并包含：

1. 任务名和当期相邻阶段，例如 `AP-T03 + AP-T04`；
2. 对两份 Module 10 文档、共享规则和相关上游文档的路径引用；
3. 当前 branch、开始 HEAD、subject、dirty/staged 基线；
4. 前一 Gate 的精确状态和证据摘要；
5. 当前目标和明确非目标；
6. 当前阶段精确文件白名单；
7. 允许操作与禁止操作；
8. 实现顺序和必须保持的公共符号/数据库合同；
9. 测试矩阵，包括真实 PostgreSQL 或明确说明本阶段尚不到真实门；
10. PASS、STOP 和不得自行扩范围条款；
11. commit 是否被本阶段明确授权；
12. 最终报告格式。

原则上一次提示词实施相邻两个阶段并在两阶段完成后统一测试，以减少重复构建。以下例外允许单独 Gate：

- AP-T00 只读预检；
- AP-T02 迁移安全门；
- 独立代码审查及其复审；
- AP-T07 closeout；
- 发现删除/迁移/数据安全风险时的停止与人工决定。

不能为了凑两个阶段跳过前置、迁移验证或复审。

## 19. 通用阶段报告格式

每个阶段或联合实施组结束时必须报告：

```text
任务：
阶段：
最终状态：PASS 状态名 / STOP

Git：
- branch：
- start HEAD：
- start subject：
- end HEAD：
- end subject：
- staged：NONE / 精确文件

规范核对：
- 引用文档及 hash：
- 权威设计逐条核对结果：
- 执行规范逐条核对结果：
- 设计偏差：NONE / 精确说明

修改：
- 新增文件：
- 修改文件：
- 删除文件：NONE / 精确文件
- 白名单外文件：NONE / 精确文件与来源
- 异常/缓存文件：NONE / 精确文件

数据库与迁移：
- 数据库类型/隔离身份：
- revision before/after：
- schema/table/constraint：
- 日常/生产数据库是否触碰：NO / 说明
- 测试容器/network/volume ID：
- 清理核验：

测试：
- 命令：
- 环境/镜像 ID：
- collected/passed/failed/skipped/xfail/rerun：
- exit code：
- 定向测试：
- Module 10 完整门：
- Module 01～09 回归：
- Docker/runtime/migration smoke：
- compileall：
- line coverage：
- branch coverage：
- combined coverage：
- missing lines/branches：

安全与审查：
- secret sentinel：
- SQL 参数化/无敏感日志：
- 普通资源前后核验：
- Review A：
- Review B：
- P0/P1/P2/P3：
- 复审：

版本控制与外部动作：
- commit：NOT_AUTHORIZED / hash
- merge：NOT_AUTHORIZED / 证据
- push：NOT_AUTHORIZED / 证据
- PR：NOT_AUTHORIZED / URL
- deploy：NOT_AUTHORIZED / 证据

延期：
- Module 11：
- Module 19：
- Module 20～22/Production：

下一步：
- 等待的用户授权或 STOP 解除条件：
```

不能省略 commit、push、PR、部署字段；未执行时必须明确写 `NOT_AUTHORIZED` 或 `NOT_RUN`，不能留空。

## 20. 代码审查逐条核对表

审查者至少逐条核对：

- [ ] 包边界和依赖方向；
- [ ] `ArtifactRepository` 三方法未变；
- [ ] 包根七项公共导出；
- [ ] 包根 import 无环境/连接/迁移副作用，context manager 不吞业务异常；
- [ ] settings 五字段、无 DSN；
- [ ] 启动异常与 Repository 异常分层；
- [ ] 数据库对象、列、CHECK、唯一约束、默认值；
- [ ] Canonical bytes 单一真源；
- [ ] 同一最外层事务的空库 schema 引导、`m10_0001`、version table schema、只向前、无 auto-upgrade；
- [ ] pool 显式生命周期、固定大小/超时、UTC、READ COMMITTED；
- [ ] `put` 单次输入快照、先重放/冲突、锁容量行、再查一次、再做所有容量检查、原子更新计数；
- [ ] unique violation 先回滚再用新事务重查，并区分 OperationId 第一胜者与 UUID/结构异常；
- [ ] `lookup` 只取元数据；
- [ ] `get` 七字段与 Registry decode；
- [ ] 精确一次连接故障重试、不可重试数据库错误的唯一映射、取消透传；
- [ ] 无 fallback、双写、UPDATE/DELETE、全表启动扫描；
- [ ] secret、prompt、raw payload、reasoning 不泄露；
- [ ] 测试和日常数据库资源严格隔离；
- [ ] M04 17 项语义一一覆盖；
- [ ] 多连接真实并发，不用单连接或 sleep 假证明；
- [ ] test 镜像无 Docker 控制权；宿主两段 Gate 真实证明 PostgreSQL 容器重启持久性；
- [ ] line/branch/combined 三项 ≥70%；
- [ ] Module 01～09 回归；
- [ ] 延期边界未提前实现；
- [ ] 白名单、Git、commit/push/PR/deploy 状态准确。

## 21. 全局停止条件

除各阶段 STOP 外，任一时刻发生下列情况立即停止：

- 用户提出的新要求会改变已确认设计但尚未批准修订；
- 当前代码事实证明设计不可执行且最小修复超出白名单；
- 发现真实凭据、日常数据或生产资源可能被读取、覆盖、迁移或删除；
- 无法证明测试资源身份或清理边界；
- 迁移/事务/并发行为只能通过降低安全性才能通过；
- 上游公共合同需要改变；
- 任何阶段要求未经授权的 commit、merge、push、PR 或部署；
- 测试通过依赖网络 live 调用、skip、xfail、rerun 或篡改上游测试；
- staged/dirty 出现来源不明文件；
- 审查仍有 P0/P1/P2；
- 最终报告无法提供前后 HEAD、文件、迁移、测试和外部动作证据。

STOP 报告必须说明：准确阻塞事实、已完成的只读/安全排查、受影响文件/资源、为什么不能在白名单内解决、需要用户决定的唯一问题。不得在 STOP 后继续扩大操作。

## 22. 完成定义

### 22.1 文档任务完成

本次需求澄清与设计任务的完成条件是：

- 两份文档存在且链接有效；
- 需求、schema、API、事务、迁移、安全、测试、延期和下游影响无已知冲突；
- 两名独立只读审查者完成初审；
- 主审只修复与已确认决策一致的文档缺陷；
- 同两名审查者复审 PASS；
- `git diff --check`、Markdown 结构、白名单和 staged 检查通过；
- 仅两份 Module 10 文档变化；
- 未运行代码测试、数据库、迁移或 Docker；
- 最终状态为 `READY_FOR_DESIGN_REVIEW`，而不是 Module 10 已实现。

### 22.2 后续实现任务完成

只有 AP-T00～AP-T07 全部按授权完成、所有测试/审查/closeout 通过且最终报告完整，才可写 `MODULE10_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE`。这仍不自动表示 commit、合并、push、PR、部署、日常库初始化或生产就绪；这些状态必须由各自独立事实证明。


## 23. 本次专用诊断增量的执行入口

原设计§23为表/容量/复用池/兜底/导出真源，原M09执行规范§29/W01单独迁移Gate、W03采集与导出。只修改其精确白名单；m10_0001只读，目标m10_0002，旧业务表不改。不重跑M10初建阶段或建第二数据库。迁移和真实PostgreSQL资格未执行不能写PASS。

## 24. M14 容量只读增量的执行入口（待实施）

合同为 [M10设计 §24](10_artifact_persistence.md) 和 [M14 §3.2、§12](14_web_application.md)，执行唯一入口 [M14执行规范 W01+W02、C02/C16](14_web_application_vibe_coding.md)。仅未来明确授权后修改app/artifact_persistence/capacity.py、runtime.py、__init__.py，新增tests/unit/artifact_persistence/test_capacity_snapshot.py与tests/integration/artifact_persistence/test_capacity_snapshot.py；精确允许tests/contract/artifact_persistence/test_public_api.py只追加新增DTO的导出预期，保留其他导出/签名/默认limits断言。unit/contract在W01+W02，真实PG容量查询在W03，不能早期收集未实现的M14 HTTP/store。repository、旧迁移、容量事务、M04端口和默认限额不修改。

测试逐条核对正常值/strict边界/未知字段/坏行/缺行/限制漂移/loop与关闭/PG故障及无写入；新云limits首次初始化和旧行拒绝在M14隔离PG Gate验收，不指向日常库。旧§22的“两份M10文档”等为历史文档任务记录，不能当作本轮M14的范围。本增量没有代码、迁移或部署执行证据，当前NOT_RUN。
