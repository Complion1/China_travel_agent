# 01 需求提取与对话领域 Vibe Coding 编码契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 2026-09-26 用户批准预算金额功能延期：[当前修订与执行合同](12_budget_amount_deferral.md) 优先适用。金额不采集、不作为确认/输出门槛；API 调用额度不变。旧预算案例属于历史合同，不代表本期验收项。

> 文档状态：v2.6（已批准的需求状态连续性维护契约）
> 当前实现与验证状态：见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 目标读者：能力较弱、必须按明确步骤实现或维护 `app/requirements` 的编码模型
> 唯一业务规范：[`01_requirement_and_dialogue.md`](01_requirement_and_dialogue.md)
> 上位边界：[`00_agent_master_design.md`](00_agent_master_design.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 当前验证快照：[`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 执行原则：本文不是摘要；文件白名单、符号、算法顺序、任务 Gate、测试矩阵、停止条件和报告字段不得由编码模型自行删减或改写

```yaml
module_id: "01"
delivery_phase: "Demo"
blocks_demo: true
blocks_production_release: true
depends_on: []
provides:
  - TravelRequirement / RequirementPatch / RequirementDomainState
  - RequirementDomainTransition（下一状态 + 本轮 outcome）
  - RequirementExtractionSession / RequirementExtractor / EvidenceSink Protocol
  - RequirementDomainOutcome 及五个固定 outcome
  - REQ-DLG-001..036
consumes: []
parallel_with: ["02"]
not_in_scope:
  - 02-23 的任何具体实现
  - Graph、数据库、安全、入口和跨模块测试
acceptance_gate_scope: "full_rebuild；maintenance/requalification 按第 0 节执行受影响 Gate"
acceptance_gate:
  - DLG-T01..DLG-T08 全部完成
  - 纯领域与 extractor contract 测试通过
  - app/requirements 对后续模块实现零 import
test_class: pure-domain
unit_test_command: python -m pytest -q tests/unit/requirements
contract_test_command: python -m pytest -q tests/contract/requirements
allowed_test_dependencies:
  - Python 标准库 / Pydantic / pytest / pytest-asyncio / Hypothesis
  - app.requirements 公共合同
forbidden_test_dependencies:
  - app.llm_runtime / app.requirement_extraction / app.graph / app.persistence / app.agent / app.tools / app.config
  - LangChain / LangGraph / FastAPI / asyncpg / psycopg / 供应商 SDK
required_fakes:
  - RecordingRequirementExtractor / RecordingExtractionSession
  - InMemoryEvidenceSink
  - FixedClock
stable_contracts:
  - TravelRequirement / RequirementPatch / RequirementDomainState
  - RequirementDomainOutcome / RequirementDomainTransition
  - RequirementExtractor / RequirementExtractionSession Protocol
production_replacement: null
```

## 0. 执行模式（必须先判定）

编码模型在读正文任务前，必须根据当前代码与 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md) 的可复核事实选择一种模式；冻结契约不保存“已实现/待实施”的易变快照：

| 模式 | 进入条件 | 允许动作 | 禁止动作 |
|---|---|---|---|
| `maintenance`（默认） | 用户要求修复、调整或审核现有模块 01 | 只修改用户任务白名单；运行受影响 Gate 和用户要求的回归 | 重建目录、重跑历史迁移、删除现有文件或按全文“重新实现” |
| `full_rebuild` | 用户明确要求从头重建模块 01，并明确允许完整文件范围 | 按 `DLG-T01..DLG-T08` 顺序执行本文全部合同 | 跳过任务、保留旧兼容层或越界实现 02–23 |
| `requalification_only` | 用户只要求重新验证 | 不改生产代码；运行指定的 Docker 合同、覆盖率与回归门禁 | 借验证之名修改实现或测试 |

用户未明确写“从头重建”时一律使用 `maintenance`。模式或白名单不清楚时不得修改代码，先报告冲突。正文中的删除、创建、固定文件清单和完整 Task 顺序只在 `full_rebuild` 生效；L0/L1、禁止越界、安全规则和相关测试断言在三种模式下始终生效。

### 0.1 需求状态连续性 maintenance 的唯一执行路由

当用户授权实施模块 01/04 状态连续性修订时，必须执行 `RSC-T00..RSC-T06`，不得套用“transition 已存在”的普通 maintenance 前置条件，也不得重跑或修改模块 04 的 Artifact、Budget、Event 实现。相邻代码阶段为 `RSC-T01 + RSC-T02`、`RSC-T03 + RSC-T04`；每组完成后统一执行该组测试，`RSC-T05` 再做正式 Docker 验收。

未运行项统一报告为 `NOT_RUN` 并说明阻断的前置 Task；禁止写 PASS、预计通过或沿用历史数字。

#### RSC-T00：Git、引用、旧基线和白名单核验

- 前置：用户明确授权在现有非 `main` 分支实施；工作区 dirty 文件归属已知。
- 允许文件：无；只读 Git、设计、状态、Dockerfile、模块 01/03/04 当前代码与测试。
- 禁止：创建/切换分支、修改文件、stage、commit、Docker、live，以及触碰其他项目。
- 命令：`git branch --show-current`、`git rev-parse HEAD`、`git status --short`、`git diff --cached --name-only`，并用 `rg`/文件读取核验下列旧基线与任务文件。
- 允许的预期旧基线：`RequirementDomainTransition` 可以尚不存在；模块 01 三个入口可以仍返回裸 `RequirementDomainOutcome`；模块 04 可以仍是 v1 九字段。必须证明它们是状态文档和 Git 可解释的现有基线，不是未知实现。
- PASS：分支/HEAD/dirty/staged 记录完整；所有必读引用存在；精确实施白名单可用；旧基线与当前代码相符。
- STOP：未知 dirty 文件、白名单不足、必须改变已批准语义、必须修改模块 02/03 生产语义或模块 04 Artifact/Budget/Event。
- NOT_RUN：本项是首个必需 Task，不得标记 `NOT_RUN`；无法执行只读核验时写 `RSC-T00 FAIL` 并停止。

#### RSC-T01：模块 01 transition DTO、service 与测试

- 前置：`RSC-T00 PASS`。
- 精确文件白名单：`app/requirements/models.py`、`app/requirements/service.py`、`app/requirements/__init__.py`、`tests/unit/requirements/test_models.py`、`tests/unit/requirements/test_requirement_service.py`。
- 禁止文件：其他 `app/**`、其他 `tests/**`、全部设计/Vibe 文档、README、状态页、Dockerfile、依赖与配置文件。
- 实施：创建并公开 transition；三个入口唯一返回 transition；增加 invalidation summary；confirm 只按 state/action；两个 8,001 字符入口在所有副作用前返回 fresh 前态；保留异常冒泡和深层解除引用。
- 命令：在此 Task 与 `RSC-T02` 同组完成后运行 `python -m pytest -q -p no:cacheprovider tests/unit/requirements/test_models.py tests/unit/requirements/test_requirement_service.py`。该宿主机命令只作快速反馈，不是正式验收证据。
- PASS：模块 01 包根公开 `RequirementDomainState/Outcome/Transition`；三个入口签名返回 transition；定向测试通过且无 skip/xfail。
- STOP：需要修改 renderer、merge、extraction contract、模块 03 生产代码或白名单外测试；需要 outcome 参数才能实现 confirm；8,001 路径仍被 DTO 提前拒绝。
- NOT_RUN：仅当 `RSC-T00` 未通过时报告 `NOT_RUN（blocked by RSC-T00）`。

#### RSC-T02：模块 03 直接消费合同同步

- 前置：`RSC-T01` 实现完成，模块 01 公共 transition 已可导入。
- 精确文件白名单：`tests/contract/requirement_extraction/test_requirement_extractor_contract.py`。
- 禁止文件：`app/requirement_extraction/**`、Prompt、模块 02、其他测试和文档。
- 实施：把旧 service 返回断言改为 `transition.outcome`，并验证 `transition.state` 可作为连续状态消费；不得改变 adapter/Prompt/runtime 生产语义。
- 命令：`python -m pytest -q -p no:cacheprovider tests/contract/requirement_extraction/test_requirement_extractor_contract.py`；与 `RSC-T01` 定向命令一起执行。
- PASS：直接消费合同通过，仍是一 Session/一 Operation/最多一次 repair，且 state/outcome 均按 public transition 消费。
- STOP：测试要求修改模块 03 生产代码、Prompt、live case 或调用预算。
- NOT_RUN：仅当 `RSC-T01` 未完成或未通过时报告 `NOT_RUN（blocked by RSC-T01）`。

#### RSC-T03：模块 04 `TravelSessionState` v2、Codec 与测试

- 前置：`RSC-T01`、`RSC-T02 PASS`；`RequirementDomainState`、`RequirementDomainOutcome`、`RequirementDomainTransition` 均已从 `app.requirements` 包根公开。
- 精确文件白名单：`app/workflow_contracts/state.py`、`tests/unit/workflow_contracts/test_state.py`、`tests/unit/workflow_contracts/test_security.py`。
- 禁止文件：模块 04 Artifact/Budget/Event/summary/providers/identifiers/errors、模块 01/03、设计文档和白名单外测试。
- 实施：v2 十字段；保存 authoritative requirement state 与 routing outcome；只调用 public transition 复验五分支；v1/unknown 拒绝且无迁移；Codec 错误优先级、fresh copy 和 repr 安全保持权威规范语义。
- 命令：与 `RSC-T04` 同组完成后运行 `python -m pytest -q -p no:cacheprovider tests/unit/workflow_contracts/test_state.py tests/unit/workflow_contracts/test_security.py tests/contract/workflow_contracts/test_requirement_state_continuity.py tests/contract/workflow_contracts/test_public_api.py tests/contract/workflow_contracts/test_import_boundaries.py`。
- PASS：v2 十字段顺序/required/non-null、五分支、连续性、v1 拒绝、错误优先级和 mutation canary 全通过。
- STOP：必须复制模块 01 五分支、引入 v1 迁移、修改无关模块 04 子系统或导入模块 01 私有文件。
- NOT_RUN：仅当阶段 A（`RSC-T01 + RSC-T02`）未通过时报告 `NOT_RUN（blocked by RSC-T01/RSC-T02）`。

#### RSC-T04：模块 01/04 连续性和 public/import boundary

- 前置：`RSC-T03` 实现完成。
- 精确文件白名单：`app/workflow_contracts/__init__.py`、`tests/contract/workflow_contracts/test_requirement_state_continuity.py`、`tests/contract/workflow_contracts/test_public_api.py`、`tests/contract/workflow_contracts/test_import_boundaries.py`。
- 禁止文件：除 `RSC-T03` 同组文件外的其他生产/测试文件；尤其禁止 Artifact/Budget/Event 变更。
- 实施：包根与 `__all__` 同步；import boundary 只允许模块 01 包根的 State/Outcome/Transition；覆盖 current state → module 01 transition → state v2 JSON → next `RequirementTurnInput.current`；验证 invalidated summary 可展示但 04 不导入 renderer。
- 命令：执行 `RSC-T03` 列出的同组定向命令，再执行 `python -m compileall -q app/requirements app/workflow_contracts`，并将 `PYTHONPYCACHEPREFIX` 指向 `/tmp` 或忽略目录。
- PASS：同组命令全部通过，公共集合与 import boundary 精确，源码树无 pycache 残留。
- STOP：需要模块 04 复制 summary renderer/confirm 完整性规则、修改模块 05 或扩大公共 API。
- NOT_RUN：仅当 `RSC-T03` 未完成或未通过时报告 `NOT_RUN（blocked by RSC-T03）`。

#### RSC-T05：coverage、Docker 与模块 01～04 完整回归

- 前置：`RSC-T00..RSC-T04 PASS`；受控源码与测试只含上述白名单变更；冻结设计/Vibe 只读。
- 允许文件：无受控源码写入；只允许 Docker 构建缓存、镜像和 `--rm` 容器内 `/tmp` 临时产物。
- 禁止：宿主机 Python 作为验收证据、网络化测试容器、live/Secret、复用旧容器、修改镜像内源码、触碰其他项目 Docker 资源。
- 命令与 PASS：完整执行本文件第 15.5 节 Docker Gate；模块 01 和模块 04 分别达到 line、branch、combined 三项 `>=95%`，compileall、三个定向套件和模块 01～04 全量 unit/contract 回归全部通过，且无残留 `china-travel-agent-rsc-*` 容器。
- STOP：镜像不是从当前源码重建；任一容器不是新建 `--rm --network none --read-only --tmpfs /tmp`；任一覆盖率分母为 0、阈值不足、测试为 0、skip/xfail 或非零退出。
- NOT_RUN：仅当 `RSC-T00..RSC-T04` 未全部通过时报告 `NOT_RUN`，并列出第一个未通过的前置 Task。

#### RSC-T06：MODULE-CLOSEOUT

- 前置：`RSC-T05 PASS`；代码与 Docker Gate 已完成；模块 05 仍暂停。
- 精确文件白名单：`README.md`、`docs/IMPLEMENTATION_STATUS.md`、`Dockerfile`。四份模块 01/04 权威设计与 Vibe 文档在代码实施和 closeout 中只读。
- 禁止文件：除上述三份外全部文件；禁止 stage、commit、push、PR、deploy、模块 05 文档/代码/测试。
- 实施：按真实代码更新 README 的已完成模块、生产包、测试数量和 Docker 说明；状态页记录日期、待审 HEAD、模块 01/04 状态、实际命令/测试/三项覆盖率；检查 Dockerfile 默认 runtime 自检与构建内容。Dockerfile 无需修改时必须引用当前 COPY/CMD/模块导入内容给出具体理由，禁止只写 `N/A`。
- 命令：说明文档同步后重新执行 runtime/test 镜像构建；运行新 runtime 镜像默认 CMD；再用新 test 容器执行模块 01～04 完整 unit/contract 回归；最后检查无验收容器残留。精确 Docker 安全参数沿用第 15.5 节。
- PASS：三份收尾文件与真实结果一致；重建、默认 CMD、完整回归和残留检查全部通过；设计/Vibe 未被改动；staged 为空。
- STOP：测试数或状态无法从实际命令证明；Dockerfile 影响未检查；需要修改设计合同；准备开始模块 05；实施模型准备 stage/commit。
- NOT_RUN：仅当 `RSC-T05` 未通过时报告 `NOT_RUN（blocked by RSC-T05）`；不得把未收尾的实现报告为完成。

`RSC-T06` 结束后固定停止并交给独立审查。实施模型不得 stage 或 commit；只有独立审查通过后，审查者才可根据用户新的明确授权提交。

## 1. 任务结果

只实现需求领域：

```text
current user message
→ strict RequirementPatch DTO
→ evidence 原文验证
→ 确定性规范化
→ 纯函数增量合并
→ 缺失/无效/冲突评估
→ RequirementDomainTransition(state, outcome)
  └── NeedClarification | NeedConfirmation | ConfirmedTravelRequirement
      | RequirementExtractionFailed | RequirementInvalidated
```

本任务不构建 Graph、数据库、安全基础设施或产品入口。编码 Agent 若发现完成某项领域行为需要这些基础设施，必须改为注入 Protocol 或返回窄 transition，不得越界创建临时实现。

## 2. 约束等级

### 2.1 L0：业务不变量

`REQ-DLG-001..036` 与 `REQ-TRANS-001..007` 全部是 L0。违反任意一条即失败，不允许通过替换测试、添加默认值或修改 Prompt 规避。

### 2.2 L1：公共领域合同

以下为 L1，名称和语义固定：

- `TravelRequirement`
- `RequirementPatch`
- `RequirementDomainState`
- `RequirementTurnInput`
- `RequirementDomainOutcome`
- `RequirementDomainTransition`
- `NeedClarification`
- `NeedConfirmation`
- `ConfirmedTravelRequirement`
- `RequirementExtractionFailed`
- `RequirementInvalidated`
- `RequirementExtractor`
- `RequirementExtractionSession`
- `ExtractionAttemptSucceeded`
- `ExtractionRepairRequired`
- `ExtractionRepairFeedback`
- `EvidenceVerified`
- `EXTRACTABLE_REQUIREMENT_FIELD_PATHS`
- `EvidenceSink`
- `RequirementDomainService`
- 领域错误 code

`RequirementDomainTransition` 必须从 `app.requirements` 包根公开。允许按 Python 规范调整其他内部导入，不允许改变字段意义、nullable 规则或 outcome 分支。

### 2.3 L2：参考实现

私有 helper 名称、单个函数长度和内部文件拆分属于 L2。默认采用本文结构；如需偏离，交付报告必须写明原因、替代结构和等价测试。

禁止在测试中导入以 `_` 开头的 helper，避免把 L2 固化为公共 API。

## 3. 禁止越界

本任务禁止创建、修改或引用以下内容作为需求领域实现的一部分：

- `app/graph/`
- `app/persistence/`
- `app/llm_runtime/`
- `app/requirement_extraction/`
- `app/agent/`
- `app/tools/`
- `app/config.py`
- `app/agent/skills.py`
- `scripts/run_agent.py`
- LangGraph `StateGraph`、`interrupt`、`Command`
- LangChain `Runnable`、chat model、provider callback 或真实 Prompt 装配
- PostgreSQL saver、SQL、连接池、snapshot 或 idempotency record
- serializer、Fernet/AES、keyring、TTL、删除队列或审计表
- Application Service / CLI response model
- 酒店、地图、路线、攻略、全局 memory/trace
- router、react planner 或共享 LLM factory 的构造
- 全 Agent smoke journey、全局 coverage 或跨模块 E2E

允许依赖抽象：注入 `RequirementExtractor` 和 `EvidenceSink`。测试必须使用 fake，不得为了单元测试启动 Graph 或数据库。

`app/requirements` 中不得出现生产 `ExtractionService`、全局 model factory、真实 Prompt 常量或 provider response 解包。01 只保留 strict DTO、`RequirementExtractor` Protocol、evidence verifier 和领域管线；真实实现全部归 03。

### 3.1 后续模块归属锁定

遇到下列需求时必须停止扩展当前任务，并在交付报告中注明对应模块；不得创建占位实现、待办标记或临时 class：

| 发现的需求 | 所属模块 | 当前任务允许做的唯一事情 |
|---|---:|---|
| DeepSeek client、Model Profile、超时、重试、配额 | 02 | 只保留 `RequirementExtractor` Protocol |
| 真实提取 Prompt、模型 runnable、结构化调用 | 03 | 使用 fake 完成 01 contract test |
| session state、phase、命令信封、interrupt payload | 04 | 只返回领域 transition，不定义 workflow 字段 |
| Demo state、artifact port、预算合同 | 04 | 04 拆分保存 transition 的 state/outcome；01 不定义 workflow 字段 |
| LangGraph 需求链路与 `InMemorySaver` | 05 | 不创建节点或路由测试 |
| 百度地图、酒店、候选研究、行程和输出 | 06–09、12 | 不创建业务 DTO 或工具 stub |
| PostgreSQL Artifact、checkpoint、执行账本和跨进程恢复 | 10、11 | 不创建数据库或恢复占位实现 |
| Demo Orchestrator | 13 | 不猜测酒店、规划、持久化或输出接口 |
| Application Service、应用 API 和前端网页 | 14 | 不定义入口 DTO |
| Demo E2E | 15 | 不把 E2E 作为 01 完成条件 |
| 局部重排、安全与观测 | 16、19–21 | 只保证 01 公共合同可被适配，不创建实现 |
| 生产恢复与质量评测 | 22、23 | 不反向修改 01 |

01 与 02 可以在隔离工作区并行，但 02 不得修改 `app/requirements`。03 只有在 01 的 `RequirementExtractor` Protocol 和严格返回语义冻结后才能开始。

## 4. 目标代码组织

默认结构：

```text
app/requirements/
├── __init__.py
├── models.py
├── extraction_contract.py
├── evidence.py
├── normalization.py
├── merge.py
├── dialogue.py
├── manual_template.py
└── service.py

tests/unit/requirements/
├── fakes.py
├── test_models.py
├── test_evidence.py
├── test_normalization.py
├── test_merge.py
├── test_dialogue_renderer.py
├── test_manual_template.py
└── test_requirement_service.py

tests/contract/requirements/
└── test_extractor_protocol.py
```

生产文件数量属于 L2，但上述职责必须物理可识别，禁止把真实模型 adapter 混入任何 01 文件。测试文件清单属于 L1：不得用单一 `test_domain.py`、`test_m0_blocking.py` 或其他 catch-all 文件替代规定的七个单元测试文件与一个 Protocol 合同测试文件。

职责和依赖方向属于 L1：

```text
models ← extraction_contract
models ← evidence
models ← normalization
models ← merge
models ← dialogue
models ← manual_template
models + extraction_contract + evidence + normalization + merge + dialogue + manual_template ← service
```

`models.py` 不得导入其他领域模块。`extraction_contract.py` 只定义 DTO/Protocol，不得导入 `app.llm_runtime`、`app.requirement_extraction`、LangChain 或 Prompt。`evidence.py`、`normalization.py`、`merge.py`、`dialogue.py` 和 `manual_template.py` 不得导入模型客户端或 service。

## 5. 开工前门槛

开始修改代码前必须：

1. 完整读取 00、模块 01 规范、本文、共享实施规则和实施状态。
2. 运行 `git status --short`，保护用户已有改动。
3. 建立 `REQ-DLG-001..036` 与 `REQ-TRANS-001..007 → public symbol → test` 对照表。
4. 使用 `rg` 列出 `app/requirements` 的当前引用。
5. 记录现有失败测试，不通过删除或降级断言制造绿灯。
6. 确认本任务不会修改 02-23 的实现目录，也不会创建 Graph、persistence、entry 或跨模块测试占位物。
7. 删除 01 测试目录中的旧 `test_m0_blocking.py`；把仍有效的领域场景按 `REQ-DLG-001..036` 重写到固定纯领域测试文件。删除已经明确越界的旧测试不属于“删除失败测试制造绿灯”。
8. 删除测试中的 `sys.path.insert/append` 和任何绝对路径；测试只能依赖 `pyproject.toml` 的 `pythonpath = ["."]`。
9. 运行禁止导入扫描；`app/requirements` 和两类 requirements 测试目录内不得出现 02-23 实现或外部框架 import。
10. 用 `pytest --collect-only` 验证完整两个目录可以收集；只收集某个通过文件不算开工基线。

## 6. 公共模型固定

所有公共 Pydantic 模型使用：

```python
model_config = ConfigDict(extra="forbid", strict=True)
```

### 6.1 基础类型

```python
class RequirementIntent(str, Enum):
    INITIAL = "initial"
    SUPPLEMENT = "supplement"
    CORRECTION = "correction"
    CONFIRMATION_REVISION = "confirmation_revision"

class FieldStatus(str, Enum):
    MISSING = "missing"
    EXTRACTED = "extracted"
    DERIVED = "derived"
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"
    INVALID = "invalid"
```

`RequirementFieldPath` 是唯一字段路径注册表。所有 extraction、metadata、issues、renderer 和 tests 必须引用它，禁止散落字符串。

### 6.2 输入

```python
class RequirementTurnInput(BaseModel):
    schema_version: Literal["1.0"]
    turn_id: UUID
    user_message: Annotated[str, StringConstraints(min_length=1)]
    session_date: date
    timezone: Literal["Asia/Shanghai"]
    intent: RequirementIntent
    current: RequirementDomainState
```

公共 DTO 只拒绝空字符串，不设置 `max_length=8000`。service 在任何 extractor Session、EvidenceSink、merge 或其他副作用前按 Python `len(str)` 的 Unicode code point 语义执行 8,000 preflight；不得按 UTF-8 byte 截断。`ExtractionInput.user_message` 仍可保留 `max_length=8000`，因为只在 preflight 通过后构造。

### 6.3 领域状态

```python
class RequirementDomainState(BaseModel):
    schema_version: Literal["1.0"]
    requirement: TravelRequirement
    metadata: FieldMetadataRegistry
    issues: tuple[RequirementIssue, ...] = ()
    confirmation_status: Literal["collecting", "awaiting_confirmation", "confirmed"]
```

不得包含 thread ID、checkpoint ID、interrupt ID、数据库 reference、Graph config、model client 或 raw supplier response。

### 6.4 outcome 判别联合

```python
RequirementDomainOutcome = Annotated[
    NeedClarification
    | NeedConfirmation
    | ConfirmedTravelRequirement
    | RequirementExtractionFailed
    | RequirementInvalidated,
    Field(discriminator="outcome_type"),
]
```

每个分支使用唯一 `Literal`。禁止返回 `dict[str, Any]`、`action: str` 或靠中文 message 判断分支。

### 6.5 状态转换模型

位置固定为 `app/requirements/models.py`，紧随 `RequirementDomainOutcome` 定义：

```python
class RequirementDomainTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: RequirementDomainState
    outcome: RequirementDomainOutcome
```

字段顺序精确为 `state`、`outcome`；两字段 required、non-null、无默认值；不得增加 `schema_version`。model validator 必须执行权威规范 §5.3 的五分支一致性规则。`RequirementInvalidated` 的 state 固定 `confirmation_status="awaiting_confirmation"`、issues 为空，并按权威规范固定的 `model_dump_json(exclude_none=False)` UTF-8 SHA-256 算法重新核验 `after_digest`；validator 不伪造或猜测前态，也不导入 dialogue renderer。service 必须证明 `before_digest` 来自入口前态，`changed_fields` 是 merge 变更路径转换成 enum 后按 `.value` 排序去重的精确 tuple，并证明 `summary == render_confirmation_summary(transition.state.requirement)`。`RequirementExtractionFailed` 允许任意合法前态，service 测试负责证明其与输入前态等值。

`RequirementInvalidated` 的公共模型精确为：

```python
class RequirementInvalidated(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome_type: Literal["invalidated"] = "invalidated"
    changed_fields: tuple[RequirementFieldPath, ...]
    before_digest: Annotated[str, StringConstraints(min_length=1)]
    after_digest: Annotated[str, StringConstraints(min_length=1)]
    reason: Annotated[str, StringConstraints(min_length=1)]
    summary: Annotated[str, StringConstraints(min_length=1)]
```

字段顺序不得改变。`summary` 由 service 对修改后的 state requirement 调用确定性 renderer 生成；不调用模型，不含 raw evidence、异常、Prompt 或基础设施信息。调用方不得拼接摘要、导入私有 renderer、伪造成 `NeedConfirmation` 或发起空白 `process_turn()`。

public transition 直接构造时，model validator 对 state/outcome 分别执行各自 TypeAdapter 的 strict Python-tree + strict JSON 往返，再校验五分支一致性；validator 内禁止调用整个 `TypeAdapter(RequirementDomainTransition)`，避免递归。构造完成时两字段已与入参及彼此解除引用。service 出口才把完整 transition 编码为 strict JSON，并只返回 `TypeAdapter(RequirementDomainTransition).validate_json(..., strict=True)` 恢复出的对象。`test_models.py` 对直接构造执行 identity 与双向 mutation canary；service 测试另证明与 input/internal 隔离，并断言 branch、UUID/Enum/tuple 等嵌套类型和值往返保持。

transition 构造 helper 必须对 state 和 outcome 分别做深层 fresh copy，不能先复制一个对象后把其嵌套值复用于另一边。返回后，state/outcome 与调用输入、service working state 及彼此都不得共享可变嵌套引用。禁止以 outer frozen 或调用方只读约定代替复制。

### 6.6 尺寸边界

Pydantic 和确定性规则必须共同执行：

| 字段 | 约束 |
|---|---|
| `RequirementTurnInput.user_message` | DTO `1..` code point；service 行为上限 8000 |
| `ExtractionInput.user_message` | `1..8000` code point；只能在 service preflight 后构造 |
| `RequirementConfirmation.revision_text` | accept 为 `None`；revise DTO 非空；service 行为上限 8000 |
| destination | `1..64` |
| hub name | `1..128` |
| room type | `1..500` |
| mobility constraints | `1..500` 或显式“无” |
| dietary restrictions | `1..500` 或显式“无” |
| other free text | `1..500` |
| each people count | `0..100` |
| total people | `1..100` |
| child age | `0..17` |
| one evidence text | `1..1000` |

`user_message` 或 revise text 超过 8,000 必须在 Session 创建、EvidenceSink、merge 和任何其他副作用前返回保留 fresh 输入前态的 `RequirementExtractionFailed(INPUT_TOO_LONG)` transition；Session 创建数、evidence 调用数和 Patch 应用数均为 0。禁止裁剪字符串后继续或记录原文。其他字段超限继续按各自 DTO/确定性规则失败。

## 7. RequirementExtractor 注入合同

### 7.1 全异步 Protocol

```python
class RequirementExtractor(Protocol):
    def start_session(self, request: ExtractionInput) -> RequirementExtractionSession: ...

class RequirementExtractionSession(Protocol):
    async def extract(self) -> ExtractionAttemptOutcome: ...
    async def repair(
        self,
        feedback: ExtractionRepairFeedback,
    ) -> ExtractionAttemptOutcome: ...
```

领域 service 的构造函数必须显式接收 extractor。禁止：

- 在模块 import 时创建模型实例。
- 从全局 factory singleton 隐式读取 extractor。
- 在测试中 monkeypatch 全局模型。
- 在 extractor 失败后调用正则或自由文本 fallback。
- 添加同步 `extract_sync`、在线程池包装同步调用或在事件循环调用 `asyncio.run()`。
- 让同一个 Session 服务两个 `turn_id`，或让 service 获取模块 02 `ModelOperation`。

`ExtractionInput` 必须新增 `turn_id: UUID`，并保存完整 `current_requirement: TravelRequirement`；旧 `current_summary: str` 字段必须删除。模块 01 不序列化模型输入，序列化属于 03。

### 7.2 Strict DTO

供应商 DTO 与领域 DTO 分离。strict DTO 固定包含：

```text
StrictRequirementPatchDTO
├── 每个可提取业务字段：required + nullable
├── is_correction: required bool
└── evidence: required list[StrictFieldEvidenceDTO]
```

`evidence` 项：

```text
StrictFieldEvidenceDTO
├── field_path: RequirementFieldPath
└── text: 1..1000 chars
```

规则：

1. 所有 object 禁止额外属性。
2. 所有属性对 strict Schema 都是 required。
3. 当前轮未表达使用 `null`，不是默认值。
4. 空列表只有在用户明确表达空语义时允许。
5. 模型不返回 turn ID、status、digest、reference 或完整 requirement。
6. 只有 `ATTR_TO_PATH` 登记的可提取业务属性要求 evidence；`is_correction` 和 `evidence` 自身不要求 evidence。list-valued DTO 属性整体算一个 leaf；一个 list field 恰好一条 evidence，不按元素重复同一 `field_path`。

### 7.3 判别 outcome 与安全 feedback

必须按规范定义 discriminated union。可 repair 结果不得使用 exception：

```python
RequirementRepairCode = Literal[
    "structured_output_invalid",
    "intent_mismatch",
    "evidence_missing",
    "evidence_duplicate",
    "evidence_not_substring",
    "orphan_evidence",
]

class ExtractionRepairFeedback(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: RequirementRepairCode
    field_paths: tuple[RequirementFieldPath, ...]

class ExtractionAttemptSucceeded(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome_type: Literal["succeeded"] = "succeeded"
    value: StrictRequirementPatchDTO

class ExtractionRepairRequired(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome_type: Literal["repair_required"] = "repair_required"
    feedback: ExtractionRepairFeedback
```

feedback 构造时必须按 `RequirementFieldPath.value` 排序并去重。禁止 `message`、`detail`、`raw_output`、`previous_value` 或任意自由文本字段。

必须在 `extraction_contract.py` 定义并从包根导出：

```python
EXTRACTABLE_REQUIREMENT_FIELD_PATHS = frozenset(
    RequirementFieldPath(path) for path in ATTR_TO_PATH.values()
)
```

四种 evidence code 的全部 path 必须属于该集合。`HOTEL_NIGHTS`、`TOTAL_PEOPLE` 等派生 enum path 不在集合中，传入时必须 validation fail，不得过滤后继续。

### 7.4 每轮调用预算

固定调用状态机：

```text
start_session(input) exactly once
└── await session.extract() exactly once
    ├── succeeded → evidence validation
    └── repair_required / evidence repair result
        └── await session.repair(feedback) at most once
            ├── succeeded + evidence verified → continue
            └── repair_required / evidence invalid → RequirementExtractionFailed
```

同一 Session 的 `extract()` 和 `repair()` 合计不得超过 2。`rules`、`dialogue` 和 `merge` 调用模型直接失败。模块 02 runtime exception 不进入 repair 分支，由 03 包装后原样冒泡。

## 8. evidence 算法固定

### 8.1 验证算法

```python
def verify_evidence(
    dto: StrictRequirementPatchDTO,
    user_message: str,
) -> EvidenceVerificationOutcome:
    leaf_paths = {
        RequirementFieldPath(path)
        for path, _value in enumerate_non_null_leaves(dto)
    }
    grouped = group_evidence_by_field(dto.evidence)

    missing = {path for path in leaf_paths if len(grouped.get(path, ())) == 0}
    if missing:
        return repair("evidence_missing", missing)

    duplicate = {path for path in leaf_paths if len(grouped.get(path, ())) > 1}
    if duplicate:
        return repair("evidence_duplicate", duplicate)

    not_substring = {
        path
        for path in leaf_paths
        if grouped[path][0].text not in user_message
    }
    if not_substring:
        return repair("evidence_not_substring", not_substring)

    orphan_paths = set(grouped) - leaf_paths
    if orphan_paths:
        return repair("orphan_evidence", orphan_paths)

    verified = tuple(
        VerifiedFieldEvidence(field_path=path, text=grouped[path][0].text)
        for path in sorted(leaf_paths, key=lambda item: item.value)
    )
    return EvidenceVerified(evidence=verified)
```

必须聚合相同 code 的全部字段路径，再排序去重；不同 code 同时出现时，优先级固定为 `evidence_missing → evidence_duplicate → evidence_not_substring → orphan_evidence`，一次只返回最高优先级 feedback。禁止抛 `EvidenceVerificationError`，禁止解析异常字符串。

`group_evidence_by_field` 的 key 必须是 `RequirementFieldPath` enum，不得在 verifier 中混用 enum 与裸字符串；`repair(code, paths)` 统一构造 `ExtractionRepairFeedback`，由其 validator 排序去重。

### 8.2 固定禁止

- 根据 bool 生成“是/否”。
- 根据空列表生成“无”。
- 根据数值生成 `str(value)` 或 `5000.0`。
- 根据日期/Enum/normalized value 反向生成 evidence。
- 对数字、日期、枚举、列表或 bool 豁免 evidence。
- 接受 evidence 只出现在旧消息或结构化摘要中。
- 在 evidence 校验前执行会丢失原文的规范化。

### 8.3 EvidenceSink

领域模块不实现存储，只调用注入协议：

```python
class EvidenceSink(Protocol):
    def accept(
        self,
        *,
        turn_id: UUID,
        evidence: tuple[VerifiedFieldEvidence, ...],
    ) -> tuple[EvidenceReceipt, ...]: ...
```

`EvidenceReceipt` 只包含 `field_path/evidence_ref/evidence_digest`。service 收到 receipt 后立即构造不含 raw text 的 Patch/metadata/outcome。

测试 sink 保存调用记录；不得连接数据库。生产 sink 由后续安全/持久化模块提供。

## 9. 规范化管线

固定顺序：

```text
verified strict DTO
→ raw semantic values
→ date/time normalization
→ number/money normalization
→ enum normalization
→ local field validation
→ RequirementPatch
→ merge
→ derived values
→ cross-field validation
```

### 9.1 日期

```python
today = session_date
tomorrow = session_date + timedelta(days=1)
day_after_tomorrow = session_date + timedelta(days=2)
```

禁止 `date(year, month, day + n)`，禁止 `date.today()` 和 `datetime.now()`。

绝对日期必须通过 `date.fromisoformat` 或等价严格日历解析。相对日期标记 pending confirmation。

### 9.2 金额

必须显式支持本文验收语料中的中文金额，如“五千”“五千左右”，输出数值与 approximate 标记。无法唯一解释时返回 normalization issue，不猜测。

### 9.3 hub type

领域 extractor 可以从用户明确的“机场/火车站/高铁站”语义映射枚举；无法确认类型时标记 issue。不得查询地图或创建坐标。

### 9.4 校验

每个 validator 返回不可变 `tuple[RequirementIssue, ...]`，不抛裸 `ValueError` 给调用方。编程错误可以抛异常，用户数据错误必须结构化。

人员与房型条件必须逐字实现：

- `children_count == 0` 时，`children_ages` 必须规范化为 `[]`，缺失字段计算不得加入儿童年龄，dialogue renderer 禁止生成儿童年龄追问。
- `children_count > 0` 时，年龄数量必须与儿童人数完全一致，每项必须为 `0..17` 整数。
- 房型原文明确为“无特殊要求”时，规范化为 `NO_PREFERENCE` 并视为已回答；禁止默认成“标准间”。

## 10. merge 算法固定

### 10.1 presence 语义

merge 必须使用 `model_fields_set` 或独立 presence map 区分：

```text
字段未出现       → 保持旧值
字段出现且有效   → 按 correction 规则合并
字段显式清空     → 仅允许字段语义支持清空且有当前证据
空列表           → 不等同于未出现
null             → 不自动等同于删除
```

禁止 `if patch.children_ages:` 一类 truthiness 判断。

### 10.2 覆盖规则

```text
old missing + new value                  → accept
old value + same new value               → no-op metadata refresh only if required
old value + different + correction=true  → replace + change record
old value + different + correction=false → conflict, preserve old
```

### 10.3 metadata

对每个实际 Patch 叶子同步更新：

- status
- source_turn_id
- evidence_ref
- evidence_digest
- normalization record
- issues

合并后执行一致性断言：每个非空叶子都有 metadata；derived 字段有 rule record；公开 outcome 无 raw evidence。

### 10.4 derived/invalidation

依赖字段变化后重算：

- `sightseeing_days`
- `hotel_check_in_date`
- `hotel_check_out_date`
- `hotel_nights`
- `total_people`
- `inferred_scenarios`

已确认状态下的关键字段变化必须返回 outcome 为 `RequirementInvalidated`、state 为修改后 `awaiting_confirmation` 快照的 transition。该 outcome 不得包含下游数据库 ID 或执行删除。

同一次返回还必须包含 `summary=render_confirmation_summary(modified_state.requirement)`。模块 05 展示该摘要后可直接把 modified state 与 accept action 交给 `confirm()`，无需先生成 `NeedConfirmation`。

## 11. 对话 renderer

### 11.1 Clarification

`render_clarification` 输入只包含结构化 missing/issues/preferences，输出一个确定性 message。

排序固定：missing → invalid → conflict → relative confirmation → interests → pace → agent decides。

同一轮只输出一个 message。不得为每个字段调用模型或产生多个独立问题。

### 11.2 Confirmation summary

`render_confirmation_summary` 必须展示规范第 11 节全部字段。字段标签、顺序和空值表达固定在 renderer 配置中。

摘要不得包含：raw evidence、metadata 内部字段、错误堆栈、模型响应或基础设施标识。

### 11.3 Confirmation action

公开动作固定：

```python
class RequirementConfirmation(BaseModel):
    decision: Literal["accept", "revise"]
    revision_text: Annotated[str, StringConstraints(min_length=1)] | None
```

- accept：revision text 必须为 `None`。
- revise：公共 DTO 要求非空，不设置 8,000 `max_length`；service 在副作用前执行 `len(revision_text) <= 8000` preflight。
- accept 只依据 state/action 验证：state awaiting confirmation、全部硬字段有效、issues 为空、没有 missing/conflict、decision=accept、revision text 为 `None`。`confirm()` 不接收也不检查当前 outcome。
- `NeedConfirmation` 和 `RequirementInvalidated` 的 awaiting-confirmation state 都可以在用户已看到对应 `summary` 后 accept；成功 transition 的 state 为 confirmed，outcome 为 `ConfirmedTravelRequirement`。
- 裸字符串“确认”不是领域公共 API 输入；文本如何映射成动作由上层入口决定。

## 12. 人工模板实现

### 12.1 Serializer 输出

必须与领域规范第 12 节逐字一致，顺序固定、UTF-8、全角冒号、中文逗号。

### 12.2 Parser

固定解析步骤：

1. 以 `splitlines()` 读取；不忽略内部空行。
2. 第一行精确解析 `模板版本`。
3. 验证全部键恰好出现一次且顺序固定。
4. 将每个组合字段拆成独立叶子。
5. 累积逐字段错误，不用首个错误覆盖其余错误。
6. 构造带 presence 的 Patch。
7. 进入与模型 Patch 相同的 normalize → merge → validate 管线。

到达/离开固定拆为：

```text
<hub_name>，<YYYY-MM-DD HH:MM>[，大约]
```

房间固定拆为：

```text
<positive integer>间，<non-empty room type>
```

禁止为缺失房型默认“标准间”。

### 12.3 Round-trip

测试必须真正执行：

```python
parsed = parse_manual_template(serialize_manual_template(requirement))
assert parsed == requirement
```

比较全部字段和类型，不只比较 destination、party 或 budget。

## 13. RequirementDomainService

### 13.1 构造函数

```python
class RequirementDomainService:
    def __init__(
        self,
        *,
        extractor: RequirementExtractor,
        evidence_sink: EvidenceSink,
        clock: Clock,
    ): ...
```

禁止可选依赖后隐式创建生产实现。测试必须显式传 fake。

模块 01 不提供 `RealClock`、生产 EvidenceSink 或具体模型 extractor。调用方必须注入 Clock；测试固定使用 `FixedClock`。任何 `date.today()`、`datetime.now()`、环境变量读取或全局 singleton 都属于越界。

### 13.2 唯一处理管线

```text
await process_turn(input)
├── preflight len(user_message) <= 8000 before every side effect
│   └── over limit → fresh input state + RequirementExtractionFailed(INPUT_TOO_LONG)
├── session = extractor.start_session(extraction_input)
├── outcome = await session.extract()
├── verify evidence → EvidenceVerified | ExtractionRepairRequired
├── when required: await session.repair(feedback) exactly once
├── evidence_sink.accept
├── normalize
├── merge
├── recompute derived
├── validate cross-field
└── decide transition
    ├── issues/missing → NeedClarification
    ├── complete       → NeedConfirmation
    └── failure        → RequirementExtractionFailed
```

确认动作使用独立 `confirm(state, action)` 方法。人工模板使用 `process_manual_template(state, submission)`，但最终复用同一 merge/validate/transition 决策函数。

精确公开返回合同为：

```python
async def process_turn(self, input: RequirementTurnInput) -> RequirementDomainTransition: ...
async def confirm(
    self,
    state: RequirementDomainState,
    action: RequirementConfirmation,
) -> RequirementDomainTransition: ...
def process_manual_template(
    self,
    state: RequirementDomainState,
    submission: ManualTemplateSubmission,
) -> RequirementDomainTransition: ...
```

`process_turn` 和包含 revision 的 `confirm` 必须异步；`confirm` 的公开方法保持 async，不能因 accept 分支无模型调用而建立另一套同步 API。三个入口直接替换旧裸 `RequirementDomainOutcome` 返回类型，不增加 deprecated alias、兼容 wrapper 或双 API。领域 service 不得捕获 `RequirementExtractionRuntimeError`、`LLMRuntimeError` 或其他 runtime/programmer exception，也不得把它们转换为 `RequirementExtractionFailed`。

`confirm()` 固定先验证 action 的 state-based 路由：revise 超过 8,000 时在 Session/EvidenceSink/merge 前返回 fresh 前态的 `INPUT_TOO_LONG` transition；合法 revise 才进入 `process_turn` 管线；accept 不读取 Graph 当前 outcome，只检查 awaiting-confirmation、完整硬字段、空 issues、无 missing/conflict 以及 revision text 为 `None`。因此 invalidated → 展示 outcome.summary → accept 是完整可执行路径。

正常五分支构造顺序固定为：先取得完整 next state → 按分支生成 outcome → 分别深拷贝 → 构造并由 public transition validator 复验一致性。clarification 为 collecting；confirmation 为 awaiting_confirmation；confirmed 为 confirmed；invalidation 保存 modified state、置 awaiting_confirmation，并以同一 modified requirement 生成完整 summary；extraction failure 对输入 current 做 fresh copy，不应用部分 Patch。public validator 复验 invalidation state/after digest/结构，不导入 renderer；service 测试逐字证明 summary 语义。

不得定义正确但从不调用的 helper。每个 normalization、metadata 和 invalidation 函数必须从唯一管线可达。

## 14. 错误处理

错误按领域 code 输出，禁止把所有异常包装成 `NetworkError` 或 `ContractError`。

| 类别 | 处理 |
|---|---|
| `process_turn`/revise 文本超过 8000 | 任何 Session、EvidenceSink、merge 前返回 fresh 前态的 INPUT_TOO_LONG transition；副作用计数均为 0 |
| DTO 空字符串或其他参数错误 | Pydantic `ValidationError` 原样抛出，不构造 transition |
| strict/schema、intent、evidence 四类 | 使用固定 feedback 最多一次 repair，之后 failed |
| 模块 02 runtime/config/network/timeout/truncated/content filter | 不 repair、不包装、不转领域 failed，直接冒泡 |
| normalization | 保留合法 state，返回 clarification |
| conflict | 保留旧值，返回 clarification |
| template | 返回全部 field errors |
| programmer error | 抛出并阻止生成正常 outcome |

只有五种可预期领域 outcome 才包装成 transition。Pydantic 参数 `ValidationError`、adapter/runtime/programmer exception 均继续抛出；禁止为了“所有函数都有返回值”而伪造 failed transition。

用户可见错误不得拼接 raw evidence、完整 user message、supplier response 或 stack trace。

## 15. 领域测试合同

### 15.0 隔离模型

01 的测试进程必须在未配置 DeepSeek、PostgreSQL、百度地图或 LangGraph 的环境中通过。测试导入图固定为：

```text
Python stdlib / Pydantic / pytest / pytest-asyncio / Hypothesis
                         ↓
               app.requirements public API
                         ↓
     RecordingRequirementExtractor / InMemoryEvidenceSink / FixedClock
```

禁止导入图：

```text
app.requirements 或 requirements tests
    -X→ app.llm_runtime / app.requirement_extraction / app.graph / app.persistence / app.agent / app.tools / app.config
    -X→ langchain / langgraph / fastapi / asyncpg / psycopg / provider SDK
```

三个固定 fake 的行为：

- `RecordingRequirementExtractor`：每次 `start_session` 创建一个 `RecordingExtractionSession`，记录完整 `ExtractionInput`。
- `RecordingExtractionSession`：接收预设判别 outcome 或异常序列，分别记录 `extract_count`、`repair_count` 和完整 typed feedback；禁止共享全局计数替代 Session 约束。
- `InMemoryEvidenceSink`：接收 verified evidence，返回只含 ref/digest 的 receipt；允许测试断言调用内容，但不得连接文件、数据库或网络。
- `FixedClock`：构造时接收固定 `date`；除 `today()` 外不访问系统时间。

测试不得 monkeypatch 全局 factory、环境变量或后续模块。需要替换的依赖如果无法通过构造函数注入，判定生产设计不符合本契约。

### 15.1 反假阳性规则

以下测试无效：

- 只构造 Pydantic object 后断言字段存在。
- 只调用私有 `_helper`。
- 在测试内复制生产算法。
- 只断言“不抛异常”。
- 用 Graph、PostgreSQL 或 entry 测试代替领域行为。
- parser 只执行 parse 而不执行 serialize → parse。
- fake extractor 返回值与输入原文不一致，却仍期待成功。
- 只运行 `test_domain.py`、单个通过文件或 `-k` 子集后宣称模块完成。
- 在 01 测试目录保留 Graph/PG/entry/security 测试，再用 `--ignore`、skip 或 xfail 隐藏。
- 修改 `sys.path`、硬编码 `<ABSOLUTE_DIR>/...` 等本机路径，或依赖开发机已启动的服务。

### 15.2 必须测试的行为

| 文件 | 必须覆盖 |
|---|---|
| `test_models.py` | strict、extra forbid、nullable、Unicode 边界；公共输入无 8000 max 但空字符串拒绝；invalidation 精确字段顺序含 summary；transition 精确字段/config、五分支一致性、非法配对拒绝、strict JSON round-trip 类型和值恢复 |
| `test_evidence.py` | 全字段 evidence、四种独立 repair code、排序去重、优先级、默认值不伪造、零异常字符串解析 |
| `test_normalization.py` | 相对日期、月末年末、金额、时间、人数、儿童条件、`NO_PREFERENCE` 房型、POI 文本 |
| `test_merge.py` | 空 Patch、补充、纠正、冲突、显式清空、metadata、derived、invalidation |
| `test_dialogue_renderer.py` | 统一追问顺序、完整摘要、无内部字段泄露 |
| `test_manual_template.py` | 全字段 round-trip、每类键和值错误、无静默默认 |
| `test_requirement_service.py` | async 完整管线、三个入口只返回 transition、每轮独立 Session、最多 extract+repair、intent 映射、空 Patch、五分支 state、runtime 冒泡、两个 8001 字符入口的零副作用/fresh 前态、invalidation modified state/digest/renderer-exact summary、NeedConfirmation 与 invalidated state-based accept、深层解除引用 |
| `test_extractor_protocol.py` | async Session、判别 outcome、typed feedback、turn_id 和 recording fake 一致；不得导入生产 adapter |

### 15.3 固定关键用例

```text
CASE-001 杭州预算5000：只产生原文中存在的 evidence
CASE-002 预算五千左右：值5000，evidence五千左右
CASE-003 默认 false/[]：未表达时不产生 evidence
CASE-004 首轮城市 + 次轮日期：城市保留
CASE-005 城市明确纠正：覆盖并产生 change
CASE-006 城市模糊矛盾：旧值保留并 conflict
CASE-007 明天/后天跨月跨年
CASE-008 儿童人数与年龄不一致
CASE-009 明确无儿童与 missing 区分；年龄固定 [] 且不追问
CASE-010 空 Patch 无任何业务副作用
CASE-011 合法 revise 重新摘要，绝不 confirmed；NeedConfirmation/invalidated awaiting state 均可按同一 state-based 门槛 accept
CASE-012 人工模板全字段 round-trip
CASE-013 user_message 8000 进入 extractor；8001 返回 INPUT_TOO_LONG transition 且 Session/EvidenceSink/Patch 为 0；revise 8001 同样失败；两者 fresh 前态；两个公共 DTO 的空字符串 ValidationError
CASE-014 strict/intent/evidence repair 后 extract=1、repair<=1
CASE-015 public outcome 无 raw evidence/基础设施对象
CASE-016 initial/supplement=false，correction/confirmation_revision=true
CASE-017 无旅行字段返回空 Patch 且控制文本不进入 notes
CASE-018 runtime exception 冒泡且 repair_count=0
CASE-019 evidence 四种错误分别编码且路径排序去重
CASE-020 transition 精确 state/outcome 字段、strict/frozen/extra forbid、无默认和无独立版本
CASE-021 五 outcome 与 confirmation_status/requirement/metadata/issues 精确一致
CASE-022 extraction failure 保留 fresh 前态；invalidation 精确六字段顺序、保存 fresh modified state、awaiting_confirmation，after_digest 匹配新 state，before_digest 匹配入口前态，changed_fields 精确等于排序去重后的 merge 变更字段，summary 逐字等于 renderer(modified state requirement)
CASE-023 state/outcome/input 之间双向 mutation 均不传播
CASE-024 参数 ValidationError 与 runtime/programmer exception 不构造 transition
CASE-025 service 输出 strict JSON round-trip 后 branch/UUID/Enum/tuple/值保持且解除引用
```

### 15.4 测试命令

必须从项目根目录按顺序执行完整命令，不得添加 `--ignore`、`-k`、单文件路径、skip 或 xfail：

```powershell
$targets = @("app/requirements", "tests/unit/requirements", "tests/contract/requirements")
$forbiddenImport = '^\s*(from|import)\s+(app\.(llm_runtime|requirement_extraction|graph|persistence|agent|tools|config)(\.|\s|$)|langchain(\.|\s|$)|langgraph(\.|\s|$)|fastapi(\.|\s|$)|asyncpg(\.|\s|$)|psycopg(\.|\s|$))'
$hits = rg -n --glob '*.py' $forbiddenImport $targets 2>$null
if ($LASTEXITCODE -eq 0) { $hits; throw "module 01 contains forbidden imports" }
if ($LASTEXITCODE -ne 1) { throw "forbidden-import scan failed" }

$testSmells = 'sys\.path\.(insert|append)|@pytest\.mark\.(skip|xfail)|pytest\.(skip|xfail)\('
$hits = rg -n --glob '*.py' $testSmells tests/unit/requirements tests/contract/requirements 2>$null
if ($LASTEXITCODE -eq 0) { $hits; throw "module 01 tests contain forbidden isolation bypasses" }
if ($LASTEXITCODE -ne 1) { throw "test-isolation scan failed" }

if (Test-Path -LiteralPath tests/unit/requirements/test_m0_blocking.py) {
    throw "legacy mixed M0 test must not remain in module 01"
}

python -m compileall -q app/requirements tests/unit/requirements tests/contract/requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m pytest --collect-only -q tests/unit/requirements tests/contract/requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m pytest -q tests/unit/requirements tests/contract/requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# maintenance：返回类型修订会直接影响模块 03 的现有跨模块合同测试
python -m pytest -q tests/contract/requirement_extraction/test_requirement_extractor_contract.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:COVERAGE_FILE = Join-Path $env:TEMP "china_travel_agent_module01.coverage"
python -m pytest -q tests/unit/requirements tests/contract/requirements `
    --cov=app.requirements --cov-branch --cov-report=term-missing --cov-fail-under=95
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Remove-Item -LiteralPath $env:COVERAGE_FILE -Force -ErrorAction SilentlyContinue

git diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

普通 full rebuild/maintenance 按本节对应 Gate 执行；需求状态连续性 maintenance 的正式门槛由 §15.5 覆盖。该 maintenance 中模块 01 和模块 04 必须分别报告 line、branch、combined 且三项均至少 95%，不能只依赖 `--cov-fail-under`。每个 `REQ-DLG-*` 和 `REQ-TRANS-*` 必须至少有一个行为测试，且每个公共分支、错误 code 和边界值都有用例。maintenance 还必须运行模块 03 的直接调用合同测试，证明其改为消费 `transition.outcome/state` 后 adapter 语义未变。`collected 0 items`、skip、xfail、导入扫描异常或 coverage 写入失败不得报告为通过。

### 15.5 状态连续性 maintenance 的正式 Docker Gate

`RSC-T05` 与 `RSC-T06` 的正式验收只能使用从当前工作区源码重新构建的 Docker 镜像；宿主机 Python 结果只作快速诊断，不是验收证据。构建可使用缓存，但必须分别执行 runtime/test target 构建，不能复用未执行本次 build 的旧 tag。

所有验收容器必须新建、使用 `--rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true`。开始前若下列固定容器名已存在则 STOP，不删除或复用未知容器；结束后这些名称必须全部不存在：

```powershell
$runtimeImage = "china-travel-agent:rsc-runtime"
$testImage = "china-travel-agent:rsc-test"
$containerNames = @(
  "china-travel-agent-rsc-runtime",
  "china-travel-agent-rsc-version",
  "china-travel-agent-rsc-module01",
  "china-travel-agent-rsc-module03",
  "china-travel-agent-rsc-module04",
  "china-travel-agent-rsc-coverage01",
  "china-travel-agent-rsc-coverage04",
  "china-travel-agent-rsc-compile",
  "china-travel-agent-rsc-regression"
)
$existing = @(docker ps -a --format '{{.Names}}' | Where-Object { $_ -in $containerNames })
if ($LASTEXITCODE -ne 0 -or $existing.Count -ne 0) { throw "RSC container name collision: $($existing -join ', ')" }

docker build --target runtime --tag $runtimeImage .
if ($LASTEXITCODE -ne 0) { throw "runtime image rebuild failed" }
docker build --target test --tag $testImage .
if ($LASTEXITCODE -ne 0) { throw "test image rebuild failed" }

docker run --rm --name china-travel-agent-rsc-runtime --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $runtimeImage
if ($LASTEXITCODE -ne 0) { throw "runtime default CMD failed" }

docker run --rm --name china-travel-agent-rsc-version --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $testImage `
  python -c "import sys,pydantic; assert sys.version_info[:2] == (3,12); assert pydantic.__version__ == '2.13.4'; print(sys.version.split()[0], pydantic.__version__)"
if ($LASTEXITCODE -ne 0) { throw "container Python/Pydantic gate failed" }

docker run --rm --name china-travel-agent-rsc-module01 --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $testImage `
  python -m pytest -q -p no:cacheprovider tests/unit/requirements/test_models.py tests/unit/requirements/test_requirement_service.py
if ($LASTEXITCODE -ne 0) { throw "module 01 target gate failed" }

docker run --rm --name china-travel-agent-rsc-module03 --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $testImage `
  python -m pytest -q -p no:cacheprovider tests/contract/requirement_extraction/test_requirement_extractor_contract.py
if ($LASTEXITCODE -ne 0) { throw "module 03 direct consumer gate failed" }

docker run --rm --name china-travel-agent-rsc-module04 --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $testImage `
  python -m pytest -q -p no:cacheprovider tests/unit/workflow_contracts/test_state.py tests/unit/workflow_contracts/test_security.py tests/contract/workflow_contracts/test_requirement_state_continuity.py tests/contract/workflow_contracts/test_public_api.py tests/contract/workflow_contracts/test_import_boundaries.py
if ($LASTEXITCODE -ne 0) { throw "module 04 target gate failed" }
```

模块 01 与模块 04 coverage 各使用一个新容器。下面的一行 Python 对 JSON totals 分别计算 line、branch、combined；`num_branches==0` 时 branch 报告为 100%，但 combined 仍严格使用同一公式 `(covered_lines + covered_branches) / (num_statements + num_branches)`。只有 combined 分母为 0 才是配置错误并失败：

```powershell
$module01CoverageCommand = @'
python -m pytest -q -p no:cacheprovider tests/unit/requirements tests/contract/requirements --cov=app.requirements --cov-branch --cov-report=term-missing --cov-report=json:/tmp/module01.json && python -c 'import json; t=json.load(open("/tmp/module01.json",encoding="utf-8"))["totals"]; s=t["num_statements"]; b=t["num_branches"]; d=s+b; assert d>0, "coverage denominator is zero"; line=100*t["covered_lines"]/s if s else 0.0; branch=100*t["covered_branches"]/b if b else 100.0; combined=100*(t["covered_lines"]+t["covered_branches"])/d; print(f"module01 line={line:.2f}% branch={branch:.2f}% combined={combined:.2f}%"); assert line>=95 and branch>=95 and combined>=95'
'@
docker run --rm --name china-travel-agent-rsc-coverage01 --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true `
  --env COVERAGE_FILE=/tmp/.coverage-module01 $testImage sh -lc $module01CoverageCommand
if ($LASTEXITCODE -ne 0) { throw "module 01 coverage gate failed" }

$module04CoverageCommand = @'
python -m pytest -q -p no:cacheprovider tests/unit/workflow_contracts tests/contract/workflow_contracts --cov=app.workflow_contracts --cov-branch --cov-report=term-missing --cov-report=json:/tmp/module04.json && python -c 'import json; t=json.load(open("/tmp/module04.json",encoding="utf-8"))["totals"]; s=t["num_statements"]; b=t["num_branches"]; d=s+b; assert d>0, "coverage denominator is zero"; line=100*t["covered_lines"]/s if s else 0.0; branch=100*t["covered_branches"]/b if b else 100.0; combined=100*(t["covered_lines"]+t["covered_branches"])/d; print(f"module04 line={line:.2f}% branch={branch:.2f}% combined={combined:.2f}%"); assert line>=95 and branch>=95 and combined>=95'
'@
docker run --rm --name china-travel-agent-rsc-coverage04 --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true `
  --env COVERAGE_FILE=/tmp/.coverage-module04 $testImage sh -lc $module04CoverageCommand
if ($LASTEXITCODE -ne 0) { throw "module 04 coverage gate failed" }

docker run --rm --name china-travel-agent-rsc-compile --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true `
  --env PYTHONPYCACHEPREFIX=/tmp/pycache $testImage `
  python -m compileall -q app/requirements app/llm_runtime app/requirement_extraction app/workflow_contracts
if ($LASTEXITCODE -ne 0) { throw "module 01-04 compileall failed" }

docker run --rm --name china-travel-agent-rsc-regression --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $testImage `
  python -m pytest -q -ra -p no:cacheprovider tests/unit tests/contract
if ($LASTEXITCODE -ne 0) { throw "module 01-04 full unit/contract regression failed" }

$leftovers = @(docker ps -a --format '{{.Names}}' | Where-Object { $_ -in $containerNames })
if ($LASTEXITCODE -ne 0 -or $leftovers.Count -ne 0) { throw "RSC containers remain: $($leftovers -join ', ')" }
```

`RSC-T06` 同步 README/状态页/Dockerfile 后，必须重新构建这两个镜像，再运行 runtime 默认 CMD 与完整 unit/contract regression；不能以 `RSC-T05` 的旧镜像结果代替。Docker Gate 不访问 DeepSeek、MCP、百度地图或 PostgreSQL，不传入 Secret，并且不得操作 `process_simulation-platform-dev` 或其他项目资源。

## 16. 失败模式检查表

| 失败 | 必须行为 | 测试 |
|---|---|---|
| 模型返回默认 false/empty list | 视为未提供，不生成 evidence | CASE-003 |
| evidence 是 normalized value | 拒绝并 repair | CASE-002 |
| user message/revise text 超 8000 | Session/EvidenceSink/merge 均为 0，返回 fresh 前态 failed transition | CASE-013 |
| NeedConfirmation 或 invalidated 后 accept | 只按 state/action 完整门槛确认，不读取 outcome | CASE-011/022 |
| 月末“明天” | 使用 timedelta 正确跨月 | CASE-007 |
| Patch 空列表 | 按 presence 判断，不用 truthiness | CASE-009/010 |
| 修改未标 correction | 冲突且旧值不变 | CASE-006 |
| metadata 漏字段 | 一致性断言失败 | merge tests |
| renderer 缺字段 | snapshot 测试失败 | renderer tests |
| template 缺房型 | 逐字段错误，不默认标准间 | template tests |
| repair 再失败 | failed，调用数恰好 2 | CASE-014 |
| intent 与 is_correction 不一致 | `intent_mismatch` repair | CASE-016 |
| runtime/network/config error | 原样冒泡，repair=0 | CASE-018 |
| 问候或 prompt injection | 合法空 Patch，不写 notes | CASE-017 |

## 17. 唯一任务清单

正文其他章节不得复制任务描述，只能引用下列 Task ID。

| Task | Stage | Depends on | Deliverable | Acceptance |
|---|---|---|---|---|
| `DLG-T01` | domain-core | — | 公共 models、field paths、outcomes、transition、bounds | model tests |
| `DLG-T02` | domain-core | `DLG-T01` | async Session/Extractor Protocol、判别 attempt outcome、typed feedback、判别 evidence verifier | evidence + contract tests |
| `DLG-T03` | domain-core | `DLG-T01` | normalization 与 validators | normalization tests |
| `DLG-T04` | domain-core | `DLG-T01`,`DLG-T03` | immutable merge、metadata、derived、invalidation | merge tests |
| `DLG-T05` | domain-core | `DLG-T01`,`DLG-T04` | clarification 与 confirmation renderer | renderer tests |
| `DLG-T06` | domain-core | `DLG-T01`,`DLG-T03`,`DLG-T04` | manual template parser/serializer | round-trip tests |
| `DLG-T07` | domain-core | `DLG-T02`,`DLG-T04`,`DLG-T05`,`DLG-T06` | async RequirementDomainService 唯一管线、单 Session repair、五分支 transition 与 fresh copy | service tests |
| `DLG-T08` | domain-proof | `DLG-T01..DLG-T07` | REQ-DLG/REQ-TRANS 逐条对照、固定纯领域测试、导入扫描与覆盖率证明 | 两组合同 ID 全覆盖、全目录收集/测试通过、零越界 import、零 skip/xfail、coverage ≥95% |

依赖 DAG：

```text
DLG-T01 models
├── DLG-T02 extraction/evidence ─┐
├── DLG-T03 normalization ───────┼→ DLG-T04 merge
└────────────────────────────────┘      ├→ DLG-T05 renderer
                                       ├→ DLG-T06 template
                                       └→ DLG-T07 service
                                                ↓
                                           DLG-T08 proof
```

同一 `app/requirements` 目录内共享模型较多，建议顺序实施，不建议多个 worktree 同时修改。

## 18. 禁止行为

- 修改规范迁就当前实现。
- 保留伪造 evidence 的 `_collect_evidence` 逻辑。
- 让 Pydantic 默认 false/empty list 表示用户已回答。
- 使用普通 `str` action 或无约束 dict outcome。
- 返回裸 `RequirementDomainOutcome`、保留旧返回兼容层或从 outcome 反向重建 state。
- 深层函数读取系统时钟。
- merge 原地修改旧 state。
- 用 truthiness 区分未提供与空列表。
- 只更新 destination metadata。
- renderer 或规则层调用模型。
- 为房型、限制、人数或日期静默补业务默认值。
- 创建 Graph、数据库、serializer、entry 或全局 test platform。
- 在 `app/requirements` 构造模型客户端、LangChain Runnable、真实 Prompt、RealClock 或生产 EvidenceSink。
- 在生产代码或测试中导入 02-23 的具体实现或禁止的第三方框架。
- 删除失败测试、降低断言或把 coverage 错误报告为成功。
- 保留、忽略、skip、xfail 或改名隐藏混合 Graph/PG/entry/security 的 `test_m0_blocking.py`。
- 用单一 `test_domain.py`、只跑通过的单文件/`-k` 子集或只断言对象可构造代替固定测试套件。
- 测试修改 `sys.path`、硬编码绝对仓库路径或连接本地已启动服务。
- 保留同步 extractor 双轨、线程池桥接或 `asyncio.run()`。
- 用 exception 字符串控制 evidence repair，或合并缺失和重复错误码。
- 把完整当前需求压缩/摘要后交给 03，或让 01 负责 Prompt 拼接。
- 让 transition.state 与 outcome 或调用输入共享可变嵌套引用。
- 让调用方自行拼接 invalidation summary、导入私有 renderer、伪造 `NeedConfirmation` 或调用空白 `process_turn()`。
- 让 `confirm()` 接收/检查当前 outcome，或拒绝已展示 `RequirementInvalidated.summary` 的合法 awaiting-confirmation state。
- 在 `RequirementTurnInput.user_message` 或 `RequirementConfirmation.revision_text` 上设置 8,000 `max_length`，导致 service 的 `INPUT_TOO_LONG` 分支不可达。
- 在需求状态连续性 maintenance 中把 Docker 设为可选、用宿主机 Python 冒充验收、跳过 `MODULE-CLOSEOUT`，或由实施模型 stage/commit。

## 19. NOT in scope

- 02、03 的 LLM Runtime 和真实 RequirementExtractor 适配。
- 04、05、13 的 workflow state、LangGraph、pause/resume 和完整 Orchestrator。
- 04 的 Demo artifact/Repository 端口与进程内参考实现、10 的 PostgreSQL artifact/evidence 持久实现、11 的 checkpoint/执行账本/恢复，以及 13 的装配。
- 19、20 的加密、删除、Trace、Metrics 和审计实现。
- 06–09、12、16 的地图、酒店、候选研究、行程、格式化和重排，以及等待可信价格来源批准的旅行费用判定。
- 14 的 Application Service、应用 API 和前端网页入口。
- 15、22、23 的全 Agent E2E、恢复测试和真实模型质量评测。
- 旧原型兼容。

编码 Agent 不得为这些项目创建待办标记、stub 或占位 class；后续模块必须按 [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) 的职责与依赖重新编写规范。

## 20. 最终交付报告

```markdown
# 01 领域实施结果

## 执行上下文与结论
- mode: maintenance | full_rebuild | requalification_only
- task_result: PASS | FAIL
- contract_complete: retained | passed | failed | not_evaluated
- live_qualified: not_applicable
- branch:
- start HEAD:
- end HEAD:
- dirty worktree preserved: PASS/FAIL + details
- task allowlist:
- files changed:
- out-of-allowlist anomalies: none | exact paths and action

## 规范对照
| Contract ID（REQ-DLG / REQ-TRANS） | Public symbol | Test | Result |
|---|---|---|---|

## L2 偏离
| Reference implementation | Actual implementation | Reason | Equivalent proof |
|---|---|---|---|

## 模型调用预算
- 正常轮调用数：
- repair 轮调用数：
- 超限输入调用数（必须为0）：

## 验证命令
| Command | Exit code | Summary |
|---|---:|---|

## Docker 与外部副作用
- Docker command/result: exact rebuild/run commands and exit codes；仅非 RSC 模式可写 NOT_APPLICABLE + mode reason
- other Docker projects touched: none (required)
- Secret/raw-content leak check: PASS/FAIL
- implementation model staged files (must be none):
- commit: none（implementation model must not commit）
- push: none | target
- PR: none | URL
- deploy: none | target

## 纯领域隔离证明
- 固定七个 unit test 文件齐全：是/否
- extractor Protocol contract test 齐全：是/否
- `test_m0_blocking.py` 不存在：是/否
- forbidden-import scan 无输出：是/否
- sys.path/skip/xfail scan 无输出：是/否
- 完整目录 collected tests：数量
- module 01 line/branch/combined coverage：三个百分比
- module 04 line/branch/combined coverage（RSC）：三个百分比 | NOT_APPLICABLE (non-RSC mode)

## RSC Task 与 MODULE-CLOSEOUT
| Task | Prerequisite | Files changed | Command | Exit code | Result/NOT_RUN reason |
|---|---|---|---|---:|---|
| RSC-T00 | | | | | |
| RSC-T01 | | | | | |
| RSC-T02 | | | | | |
| RSC-T03 | | | | | |
| RSC-T04 | | | | | |
| RSC-T05 | | | | | |
| RSC-T06 | | | | | |

- README truth sync:
- IMPLEMENTATION_STATUS truth sync:
- Dockerfile impact check and exact change/no-change reason:
- post-closeout image rebuild:
- post-closeout runtime default CMD:
- post-closeout full unit/contract regression:
- authoritative design/Vibe remained read-only during code/closeout: PASS/FAIL
- module 05 remained paused: PASS/FAIL

## 明确失败项
- collected 0 tests：是/否
- skip/xfail：数量
- coverage 写入失败：是/否
- app/requirements 越界导入 Graph/PG：是/否
- app/requirements 导入 02-23 具体实现：是/否
- tests 导入 LLM/Graph/PG/entry/security：是/否
- tests 硬编码绝对路径或修改 sys.path：是/否
- 只运行单文件或 `-k` 子集：是/否
- 为 02-23 创建待办标记/stub/占位 class：是/否
- public outcome 含 raw evidence：是/否

## 剩余风险
- 仅列本领域风险；后续模块不在此伪装完成。
```

不得使用“基本完成”“应该可用”替代逐项证据。

结论按模式判定：`full_rebuild` 只有 `DLG-T01..DLG-T08` 和全部适用 Gate 通过时 `task_result=PASS, contract_complete=passed`；普通 `maintenance` 只对用户白名单和受影响 Gate 判定；需求状态连续性 maintenance 只有 `RSC-T00..RSC-T06`、两模块三项 coverage、Docker 及 post-closeout 重建/回归全部 PASS 时才能写 `task_result=PASS, contract_complete=retained`。模块 01 没有 live 外部边界，固定写 `live_qualified=not_applicable`。未运行且属于当前模式必需的命令必须导致 `task_result=FAIL`。实施模型的 staged/commit 必须均为 none；独立审查者只能在用户另行明确授权后提交。

## 枢纽澄清维护执行入口（2026-10-07，用户已批准）

这是当前未合并演示链的 maintenance，不重建 RSC/DLG 原阶段。目标为 D01“枢纽澄清维护增量”HUB-01..08、D03“枢纽角色提取维护增量”、D13“枢纽输入失败映射增量”、D14“枢纽澄清与安全输入提示增量”。必读其配套执行规范同名入口、SHARED_IMPLEMENTATION_RULES §3–9、README、IMPLEMENTATION_STATUS；公共依赖按 IMPLEMENTATION_ROADMAP depends_on，D03 消费 D01 §7/evidence 与 D02 Runtime/Profile/一次 repair；D13 消费 D01确认、D03提取、D04 TerminalOutcome/Registry、D05原 interrupt/resume、D06 typed 地图、D07人工酒店、D08研究 outcome、D09初始 input_invalid、D10 Artifact、D11公共 get_state/租约安全、D12结果；D14沿D13公共主图入口。只修改本次明确接缝。

基线分支 feature/module-09-structured-output-fix，HEAD b3438ca13e3c88e4669c942d18018e6b133b7857；保留已有 dirty 与四个 staged 文件，索引必须与开工快照一致。用户本次授权设计同步、代码和离线验收；未授权 stage/commit/merge/push/PR、真实 API、部署、迁移或日常任务恢复/取消。历史 .env/Key/数据库/18080/8080 与其他项目只保留，不读取供应商原始内容。不得创建诊断文档。

HF00：前置为用户同意方案。保存 Git/index/文件哈希基线，核验引用与当前资源；同步 D01/E01、D03/E03、D13/E13、D14/E14 八份既有文档，不新建报告。代码阶段开始前冻结本增量。

2026-10-07 用户续批：生产白名单增加 app/requirement_extraction/events.py，仅为 prompt_version 增加 v4 并保留 v1/v2/v3；授权取消本轮两个 v4 Prompt 与两个新增测试的暂存，保留内容和原四项暂存。其余权限不变。双审意见在原白名单内修正：按当前 Issue.field_path 限定补充替换；排除含糊及明确非铁路名称；私有 merger 只接收已验证路径证明，不将原文重新放入 Patch；修正新 repair 的规则引用并同步实际 hash。

HF01 + HF02 连续实施后统一测试：HF01 实现 M01 公开纯检查、必填/合并/确认和 M03 新 Prompt；HF02 实现 M14 问题/类型选项/有限中文提交、M13输入错误映射及终态安全提示。生产白名单：app/requirements/{normalization.py,merge.py,service.py,dialogue.py,__init__.py}；app/requirement_extraction/prompt_loader.py、prompts/{extract_v4_hub_roles.txt,repair_v4_hub_roles.txt}；app/demo_workflow/nodes.py；app/web_application/{models.py,__init__.py,presentation.py,service.py,static/app.js,static/task.html}。历史 Prompt 字节不改，M09输入校验/其他预算/持久与命令版本不改。

测试白名单：tests/unit/requirements/{test_hub_confirmation.py,test_normalization.py,test_merge.py,test_requirement_service.py,test_dialogue_renderer.py}；tests/unit/requirement_extraction/test_prompt_loader.py；tests/unit/web_application/{test_models_settings.py,test_service_control.py,test_hub_input_details.py,fakes.py}；tests/contract/web_application/test_public_contract.py；tests/contract/demo_workflow/test_upstream_contracts.py；tests/integration/web_application/test_workflow_api.py；tests/browser/web_application/test_demo_pages.py；tests/requirement_subgraph_fakes.py、tests/support/demo_workflow_fakes.py。既有合法完整 fixture 必须显式提供推导类型；禁止放宽断言或绕过真实 M01/M03。新增 test 文件仅用于本次行为。

统一矩阵：HUB-01..08；extract/repair hash/规则与旧资源；缺项/冲突表单有选项且无预选，实际中文经真 M03/M01 修正后到确认；M09真实输入检查通过；错误代码映射；仅 owned+details+输入失败 closed 读取 state，SSE/list/其他失败不读、失败读取不写、历史原code/summary不变；浏览器显示具体原因且关闭终态不提供澄清按钮。网络全部 offline，不调用真实模型/地图。定向通过后，两位现有子agent逐条对照上述文档只读审查代码，不能只看pytest；主模型修复已授权白名单内意见并重验。

HF03：前置 HF01/02 定向与双审通过。构建当前 runtime/test/browser-test 镜像；运行受影响 M01/M03/M05/M13/M14 模块与完整 unit/contract，M01/M03覆盖率按原 maintenance Gate（不冒充旧正式资格）；内部隔离 PG 上完成网页真实主图与 Chromium 回归。隔离前核验 owner/prefix，无主机发布端口，不使用现有业务卷；结束仅清理本次已核验资源。数据库 schema 沿既有迁移在隔离库初始化，生产迁移 none。

MODULE-CLOSEOUT 白名单仅 README.md、docs/IMPLEMENTATION_STATUS.md；记录当前真实证据，检查 Dockerfile 的 COPY app/tests 与 CMD 已覆盖变更，不为通过而改设计。同步后再构建 runtime/test/browser-test，重跑默认 CMD（无配置安全退出）、受影响 Gate 与完整离线回归；M03 新 Prompt live 资格 NOT_RUN，不能报告供应商已验证。

STOP：白名单不足/需其他设计变更、索引或 HEAD 改动、历史字节受损、生产资源变动、需要外部 API、离线失败或安全读取不确定。报告至少含阶段/状态、规范逐项符合、分支与前后HEAD、修改文件/异常文件、迁移、定向/模块/coverage/PG/browser/全量与 Docker 结果、双审意见处置、正式资格限制、staged/commit/merge/push/PR/部署。最终保持未提交待用户审阅。


## 2026-10-11 批准执行入口：研究合同修复

执行本设计同日R条款及 [M08 R1～R6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约拥有阶段目标、前置/分支、联合文件白名单、允许操作、测试矩阵、停止条件和最终报告。R1/R2、R3/R4、R5/R6每组结束两个子agent逐条对照文档审查。未经新的提交授权保持未暂存；禁止push/PR/部署。
