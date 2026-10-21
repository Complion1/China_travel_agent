# 02 LLM Runtime 与 Model Profiles Vibe Coding 编码契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次规划角色上限17、有效工具/准入回调和完整交互采集见 [M02设计§6/19](02_llm_runtime_and_model_profiles.md)；阶段规则归 [M09§12～15](09_itinerary_planning_loop.md)，专用存储归 [M10§23](10_artifact_persistence.md)。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档状态：v1.3（DeepSeek Flash 服务端别名兼容版）
> 实现状态：模块 02 已完成；本文继续作为维护、重新资格与回归时的精确执行契约
> 目标读者：能力较弱、必须按明确步骤实现或维护 `app/llm_runtime` 的编码模型
> 唯一模块规范：[`02_llm_runtime_and_model_profiles.md`](02_llm_runtime_and_model_profiles.md)
> 上位边界：[`00_agent_master_design.md`](00_agent_master_design.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 当前验证快照：[`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 执行原则：本文不是摘要；文件白名单、符号、算法顺序、任务 Gate、测试矩阵、停止条件和报告字段不得由编码模型自行删减或改写

```yaml
module_id: "02"
delivery_phase: "Demo"
blocks_demo: true
blocks_production_release: true
depends_on: []
provides:
  - DeepSeekRuntime / ModelOperation
  - ModelRole / ModelProfile / 固定 Profile Registry
  - PromptEnvelope / ToolContract / ToolResult
  - StructuredInvocationOutcome / ToolTurnOutcome
  - LLMUsage / LLMRuntimeError / LLMErrorCode
  - DeepSeekConfigurationError
  - ProviderAdapter / Sleeper Protocol
  - run_capability_probe / CapabilityProbeReport
  - REQ-LLM-001..040
consumes: []
parallel_with: ["01"]
not_in_scope:
  - app/requirements 和 03 的 RequirementExtractor、业务 Prompt、strict DTO
  - 04-23 的 Graph、数据库、地图、规划、格式化、入口和系统评测
  - Agent Skill 注册/调度、MCP 工具发现、MCP client/session、MCP Gateway 和 MCP 工具执行
  - 多供应商、动态模型、fallback、流式输出和 Beta strict tool endpoint
acceptance_gate_scope: "full_rebuild；maintenance/requalification 按第 0 节执行受影响 Gate"
acceptance_gate:
  - LLM-T01..LLM-T08 全部完成
  - 单元与 fake contract 测试全部通过且无 skip/xfail
  - app/llm_runtime line/branch 综合覆盖率不低于 95%
  - 真实 deepseek-v4-flash capability probe 全部通过
  - requirements.lock 在第二个干净环境中可重建并通过 01、02 测试
  - app/llm_runtime 对 01 和 03-23 实现零 import
test_class: adapter-contract
unit_test_command: python -m pytest -q tests/unit/llm_runtime
contract_test_command: python -m pytest -q tests/contract/llm_runtime
allowed_test_dependencies:
  - Python 标准库 / Pydantic / pytest / pytest-cov / Hypothesis
  - langchain-core==1.4.9
  - langchain-deepseek==1.1.0
  - fake provider；显式 live command 中允许官方 DeepSeek API
forbidden_test_dependencies:
  - app.requirements / app.graph / app.persistence / app.agent / app.tools
  - LangGraph / FastAPI / asyncpg / psycopg / 百度地图 SDK
  - mcp / langchain-mcp-adapters
  - 03-23 的任何实现
required_fakes:
  - ScriptedProviderAdapter
  - RecordingSleeper
stable_contracts:
  - DeepSeekRuntime / ModelOperation / ModelProfile
  - typed invocation outcomes / LLMRuntimeError
production_replacement:
  - Production v1 可在保持公共合同前提下增加限流、降级或多模型 adapter
```

## 0. 执行模式（必须先判定）

模块 02 已完成。编码模型在读正文任务前，必须且只能选择一种模式：

| 模式 | 进入条件 | 允许动作 | 禁止动作 |
|---|---|---|---|
| `maintenance`（默认） | 用户要求修复、调整或审核现有模块 02 | 只修改用户任务白名单；运行受影响 Gate 和用户要求的回归 | 重新生成整个 Runtime、擅自改锁文件或按全文“重新实现” |
| `full_rebuild` | 用户明确要求从头重建模块 02，并明确允许完整文件范围 | 按 `LLM-T01..LLM-T08` 顺序执行本文全部合同 | 跳过任务、改变冻结 Profile/错误合同或越界实现其他模块 |
| `requalification_only` | 用户只要求重新验证；任务必须明确 `qualification_scope=offline` 或 `qualification_scope=live` | 不改生产代码、Prompt、Profile 和测试阈值；只运行所选 scope 的指定门禁 | scope 不明时猜测、offline scope 调用真实 API、失败后循环重跑、修改实现或把诊断当正式资格 |

用户未明确写“从头重建”时一律使用 `maintenance`。模式、白名单或 requalification scope 不清楚时不得修改代码或调用真实 API，先报告冲突。`qualification_scope=offline` 只运行离线门禁并固定写 `live_qualified=not_run`；`qualification_scope=live` 必须有用户对本次真实 DeepSeek 调用的明确授权，才可执行一次正式 probe。正文中的创建文件、生成 lock 和完整 Task 顺序只在 `full_rebuild` 生效；L0/L1、生命周期、安全、禁止越界和相关测试断言在三种模式下始终生效。

## 1. 最终交付物

本任务只允许交付：

```text
app/llm_runtime/
tests/unit/llm_runtime/
tests/contract/llm_runtime/
tests/live/llm_runtime/
scripts/probe_deepseek_runtime.py
pyproject.toml 中模块 02 的精确依赖
requirements.lock
```

实现完成后，后续模块只能这样使用：

```python
operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
outcome = operation.invoke_structured(
    prompt=PromptEnvelope(system_text=..., user_text=...),
    schema=ConsumerOwnedStrictModel,
)
```

不得要求后续模块直接构造 `ChatDeepSeek`、处理 provider exception、解析 JSON 字符串、读取 reasoning content 或自行叠加 retry。模块 03 每个需求提取 Session 只创建一个 `ModelOperation`，并只使用其 async 结构化入口完成最多一次 extract 和一次 repair；这是 consumer 调用预算，不得写入 02 的业务分支。

## 2. 执行纪律

编码 Agent 必须遵守：

1. 先读完 00、02 规范、02 编码契约、共享实施规则和实施状态。
2. 不修改 01 公共合同，不提前创建 03-23 业务实现。
3. `full_rebuild` 不根据旧代码设计兼容层；`maintenance` 必须保留已冻结公共合同，只修复任务白名单内问题。
4. 每完成一个任务先运行该任务的固定测试，再开始下一任务。
5. 不留下 `TODO`、`pass`、未使用 stub、假成功 probe 或宽泛 `except Exception: return None`。
6. 不通过降低 strict、扩大 nullable、删除测试、添加 skip/xfail 或把 provider 调用换成自由文本来获得绿灯。
7. L0/L1 偏离必须停止并请求用户批准；不得自行修改文档。
8. L2 偏离必须在最终交付报告中列出原因和等价测试。

## 3. 约束等级

### 3.1 L0：不可绕过

- `deepseek-v4-flash` 唯一模型。
- 官方稳定 Base URL 唯一端点。
- 四角色全部 thinking enabled、reasoning effort high。
- 非流式、无采样参数、`ChatDeepSeek.max_retries=0`。
- 02 自己最多执行一次 retry。
- 输入超限不发送、不截断；输出截断整体作废。
- 结构化输出必须本地 Pydantic strict decode。
- reasoning content 不得离开 Operation。
- 无多供应商、无 fallback、无 Beta strict endpoint。

### 3.2 L1：公共合同固定

以下名称、字段语义和判别分支固定：

- `ModelRole`
- `ModelProfile`
- `DeepSeekSettings`
- `DeepSeekConfigurationError`
- `PromptEnvelope`
- `ToolContract`
- `ToolResult`
- `ValidatedToolCall`
- `LLMUsage`
- `InvocationMetadata`
- `LLMErrorCode`
- `LLMRuntimeError`
- `StructuredInvocationSuccess`
- `ToolCallsReady`
- `ToolTurnCompleted`
- `LLMInvocationFailure`
- `ProviderAdapter`
- `Sleeper`
- `DeepSeekRuntime`
- `ModelOperation`
- `CapabilityProbeReport`
- `run_capability_probe`

### 3.3 L2：允许调整但必须报告

- 私有 helper 名称。
- 单个私有函数如何拆分。
- 测试 helper 的非公共字段。
- 同一文件内私有类型的排列顺序。

不允许以 L2 为由改变目录职责、公开类型、Profile 数值、重试表或错误码。

## 4. 禁止越界

遇到以下需求必须停止扩展并报告归属，不得创建临时实现：

| 发现的需求 | 所属模块 | 02 可做的唯一事情 |
|---|---:|---|
| 需求提取 Prompt、StrictRequirementPatchDTO | 03 | 接收 consumer schema，不导入它 |
| phase、thread、checkpoint、interrupt | 04、05、11、13 | Operation 保持不可序列化 |
| artifact、幂等记录 | 04、10、11 | 只返回无原文 metadata |
| 审计记录 | 20、21 | 只返回安全 usage/error DTO |
| 加密、TTL、删除 | 19 | 不保存 secret/reasoning |
| Trace、Metrics、成本事件 | 20 | 返回 usage/error DTO，不写 Sink |
| 地图工具实现 | 06 | 只验证 tool call args，不执行 |
| MCP 工具发现、连接、会话、重连和原始响应 | 06 或其他工具 adapter 所属模块 | 不导入 MCP 包；只返回已验证 tool call 意图 |
| ReAct 迭代/停止/替换 | 08、09、16 | 只提供固定调用硬上限和 tool turn |
| 路由和反馈循环 | 13、20、21 | 只返回判别 outcome |
| Application Service、应用 API 和前端网页 | 14 | streaming 固定 false；具体 Web 框架在 M14 设计时确认 |
| 公共部署或流式 UI | Future，未编号 | 不创建入口或提前冻结合同 |
| 真实 E2E 和旅游质量 | 15、22、23 | live probe 只验证 provider 能力 |

生产代码禁止 import：

```text
app.requirements
app.graph
app.persistence
app.agent
app.tools
langgraph
fastapi
asyncpg
psycopg / psycopg2
百度地图 SDK
mcp
langchain-mcp-adapters
```

## 5. 固定目标结构

必须创建以下文件；不得用单一 `runtime.py` 包含全部实现：

```text
app/llm_runtime/
├── __init__.py
├── models.py
├── profiles.py
├── settings.py
├── protocols.py
├── sizing.py
├── provider.py
├── runtime.py
└── probe.py

tests/unit/llm_runtime/
├── __init__.py
├── fakes.py
├── test_models_and_profiles.py
├── test_settings.py
├── test_sizing_and_schema.py
├── test_retry_and_errors.py
├── test_provider_adapter.py
├── test_runtime_contract_edges.py
├── test_structured_runtime.py
├── test_tool_operation.py
└── test_privacy_and_serialization.py

tests/contract/llm_runtime/
├── test_provider_protocol.py
└── test_capability_probe_fake.py

tests/live/llm_runtime/
└── test_deepseek_capabilities.py

scripts/
└── probe_deepseek_runtime.py
```

职责固定：

| 文件 | 唯一职责 |
|---|---|
| `models.py` | 公共 enum、DTO、outcome、error、tool contracts |
| `profiles.py` | 四个不可变 Profile 和 lookup |
| `settings.py` | 显式 settings 加载与 Secret 校验 |
| `protocols.py` | ProviderAdapter、Sleeper 和 provider-normalized 私有边界 |
| `sizing.py` | canonical serialization、byte 计量、consumer schema 预检 |
| `provider.py` | 唯一 `ChatDeepSeek` 生产适配和 SDK error 规范化 |
| `runtime.py` | DeepSeekRuntime、ModelOperation、配额、retry、decode、tool continuity |
| `probe.py` | fake/live 共用的能力探针逻辑 |
| `__init__.py` | 只导出 L1 公共 API |

依赖方向固定：

```text
models ← profiles
models ← settings
models ← protocols
models ← sizing
models + profiles + settings + protocols ← provider
models + profiles + protocols + sizing ← runtime
models + profiles + protocols + runtime ← probe
all public symbols ← __init__
```

`models.py` 不得导入同包其他模块。`provider.py` 不能导入 `runtime.py`。`probe.py` 不能反向成为生产 runtime 的依赖。

## 6. 公共模型逐字段实现

所有公共 Pydantic DTO 固定：

```python
model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
```

### 6.1 ModelRole

逐字实现：

```python
class ModelRole(str, Enum):
    REQUIREMENT_EXTRACTION = "requirement_extraction"
    CANDIDATE_RESEARCH = "candidate_research"
    ITINERARY_PLANNING = "itinerary_planning"
    REPLANNING = "replanning"
```

禁止 `_missing_` fallback、custom role、字符串自动 coercion。

### 6.2 PromptEnvelope

```python
class PromptEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    system_text: Annotated[str, StringConstraints(min_length=1)]
    user_text: Annotated[str, StringConstraints(min_length=1)]
```

只允许 system + user 两段。不得允许 caller 传完整 `list[BaseMessage]`、assistant history、reasoning content、provider metadata 或 tool history。工具子轮由 Operation 私有管理。

不得 strip 后静默接受空白；validator 对 `value.strip()` 为空直接拒绝，但保留非空文本原样。

### 6.3 ToolContract

`ToolContract` 使用普通 frozen dataclass，因为字段包含 Python class：

```python
@dataclass(frozen=True, slots=True)
class ToolContract(Generic[TArgs]):
    name: str
    description: str
    args_schema: type[TArgs]
```

构造时固定校验：

- `name` 匹配 `^[A-Za-z0-9_-]{1,64}$`。
- description 去除首尾空白后长度 `1..1024`，禁止静默截断。
- `args_schema` 必须是 Pydantic `BaseModel` 子类。
- schema 的 `extra="forbid"`、`strict=True` 必须成立。
- 同一次 tool operation 的 name 必须唯一。

### 6.4 ToolResult 与 ValidatedToolCall

```python
class ToolResult(BaseModel):
    call_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    content: Annotated[str, StringConstraints(min_length=1)]

class ValidatedToolCall(BaseModel):
    call_id: str
    tool_name: str
    arguments: BaseModel
```

`arguments` 的运行时具体类型必须等于对应 `args_schema`。工具结果 content 的总长度仍进入下一请求 byte 预检；02 不裁剪。

### 6.5 usage 与 metadata

逐字段固定：

```python
class LLMUsage(BaseModel):
    prompt_tokens: NonNegativeInt
    completion_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    reasoning_tokens: NonNegativeInt
    prompt_cache_hit_tokens: NonNegativeInt
    prompt_cache_miss_tokens: NonNegativeInt

class InvocationMetadata(BaseModel):
    role: ModelRole
    model_name: Literal["deepseek-v4-flash"]
    finish_reason: Literal[
        "stop", "tool_calls", "length", "content_filter",
        "insufficient_system_resource"
    ]
    provider_attempts: Annotated[int, Field(ge=1, le=2)]
    possible_duplicate: bool
    usage: LLMUsage
```

一致性校验：

```text
total_tokens == prompt_tokens + completion_tokens
prompt_cache_hit_tokens + prompt_cache_miss_tokens == prompt_tokens
reasoning_tokens <= completion_tokens
```

若真实 provider 暂不返回某个细分字段，生产 adapter 可以把“可选的细分字段”规范化为 0，但必须满足：顶层 prompt/completion/total 三项存在。哪些字段是 provider 可选必须在 `provider.py` 单点定义，consumer 不感知。

### 6.6 Error

`LLMErrorCode` 只能包含 02 规范第 11.1 节列出的 22 个值，不增加 `unknown_error`。

```python
class LLMRuntimeError(BaseModel):
    code: LLMErrorCode
    role: ModelRole
    retryable: bool
    possible_duplicate: bool
    provider_status_code: int | None = None
    retry_after_seconds: float | None = None
    actual_bytes: int | None = None
    max_bytes: int | None = None
```

错误对象不提供 `message`、`detail`、`raw_error`、`request`、`response`、`cause` 字段。

### 6.7 outcome

每个 outcome 使用唯一 `Literal`：

```text
structured_success
tool_calls
completed
failure
```

`LLMInvocationFailure` 只含 `outcome_type` 和 `error`。公共 outcome 禁止携带 raw `AIMessage`、JSON string、parsing_error exception 或 reasoning content。

## 7. Profile Registry 精确实现

2026-09-27 用户批准 LIVE-021，仅把固定规划角色的 `max_request_utf8_bytes` 改为 2,097,152（2 MiB）。通用默认及其他角色、输出 token、思考设置、超时、调用额度、重试保持原值。同步/异步续调必须验证旧限额以上可调用、新限额等号通过、超 1 字节零 provider 调用且不消费 semantic 配额；完整历史原样保留，不截断或摘要。该批准不修改 Prompt 和规划业务 Gate；见 [统一问题台账](VALIDATION.md)。

### 7.1 常量

```python
MODEL_NAME = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
THINKING_TYPE = "enabled"
REASONING_EFFORT = "high"
CONNECT_TIMEOUT_SECONDS = 10
WRITE_TIMEOUT_SECONDS = 30
POOL_TIMEOUT_SECONDS = 10
PROVIDER_ATTEMPTS_PER_INVOCATION = 2
```

### 7.2 四个实例

必须逐个显式构造，不从环境或 YAML 生成：

```python
REQUIREMENT_EXTRACTION_PROFILE = ModelProfile(
    role=ModelRole.REQUIREMENT_EXTRACTION,
    model_name=MODEL_NAME,
    base_url=BASE_URL,
    thinking_type=THINKING_TYPE,
    reasoning_effort=REASONING_EFFORT,
    streaming=False,
    connect_timeout_seconds=10,
    read_timeout_seconds=120,
    write_timeout_seconds=30,
    pool_timeout_seconds=10,
    provider_attempts_per_invocation=2,
    semantic_invocation_limit=2,
    max_request_utf8_bytes=32_768,
    max_output_tokens=32_768,
)
```

完整矩阵固定；Python 实例中的下划线只是数值分隔符，不改变数值：

| role | read | semantic limit | bytes | output tokens |
|---|---:|---:|---:|---:|
| requirement extraction | 120 | 2 | 32,768 | 32,768 |
| candidate research | 120 | 12 | 262,144 | 131,072 |
| itinerary planning | 120 | 19 | 2,097,152 | 131,072 |
| replanning | 120 | 8 | 262,144 | 32,768 |

Registry 用 `MappingProxyType` 包装。`get_profile(role)` 对非 `ModelRole` 输入不得 coercion。

2026-09-26 首次批准将需求抽取上限从 8,192 调整为 16,384，通用 `ModelProfile` 默认值同步为 16,384。第 10/11 轮 repair 连续截断后，用户再次确认固定需求抽取角色提高到 32,768；通用默认仍为 16,384，其他 Profile、思考设置、超时、semantic 次数及 provider 重试次数不变。必须验证 SDK 构造对该角色传入 32,768、其余矩阵不变、截断仍不可重试。批准依据和回归记录见 [统一问题台账](VALIDATION.md)。Prompt v3 字节及证据校验不变，运行时仍不得自行扩容或重试。

## 8. Settings 精确实现

```python
_PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

def load_project_env() -> None:
    load_dotenv(
        dotenv_path=_PROJECT_ENV_FILE,
        override=False,
        interpolate=False,
    )

def load_settings(
    environ: Mapping[str, str] | None = None,
) -> DeepSeekSettings:
    if environ is None:
        load_project_env()
        environ = os.environ
    raw = environ.get("DEEPSEEK_API_KEY")
    if raw is None or not raw.strip() or raw.strip().lower() in {
        "changeme", "your-api-key", "<your-api-key>"
    }:
        raise DeepSeekConfigurationError(LLMErrorCode.CONFIGURATION_ERROR)
    return DeepSeekSettings(api_key=SecretStr(raw.strip()))
```

要求：

- `load_project_env()` 只能读取项目根目录固定 `.env`，必须使用 `override=False`。
- `load_project_env()` 必须使用 `interpolate=False`，Secret 按原始文本读取，禁止 `$NAME` 插值或引用其他变量。
- `load_settings()` 无参数时先加载固定 `.env`，再读取 `os.environ`；已有进程变量必须优先。
- `load_settings(mapping)` 只看显式 Mapping，不加载 `.env`，用于全部离线测试。
- 模块 import 本身不得读取 `.env`，只有显式调用上述函数才能加载。
- 不支持其他 key 文件路径、命令行 key、默认 key 或交互输入。
- `.env` 必须写入 `.gitignore`；只允许提交 `.env.example`，其值固定为 `your-api-key`。
- Settings 异常不回显环境值。
- `DeepSeekConfigurationError` 的 code 固定为 configuration_error，不含 role、raw value 或任意 detail。
- `DeepSeekSettings.model_dump()` 不得用于日志；测试覆盖 repr/str。

## 9. canonical sizing 与 Schema 预检

### 9.1 canonical request

`sizing.py` 实现纯函数：

```python
def canonical_request_bytes(
    *,
    profile: ModelProfile,
    prompt: PromptEnvelope,
    json_schema: dict[str, object] | None,
    tools: tuple[CanonicalToolDefinition, ...],
    internal_tool_history: tuple[CanonicalProviderMessage, ...] = (),
) -> bytes: ...
```

规范化规则：

- `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`。
- 编码固定 UTF-8。
- 包含 model、thinking、reasoning effort、max tokens、messages、Schema、tools。
- 不包含 api_key、timeout、retry 计数和 Python repr。
- 同一语义输入必须产生完全相同 bytes。

### 9.2 byte gate

调用次序必须是：

```text
validate role/prompt/schema/tools
→ build canonical bytes
→ compare limit
→ claim semantic quota
→ provider call
```

因此 schema invalid 或 input too large 均不消耗 quota。`actual == limit` 允许发送；只有 `actual > limit` 拒绝。

### 9.3 consumer Schema

固定 validator：

1. 必须是 `type[BaseModel]`。
2. 顶层 `extra="forbid"`。
3. 顶层 `strict=True`。
4. `model_json_schema()` 必须成功。
5. canonical JSON Schema 必须是 object root。
6. Schema 中不允许 callable、任意 Python object 或非 JSON 值。
7. Schema 失败返回 `schema_unsupported`，不调用 provider。

模块 02 不修改 consumer Schema，不自动删除 unsupported keyword，也不把 required 改 optional。

## 10. ProviderAdapter 与生产 adapter

### 10.1 Protocol

`ProviderAdapter` 必须同时提供 sync/async 的 JSON、首轮 tool、tool continuation，以及 sync/async 生命周期关闭。返回模块内部 normalized response/failure，不返回 SDK object：

```python
@dataclass(frozen=True, slots=True)
class ProviderJSONRequest:
    system: str
    user: str
    json_schema: dict[str, object] | None
    max_output_tokens: int
    thinking_type: str
    reasoning_effort: str
    model: str
    profile: ModelProfile

@dataclass(frozen=True, slots=True)
class ProviderToolRequest:
    system: str
    user: str
    tools: tuple[dict[str, object], ...]
    max_output_tokens: int
    thinking_type: str
    reasoning_effort: str
    model: str
    profile: ModelProfile

@dataclass(frozen=True, slots=True)
class ProviderToolContinuation:
    messages: tuple[dict[str, object], ...]
    tools: tuple[dict[str, object], ...]
    max_output_tokens: int
    thinking_type: str
    reasoning_effort: str
    model: str
    profile: ModelProfile

@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str | None
    tool_calls: tuple[tuple[str, str, dict[str, object]], ...]
    finish_reason: str
    model_name: str
    usage: dict[str, int]
    reasoning_content: str | None

@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    retryable: bool
    provider_status_code: int | None
    retry_after_seconds: float | None

class ProviderAdapter(Protocol):
    def invoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure: ...
    async def ainvoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure: ...
    def invoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure: ...
    async def ainvoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure: ...
    def invoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure: ...
    async def ainvoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure: ...
    def list_models(self) -> tuple[str, ...]: ...
    async def alist_models(self) -> tuple[str, ...]: ...
    def close(self) -> None: ...
    async def aclose(self) -> None: ...
```

Provider request/response/failure DTO 属于模块内部边界，不从 package root 导出。生产 Adapter 以完整不可变 `ModelProfile` 作为模型客户端缓存键，不能只用 `role` 或 `model_name`；同一 Profile 复用客户端，不同 Profile 不得误复用。`close()` / `aclose()` 必须幂等。`DeepSeekRuntime` 是共享 Provider 生命周期所有者，其 `close()` / `aclose()` 恰好关闭 Provider；`ModelOperation.close()` 只关闭操作私有状态，不得关闭共享 Provider。

### 10.2 ChatDeepSeek 构造

生产 `LangChainDeepSeekAdapter` 必须检查构造参数，不允许 caller kwargs：

```python
ChatDeepSeek(
    model=profile.model_name,
    api_key=settings.api_key,
    base_url=profile.base_url,
    timeout=httpx.Timeout(
        connect=10.0,
        read=float(profile.read_timeout_seconds),
        write=30.0,
        pool=10.0,
    ),
    max_retries=0,
    max_tokens=profile.max_output_tokens,
    streaming=False,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
```

不得传 temperature/top_p/presence/frequency。不得使用 `init_chat_model`、`ChatOpenAI`、OpenAI SDK 或自写 HTTP 发起 chat completion。

`list_models()` 是唯一控制面例外：只能调用当前锁定版 `ChatDeepSeek.root_client.models.list()`，复用同一个官方 Base URL 和 Secret。当前服务端在 `/models` 和响应元数据中返回 `deepseek-flash`；adapter 仅允许把这个精确输入别名归一化为公共规范名 `deepseek-v4-flash`，并在列表归一化后按首次出现去重。不得改写其他模型名，不得把归一化变成模型 fallback。禁止为 `/models` 另建 client，禁止访问其他控制面接口，禁止用 root client 绕过 LangChain 发起推理。若此映射再次变化，live probe 必须失败并触发文档复审。

### 10.3 JSON mode

生产 adapter 使用：

```python
model.with_structured_output(
    schema,
    method="json_mode",
    include_raw=True,
)
```

调用前 runtime 在 system text 末尾追加固定 `JSON_ONLY_INSTRUCTION` 和 canonical schema。`parsed` 只是 LangChain 的解析结果，不是最终可信 DTO。仅当 `parsing_error is None` 且 `parsed is not None` 时，provider 才把 parsed canonical 序列化成 normalized JSON；若 parser failure 或 parsed 为 None，则保留 metadata 完整的 raw content 交给 runtime。两条路径都必须执行 `schema.model_validate_json(response.content, strict=True)`。

禁止写成 `json.loads(response.content)` 后再调用 `schema.model_validate(parsed, strict=True)`。原因是 JSON Enum/日期只能表示为字符串，而 Python-object strict 模式会要求它们已经是 Enum/日期实例，导致合法模型 JSON 被误拒绝。必须分别测试普通字段、Enum 字段以及同步/异步入口。

若 `include_raw` 返回 parsing error 或 parsed is None，且 raw metadata 完整、content 为字符串，则交给 runtime strict validation；只有 raw metadata 缺失/非法才是 `malformed_provider_response`。`parsing_error` 不得暴露，不得返回 partial value，不得自行 strip Markdown fence、正则提取或使用宽松 JSON parser；任何 raw/reasoning 在 adapter 转换完 normalized response 后仍不得进入公共结果。

### 10.4 tool mode

- `bind_tools` 使用 consumer ToolContract 转换后的 Pydantic schema。
- 不传 `strict=True`。
- 不传 caller `tool_choice`。
- 不使用 `/beta`。
- 返回 tool args 后，runtime 使用 `canonical_json(arguments)` 恢复 JSON 文本，再执行 `args_schema.model_validate_json(..., strict=True)`。禁止对 JSON-origin 字典直接调用 `model_validate(..., strict=True)`；必须覆盖 Enum 工具参数的同步/异步测试。
- 思考模式 tool continuity 由同一 `ModelOperation` 私有 provider history 完成。
- 本节结束于 `ValidatedToolCall`。禁止调用 `load_mcp_tools`、创建 MCP client/session、连接 MCP Server、执行工具或把 MCP 原始结果塞回 provider history；这些动作必须由后续 Skill 与工具 adapter 按上位设计分工实现。

### 10.5 provider exception 规范化

生产 adapter 捕获已知 SDK/transport exception 并转换为内部 `ProviderFailure`。禁止把 `str(exc)` 放入公共对象。

| 条件 | code | retry |
|---|---|---|
| 400/422 | invalid_request | 否 |
| 401 | authentication_failed | 否 |
| 402 | insufficient_balance | 否 |
| 403 | permission_denied | 否 |
| 404 | model_not_found | 否 |
| 明确 context length 错误 | context_length_exceeded | 否 |
| 429 | rate_limited | 是 |
| 500/502/503/504 | provider_unavailable | 是 |
| connect/reset | transport_error | 是 |
| read timeout | timeout | 是 |
| SDK LengthFinishReasonError | output_truncated | 否 |
| SDK ContentFilterFinishReasonError | content_filtered | 否 |
| 其他 SDK exception | malformed_provider_response | 否 |

不得使用 `unknown_error`，不得对未知异常重试。

## 11. Runtime 与 Operation 算法

### 11.1 start_operation

```python
class DeepSeekRuntime:
    def __init__(self, provider: ProviderAdapter, sleeper: Sleeper) -> None: ...
    def start_operation(self, role: ModelRole) -> ModelOperation: ...
    def close(self) -> None: ...
    async def aclose(self) -> None: ...
```

每次 `start_operation` 通过 `get_profile(role)` 创建新 Operation。Runtime 只持有 `ProviderAdapter`、`Sleeper` 和幂等生命周期状态；`DeepSeekSettings` 只传给生产 Adapter 构造，不传给 Runtime。Runtime 不持有业务 history、thread state 或全局调用计数。`close()` 调用 Provider `close()` 至多一次；`aclose()` 优先 await Provider `aclose()` 至多一次，仅在不存在异步入口时回退同步关闭。关闭后 `start_operation` 必须失败。

### 11.2 semantic claim

Operation 内部字段：

```text
_profile
_provider
_sleeper
_semantic_invocations_used
_provider_attempts_used
_private_tool_history
_closed
_mode  # unset | sync | async
_sync_lock / _async_lock
```

claim 算法：

```text
if closed → programming error
if used >= profile limit → quota_exhausted
else used += 1 exactly once
```

preflight failure 不 claim。完成 provider attempt 后不得回退计数。

第一次 public invoke 把 `_mode` 从 unset 固定为 sync 或 async；之后调用另一模式立即抛编程错误，不返回 provider failure，也不发送请求。这样禁止两个互不协调的锁造成 quota 超卖。

### 11.3 retry algorithm

```python
for attempt_number in (1, 2):
    response = provider.invoke(...)
    if success:
        return decode(response)
    if not failure.retryable or attempt_number == 2:
        return failure_outcome
    sleeper.sleep(clamp_retry_after(failure.retry_after) or 1.0)
```

第二次 provider attempt 不增加 semantic count，但增加 provider attempts。sync/async 共享同一错误表和等待计算纯函数。

### 11.4 possible_duplicate

以下失败后发生第二次 attempt 时标记 true：read timeout、connection reset、HTTP 5xx、insufficient resource。纯 connect-before-send failure 和 429 可标 false；无法证明请求未发送时偏向 true。

## 12. structured invocation 精确流程

同步与异步必须采用同一阶段：

```text
1 validate PromptEnvelope
2 validate consumer schema
3 append fixed JSON-only instruction
4 canonicalize full request
5 byte gate
6 semantic quota claim
7 provider attempt 1
8 optional wait + provider attempt 2
9 require exact model name
10 require known finish reason
11 require top-level usage
12 reject length/content_filter/resource finish
13 require parsed value
14 schema.model_validate_json(response.content, strict=True)
15 construct narrow success and discard raw
```

任何阶段失败不得返回前面阶段的 partial data。

## 13. tool operation 精确流程

### 13.1 第一个 tool turn

```python
outcome = operation.invoke_tools(
    prompt=PromptEnvelope(...),
    tools=(ToolContract(...), ...),
)
```

固定行为：

- 至少一个 tool。
- tool name 唯一。
- preflight 包含所有 tool schema。
- provider 返回 `tool_calls` 时逐个验证；任一 call 无效则整个 turn 失败。
- call ID 非空且同一 turn 唯一。
- 保存完整 raw assistant message 到 `_private_tool_history`，公共结果只含 validated calls。
- provider 返回 `stop` 时输出 `ToolTurnCompleted(final_text=...)`；它只是文本结果，不允许模块 02 将其解释成业务状态。

### 13.2 继续 tool turn

```python
outcome = operation.continue_tools(
    results=(ToolResult(...), ...),
)
```

固定校验：

- 每个 pending call 恰好一个 result。
- 不允许缺失、重复、未知 call ID。
- results 按 provider call 原顺序拼接。
- 完整私有 assistant message（含 reasoning）只在 Operation 内回传。
- 下一请求重新执行 byte gate 和 semantic quota claim。
- loop 是否继续由 consumer 决定，但不能超过 Profile semantic limit。

### 13.3 清理

- `close()` 幂等清空 private history 并阻止继续调用。
- 成功 `ToolTurnCompleted` 后自动 close。
- failure 后自动 close。
- context manager / async context manager exit 必须 close。
- `__repr__` 只显示 role、计数和 closed，不显示 prompt/history/provider/settings。
- `__getstate__` 明确抛 `TypeError`，证明不可 pickle。

## 14. 长输入与长输出测试固定

每个 Profile 至少测试：

1. canonical request `limit-1` 允许。
2. `limit` 允许。
3. `limit+1` 返回 input_too_large。
4. 多字节中文按 UTF-8 bytes 计数，不按 Python 字符数。
5. Schema 和 tool definition 能把原本不过限的 message 推过上限。
6. tool result 导致下一轮超限时不发送且不清除 pending state，随后 Operation 关闭失败。
7. finish reason length 的 raw content 即使是可解析 JSON 也必须丢弃。
8. output truncated 不自动重试、不增大 max tokens。
9. provider actual token usage 超过项目预期但未违反 provider finish 时仍记录真实值，不伪造。

禁止测试只使用 ASCII；必须覆盖中文、emoji 和混合文本。

## 15. reasoning content 防泄漏

测试构造唯一哨兵：

```text
REASONING_SENTINEL_DO_NOT_LEAK_9f5c...
```

让 fake provider 把哨兵放入 reasoning content，随后递归扫描：

- 所有 public outcome 的 `model_dump_json()`。
- `repr(runtime)`、`repr(operation)`、`repr(outcome)`。
- error DTO。
- capability report。
- fake callback/recorded public events。

任一位置出现哨兵即失败。仅允许 fake provider 的内部原始调用记录持有哨兵，用于证明 continuation 确实回传。

## 16. Capability Probe 精确实现

### 16.1 report

```python
class CapabilityProbeReport(BaseModel):
    schema_version: Literal["1.0"]
    provider: Literal["deepseek"]
    model_name: Literal["deepseek-v4-flash"]
    models_endpoint_ok: bool
    thinking_ok: bool
    structured_output_ok: bool
    tool_call_ok: bool
    async_ok: bool
    usage_ok: bool
    passed: bool
    failures: tuple[LLMErrorCode, ...]
```

不包含响应文本、Prompt、latency 原始 trace、API Key、reasoning 或 SDK metadata。

`passed` 必须等于全部六个能力字段为 true 且 failures 为空，不能由 caller 赋值制造矛盾。

### 16.2 fake probe

使用 ScriptedProviderAdapter 分别证明每个布尔字段只由实际步骤产生；不能直接构造全 true report 作为测试。

### 16.3 live probe

固定最小行为：

- `/models` 经唯一允许的 `deepseek-flash` → `deepseek-v4-flash` 输入归一化后查精确规范 ID。
- structured schema 只含 `status: Literal["ok"]`。
- tool 只含一个无副作用 `echo_probe(value: Literal["probe"])`，测试代码返回固定结果。
- 同步/异步各一次；不得调用旅游业务。
- `max_tokens` 仍取 extraction Profile，不创建 probe 专用隐藏 Profile。
- 输出只打印 report JSON。

live test 缺 `DEEPSEEK_API_KEY` 时必须 fail fast，不得 skip。只有显式运行 live command 时才收集该目录。

## 17. Fakes 精确合同

### 17.1 ScriptedProviderAdapter

- 构造接收 response/failure 序列。
- 每个调用弹出一个；序列耗尽抛 AssertionError。
- 记录 normalized request，不记录 Secret。
- sync/async 使用各自记录，但相同脚本语义。
- 提供 `call_count`、`requests` 只读视图。
- 不 import `unittest.mock` 模拟整个 runtime。

### 17.2 RecordingSleeper

- `sleep(seconds)` 只记录，不真实等待。
- `async_sleep(seconds)` 只记录并 yield control，不真实等待。
- 负数直接 AssertionError。

## 18. 固定任务顺序

### LLM-T01：依赖与公共模型

只做：

- 精确 pin 三个直接依赖。
- 创建目录和 `models.py`、`__init__.py` 初始导出。
- 实现 ModelRole、PromptEnvelope、tool/usage/error/outcome DTO。
- 写 `test_models_and_profiles.py` 中的模型边界部分。

完成命令：

```powershell
python -m pytest -q tests/unit/llm_runtime/test_models_and_profiles.py
```

### LLM-T02：Profile 与 Settings

只做：

- 四个 frozen Profile 和只读 registry。
- load_settings 与 Secret 防泄漏。
- Profile 精确字段 snapshot。

禁止构造真实 client。

### LLM-T03：Schema 与请求 sizing

只做：

- consumer schema validator。
- JSON-only instruction。
- canonical request bytes。
- 四 Profile byte 边界测试。

禁止网络、provider、retry。

### LLM-T04：Provider 与错误分类

只做：

- Protocol、internal request/response/failure。
- ScriptedProviderAdapter、RecordingSleeper。
- LangChainDeepSeekAdapter 精确构造。
- provider exception/finish/usage 规范化。
- retry wait 纯函数。

禁止 Operation 和业务 Prompt。

### LLM-T05：Structured Runtime

只做：

- DeepSeekRuntime/start_operation。
- quota claim。
- sync/async structured invocation。
- 一次 retry。
- strict local validation 和 narrow outcome。

### LLM-T06：Tool Operation 与 reasoning 隔离

只做：

- tool contract 转换与 args validation。
- invoke_tools/continue_tools。
- private reasoning replay、close、不可序列化。
- sentinel 防泄漏测试。

### LLM-T07：Capability Probe

只做：

- deterministic probe service/report。
- fake contract tests。
- live test 与单独脚本。
- live 输出脱敏。

禁止把 live test 放入默认 unit/contract 收集目录。

### LLM-T08：锁文件与最终证明

只做：

- 从干净 venv 生成 requirements.lock。
- 第二个干净 venv 重建。
- 完整测试、覆盖率、forbidden import、skip/xfail、secret/reasoning 扫描。
- 真实 capability probe。
- 最终交付报告。

不得在 T08 才补大量生产功能；发现缺失应返回对应任务修复。

### 18.9 任务到规范 ID 的固定映射

| Task | 必须覆盖的规范 ID |
|---|---|
| LLM-T01 | REQ-LLM-004、REQ-LLM-021、REQ-LLM-032..034 |
| LLM-T02 | REQ-LLM-001..010 |
| LLM-T03 | REQ-LLM-017..019、REQ-LLM-022..024 |
| LLM-T04 | REQ-LLM-002..007、REQ-LLM-013..016、REQ-LLM-035 |
| LLM-T05 | REQ-LLM-011..024、REQ-LLM-031..035 |
| LLM-T06 | REQ-LLM-025..030、REQ-LLM-033 |
| LLM-T07 | REQ-LLM-036..038 |
| LLM-T08 | REQ-LLM-039、REQ-LLM-040，并复验 REQ-LLM-001..038 |

每个规范 ID 至少由一个明确行为测试覆盖；只在测试名或注释中出现 ID 不算覆盖。

## 19. 每任务停止条件

编码 Agent 在任一情况必须停止当前实现并报告，不得猜测：

- 官方 API 不接受 `deepseek-v4-flash`。
- `langchain-deepseek==1.1.0` 无法显式发送 thinking 参数。
- thinking + JSON mode 或 thinking + tool call live probe 不兼容。
- LangChain 丢失工具连续性所需 reasoning content。
- 需要 `/beta` 才能满足本文合同。
- 需要持久化 reasoning content 才能恢复。
- 需要修改 01 或提前定义 03/08/09/16 业务 Schema。
- 需要放宽 Profile、增加模型或发送 unsupported sampling 参数。
- requirements.lock 无法在第二个干净环境复现。

这些均是合同/依赖阻断，不得用 fallback 掩盖。

## 20. 固定测试矩阵

| Case | 输入/故障 | 断言 |
|---|---|---|
| LLM-CASE-001 | 四个 role | 精确 Profile、同一模型、全部 thinking |
| LLM-CASE-002 | unknown role string | strict reject |
| LLM-CASE-003 | key missing/blank/placeholder | configuration_error，零 client |
| LLM-CASE-004 | settings/runtime repr | 无 Secret |
| LLM-CASE-005 | request=limit | provider 一次 |
| LLM-CASE-006 | request=limit+1 | input_too_large，零 provider/零 quota |
| LLM-CASE-007 | schema extra not forbid | schema_unsupported |
| LLM-CASE-008 | valid JSON + strict model | structured_success typed value |
| LLM-CASE-009 | invalid JSON/extra field | structured_output_invalid |
| LLM-CASE-010 | finish=length 且 JSON 可解析 | output_truncated，无 value |
| LLM-CASE-011 | 429→success | 两 attempts、一次 sleep |
| LLM-CASE-012 | 503→503 | 两 attempts、provider_unavailable |
| LLM-CASE-013 | 401 | 一 attempt、无 sleep |
| LLM-CASE-014 | read timeout→success | possible_duplicate=true |
| LLM-CASE-015 | quota boundary | 第 N+1 次零 provider |
| LLM-CASE-016 | valid tool call | typed arguments |
| LLM-CASE-017 | unknown tool | unknown_tool，未执行 |
| LLM-CASE-018 | one bad among multiple calls | entire turn failure |
| LLM-CASE-019 | missing/duplicate tool result | local failure，零 provider |
| LLM-CASE-020 | reasoning sentinel | continuation 内存在；公共输出不存在 |
| LLM-CASE-021 | pickle Operation | TypeError |
| LLM-CASE-022 | sync vs async | 同结果、同计数、同 retry |
| LLM-CASE-023 | wrong returned model | unexpected_model |
| LLM-CASE-024 | missing usage/finish | malformed_provider_response |
| LLM-CASE-025 | fake capability sequence | report derived，不可手填 passed |
| LLM-CASE-026 | live capability | 六项全 true、无敏感输出 |
| LLM-CASE-027 | structured JSON 含 Enum 字符串，sync/async | `model_validate_json` 后得到精确 Enum；不得 structured_output_invalid |
| LLM-CASE-028 | tool arguments 含 Enum 字符串，sync/async | canonical JSON 后严格得到精确 Enum；不得 tool_arguments_invalid |

## 21. 固定验证命令

以下以仓库根目录 PowerShell 执行。不得只运行单个绿灯文件后宣称完成。

```powershell
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'

$forbidden = 'app\.(requirements|graph|persistence|agent|tools)|langgraph|fastapi|asyncpg|psycopg|baidu|^\s*(from|import)\s+(mcp|langchain_mcp_adapters)(\.|\s|$)'
$hits = rg -n --glob '*.py' $forbidden app/llm_runtime tests/unit/llm_runtime tests/contract/llm_runtime 2>$null
if ($LASTEXITCODE -eq 0) { $hits; throw 'module 02 contains forbidden imports' }
if ($LASTEXITCODE -ne 1) { throw 'forbidden-import scan failed' }

$smells = 'sys\.path\.(insert|append)|@pytest\.mark\.(skip|xfail)|pytest\.(skip|xfail)\('
$hits = rg -n --glob '*.py' $smells tests/unit/llm_runtime tests/contract/llm_runtime tests/live/llm_runtime 2>$null
if ($LASTEXITCODE -eq 0) { $hits; throw 'module 02 tests contain skip/xfail or path bypass' }
if ($LASTEXITCODE -ne 1) { throw 'test-smell scan failed' }

$leaks = 'reasoning_content|DEEPSEEK_API_KEY\s*=\s*["''][^"'']+["'']'
$hits = rg -n --glob '*.py' $leaks app/llm_runtime tests/unit/llm_runtime tests/contract/llm_runtime 2>$null
if ($LASTEXITCODE -eq 0) {
    # reasoning_content 只允许出现在 provider 私有适配、私有 Operation 与 sentinel 测试中；逐项人工核对。
    $hits
}

$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'china-travel-agent-module02-pycache'
python -m compileall -q app/llm_runtime tests/unit/llm_runtime tests/contract/llm_runtime tests/live/llm_runtime
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m pytest --collect-only -q tests/unit/llm_runtime tests/contract/llm_runtime
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m pytest -q tests/unit/llm_runtime tests/contract/llm_runtime
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:COVERAGE_FILE = Join-Path $env:TEMP 'china_travel_agent_module02.coverage'
python -m pytest -q tests/unit/llm_runtime tests/contract/llm_runtime `
    --cov=app.llm_runtime --cov-branch --cov-report=term-missing --cov-fail-under=95
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

真实 probe 单独执行且是 `live_qualified` 门槛；离线门禁证明 `contract_complete`。即使在 `full_rebuild`，没有用户对本次真实 DeepSeek 调用的明确授权也不得执行 live 命令，必须停止并报告资格未完成：

```powershell
python -c "from app.llm_runtime import load_settings; load_settings(); print('DeepSeek configuration available')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pytest -q tests/live/llm_runtime
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python scripts/probe_deepseek_runtime.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

live 输出必须人工/自动确认不含 Prompt、正文、reasoning、API Key。

## 22. requirements.lock 固定生成与复现

不得从长期使用的 `.venv` 直接 freeze。固定流程：

```powershell
$ErrorActionPreference = 'Stop'
$lockVenv = Join-Path $env:TEMP 'china-travel-agent-lock-build'
$verifyVenv = Join-Path $env:TEMP 'china-travel-agent-lock-verify'

python -m venv $lockVenv
& "$lockVenv\Scripts\python.exe" -m pip install --upgrade pip
& "$lockVenv\Scripts\python.exe" -m pip install -e '.[dev]'
& "$lockVenv\Scripts\python.exe" -m pip freeze --all --exclude-editable | Set-Content -Encoding utf8 requirements.lock

$badLock = rg -n '(@ file:|^-e |DEEPSEEK|api[_-]?key|token=)' requirements.lock 2>$null
if ($LASTEXITCODE -eq 0) { $badLock; throw 'requirements.lock contains local path or secret' }
if ($LASTEXITCODE -ne 1) { throw 'lock scan failed' }

python -m venv $verifyVenv
& "$verifyVenv\Scripts\python.exe" -m pip install --upgrade pip
& "$verifyVenv\Scripts\python.exe" -m pip install -r requirements.lock
& "$verifyVenv\Scripts\python.exe" -m pip install --no-deps -e .
& "$verifyVenv\Scripts\python.exe" -m pytest -q tests/unit/requirements tests/contract/requirements tests/unit/llm_runtime tests/contract/llm_runtime
```

若 lock 在第二环境无法安装或测试失败，不得手工编辑几行掩盖；回到 pyproject pins 和解析环境修复后重新完整生成。

## 23. 禁止行为总表

- 不允许 `os.getenv` 出现在 import 顶层。
- 不允许全局 `ChatDeepSeek(...)`。
- 不允许 caller 传 model/base_url/model_kwargs/extra_body。
- 不允许 `deepseek-chat`、`deepseek-reasoner`、`deepseek-v4-pro`。
- 不允许自动 fallback 到其他供应商或模型。
- 不允许思考模式关闭或 reasoning effort 自适应改成 max。
- 不允许发送 temperature/top_p/presence/frequency。
- 不允许 LangChain 内建 retry 与自有 retry 同时开启。
- 不允许 `except Exception` 后 retry。
- 不允许 input 自动 truncate/summarize。
- 不允许 output length 后解析 partial JSON。
- 不允许自由文本替代 structured failure。
- 不允许 `json.loads(ai_message.content)` 散落在 consumer；decode 只在 runtime。
- 不允许 reasoning content 进入公共 DTO、日志、状态或持久化。
- 不允许 Operation 序列化或跨进程恢复。
- 不允许 02 执行业务工具。
- 不允许 02 导入 MCP 包、发现 MCP 工具、创建 MCP client/session 或执行 MCP 调用。
- 不允许依赖 `/beta` strict tool endpoint。
- 不允许 live test 缺 key 时 skip。
- 不允许 fake probe 直接返回预制全 true report。
- 不允许 coverage 失败、零测试或部分测试冒充完成。

## 24. 最终交付报告模板

编码 Agent 最终只按以下结构报告：

```markdown
# 02 LLM Runtime 实施结果

## 执行上下文与结论
- mode: maintenance | full_rebuild | requalification_only
- qualification_scope: offline | live | not_applicable
- task_result: PASS | FAIL
- contract_complete: retained | passed | failed | not_evaluated
- live_qualified: passed | failed | not_run
- branch:
- start HEAD:
- end HEAD:
- dirty worktree preserved: PASS/FAIL + details
- task allowlist:
- files changed:
- out-of-allowlist anomalies: none | exact paths and action

## 规范对照
- REQ-LLM-001..040：逐项 PASS/FAIL + test ID

## Profile 实值
- 四个 Profile 的 model/thinking/timeouts/quota/bytes/max_tokens

## 约束解码
- JSON mode 参数
- local strict Pydantic 验证证明
- tool args 验证证明

## 调用与重试
- 每类错误的 attempts/sleep/possible_duplicate

## 长度边界
- 四 Profile limit-1/limit/limit+1
- output_truncated 无 partial result

## 隐私
- Secret sentinel 扫描
- reasoning sentinel 扫描
- Operation 不可序列化证明

## 离线验证
- collect 数量
- unit/contract 通过数量
- line/branch coverage
- forbidden import / skip / xfail 扫描

## Live capability probe
- model listing / thinking / structured / tool / async / usage
- report path 或脱敏摘要

## Maintenance live 触发检查
- changed trigger paths/contracts:
- live required: yes/no + rule row
- live authorization present: yes/no/not_required

## Docker 与外部副作用
- Docker command/result: not_run | exact command and result
- other Docker projects touched: none (required)
- Secret/raw-content leak check: PASS/FAIL
- commit: none | hash
- push: none | target
- PR: none | URL
- deploy: none | target

## 锁文件复现
- Python 版本
- requirements.lock 生成环境
- 第二环境安装与 01+02 测试结果

## L2 偏离
- 无；或逐项理由与等价测试

## 明确失败项
- 无；或阻断原因
```

结论按模式判定：

- `full_rebuild`：任一 L0、离线合同、95% coverage、lock 重建、安全检查或正式 live probe 失败，写 `task_result=FAIL`；离线项失败时 `contract_complete=failed`，live 失败时 `live_qualified=failed`。
- `maintenance`：只对用户白名单、受影响 Gate 和用户要求的回归判定 `task_result`；离线合同仍通过时 `contract_complete=retained`。按下表未触发 live 时写 `live_qualified=not_run`，不得伪造通过；已触发但没有本次真实 API 授权时不得调用 API，写 `task_result=FAIL, live_qualified=not_run` 并报告授权阻塞。
- `requalification_only, qualification_scope=offline`：不改代码、不调用真实 API；指定离线门禁通过写 `task_result=PASS`，失败写 `FAIL`，固定写 `live_qualified=not_run`。
- `requalification_only, qualification_scope=live`：不改代码；只有本次真实 API 授权存在时才执行一次正式 probe。通过写 `task_result=PASS, live_qualified=passed`，失败写 `task_result=FAIL, live_qualified=failed`。供应商波动不得把既有 `contract_complete` 改成 failed；未重跑离线门禁时写 `retained` 并引用最近状态证据。

maintenance 的正式 live 触发规则固定为：

| 变更 | 是否必须重新 live |
|---|---|
| `app/llm_runtime/` 任一生产文件 | 是 |
| `scripts/probe_deepseek_runtime.py` 或 `tests/live/llm_runtime/` | 是 |
| `pyproject.toml` / `requirements.lock` 中 DeepSeek、LangChain、Pydantic、OpenAI SDK、HTTP client 的版本或解析结果 | 是 |
| 只改 02 文档、fake、unit/contract 测试，且生产合同与依赖未变 | 否；运行受影响离线门禁，`live_qualified=not_run` |

### 本次修复的回归门槛

- include_raw 的 raw metadata 完整且 parsed 成功时，Provider 必须 canonical 序列化 parsed；parsed 失败时必须把 raw content 交给 Runtime，最终由 strict validation 决定成功或 `structured_output_invalid`。
- raw metadata 缺失时仍必须得到 `malformed_provider_response`；不得暴露 parser exception 或 raw content。
- 同一 Provider 对同一 Profile 复用模型客户端，关闭通过幂等 `close` / `aclose` 完成；异步关闭必须在有效 event loop 内 await。

## 2026-09-29 四项改进增量修订（用户已批准）

以 [M09四项改进设计及验收](VALIDATION.md) 为本次增量依据：M09供应商工具使用beta严格schema，原本地完整校验保留；非法工具调用整批拒绝；Directions给出代码推导时长，路线end_at可空并由代码计算（显式旧值须一致）；Prompt减少格式/计算重复；提交反馈包含稳定reason/field。仅修改模型输入端，不改变历史持久行程schema、预算、硬门槛或数据库。本文此前涉及统一非严格工具输出、路线end_at必填和泛化反馈的条款由本节限定覆盖，其余条款继续有效。实施范围、前置条件、测试矩阵及Git限制见上述文档。

2026-09-29修复重跑补充：依据用户“修复问题然后重跑，有三次重跑机会”授权，strict wire完全展开无递归本地引用；被供应商限制移除的数量/长度及日期格式保留为说明，本地验证不变。M09终结提交拒绝在返回通用状态前记录代码内reason/field与terminal_only，不输出参数或异常正文，不改公开错误/持久化合同。三次真实结果、白名单及验收详见[m09_four_improvements.md](VALIDATION.md)；最后业务提交仍未通过，不据此扩大重跑或预算。

2026-09-29轻量错误定位增量：按用户授权实现[错误定位日志](error_location_logging.md)。在JSON解析、本地工具参数校验和业务拒绝转换为通用结果之前记录脱敏字段/规则/源码位置；最后一次提交同样记录，真实测试单独保存error-locations.jsonl。仅复用日志与run_id，不增加数据库表、迁移、错误结果字段、回放系统或调用额度。详见该文档的范围、测试矩阵及限制。


## 本次完整记录执行入口（2026-09-30）

模型职责按原设计§19，当前规划角色17。本次只执行原M09执行规范§29/W01（DTO）及W02+W03（有效tools/准入/capture）；不重建M02。其他角色和物理重试额度不变，未来提示词须引用该设计和§29白名单/矩阵。


## 通用参数修正实施增量（2026-10-06，用户已批准）

设计真源为M02 §20，完整当前阶段提示词见[E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)。按R01+R02统一测试及两个独立核查；M02既有95% line/branch联合Gate必须单独通过，不能以M09门槛替代。旧默认参数失败关闭规则仅在显式opt-in时使用新增拒绝/repair能力，禁止内部自动循环。

## M08 生成额度维护入口（2026-10-07，用户已批准）

本次使用 `maintenance`，只实施相邻 L01+L02，统一验证后由两个子agent逐条独立审查。设计真源为 [M02 §6/7/9/10/15/20](02_llm_runtime_and_model_profiles.md)，执行合同为本文 §7/14/21/维护 live 触发规则及 [共享规则 §1/3～7/9](SHARED_IMPLEMENTATION_RULES.md)。消费方只读核验 [M08 §9/13](08_candidate_research_loop.md) 和 [M08执行 §4.2](08_candidate_research_loop_vibe_coding.md)；不得重建 M08 或实施讨论中的两次输出恢复。

前置条件：用户已明确批准候选研究输出额度 32,768→131,072；依照 [实施路线图](IMPLEMENTATION_ROADMAP.md) 的 `depends_on`，M02 无直接依赖。沿用 `feature/module-09-structured-output-fix`，开始 HEAD 为 `b3438ca13e3c88e4669c942d18018e6b133b7857`；保留既有 dirty 文件和索引，核验当前符号、测试入口与基线，不借用旧测试结果授予本次资格。

文件白名单仅限以下八份：

- `app/llm_runtime/profiles.py`
- `tests/unit/llm_runtime/test_models_and_profiles.py`
- `tests/unit/llm_runtime/test_provider_adapter.py`
- `docs/02_llm_runtime_and_model_profiles.md`
- `docs/02_llm_runtime_and_model_profiles_vibe_coding.md`
- `docs/08_candidate_research_loop_vibe_coding.md`
- `README.md`
- `docs/IMPLEMENTATION_STATUS.md`

`Dockerfile`、`.dockerignore`、锁文件、M08 业务代码、Prompt 和生产装配只读。允许修改上述静态值与相应断言、同步现有权威/消费方规范、在本项目命名空间构建隔离 Docker 镜像并执行离线测试；全量持久化合同使用临时 PG，按原合同初始化既有 M10/M11 结构，不新增报告文档。禁止真实 API、追加模型调用/恢复额度、改超时或 schema、依赖升级、新增 SQL 迁移或修改现有运行库、stage/commit/merge/push/PR 和部署，不重启现有网页或数据库。

| 阶段 | 当前目标与操作 | 验收 |
|---|---|---|
| L01 | 先同步获批设计与消费方执行数值，再调整候选研究 Profile 及现有 Profile/SDK 构造断言 | 只候选研究 cap=131,072；其他角色、thinking/high、输入、超时和调用预算不变 |
| L02 | 执行定向、模块、全仓离线 Gate，同步 README/状态，按共享规则从同步后源码重建 runtime/test，再执行 Gate；双agent逐条审查 | 离线资格与待正式 live 资格分别记录；保持运行中服务及 Git 索引 |

统一测试矩阵：

| 检查 | 命令或精确断言 |
|---|---|
| 定向 | Docker 内 `python -m pytest -q -p no:cacheprovider tests/unit/llm_runtime/test_models_and_profiles.py tests/unit/llm_runtime/test_provider_adapter.py` |
| Profile/SDK | 四角色输出 32,768/131,072/131,072/32,768，semantic 2/12/19/8；SDK `max_tokens` 与角色一致，显式 enabled/high，read=120、技术尝试=2、SDK retries=0；M09 输出更新按下文 2026-10-08 维护入口，M08 历史验证不追溯改写 |
| M02 owner Gate | Docker 内 `python -m pytest -q -p no:cacheprovider tests/unit/llm_runtime tests/contract/llm_runtime --cov=app.llm_runtime --cov-branch --cov-report=term-missing --cov-fail-under=95`；coverage 写容器 `/tmp`，保留原综合 95% 门槛 |
| M08消费回归 | Docker 内 `python -m pytest -q -p no:cacheprovider tests/unit/candidate_research tests/contract/candidate_research`；保持研究6轮、地图48次、semantic12和严格输出合同 |
| 全项目离线 | 新 test 镜像默认 CMD，覆盖当前 unit/contract；原持久化合同接入仅限本次内部网络的临时 PG，既有 M10/M11 初始化成功后运行；无 skip/xfail/自动 rerun |
| runtime | 新 runtime 镜像 import/Profile 自检，COPY app/scripts、test COPY tests；无网络、无凭据、无端口执行默认 `python -m app.web_application`，缺配置须安全 exit=2，不将其当作可用网页启动资格 |
| 独立审查 | 两个子agent只读，对照上述设计、白名单、预算、失败规则、Docker/closeout与实际测试证据；不得仅因测试通过判定完成 |
| 正式 live/部署 | 本次均 `NOT_RUN`；生产 Profile 变更触发本文维护 live 规则，未取得新的真实 API 授权不得调用或宣称供应商资格；现有18080服务的额度不因源码修改自动生效 |

停止条件：白名单不足、需改冻结语义/依赖/预算、原始内容泄漏、离线零收集或失败、覆盖率未达门槛、索引/运行服务被意外改变时停止对应后续验收并报告最小原因。测试失败不得放宽校验；没有本次 live 授权时只完成已授权代码与离线核查，按原维护规则报告 `task_result=FAIL, live_qualified=not_run`（资格待授权），不得冒称完整 COMPLETE。

最终直接向用户报告：分支、前后 HEAD、八份增量文件与白名单外异常、设计逐条对照、迁移、定向/M02覆盖率/M08/全量/Docker实收、双审结果、索引及已有 dirty 保留、commit/push/PR/部署、真实调用0和未完成资格；不另生成诊断或审查报告文件。

## M09 生成额度维护入口（2026-10-08，用户已批准）

本次为 `maintenance`：依据 [D02 §6](02_llm_runtime_and_model_profiles.md#6-固定-profile-矩阵)、[D09 §27](09_itinerary_planning_loop.md#27-m09-单次生成额度128k2026-10-08用户已批准)，执行 [E09 §31](09_itinerary_planning_loop_vibe_coding.md#31-m09-单次生成额度维护2026-10-08用户已批准) 的 C01+C02。唯一生产数值变化是 M09 `max_output_tokens=131_072`；SDK 首调/续调/显式修正均使用该 Profile。既有 semantic=19、enabled/high、2 MiB 输入、读取 120 秒、网络两尝试、严格校验和输出截断失败语义逐项保持。详细白名单、前置、测试矩阵、停止条件和报告字段集中在 E09 §31，不重复旧阶段内容。正式供应商资格未分配本次真实调用，按维护规则记录 `live_qualified=not_run`；源码与离线验证完成不能替代运行中网页更新。

## 2026-10-11 不确定标志维护执行入口（用户批准）

目标/引用：修复 D02 §11 及末尾“结构化失败的不确定标志保真”，消费方依 D08 R08-11～15 与 E08 R7/R8。前置是双审已复现 timeout→非法JSON/length 丢标志；用户明确批准最小 M02 扩展。分支 feature/module-09-structured-output-fix，开始HEAD 237b243f44555a5a941b07148a5d6778d60c5bd6。

精确生产白名单 app/llm_runtime/runtime.py，仅 _decode_structured 返回错误时传递既有 possible_duplicate 参数；测试白名单 tests/unit/llm_runtime/test_structured_runtime.py；文档 D/E02、本E08授权补录、README/IMPLEMENTATION_STATUS。M08白名单及共享恢复接缝沿E08，不修改SDK/依赖/Profile/DTO/额度或技术重试算法。

允许断网Docker修改/测试；先补同步和异步、timeout与transport_error技术重试→非法JSON/schema/length/metadata等分支，以及无不确定尝试 false；再M02完整owner综合coverage≥95%、M08完整owner、最终Closeout重建全量；两位子agent逐文档复审，不能只看绿灯。M08还验证不确定的合法结构判断不得进入归属纠错；真实复验仅使用用户已批准的三城各一次，不新增单独供应商诊断调用。

停止条件：白名单外实质变更、未批准合同/预算变化、未知外部结果、泄漏、零收集/skip/xfail/rerun或门禁失败。无迁移，不更新18080/8080，禁止stage/commit/merge/push/PR/部署。最终报告按E08包含分支前后HEAD、修改/异常文件、迁移、定向/coverage/全量/双审/真实实收、commit/push/PR与最终状态，等待提交。

用户已追加批准同一runtime.py内只读semantic_invocations_consumed属性及M08取消消费核对；测试在同一M02测试文件、既有M08恢复测试和tests/support/candidate_research_fakes.py，包含准入前/后取消、恢复期间取消、0..上限、只读、技术重试不双计。其余上述分支/操作/门禁/停止与报告约束保持。
