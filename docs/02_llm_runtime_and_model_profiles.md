# 02 LLM Runtime 与 Model Profiles 规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次规划角色上限17、有效工具/准入回调和完整交互采集见 [M02设计§6/19](02_llm_runtime_and_model_profiles.md)；阶段规则归 [M09§12～15](09_itinerary_planning_loop.md)，专用存储归 [M10§23](10_artifact_persistence.md)。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档状态：v1.3（DeepSeek Flash 服务端别名兼容版）
> 实现状态：模块 02 已完成；公共合同按本版本冻结，验证快照见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 上位规范：[`00_agent_master_design.md`](00_agent_master_design.md)
> 依赖与工作包：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 配套编码契约：[`02_llm_runtime_and_model_profiles_vibe_coding.md`](02_llm_runtime_and_model_profiles_vibe_coding.md)
> 规范属性：本文件只定义共享 LLM 运行时、角色 Profile、通用约束解码、调用边界与供应商适配合同

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
  - 需求提取 Prompt、RequirementExtractor 实现和 01 的 DTO
  - 旅游业务 Schema、字段语义、规划算法、ReAct 停止条件和工具执行
  - LangGraph、interrupt、checkpoint、PostgreSQL、artifact、审计和 CLI
  - Agent Skill 注册/调度、MCP 工具发现、MCP client/session、MCP Gateway 和供应商工具执行
  - 百度地图、酒店、景点、餐馆、路线、预算、攻略和局部重排业务
acceptance_gate:
  - 单元测试与 fake provider 合同测试全部通过
  - app/llm_runtime line/branch 综合覆盖率不低于 95%
  - 真实 deepseek-v4-flash 能力探针全部通过且输出已脱敏
  - requirements.lock 由干净环境生成并记录传递依赖精确版本
  - app/llm_runtime 不导入 01 或 03-23 的实现
test_class: adapter-contract
unit_test_command: python -m pytest -q tests/unit/llm_runtime
contract_test_command: python -m pytest -q tests/contract/llm_runtime
allowed_test_dependencies:
  - Python 标准库 / Pydantic / pytest / pytest-cov / Hypothesis
  - langchain-core==1.4.9
  - langchain-deepseek==1.1.0
  - fake provider；live contract 中允许官方 DeepSeek API
forbidden_test_dependencies:
  - app.requirements / app.graph / app.persistence / app.agent / app.tools
  - LangGraph / FastAPI / asyncpg / psycopg / 百度地图 SDK
  - mcp / langchain-mcp-adapters
  - 03-23 的任何业务实现
required_fakes:
  - ScriptedProviderAdapter
  - RecordingSleeper
stable_contracts:
  - DeepSeekRuntime / ModelOperation / ModelProfile
  - typed invocation outcomes / LLMRuntimeError
production_replacement:
  - Production v1 可在保持公共合同前提下增加限流、降级或多模型 adapter
```

## 1. 目标

模块 02 为所有需要大模型的后续模块提供一个共享、受约束、可测试的 DeepSeek 运行时。它解决的是“如何安全且一致地调用模型”，不是“让模型完成什么旅游业务”。

固定处理链路：

```text
consumer messages + consumer-owned schema/tools
→ ModelRole 选择固定 Profile
→ 配置、配额和完整请求 byte 预检
→ DeepSeek provider 调用（思考模式开启）
→ 统一响应完整性检查
→ 通用 JSON / tool arguments 本地严格解码
→ usage 与 finish reason 规范化
→ typed success | typed failure
```

模块必须保证：

1. 生产模型只能是 `deepseek-v4-flash`，生产端点只能是 `https://api.deepseek.com`。
2. 四个角色全部开启思考模式并固定 `reasoning_effort="high"`。
3. 任何会被代码消费的输出都必须经过结构化模式和本地 Pydantic 严格校验。
4. 不支持的能力、超长输入、配额耗尽和截断输出必须显式失败，禁止退回自由文本。
5. 网络级重试最多一次，不得与 LangChain 内建重试叠加。
6. 模块不泄露 API Key、原始 provider error body、输入原文或 `reasoning_content`。
7. fake 测试证明确定性行为；真实能力探针证明锁定版本与官方服务仍兼容。

## 2. 已锁定的供应商事实与兼容基线

本文以 2026-09-11 官方资料和真实 API 探针为基线：

- 本项目生产 Profile、出站请求和公共元数据继续使用规范名 `deepseek-v4-flash`。
- DeepSeek 当前 `/models` 和响应元数据返回服务端名 `deepseek-flash`；Provider 适配器必须仅在输入边界把这个精确别名归一化为 `deepseek-v4-flash`。未知模型名保持原值并由既有严格合同拒绝，禁止借此增加 fallback。
- `deepseek-v4-flash` 支持 1M context、JSON Output、tool calls、思考/非思考双模式；本项目只用思考模式。
- 思考模式支持 `high|max`；本项目固定 `high`。
- 思考模式不支持 `temperature`、`top_p`、`presence_penalty`、`frequency_penalty`，因此运行时禁止发送这些无效参数。
- JSON Output 只保证合法 JSON；业务 Schema 合规仍须本地 Pydantic 校验。
- 思考模式的多轮工具调用必须把当前逻辑操作内的 `reasoning_content` 回传供应商。
- DeepSeek 服务端 strict tool mode 仍使用 Beta 合同；Demo 不依赖 `/beta`。

固定参考来源：

- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)
- [DeepSeek Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)
- [DeepSeek Error Codes](https://api-docs.deepseek.com/quick_start/error_codes)
- [DeepSeek Token Usage](https://api-docs.deepseek.com/quick_start/token_usage)
- [LangChain ChatDeepSeek](https://docs.langchain.com/oss/python/integrations/chat/deepseek)
- [`langchain-deepseek==1.1.0`](https://pypi.org/project/langchain-deepseek/1.1.0/)

官方能力或已锁版本改变时，不允许编码 Agent静默适配。必须先让能力探针失败，再由用户批准修改本文、配套编码契约和锁文件。

## 3. 规范优先级

优先级固定为：

1. 用户明确确认的最新决策。
2. `00_agent_master_design.md`。
3. 本规范。
4. 可执行合同测试；只证明实现，不反向定义语义。
5. 配套 vibe coding 精确执行契约。
6. `IMPLEMENTATION_STATUS.md` 验证快照。
7. 锁文件与当前代码。

若代码、测试、供应商示例或 LangChain 默认行为与本文冲突，必须停止并按上述层级核验。不得通过修改测试、扩大环境覆盖项或切换模型来迁就实现。

## 4. 职责边界

### 4.1 本模块负责

- 显式加载 `DEEPSEEK_API_KEY` 并创建 `ChatDeepSeek`。
- 定义四个固定 `ModelRole` 与不可变 `ModelProfile`。
- 创建一次性、不可序列化的 `ModelOperation` 并执行本地配额计数。
- 统一同步和异步调用入口。
- 规范化完整请求并执行 UTF-8 byte 上限预检。
- 为结构化输出启用稳定 JSON Output，并执行本地严格 Pydantic 解码。
- 把 consumer 提供的 Pydantic tool args schema 转换为工具合同并验证返回参数。
- 在当前内存 Operation 内维护思考模式工具调用所需的供应商连续性。
- 分类 finish reason、provider/transport error、重试属性和重复风险。
- 规范化 token usage、缓存命中和 reasoning token 计数。
- 提供 fake provider 端口与真实 DeepSeek 能力探针。

### 4.2 本模块不负责

- 不定义需求、酒店、景点、餐馆、路线、行程、预算或重排 Schema。
- 不拥有任何业务 Prompt；只允许追加固定的非业务 JSON 输出约束片段。
- 不决定哪些历史、候选或工具结果可以压缩、删除或摘要。
- 不执行地图工具、数据库查询或其他业务工具。
- 不导入 `mcp` 或 `langchain-mcp-adapters`，不发现 MCP 工具，不创建 MCP client/session，不管理 MCP 重连或执行 MCP 调用。
- 不实现 ReAct 循环的业务停止条件、替换策略或质量判断。
- 不持有 thread ID、checkpoint ID、interrupt ID、plan version 或 artifact ID。
- 不写 Trace、Metrics、审计表或 checkpoint；模块 20 可以消费本模块的无原文结果。
- 不在 import 时读取 `.env`、创建全局模型 singleton、访问环境或网络。只有 composition root 或显式调用无参数 `load_settings()` 时，才允许加载项目根目录固定 `.env`。
- 不提供 OpenAI、Anthropic、Ollama、OpenRouter 或本地模型的生产 fallback。
- 不使用 `deepseek-chat`、`deepseek-reasoner`、`deepseek-v4-pro` 或未列出的模型别名。

### 4.3 相邻模块归属

| 需求 | 唯一所有者 | 模块 02 的边界 |
|---|---:|---|
| 需求提取 Schema、Prompt、evidence 和一次语义 repair | 03 | 03 为每轮创建一个异步 Session 和一个 `ModelOperation`；01 的 typed validation 决定是否由同一 Session 发起第二次调用，02 只执行被请求的单次通用结构化调用 |
| 工作流状态、phase、命令信封 | 04 | 02 的 Operation 不可序列化且不得进入 durable state |
| 模型事件、成本、redaction | 20 | 02 只返回无原文 usage/error DTO；20 负责记录策略 |
| Graph 节点与暂停恢复 | 05、13、21 | 02 不导入 LangGraph，不决定路由 |
| Agent Skill 调度、MCP 会话和工具执行 | 06、08、09、13、16 | 02 只绑定 tool schema、严格验证模型返回参数并返回调用意图 |
| 候选研究工具和停止条件 | 08 | 02 提供 candidate_research Profile 和通用 tool turn |
| 行程规划 Schema 与优化循环 | 09 | 02 提供 itinerary_planning Profile |
| 局部重排 Schema 与影响范围 | 16 | 02 提供 replanning Profile |
| 真实 Demo E2E、恢复与模型质量 | 15、22、23 | 02 live probe 只证明 API 合同，不评估旅游质量 |

固定依赖方向：

```text
langchain-core + langchain-deepseek + Pydantic
                    ↓
             app.llm_runtime
                    ↓
           03 / 08 / 09 / 16
```

模块 02 不得导入 `app.requirements` 或任何后续模块实现。

## 5. 公共合同

### 5.1 角色枚举

```python
class ModelRole(str, Enum):
    REQUIREMENT_EXTRACTION = "requirement_extraction"
    CANDIDATE_RESEARCH = "candidate_research"
    ITINERARY_PLANNING = "itinerary_planning"
    REPLANNING = "replanning"
```

禁止提供 `CUSTOM`、任意字符串角色或运行时注册新角色的 API。

### 5.2 固定 Profile

```python
class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role: ModelRole
    model_name: Literal["deepseek-v4-flash"]
    base_url: Literal["https://api.deepseek.com"]
    thinking_type: Literal["enabled"]
    reasoning_effort: Literal["high"]
    streaming: Literal[False]
    connect_timeout_seconds: Literal[10]
    read_timeout_seconds: int
    write_timeout_seconds: Literal[30]
    pool_timeout_seconds: Literal[10]
    provider_attempts_per_invocation: Literal[2]
    semantic_invocation_limit: int
    max_request_utf8_bytes: int
    max_output_tokens: int
```

Profile Registry 是只读 `Mapping[ModelRole, ModelProfile]`。调用方只能按角色获取，不能覆盖字段、复制后修改或注入字典替代。

### 5.3 设置

```python
class DeepSeekSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    api_key: SecretStr

def load_project_env() -> None: ...
def load_settings(environ: Mapping[str, str] | None = None) -> DeepSeekSettings: ...
```

`load_project_env` 只加载项目根目录固定文件 `.env`，并且 `override=False`，不得覆盖进程中已经存在的变量。`load_settings()` 无参数时先调用 `load_project_env`，再读取 `os.environ`；传入显式 Mapping 时只读取该 Mapping，保证离线测试不接触真实 secret。两条路径都只读取 `DEEPSEEK_API_KEY`。缺失、空白或占位值立即抛出不含原值的 `DeepSeekConfigurationError(code=configuration_error)`。该异常是构造期错误，不伪装成一次模型调用 outcome。环境中的 `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、超时或 Profile 覆盖项一律忽略并视为禁止配置；不得为其编写支持代码。

### 5.4 Operation 与配额

```python
runtime = DeepSeekRuntime(provider=..., sleeper=...)
operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
outcome = operation.invoke_structured(prompt=PromptEnvelope(...), schema=StrictOutput)
```

`DeepSeekSettings` 由生产 `LangChainDeepSeekAdapter` 构造阶段消费，不传给 `DeepSeekRuntime`。`ModelOperation` 规则：

- 一个实例只服务一个逻辑业务操作和一个固定角色。
- 记录 `semantic_invocations_used` 与 `provider_attempts_used`。
- 每次 public invoke 在发送前原子 claim 一个 semantic invocation。
- 单次 semantic invocation 最多含 2 个 provider attempts。
- Operation 不是 Pydantic 模型，不实现 JSON/pickle serializer，不可放入 Graph state。
- Operation 不得跨 thread、跨进程或跨业务操作复用。
- 配额耗尽返回 `quota_exhausted`，provider 调用数不增加。
- sync Operation 不得同时被多线程调用；async Operation 用锁保护 claim，禁止并发超卖。
- 第一次调用把 Operation 固定为 `sync` 或 `async` 模式；同一 Operation 混用同步和异步 API 必须抛编程错误。

### 5.5 调用结果

公共结果固定为判别联合，不返回 provider SDK 对象：

```text
StructuredInvocationOutcome[T]
├── StructuredInvocationSuccess[T]
│   ├── outcome_type = structured_success
│   ├── value: T
│   └── metadata: InvocationMetadata
└── LLMInvocationFailure
    ├── outcome_type = failure
    └── error: LLMRuntimeError

ToolTurnOutcome
├── ToolCallsReady
│   ├── outcome_type = tool_calls
│   ├── calls: tuple[ValidatedToolCall, ...]
│   └── metadata
├── ToolTurnCompleted
│   ├── outcome_type = completed
│   ├── final_text
│   └── metadata
└── LLMInvocationFailure
```

`StructuredInvocationSuccess.value` 必须已经是 consumer 提供的 Pydantic类型实例。任何 JSON、Pydantic 或 tool args 错误都不能出现在 success 中。

### 5.6 Metadata 与 usage

```text
InvocationMetadata
├── role
├── model_name
├── finish_reason
├── provider_attempts
├── possible_duplicate
└── usage

LLMUsage
├── prompt_tokens
├── completion_tokens
├── total_tokens
├── reasoning_tokens
├── prompt_cache_hit_tokens
└── prompt_cache_miss_tokens
```

所有 token 字段为大于等于 0 的整数。若供应商成功响应缺少 model、finish reason 或必要 usage，返回 `malformed_provider_response`，不得用 0 补齐后伪装成功。

## 6. 固定 Profile 矩阵

2026-09-27 用户批准 LIVE-021：仅将固定规划角色的输入上限从 524,288 bytes 调为 2,097,152 bytes（2 MiB），用于完整工具续调请求。输出 token、其余角色及通用默认、思考设置、次数和超时均保持原值；仍按 §8.4 保留供应商要求的私有历史，并按 §9.1–9.2 对完整请求计量，超限零 provider 调用、不消费 semantic 配额，不截断、摘要或自动扩容。批准与验证见 [统一问题台账](VALIDATION.md)。下文 2026-09-26 的额度说明为各次批准当时的边界。

| Role | read timeout | semantic 调用硬上限 | 完整请求 UTF-8 上限 | `max_tokens` |
|---|---:|---:|---:|---:|
| `requirement_extraction` | 120 秒 | 2 | 32 KiB（32,768 bytes） | 32,768 |
| `candidate_research` | 120 秒 | 12 | 256 KiB（262,144 bytes） | 131,072 |
| `itinerary_planning` | 120 秒 | 19 | 2 MiB（2,097,152 bytes） | 131,072 |
| `replanning` | 120 秒 | 8 | 256 KiB（262,144 bytes） | 32,768 |

所有 Profile 共同固定：

```text
model_name = deepseek-v4-flash
base_url = https://api.deepseek.com
thinking = enabled
reasoning_effort = high
streaming = false
connect timeout = 10s
write timeout = 30s
pool timeout = 10s
provider attempts per semantic invocation = 2
```

思考模式下禁止发送 `temperature`、`top_p`、`presence_penalty`、`frequency_penalty`。禁止依赖供应商“默认开启思考”，必须显式发送 `thinking={"type":"enabled"}` 与 `reasoning_effort="high"`。

后续模块可以为单个业务流程设置更低的 semantic 调用上限，但不能高于 Profile。Profile 值变更属于 L1 合同变更，必须先修改两份 02 文档并获得用户确认。

2026-10-07 用户批准只将固定 `CANDIDATE_RESEARCH_PROFILE.max_output_tokens` 从 32,768 提高到 131,072（128K），共同作用于 M08 Action 与 Observation Judgment。保持 `thinking=enabled`、`reasoning_effort=high`、读取超时 120 秒、semantic 上限 12、请求上限 262,144 bytes 和每次最多两次技术尝试；M08 研究 6 轮、地图 48 次不变。需求抽取/规划/重排输出上限仍分别为 32,768/49,152/32,768，通用默认仍为 16,384；当前规划 semantic=19 继续按 §20 的已批准增量执行。该变更是静态额度调整，不新增输出恢复调用，不改变 §9/10 的截断与严格校验规则，也不保证每次请求一定在额度内完成。正式供应商资格与本机服务更新分别执行，不以离线结果或此前诊断代替。

2026-10-08 用户明确批准将固定 `ITINERARY_PLANNING_PROFILE.max_output_tokens` 从 49,152（48K）提高到 131,072（128K），作用于 M09 首次规划、普通续调和参数修正。当前四角色输出依次为 32,768/131,072/131,072/32,768，通用默认仍为 16,384；规划 semantic=19，普通 17 与共享格式修正 2 的分类按 §20 和 [M09 §26](09_itinerary_planning_loop.md#26-通用参数修正与共享储备2026-10-06用户已批准) 保留。输入 2,097,152 bytes、enabled/high、读取 120 秒、每次最多两次技术尝试以及原截断失败规则不变；本次不增加输出恢复调用。实施与验收按 [E09 §31](09_itinerary_planning_loop_vibe_coding.md#31-m09-单次生成额度维护2026-10-08用户已批准)，正式 live 与现有网页更新分别记录。

2026-09-26 用户首次批准需求抽取输出上限由 8,192 调整为 16,384，通用 `ModelProfile` 默认值同步为 16,384。第 10/11 轮 repair 连续截断后，用户再次明确批准只将固定 `REQUIREMENT_EXTRACTION_PROFILE` 输出上限提高到 32,768，作用于该角色的 extract/repair；通用默认仍为 16,384，其余角色矩阵、思考设置、超时及次数保持原值。依据及验证见 [统一问题台账](VALIDATION.md)。这是经批准的静态 Profile 变更，运行时不因截断自行提高额度或重试；证据子串校验保持严格。

## 7. 模型构造合同

生产推理 adapter 必须使用 `langchain_deepseek.ChatDeepSeek`，禁止自行使用 `requests`、`httpx` 或 OpenAI SDK 重写 chat completion。能力探针的 `/models` 控制面查询只能复用 `ChatDeepSeek` 在锁定版本中已经创建的 `root_client.models.list()`；不得另建第二个带 Key 的 HTTP/OpenAI client，也不得通过 root client 发起 chat completion。

等价构造参数固定为：

```python
ChatDeepSeek(
    model="deepseek-v4-flash",
    api_key=settings.api_key,
    base_url="https://api.deepseek.com",
    timeout=httpx.Timeout(
        connect=10.0,
        read=profile.read_timeout_seconds,
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

`max_retries=0` 是 L0：重试只能由模块 02 的分类器执行，避免 LangChain/OpenAI SDK 隐式重试与外层重试相乘。

禁止设置或透传：

- `temperature`
- `top_p`
- `presence_penalty`
- `frequency_penalty`
- `n != 1`
- `streaming=True`
- 任意 proxy、organization、alternate base URL
- 任意 caller `model_kwargs` / `extra_body` 合并

## 8. 通用约束解码

### 8.1 归属原则

模块 02 拥有“如何约束和解码”的通用机制；consumer 拥有“约束成什么”的具体 Schema。

| 内容 | 所属模块 |
|---|---:|
| JSON mode、完整响应检查、Pydantic decode、tool args decode | 02 |
| 需求 Strict DTO 和提取 Prompt | 03 |
| 候选研究 action/tool Schema | 08 |
| 行程草案 Schema | 09 |
| 局部重排 Schema | 16 |
| 业务值交叉校验 | 对应领域模块 |
| 对 decode outcome 的流程路由 | 13 |

### 8.2 结构化输出固定算法

Demo 使用稳定端点 JSON Output 加本地严格校验，不依赖 Beta strict tool mode：

```text
Pydantic schema class
→ 验证 schema 是 BaseModel 子类且 extra=forbid、strict=True
→ 生成 canonical JSON Schema
→ 追加固定 JSON-only 指令和 canonical schema
→ ChatDeepSeek.with_structured_output(
      schema,
      method="json_mode",
      include_raw=True
   )
→ 收集完整非流式响应
→ 检查 model / finish_reason / usage
→ parsing_error is None 且 parsed 非空：parsed canonical JSON 序列化
→ parsing_error 非空或 parsed 为 None：保留 raw.content
→ 对最终 JSON 文本执行 schema.model_validate_json(..., strict=True)
→ success 或 structured_output_invalid
```

`parsed` 只是锁定版 LangChain 的解析结果，不是最终可信 DTO。解析成功时 Provider 只对它执行 canonical JSON 序列化；解析失败时保留结构完整 raw 消息的安全 metadata 和字符串 content。两条路径都必须由 Runtime 再执行 `schema.model_validate_json(..., strict=True)`。`parsing_error` 不得暴露，parser failure 不得返回 partial value；项目不得自行 strip Markdown fence、正则提取或使用宽松 JSON parser 修补 raw content。

这里必须保留 JSON-origin 语义：JSON 中 Enum、日期等值以字符串表示，禁止先 `json.loads` 再调用 `model_validate(..., strict=True)`，否则会把合法 JSON 字符串误判为必须已经是 Python Enum/日期实例。工具参数同理：provider-normalized arguments 必须先 canonical JSON 序列化，再调用 `args_schema.model_validate_json(..., strict=True)`。

固定 JSON 指令由 02 生成，只表达格式，不得包含任何旅游业务规则：

```text
Return exactly one JSON object matching the supplied JSON Schema.
Do not wrap it in Markdown. Do not add commentary before or after the JSON.
Every required property must be present. Use null only where the schema permits null.
```

consumer 仍必须在自己的 Prompt 中解释字段业务语义。02 不自动生成业务描述。

### 8.3 工具调用固定算法

- consumer 只能提供具名 Pydantic args schema，不接受无类型 `dict[str, Any]` 工具参数。
- 工具名匹配 `^[A-Za-z0-9_-]{1,64}$`，描述 `1..1024` code points。
- tool call arguments 必须通过对应 Pydantic schema 的 strict validation。
- 未知工具名返回 `unknown_tool`，不执行最相似名称猜测。
- 参数错误返回 `tool_arguments_invalid`，不把原始 arguments 放进错误对象。
- 模块 02 只返回 validated call，不执行工具函数。
- validated call 的后续执行只能由消费方 Skill 按白名单交给 typed adapter；若该 adapter 使用 MCP，MCP 会话与原始 payload 始终留在模块 06 等工具 adapter 内，不回流模块 02。
- Demo 不发送 `strict=true`，不使用 `/beta`，不向 provider 发送 caller 自定义 `tool_choice`。
- capability probe 必须证明思考模式工具调用可用；失败时模块阻断，不退回文本描述工具意图。

### 8.4 思考内容连续性

思考模式工具调用的供应商合同要求下一子轮回传 `reasoning_content`。固定实现：

1. `ModelOperation` 在内存私有字段中短暂保留当前工具循环的原始 assistant message。
2. public `ToolCallsReady` 只暴露 call ID、工具名和已验证参数。
3. consumer 执行工具后，把 `ToolResult(call_id, content)` 交回同一个 Operation。
4. Operation 内部重放供应商要求的 assistant message 和 reasoning content。
5. 工具循环结束或 Operation 释放时立即丢弃私有 provider history。

禁止：

- 把 reasoning content 放入 DTO、repr、日志、异常、checkpoint、artifact 或审计事件。
- 让 consumer 自行拼接 reasoning content。
- 跨进程恢复 Operation。
- 崩溃后从中间 tool turn 继续。崩溃恢复必须从最后一个业务可重放边界重启整个逻辑操作，并由上层标记可能重复。

## 9. 输入与输出超长处理

### 9.1 完整请求计量

发送前把以下内容按固定 key 排序和无多余空白的 JSON 形式规范化，再计算 UTF-8 bytes：

- system messages
- user/assistant/tool messages
- structured output JSON Schema
- tool definitions
- 固定 JSON-only 指令
- provider 必需控制字段，但不含 API Key

不能只统计用户消息。byte 数是项目安全门禁，不宣称是精确 token 数；实际 token 只采用 provider usage。

### 9.2 输入超限

- `request_bytes > profile.max_request_utf8_bytes` 返回 `input_too_large`。
- provider attempts 必须为 0，semantic invocation claim 是否消耗固定为“不消耗”。
- 错误只包含 `actual_bytes`、`max_bytes` 和 role，不包含原文。
- 禁止截断消息、Schema、工具结果或只保留尾部。
- 禁止模块 02 调用模型摘要、关键词抽取或自动删除历史。
- consumer 压缩后可再次提交；如何压缩由其业务文档规定，并受 semantic 调用配额约束。

### 9.3 输出超限与非正常结束

| provider finish reason | 02 outcome |
|---|---|
| `stop` | 继续进行完整性和本地 decode |
| `tool_calls` | 继续进行工具名和参数 decode |
| `length` | `output_truncated`，整个结果作废 |
| `content_filter` | `content_filtered`，整个结果作废 |
| `insufficient_system_resource` | `provider_unavailable`，允许有限重试 |
| 缺失或未知值 | `malformed_provider_response` |

禁止解析、展示或应用被截断的 JSON、自由文本和 tool arguments。禁止因截断自动提高 `max_tokens`。

SDK 在返回 response 之前抛出的 `LengthFinishReasonError` 和 `ContentFilterFinishReasonError` 分别按 `length`、`content_filter` 处理；不得降级为未知响应或暴露异常携带的原文。

## 10. 重试与退避

### 10.1 次数

每个 semantic invocation 固定：

```text
attempt 1
└── retryable failure → wait → attempt 2
    └── 任意 failure → 返回失败
```

最多 2 个 provider attempts。没有第三次尝试，没有模型切换，没有自由文本 fallback。

### 10.2 允许重试

- HTTP 429。
- HTTP 500、502、503、504。
- 连接建立失败、连接重置。
- read timeout。
- finish reason `insufficient_system_resource`。

### 10.3 禁止重试

- HTTP 400、401、402、403、404、422。
- API Key/configuration 错误。
- 模型不存在或返回模型名不符。
- Schema 不支持、本地 Pydantic 错误、tool arguments 错误。
- 输入超限、context length exceeded、quota exhausted。
- output truncated、content filtered、malformed response。
- consumer/业务异常。

### 10.4 等待

- 没有合法 `Retry-After` 时等待 1 秒。
- 有合法 `Retry-After` 时采用其值，但截断到 `0..30` 秒。
- `Sleeper` 必须注入，单元测试不得真实等待。
- 同步路径调用 `sleep`，异步路径调用 `async_sleep`。
- attempt 1 已可能被供应商接收的失败，最终 metadata/error 标记 `possible_duplicate=true`。

## 11. 错误合同

### 11.1 固定错误码

```text
configuration_error
capability_unavailable
schema_unsupported
input_too_large
quota_exhausted
authentication_failed
insufficient_balance
permission_denied
model_not_found
invalid_request
context_length_exceeded
rate_limited
provider_unavailable
timeout
transport_error
structured_output_invalid
tool_arguments_invalid
unknown_tool
output_truncated
content_filtered
malformed_provider_response
unexpected_model
```

### 11.2 错误对象

`LLMRuntimeError` 只允许包含：

- `code`
- `role`
- `retryable`
- `possible_duplicate`
- `provider_status_code`（nullable）
- `retry_after_seconds`（nullable，已裁剪）
- `actual_bytes/max_bytes`（仅 input_too_large）

禁止包含完整异常字符串、provider response body、headers、request body、messages、Schema、tool arguments、API Key、reasoning content 或堆栈。

`DeepSeekConfigurationError` 只允许暴露固定 code `configuration_error`；其 `str/repr` 不得包含环境变量值。它不含 role，因为配置加载发生在选择模型角色之前。

## 12. 配置与 Secret

- 唯一环境 Secret：`DEEPSEEK_API_KEY`。
- 本地长期配置固定存放在项目根目录 `.env`，格式只能是 `DEEPSEEK_API_KEY=<真实值>`。
- `.env` 必须被 `.gitignore` 忽略；仓库只提交值为 `your-api-key` 的 `.env.example`，禁止提交真实 key。
- 进程环境变量优先于 `.env`；`.env` 只作本地后备，禁止覆盖已经存在的进程变量。
- Production composition root 和 live gate 调用无参数 `load_settings()`；纯离线测试必须传入显式 Mapping。
- `SecretStr` 的真实值只在创建官方 client 时解包一次。
- Profile、错误、probe report 和测试 snapshot 中不得出现 secret。
- `repr(runtime)`、`repr(settings)`、异常和失败 DTO 必须经过 secret 泄漏测试。
- 模块不读取 LangSmith 配置，不启用自动 tracing；08 定义后通过显式安全 callback 接入。
- 禁止 proxy 和 alternate base URL，防止 key 被发送到非官方端点。

## 13. 能力探针

### 13.1 fake probe

默认 contract tests 使用 `ScriptedProviderAdapter`，确定性覆盖：

- 模型存在/不存在。
- 思考参数已发送。
- sync/async 调用。
- JSON decode 成功/失败。
- tool call 成功、未知工具和参数错误。
- usage 缺失、finish reason 异常和模型名不符。
- 每种 retryable/non-retryable 错误。

### 13.2 live probe

真实 probe 是模块 02 的 `live_qualified` 门槛，与默认离线单元测试隔离；`contract_complete` 由离线合同门禁证明。固定检查：

1. 官方 `/models` 经上述唯一别名归一化后包含精确规范 ID `deepseek-v4-flash`。
2. 非流式思考请求成功，返回模型名一致。
3. `reasoning_effort=high` 与显式 thinking 参数可用。
4. JSON Output 可返回并通过一个最小 strict Pydantic schema。
5. tool call 返回已知工具且 args 可本地校验。
6. async 请求成功。
7. usage 包含 prompt/completion/total token；若 provider 提供 reasoning/cache 细分则正确映射。
8. probe 输出不含 prompt 原文、响应正文、API Key 或 reasoning content。

live probe 使用专用最小 Prompt 和极小输出 Schema，不执行旅游业务，不衡量模型质量。真实 401、402、模型缺失或能力不兼容均判定本次 `live_qualified` 失败；不反向抹除已经通过的确定性 `contract_complete`，但依赖真实模型的下游验收不得使用过期资格继续推进。

## 14. 依赖锁定

Python 基线固定为 3.12。模块 02 的直接兼容基线：

```text
langchain-deepseek==1.1.0
langchain-core==1.4.9
pydantic==2.13.4
```

`langchain-openai`、`openai`、`httpx`、`tenacity` 等传递依赖必须出现在由干净虚拟环境生成的 `requirements.lock` 中，不得靠当前开发环境的偶然版本。

规则：

- `pyproject.toml` 中模块 02 的关键直接依赖使用 `==`。
- 从全新临时 venv 安装项目与 dev extra 后生成 lock。
- 删除路径、本地 editable 标记、凭证和私有 index URL 后再检查 lock。
- 用第二个全新 venv 仅从 lock 安装并运行模块 01、02 测试。
- 编码 Agent 不得自行升级或降级版本。

## 15. 测试合同

### 15.1 默认离线测试

`tests/unit/llm_runtime` 和默认 `tests/contract/llm_runtime`：

- 不读取真实 `DEEPSEEK_API_KEY`。
- 不访问网络、数据库、Graph 或地图。
- 不真实 sleep。
- 不使用 monkeypatch 替换全局 singleton；通过构造函数注入 fake。
- 不依赖测试执行顺序。
- 不含 skip/xfail。

### 15.2 必测行为

- 四个 Profile 每个字段精确相等且 frozen。
- 未登记 role 无法构造。
- settings 缺失、空白和 Secret repr。
- `ChatDeepSeek` 构造参数精确验证，包括 `max_retries=0` 和不发送无效采样参数。
- UTF-8 byte 边界：`limit-1`、`limit`、`limit+1`。
- input too large 零 provider call、零 quota 消耗。
- semantic quota 边界与并发 claim。
- 所有 retry/error 映射和精确 attempt 次数。
- `Retry-After` 负数、非数字、0、30、超过 30。
- structured success、invalid JSON、Pydantic failure、extra field，以及同步/异步 JSON Enum 字符串的 strict success。
- output truncated 不返回 partial value。
- tool name/args、未知 tool、重复 call ID，以及同步/异步 Enum 工具参数的 JSON-origin strict success。
- reasoning content 不出现在任何公共对象、repr 或序列化结果。
- sync/async 行为等价。
- provider model/usage/finish reason 完整性。
- Operation 不可 pickle/JSON serialize。

### 15.3 覆盖率

模块 02 自身 line/branch 综合覆盖率必须至少 95%。live probe 不计入覆盖率分母，也不能替代 fake 错误矩阵测试。

## 16. 规范 ID

| ID | 固定行为 |
|---|---|
| REQ-LLM-001 | 生产供应商只能是官方 DeepSeek |
| REQ-LLM-002 | 模型名固定 `deepseek-v4-flash` |
| REQ-LLM-003 | Base URL 固定官方稳定端点 |
| REQ-LLM-004 | 四角色枚举封闭，禁止 custom role |
| REQ-LLM-005 | 四角色全部显式开启思考模式 |
| REQ-LLM-006 | reasoning effort 固定 high |
| REQ-LLM-007 | 不发送思考模式不支持的采样参数 |
| REQ-LLM-008 | Profile frozen 且不可环境覆盖 |
| REQ-LLM-009 | 仅显式读取 DEEPSEEK_API_KEY |
| REQ-LLM-010 | import 时不读环境、不建 client、不访问网络 |
| REQ-LLM-011 | 每逻辑操作使用独立不可序列化 Operation |
| REQ-LLM-012 | semantic quota 在发送前原子 claim |
| REQ-LLM-013 | 单次 semantic invocation 最多两个 provider attempts |
| REQ-LLM-014 | LangChain 内建 retry 必须关闭 |
| REQ-LLM-015 | 只有固定 transient error 可重试 |
| REQ-LLM-016 | Retry-After 最大 30 秒 |
| REQ-LLM-017 | 完整请求按 canonical UTF-8 bytes 预检 |
| REQ-LLM-018 | 输入超限零 provider call 且不截断 |
| REQ-LLM-019 | 02 不做业务摘要或上下文裁剪 |
| REQ-LLM-020 | output length 截断使整个结果作废 |
| REQ-LLM-021 | provider model/finish reason/usage 缺失不得成功 |
| REQ-LLM-022 | 结构化输出使用稳定 JSON mode |
| REQ-LLM-023 | consumer Schema 必须为 strict Pydantic model |
| REQ-LLM-024 | 所有 parsed value 再执行本地 strict validation |
| REQ-LLM-025 | tool args 使用 consumer Pydantic schema 验证 |
| REQ-LLM-026 | 02 不执行业务工具 |
| REQ-LLM-027 | Demo 不依赖 Beta strict tool endpoint |
| REQ-LLM-028 | reasoning content 不离开内存 Operation |
| REQ-LLM-029 | reasoning content 不进入日志、状态或异常 |
| REQ-LLM-030 | tool loop 崩溃后重启逻辑操作，不持久化思维链 |
| REQ-LLM-031 | streaming 固定关闭 |
| REQ-LLM-032 | usage 只含数值和无原文 metadata |
| REQ-LLM-033 | public outcome 不暴露 provider SDK 对象 |
| REQ-LLM-034 | provider error body 不进入公共错误 |
| REQ-LLM-035 | sync/async 使用同一规则与错误表 |
| REQ-LLM-036 | fake provider 覆盖完整错误矩阵 |
| REQ-LLM-037 | live probe 验证模型、思考、JSON、tool、async、usage |
| REQ-LLM-038 | live probe 未通过时 `live_qualified=false`，不得推进依赖真实模型资格的下游验收；不抹除已通过的 `contract_complete` |
| REQ-LLM-039 | 关键依赖精确锁定并生成 requirements.lock |
| REQ-LLM-040 | 02 不导入或实现旅游业务、Graph、持久化、入口、Agent Skill 调度或 MCP |

### 16.1 Structured include_raw 与客户端生命周期补充

`ChatDeepSeek.with_structured_output(..., include_raw=True)` 的 `raw` 消息是供应商响应边界的一部分。只要 raw 消息的 `model`、`finish_reason`、usage、tool calls 和 content 类型完整，解析成功时使用 parsed 的 canonical JSON，解析失败时保留 raw content；两条路径都交给本地 `model_validate_json(..., strict=True)`。非法 JSON、缺字段、额外字段和类型错误统一为 `structured_output_invalid`；只有响应结构或必要 metadata 缺失/非法才是 `malformed_provider_response`，不得返回 partial value 或 parser 异常文本。

`ProviderAdapter` 持有按不可变 Profile 缓存的模型客户端，并提供幂等 `close()` / `aclose()`。`DeepSeekRuntime` 是共享 Provider 的生命周期所有者；`ModelOperation.close()` 只清理单次操作私有状态。异步客户端必须在所属事件循环仍有效时由 `aclose()` await 关闭，同步组装、probe 和 fixture 必须通过 context manager 或 `try/finally` 释放资源，不得依赖垃圾回收或扩大 pytest event loop scope。

## 17. 验收场景

| 场景 | 必须结果 |
|---|---|
| API Key 缺失 | configuration_error；零 client/network call |
| caller 尝试换模型 | 无覆盖 API；构造阶段拒绝 |
| caller 传 32 KiB+1 的 extraction 请求 | input_too_large；零 provider call |
| 429 后成功 | 等待一次，provider attempts=2 |
| 401 | authentication_failed；不重试 |
| invalid JSON | structured_output_invalid；无 partial value |
| extra field | 本地 strict validation 失败 |
| finish_reason=length | output_truncated；原始 partial 丢弃 |
| tool args 缺字段 | tool_arguments_invalid；工具未执行 |
| tool turn 含 reasoning | 仅 Operation 内回传，公共结果不可见 |
| semantic quota 用尽 | quota_exhausted；provider attempts 不增加 |
| live /models 无 flash | capability_unavailable；本次 `live_qualified=false`，既有 `contract_complete` 保持 |

## 18. NOT in scope

- 01 的领域提取和对话行为。
- 03 的真实需求提取 Prompt 与 repair。
- 04–05、13 的状态、Graph 和 Demo 编排。
- 06–09、12、16 的地图、酒店、候选研究、行程、输出和局部重排业务。
- 10–11、14–15、19–23 的持久化、恢复、入口、装配、E2E 和质量评测；17、18 当前未分配能力。
- 多供应商路由、自动 fallback、模型 AB test、动态模型发现。
- 流式 UI、语音、图像、文件上传或多模态输入。
- 自托管 DeepSeek、代理服务或非官方兼容端点。
- 供应商服务端 Beta strict tool mode。
- 业务上下文摘要、压缩策略和长期记忆。

## 2026-09-29 四项改进增量修订（用户已批准）

以 [M09四项改进设计及验收](VALIDATION.md) 为本次增量依据：M09供应商工具使用beta严格schema，原本地完整校验保留；非法工具调用整批拒绝；Directions给出代码推导时长，路线end_at可空并由代码计算（显式旧值须一致）；Prompt减少格式/计算重复；提交反馈包含稳定reason/field。仅修改模型输入端，不改变历史持久行程schema、预算、硬门槛或数据库。本文此前涉及统一非严格工具输出、路线end_at必填和泛化反馈的条款由本节限定覆盖，其余条款继续有效。实施范围、前置条件、测试矩阵及Git限制见上述文档。

2026-09-29修复重跑补充：依据用户“修复问题然后重跑，有三次重跑机会”授权，strict wire完全展开无递归本地引用；被供应商限制移除的数量/长度及日期格式保留为说明，本地验证不变。M09终结提交拒绝在返回通用状态前记录代码内reason/field与terminal_only，不输出参数或异常正文，不改公开错误/持久化合同。三次真实结果、白名单及验收详见[m09_four_improvements.md](VALIDATION.md)；最后业务提交仍未通过，不据此扩大重跑或预算。

2026-09-29轻量错误定位增量：按用户授权实现[错误定位日志](error_location_logging.md)。在JSON解析、本地工具参数校验和业务拒绝转换为通用结果之前记录脱敏字段/规则/源码位置；最后一次提交同样记录，真实测试单独保存error-locations.jsonl。仅复用日志与run_id，不增加数据库表、迁移、错误结果字段、回放系统或调用额度。详见该文档的范围、测试矩阵及限制。


## 19. Demo 完整交互记录（2026-09-30）

本次按用户确认D-10～D-15修订。仅增加专用诊断，普通日志/公共结果/业务Artifact不放正文。整体设计仍待批准，尚未实施；阶段白名单和测试见 [M09执行规范§29](09_itinerary_planning_loop_vibe_coding.md)。

### 19.1 采集边界

沿现有 ChatDeepSeek 创建的 HTTP client 挂 request/response 观察钩子，生产推理仍经 ChatDeepSeek。保存出站请求正文和已接收响应正文，先读取原始字节，再交 SDK 解析；不能以重序列化 DTO 代替原文。钩子不改请求、响应、strict mode、重试或输出上限，不创建第二条推理通路。

M02 在每个逻辑调用中建立 call_id，每个实际 attempt 记录 request、response 或 transport_failure；所有两次尝试都指向同一个 call_id。SDK malformed、JSON invalid、schema invalid、length 截断或业务拒绝后再追加处理结果。transport 无响应时 body/usage 明确缺失；不得从 M02 正常化为 0 的可选字段反推供应商实际值。

供应商返回的 reasoning_content 属于诊断原文，不表示能够取得模型全部内部思考。response 原文同时保存原始工具 arguments，即使非法也只用于查看；参数及截断输出不能回流业务执行。M03/M08/M09 普通日志、异常、事件、repr、Graph state、业务 Artifact 和网页攻略仍不放正文。

### 19.2 固定记录模型

M02 新增固定 InteractionRecord/InteractionRecorder 端口及 no-op/in-memory 实现，不接受任意 metadata。record 必须 strict/frozen/extra=forbid，schema_version=1.0，正文字段 repr=False。字段组如下：

| 字段组 | 必填含义 |
|---|---|
| 身份 | record_id UUID；run_id UUID；trace_id UUID；call_id UUID或None；model_operation_id UUID或None；captured_at UTC。 |
| 关联 | role:ModelRole或None（仅run_summary可空）；purpose（extract/repair/research_action/research_judgment/planning/run）；logical_call_index 或None；provider_attempt 1/2或None；发起阶段和完成后 budget_category 可空。 |
| 类型 | request、response、transport_failure、validation、tool_result、call_summary、run_summary 的判别联合。 |
| 正文 | request/response/实际工具反馈：encoding=utf8或base64、content、保留字节长度/sha256、redacted；非法 UTF-8 用 base64 保留，不修复。 |
| usage | 原始 response 中实际存在的 6 项：prompt、cache_hit、cache_miss、completion、reasoning、total；每项可空，另有 source/complete，不能默认填 0。 |
| 处理结果 | 固定 error/stop code、field、SDK/结构/业务是否通过；tool 名、provider call_id/ordinal、业务 Observation Ref 的规范 JSON（可空）；没有对应资料明确可空。 |

字段均 required，可空也不能省略。禁止 Any/无界 dict 或 SDK 对象。下表冻结各类型的字段，规范正文可保存为JSON字符串，但不能将其解码为执行动作。ModelOperation 的安全 call_id/逻辑索引可通过 [M09 §12.2](09_itinerary_planning_loop.md)供 M09 关联，历史正文不公开给消费方。M03/M08 只设置调用用途上下文；M13 绑定 run/trace；业务工具执行用相同 call_id 写结果。ContextVar 在 finally 复原，异步 sibling 不能串 run。

工具记录复用控制器已经得到的 typed Observation、返回模型的反馈及业务 Artifact Ref，不重复调用地图、读模型或改变结果。M08 action 与 judgment 分开；M09 evidence/map/submit/supplement 含成功、无结果、拒绝、缓存和未执行原因。原始 MCP/百度 HTTP payload 不属于此次完整业务交互。

### 19.3 凭证和完整性

不采集 HTTP headers、cookie、Authorization、连接串、配置对象或 pause credential。保存前对已配置的 API key、数据库密码等真实凭证值作精确移除；实际业务正文保留，记录 redacted 标志。不得用“全部脱敏”声称能够识别任意用户自行输入的秘密，也不建设通用 PII/DLP 引擎。fixture 必须使用明显虚构原文/思考及 secret sentinel，确保真正配置凭证不落入数据库、文件和导出。

记录成功不影响业务正确性；业务正确性也不证明诊断完整。run_summary 包含已观察调用数、已知 usage、记录失败/缺失信息和 complete flags。结束摘要缺失、attempt 响应缺失、未保存处理结果或无法对齐实际调用时，导出只报告已知值且 complete=false，不推算缺失内容。

### 19.4 端口、关联与准入

本节新增明确公共导出 InteractionRecord、InteractionRecorder、InteractionWriteResult、InteractionCallIdentity、interaction_run_context、interaction_call；供消费者注入和关联，不导出 HTTP/数据库实现。interaction_run_context 由 M13 绑定 run_id/trace_id/recorder；interaction_call(purpose) 为每个逻辑调用建立一个 M02 生成的 call_id，返回只含安全身份的 frozen 关联对象，供调用结束后的工具结果继续引用。同一 scope 仅容纳一次 invoke/continue，extract 与 repair、action 与 judgment 各建 scope；M02 内部补逻辑索引/attempt。finally 复原 ContextVar，不能读取私有 history 或给公共 metadata 追加正文。

InteractionRecorder 提供 async append(record) 和 async read_run(run_id)（后者只用于导出）；InteractionWriteResult为required/strict/frozen的record_id:UUID、status:saved/fallback/missing、code:安全固定诊断code或None；生产实现处理诊断写入故障并返回此安全结果，不把消息串入异常。no-op、in-memory 属内部实现，缺根run的独立探针可以no-op，正式Demo不可静默漏注入。M10实现此端口，M02不导入M10。诊断异常不能改原始业务异常；控制取消原样传播。

M02 async invoke/continue 接受可选准入回调（安全 call_id/逻辑索引）及当前有效工具集合。完整 byte/quota 预检→一次回调→semantic claim→provider；最多两次物理尝试不重复回调。操作锁仅覆盖检查/回调/claim，不持锁等待供应商。回调失败禁止发请求。continuation 保留原历史，只更换当次真正送给供应商的 tools 并同步本地响应校验；不能让已移除工具借旧定义执行。M09具体额度归属见其§12.2；其他角色/同步入口的现有默认行为不改。

### 19.5 记录字段冻结

InteractionRecord 使用固定信封加 record_kind 判别 payload。信封身份/关联字段见§19.2；model_operation_id 暂可空，现有M02没有公开Operation UUID时不得伪造业务ID。模型call均须有call_id/role/purpose；run_summary的call_id/role/model_operation_id/逻辑索引/attempt均为None，purpose=run。stage/category仅M09使用，其他模块为None。总正文不是业务Artifact，不使用M04Registry注册。

| 类型 | required字段及约束 |
|---|---|
| BodySnapshot | encoding:utf8/base64；content:str，repr=False；retained_size_bytes:int≥0；content_hash:sha256 digest；redacted:bool。对保留字节验hash/size，已配置凭证在字节层替换后再编码，不重序列化HTTP业务JSON。 |
| RawUsage | prompt_tokens/cache_hit_tokens/cache_miss_tokens/completion_tokens/reasoning_tokens/total_tokens:int≥0或None；source:provider_response/missing；complete:bool。六项取实际响应；缺字段留None，全部已知且一致才complete。 |
| request | body:BodySnapshot；attempt=1或2。 |
| response | body:BodySnapshot；http_status:int[100..599]；usage:RawUsage；attempt=1或2。SDK失败不丢原文。 |
| transport_failure | code:固定LLMErrorCode；response_received:bool；attempt=1或2。不得保存底层异常文本；无完整body时无response记录并标缺失。 |
| validation | stage:sdk/tool_schema/batch/policy/submit/controller；passed:bool；code:str[1..128]或None；field:str[1..256]或None；budget_category可空。code/field取代码内固定安全反馈，不直接收ValidationError/用户文本。 |
| tool_result | tool_name:str[1..128]；provider_tool_call_id:str[1..256]或None；ordinal:int[0..127]；outcome:success/no_result/rejected/cached/not_issued/failure；feedback:BodySnapshot；observation_ref_json:str或None（现有Ref七字段canonical JSON）；预算分类可空。无执行结果只保存真实未执行原因。 |
| call_summary | admitted:bool；observed_provider_attempts:int[0..2]；usage:RawUsage；stop_code:str[1..128]或None；records_complete:bool；record_failure_count:int≥0。预算分类由消费方后续validation/tool_result关联，不改历史请求。 |
| run_summary | observed_admitted_calls:int≥0；observed_provider_attempts:int≥0；known_usage:RawUsage；records_complete:bool；record_failure_count:int≥0；missing_record_ids:tuple[UUID]；stop_code:str[1..128]或None。最后只有M13根装配发布；省略摘要表示不完整。 |

诊断完整性和usage完整性分开：原始响应保存完整但供应商未返回某usage字段时，records_complete仍可true，usage.complete=false；响应/处理记录/结束摘要缺失时records_complete=false。每次实际attempt恰需request加response或transport_failure及处理记录，导出不能把两者误算两个调用。已知完成摘要只覆盖本次可观察历史，不把进程重启前未知记录填零。存储/导出规则及失败兜底见 [M10 §23](10_artifact_persistence.md)；验收AC-07/08见 [M09 §25.3](09_itinerary_planning_loop.md)。

## 20. 通用工具参数拒绝与显式修正（2026-10-06，用户已批准）

状态：APPROVED_FOR_IMPLEMENTATION；本次增量按用户“同意，开始修复”授权。它限定替代§8/§10/§11中工具参数错误立即关闭、不得修正输出的旧规则；原本地严格 schema、网络重试表和其他消费者默认行为保留。真实 strict 接入诊断已证明供应商接受合法 strict schema 后仍可能漏必填字段，不能把 HTTP 200 等同于参数合同合规。

- R-02-01：新增显式 opt-in（invoke_tools/ainvoke_tools 的 allow_argument_repair=True）；默认 False 仍终止。新增 frozen/strict/extra-forbid 的 ToolArgumentIssue、RejectedToolCall、ToolTurnRejected；后者只返回已核验 InvocationMetadata、tool_arguments_invalid（retryable=False）及有界安全字段位置/规则，不返回原始 arguments、reasoning 或异常正文。坏 JSON、missing、严格类型、enum/literal、extra、长度/数量/范围、其他消费者 schema 验证错误使用同一机制，不特判旅行字段。
- R-02-02：响应首先验证 model/finish/usage，再对完整批次先检查所有 call ID 非空、长度、唯一和所有工具名属于本次合同，然后验证所有参数。后置 unknown/duplicate 必须优先于前置参数错；坏 metadata、截断、过滤、未知工具、协议异常不进入格式修正。任一参数错整批零执行，合法兄弟也必须收到未执行反馈。
- R-02-03：ProviderResponse 私有原始工具批次按 SDK additional_kwargs 的完整原始顺序保存每个 arguments 字符串（repr=False）；不能从 parsed/invalid 两列表拼顺序、宽松修 JSON、补必填值或从诊断数据库重建。Continuation 的 SDK 实际 wire 必须保留原始 assistant tool_calls 和 reasoning，包括混合合法/非法 JSON；用受控 HTTP fixture 验证真实请求。
- R-02-04：operation 处于 pending-rejection 时仅允许 repair_tools/arepair_tools、close/cancel；禁止普通 continue、重新 invoke 和并发修正。repair 一次 API 调用至多发起一次语义调用，仍沿原技术 retry 最多两物理 attempts；自身不 while 重发。工具合同重新预检且保持拒绝那轮的精确权限，不接受消费者提供原始参数。反馈由 M02 为所有 ID 生成，说明整批未执行并要求模型重新输出完整合法批次；完整私有 history 仍经过字节门、语义 quota 和消费者 admission callback。
- R-02-05：只有整批 schema 验证通过才清除 pending 错误链。close/cancel 后迟到的响应不得写回 history 或恢复 pending。M02 不实现 M09 的业务校验、阶段额度或两次格式储备，M09 独立选择是否发起下一次修正。
- R-02-06：M09 Profile semantic_invocation_limit=19；其他 Profile、token/字节/单调用超时、技术重试不变。诊断 Category 增加 format_repair，ToolTurnRejected 记录为验证失败而非成功；保留既有专用完整交互记录授权与失败不干扰主链规则。

验收：两个无旅行字段的 toy contracts 验证通用性；覆盖上述六类参数错误、整批拒绝、协议优先、sync/async/default opt-out、原始 wire 顺序、byte/quota 零外调、并发/取消/close、repr/日志不泄漏；原 M02 单独 line+branch 覆盖率门槛 95% 保留。执行入口见 [E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)。

## 2026-10-11 结构化失败的不确定标志保真（用户批准）

本次维护落实 §11 的既有 possible_duplicate 合同：结构化调用的第一次技术尝试可能已被供应商接受时，无论第二次返回成功、非法 JSON/schema、截断、内容过滤或非法 metadata，最终 metadata/error 必须保留 true。_decode_structured 的所有失败分支传递已累积的标志，不重新设为 false；没有不确定尝试时保持 false。公共 DTO、供应商重试策略/次数、semantic/token 额度和严格解析均不变。M08 按其 R08-12 在标志为 true 时不执行输出恢复。仅此不确定性传播属于本次跨模块授权。

2026-10-11 用户随后批准取消统计的最小查询合同：ModelOperation.semantic_invocations_consumed 是只读整数属性，返回该操作已经准入的语义调用数（0..该角色上限），不含请求、响应、reasoning或私有计费信息，不消费预算。M08 在每次结构化调用前及 CancelledError 时读取公共计数差；只核对本次准入的0/1次，不读私有字段、不猜测。取消仍原样传播，无恢复/额外外调；未完成请求的provider attempts/usage不可证明时标不完整。M02技术重试共用一次semantic，不使属性重复增长。
