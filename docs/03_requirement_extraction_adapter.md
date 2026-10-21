# 03 需求提取适配器规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次消费M02专用交互记录，仅增加调用用途/工具结果关联；需求业务语义及M08原6轮/48Place/12逻辑调用不扩大。普通日志/业务Artifact禁正文的约束不变。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 2026-09-26 用户批准 LIVE-013：当前 Prompt 为 `requirement-extraction-v3-explicit-negatives`，明确无行动/饮食限制按带否定词的原文提取，未提及仍为 null，每字段独立 evidence；继承预算延期。新增 `extract_v3_explicit_negatives.txt` / `repair_v3_explicit_negatives.txt`，v1/v2 保持只读。UTF-8/LF SHA256 分别为 `1398108807fe64cd6c49d65c0a95e88a500c09842c1c43a09b904706e3ee2e0b` / `ea2f9faa8c9e513b42fd10cd5b593d766ddae83dd4261c7baa3abf5ccc3ff496`。本批资格和问题统一记录在 [修复台账](VALIDATION.md)，不能沿用历史 v1 的 live_qualified。

> 2026-09-26 用户批准预算金额功能延期：[当前修订与执行合同](12_budget_amount_deferral.md) 优先适用。金额不采集、不作为确认/输出门槛；API 调用额度不变。旧预算案例属于历史合同，不代表本期验收项。

> 文档状态：v1.1（Demo/Production 拓扑同步版）
> 实现状态：模块 03 已完成；公共合同与 Prompt 资格按本版本冻结，验证快照见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 上位规范：[`00_agent_master_design.md`](00_agent_master_design.md)
> 依赖与工作包：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 前置合同：[`01_requirement_and_dialogue.md`](01_requirement_and_dialogue.md)、[`02_llm_runtime_and_model_profiles.md`](02_llm_runtime_and_model_profiles.md)
> 配套编码契约：[`03_requirement_extraction_adapter_vibe_coding.md`](03_requirement_extraction_adapter_vibe_coding.md)
> 规范属性：本文件只定义需求提取模型适配、Prompt、Session 调用预算、错误映射、安全事件和模块级验收

```yaml
module_id: "03"
delivery_phase: "Demo"
blocks_demo: true
blocks_production_release: true
depends_on: ["01", "02"]
provides:
  - DeepSeekRequirementExtractor / DeepSeekRequirementExtractionSession
  - RequirementExtractionRuntimeError
  - ExtractionInvocationEvent / ExtractionInvocationSink
  - requirement-extraction-v1 Prompt assets and hashes
  - REQ-EXT-001..048
consumes:
  - app.requirements public contracts
  - app.llm_runtime public contracts
parallel_with: ["04"]
not_in_scope:
  - 需求规范化、合并、追问、确认和人工模板
  - DeepSeek 配置加载、Runtime 构造、provider retry 和通用约束解码
  - LangGraph、checkpoint、数据库、MCP、百度地图、Agent Skill 和产品入口
acceptance_gate:
  - EXT-T00..EXT-T10 按顺序完成
  - 单元与 fake contract 测试无 skip/xfail 且 app/requirement_extraction line/branch coverage >= 95%
  - 真实 deepseek-v4-flash 固定 16 用例在一次完整运行中 16/16 通过
  - 进程环境变量和项目 `.env` 都缺少 DEEPSEEK_API_KEY 时 live suite 明确失败而不是 skip
  - app/requirement_extraction 只导入 app.requirements 与 app.llm_runtime 的包根公共符号
test_class: adapter-contract
unit_test_command: python -m pytest -q tests/unit/requirement_extraction
contract_test_command: python -m pytest -q tests/contract/requirement_extraction
allowed_test_dependencies:
  - Python 标准库 / Pydantic / pytest / pytest-asyncio / pytest-cov
  - app.requirements / app.llm_runtime 公共合同
  - fake Runtime；tests/live 中允许真实 DeepSeek API
forbidden_test_dependencies:
  - LangGraph / FastAPI / 数据库驱动 / MCP / 百度地图 SDK
  - app.graph / app.persistence / app.agent / app.tools / app.config
  - 04-23 的任何实现
required_fakes:
  - ScriptedModelOperation / RecordingDeepSeekRuntime
  - RecordingExtractionInvocationSink
stable_contracts:
  - DeepSeekRequirementExtractor / DeepSeekRequirementExtractionSession
  - RequirementExtractionRuntimeError / ExtractionInvocationEvent
production_replacement: null
```

## 1. 目标

模块 03 把模块 01 的领域提取协议适配到模块 02 的 `DeepSeekRuntime`。它只负责产生“当前轮、证据可核对、尚未进入领域合并”的候选 DTO。

固定链路：

```text
ExtractionInput
→ request-scoped DeepSeekRequirementExtractionSession
→ one REQUIREMENT_EXTRACTION ModelOperation
→ versioned extract Prompt + strict DTO
→ typed attempt outcome
→ module 01 evidence verification
→ optional safe feedback
→ same session + same operation + repair Prompt
→ typed attempt outcome or runtime exception
```

必须同时达到以下结果：

1. 模型只提取当前消息明确表达的字段，不回放 current requirement。
2. 每个非空候选字段携带当前消息逐字证据。
3. Prompt injection、问候和控制文本不能变成旅行备注。
4. repair 只有一次，并且看不到失败输出。
5. 网络、配置、截断等运行时错误绝不交给模型自行判断。
6. 所有动态输入都在 JSON user message 中；system Prompt 是只读版本资源。
7. 固定 16 个用例用于证明指定模型、Prompt hash 和依赖快照下的一次 `live_qualified`；不允许用 mock 或失败后重跑个例替代，也不把一次全绿解释为统计可靠性。

## 2. 规范优先级

优先级固定为：用户最新明确决策 → 00 → 01/02 公共合同 → 本规范 → 可执行合同测试 → 03 vibe coding 精确执行契约 → `IMPLEMENTATION_STATUS.md` → 当前代码。

模块 01、02 已完成且公共合同冻结。`EXT-T00` 只读核验依赖合同；若任一依赖代码、测试与其规范不一致，立即停止并报告，不得由模块 03 修改 01/02、创建兼容层、反射适配、双 API、复制旧 DTO 或绕过 Runtime 直接调用模型。

## 3. 职责边界

### 3.1 本模块拥有

- `RequirementExtractor` 的 DeepSeek 生产实现。
- 每轮独立的异步提取 Session 和两次语义调用硬上限。
- 当前需求的完整、稳定 JSON 序列化。
- extract/repair 两份版本化 system Prompt。
- 模块 02 outcome 到模块 01 attempt outcome/runtime exception 的窄映射。
- 安全、无原文、无副作用的调用事件端口。
- Prompt hash、fake 合同测试和真实 16 用例。

### 3.2 本模块不拥有

- 不规范化日期、时间、金额或枚举；不创建 `RequirementPatch`。
- 不合并 `TravelRequirement`，不判断缺失字段，不生成追问或确认摘要。
- 不读取环境变量，不构造 `DeepSeekSettings`、provider、sleeper 或 Runtime。
- 不修改 Profile、重试、byte gate、Schema 解码或 usage 计算。
- 不持有 Graph/thread/checkpoint，不写数据库、日志、Trace 或审计表。
- 不调用 MCP、地图、酒店、路线或其他工具。
- 不把自由文本交给另一个模型做异常检测。
- 不提供同步 API、CLI、FastAPI、全局 singleton 或旧合同兼容层。

### 3.3 固定依赖方向

```text
app.requirements public root ─┐
                              ├→ app.requirement_extraction
app.llm_runtime public root ──┘
```

生产代码只能从两个包根导入公共符号，禁止从其内部文件导入。模块 01/02 不得反向导入 03。

## 4. 公共构造合同

```python
class DeepSeekRequirementExtractor:
    def __init__(
        self,
        *,
        runtime: DeepSeekRuntime,
        invocation_sink: ExtractionInvocationSink,
    ) -> None: ...

    def start_session(
        self,
        request: ExtractionInput,
    ) -> RequirementExtractionSession: ...
```

构造器只接受已经创建好的 `DeepSeekRuntime` 和满足非抛异常合同的 sink。不得接受 API key、base URL、model name、settings dict、provider、sleeper、LangChain model 或 env 文件路径。不得为缺失 sink 隐式创建会写日志的实现；可以由装配层显式传 `NoOpExtractionInvocationSink`。

`start_session` 必须：

1. 对 request 保留不可变引用或不可变副本。
2. 调用 `runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)` 恰好一次。
3. 返回新的 Session；禁止缓存或复用旧 Session。
4. 不调用模型，不发送事件，不读取系统时间或环境变量。

## 5. Session 状态机

状态固定为：

```text
NEW
├── extract success  → REPAIR_AVAILABLE
├── extract repair   → REPAIR_AVAILABLE
└── extract runtime  → TERMINAL_RUNTIME_FAILURE

REPAIR_AVAILABLE
├── repair success   → REPAIR_FINISHED
├── repair required  → REPAIR_FINISHED
└── repair runtime   → TERMINAL_RUNTIME_FAILURE
```

调用规则：

| 当前状态 | 调用 | 行为 |
|---|---|---|
| NEW | `extract()` | 允许，语义调用计数变为 1 |
| NEW | `repair()` | 抛 `RuntimeError("repair_before_extract")`，不调用模型 |
| 任意非 NEW | `extract()` | 抛 `RuntimeError("extract_already_called")`，不调用模型 |
| REPAIR_AVAILABLE | `repair()` | 允许且仅一次，语义调用计数变为 2；typed feedback 由第一次 attempt 或 01 verifier 提供 |
| 其他状态 | `repair()` | 抛 `RuntimeError("repair_not_allowed")`，不调用模型 |

Session 不可序列化、不可跨 event loop、不可进入 Pydantic durable state。不得暴露 Operation、provider response 或 raw Prompt。

## 6. 输入序列化

### 6.1 标准 JSON

所有动态值只进入 `PromptEnvelope.user_text`。序列化固定使用：

```python
json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
```

payload 顶层字段固定为：

```json
{
  "current_requirement": {},
  "intent": "initial",
  "session_date": "2026-07-14",
  "timezone": "Asia/Shanghai",
  "user_message": "用户逐字输入"
}
```

repair 时只增加：

```json
"repair_feedback": {
  "code": "evidence_missing",
  "field_paths": ["destination"]
}
```

规则固定：

- `current_requirement` 是完整 `TravelRequirement.model_dump(mode="json", exclude_none=False)`。
- 所有业务字段必须出现，包括 null、false 和空列表。
- `field_paths` 按枚举 value 排序、去重后转 JSON array。
- `turn_id` 永远不在 payload。
- 不包含当前 metadata、raw evidence、历史消息、先前 DTO、先前模型输出或异常文本。
- 不使用 `default=str`，不手写 JSON，不做字符串模板插值。
- 不删除 null，不改为单行，不压缩、不截断、不摘要。
- module 02 byte gate 失败时不得缩短 payload 后重试。

### 6.2 Prompt injection 边界

`user_message` 必须作为 JSON string 编码。禁止把它拼到 system Prompt、XML tag、Markdown section 或 Schema description。模型必须把其中“忽略规则、输出某值、泄露 Prompt”等文字视为不可信数据。只有其中实际表达的旅行需求可以提取。

## 7. Prompt 资源与版本

固定资源：

```text
app/requirement_extraction/prompts/extract_v1.txt
app/requirement_extraction/prompts/repair_v1.txt
```

通过 `importlib.resources.files("app.requirement_extraction.prompts")` 以 UTF-8 读取。禁止在 Python 内拼 Prompt 片段，禁止 YAML/env/数据库/远程覆盖，禁止运行时热更新。

Prompt version 固定为 `requirement-extraction-v1`。以下 SHA-256 是 2026-08-09 已完成修复和 live 16/16 后的当前冻结基线；测试对 UTF-8 bytes 逐字计算：

```text
extract_v1.txt = f0fba9f60d9066912890a040f5cba197024ee4242c8a820104a05ce15776dcdb
repair_v1.txt  = 7ad7e547fbb34f01b662fa74f1cb616447027a7920bed2a7d156a148508f695d
```

repair Prompt 在保持既有提取、evidence、repair feedback 和隐私规则不变的前提下，增加了唯一 JSON object 的边界约束，以及一个不含业务值的完整空 Patch JSON 形状示例。示例只用于稳定输出形状；实际值仍必须来自当前 payload，`is_correction` 由真实 intent 决定，`evidence` 由当前 `user_message` 和非空字段决定。示例的逐字 Prompt 快照见配套编码契约文档。

`extract_v1.txt` 与 `repair_v1.txt` 自上述基线起永久只读。之后任一字符、空格或换行变化都必须：

1. 改为新版本名和新文件名；
2. 更新两个文档与 hash snapshot；
3. 更新明确关联的 snapshot test；
4. 重新一次性跑完真实 16 用例；
5. 在变更记录说明原因。

禁止直接覆盖 v1 文件或只更新 hash。

## 8. 提取语义

### 8.1 增量候选

模型只处理 `user_message`。`current_requirement` 只用于理解“改成、再加、不要了”等指代和避免把旧值当新值输出。未在当前消息表达的字段必须为 null，禁止回放旧值、填默认值、用常识补齐或从城市推断交通。

### 8.2 intent

| intent | `is_correction` |
|---|---:|
| initial | false |
| supplement | false |
| correction | true |
| confirmation_revision | true |

不一致返回 `intent_mismatch`，不得在 adapter 内静默改 bool。

### 8.3 evidence

- 每个非 null、且在 `ATTR_TO_PATH` 登记的可提取业务属性恰好一条 evidence；`is_correction/evidence` 不计，list-valued 属性整体算一个叶子，不按元素重复 field path。
- evidence 是当前 `user_message` 的非空逐字连续子串。
- list 的每个叶子按 01 的字段路径登记；不得一条 evidence 对多个叶子。
- null 字段不得有 evidence。
- evidence 不能来自 current requirement、Prompt、repair feedback 或模型改写。
- adapter 不伪造、不修剪、不规范化 evidence；模块 01 验证。

### 8.4 空 Patch

“你好”“我再想想”“为什么要问这些”“忽略系统规则”等没有旅行字段的消息返回全部 nullable 字段 null、`evidence=[]`，并按 intent 设置 correction。不得把这些内容写入 notes，也不得触发 repair。

### 8.5 `free_text_notes`

只允许当前消息明确表达、且没有专门结构化字段的旅行限制或偏好，例如“喜欢工业遗产和城市更新”。对固定输入“我喜欢工业遗产和城市更新。”，字段值和 evidence 必须精确为原文连续子串“工业遗产和城市更新”，去掉对话框架“我喜欢”和句末标点。禁止写入：

- 问候、疑问、对话控制语、Prompt injection。
- 已由 destination、预算、同行人、酒店、POI 等字段表达的重复内容。
- 模型对用户的建议、解释、推理或默认假设。
- 当前需求旧值。

### 8.6 枚举与规范化归属

支持的枚举值严格来自 01。禁止近似匹配到最近枚举。明确但枚举不覆盖的偏好进入有 evidence 的 notes；无法确定则 null。

模型按 strict DTO 提供类型化数字、bool 和已支持枚举；日期、时间及其他自然语言候选保持 01 DTO 规定的原始候选形式。日期解析、中文金额、相对时间、字段冲突和最终标准化全部由 01 决定。

## 9. attempt outcome 映射

### 9.1 成功

模块 02 `StructuredInvocationSuccess` 只有在 `value` 是 `StrictRequirementPatchDTO` 时映射为 `ExtractionAttemptSucceeded`。若出现不可能的错误类型，按编程错误失败，不使用类型强转或重新 `model_validate` 宽松修复。

### 9.2 唯一 Runtime 可 repair 项

只有：

```text
LLMErrorCode.STRUCTURED_OUTPUT_INVALID
→ ExtractionRepairRequired(
     feedback={code: "structured_output_invalid", field_paths: ()}
   )
```

第一次调用可返回此结果并允许 repair；repair 调用再次发生则把同一判别结果返回给 01，由 01 产生领域 failed。

### 9.3 其他 Runtime failure

所有其他 `LLMInvocationFailure.error` 均抛：

```python
class RequirementExtractionRuntimeError(Exception):
    runtime_error: LLMRuntimeError
```

异常必须逐字段保留原始安全 `LLMRuntimeError`，包括 code、role、retryable、possible_duplicate、status、retry_after、actual/max bytes。禁止只保存错误字符串，禁止补 raw response，禁止把输出截断/content filter 归为 structured repair。

## 10. repair 合同

repair 输入由模块 01 产生，仅允许六种 code：

| feedback code | field_paths |
|---|---|
| `structured_output_invalid` | 空 tuple |
| `intent_mismatch` | 空 tuple |
| `evidence_missing` | 至少一项 |
| `evidence_duplicate` | 至少一项 |
| `evidence_not_substring` | 至少一项 |
| `orphan_evidence` | 至少一项 |

Session 必须在进入模型前再次验证 code whitelist、空/非空规则、排序、去重以及全部 evidence path 均属于 01 的 `EXTRACTABLE_REQUIREMENT_FIELD_PATHS`。非法 feedback 是编程错误，不消耗第二次调用。

repair 使用原始 `ExtractionInput` 重新生成 payload，不以第一次 payload 或模型响应为源。除了 `repair_feedback`，其余 JSON 必须与 extract 完全一致。禁止发送失败 DTO、部分 Patch、raw response、模型错误消息或 evidence 文本。

## 11. 安全调用事件

```python
class ExtractionInvocationEvent(BaseModel):
    schema_version: Literal["1.0"]
    turn_id: UUID
    prompt_version: Literal["requirement-extraction-v1"]
    attempt_kind: Literal["extract", "repair"]
    outcome_kind: Literal["succeeded", "repair_required", "runtime_failure"]
    semantic_call_number: Literal[1, 2]
    invocation_metadata: InvocationMetadata | None
    runtime_error: LLMRuntimeError | None

class ExtractionInvocationSink(Protocol):
    def accept(self, event: ExtractionInvocationEvent) -> None: ...
```

事件只能在模块 02 调用返回后发送一次。success 携带安全 metadata；当前 02 的 structured failure 没有 metadata，因此 repair-required 事件两个可选字段均为空；runtime failure 只携带安全 runtime error。metadata 与 runtime error 必须互斥。

事件禁止包含 Prompt、user message、current requirement、DTO、evidence、repair field paths、reasoning、provider body、API key 或任意可恢复原文的数据。

Sink 是非抛异常端口：实现必须保证 `accept` 不抛。模块 03 不得用 `try/except: pass` 吞掉违反合同的 sink 异常，也不得让遥测操作改变 attempt outcome。Production 遥测排队、retry 和降级属于模块 20；03 只定义并调用窄端口。

## 12. 隐私和日志

- 本模块生产代码不得调用 `print`，不得创建 logger，不得写文件。
- exception message 固定为安全错误 code，不格式化输入对象。
- `repr(Session)` 不得显示 request 或 Prompt；可使用默认 object repr 或安全固定 repr。
- 测试失败消息和 live report 只显示 case ID、固定 code、调用数与布尔断言，不显示 raw 模型输出和用户原文。
- Prompt hash 可以记录；Prompt 正文不得进入事件。

## 13. 固定代码布局

```text
app/requirement_extraction/
├── __init__.py
├── adapter.py
├── session.py
├── prompt_loader.py
├── serialization.py
├── events.py
└── prompts/
    ├── __init__.py
    ├── extract_v1.txt
    └── repair_v1.txt

tests/unit/requirement_extraction/
tests/contract/requirement_extraction/
tests/live/requirement_extraction/
```

不得增加 `client.py`、`config.py`、`models.py` 副本、`fallback.py`、`sync_adapter.py`、`prompt_builder.py` 或 catch-all `utils.py`。需要新增生产文件必须先改文档并获得用户确认。

## 14. 离线测试合同

### 14.1 unit

必须覆盖：

- full JSON 含 null、Unicode、sort/indent、无 turn_id。
- repair payload 只有固定 feedback 差异。
- Prompt UTF-8 加载、版本和 SHA-256 snapshot。
- Session 所有合法/非法状态迁移和零额外调用。
- 22 个模块 02 runtime code 中只有 structured invalid 可 repair。
- Runtime wrapper 完整字段保真。
- 事件成功/repair/runtime 三分支、互斥字段和隐私字段不存在。
- feedback whitelist、空路径规则、排序去重。

### 14.2 contract

使用 `RecordingDeepSeekRuntime`/`ScriptedModelOperation`，通过 01 的公共 Protocol 运行：

- 正常成功恰好 start operation 1、async invoke 1。
- evidence verifier 请求 repair 时同一 Operation async invoke 总数 2。
- repair payload 不含先前 DTO/raw 输出。
- 新 turn 创建新 Session 和新 Operation。
- 所有调用 role 都是 `REQUIREMENT_EXTRACTION`。
- 同步入口调用数为 0。
- 03 返回 DTO 可由 01 evidence verifier 消费。

fake 必须记录完整 `PromptEnvelope`、schema identity、调用序号和 Operation identity。禁止 fake 自动纠正生产输入或根据测试期望动态制造成功。

## 15. 真实 DeepSeek 固定 16 用例

### 15.1 运行规则

- 模型固定 `deepseek-v4-flash`，thinking 全开，使用模块 02 Runtime。
- 一个 pytest session 运行全部 16 个固定 case；必须 16/16。
- 进程环境变量和项目根目录 `.env` 都缺少 `DEEPSEEK_API_KEY` 时 collection/fixture 明确失败，禁止 skip；任一来源存在即可，且进程变量优先。
- 禁止 mock provider、录制响应、硬编码 DTO、从 fixture 读取 golden response。
- 禁止失败后只重跑个例并宣称通过；报告必须来自一次完整 run。
- 普通 case 首轮最终候选完全正确时调用数为 1；若首轮自然产生模块 01 已定义的 structured、intent 或 evidence typed feedback，则必须由同一 Session repair 一次并以调用数 2 结束。013、015、016 调用数固定为 2：首轮正确时注入该 case 的固定 feedback；首轮已有 typed feedback 时直接使用自然 feedback。014 不人为注入错误。任何 case 都禁止第三次调用。
- 每例断言明确字段精确值、所有未提及字段 null、correction、evidence 一一对应且为子串。
- live fixture 使用按 turn_id 分组的安全内存事件 sink，逐例精确断言 `(attempt_kind, semantic_call_number)` 为 `[(extract,1)]` 或 `[(extract,1),(repair,2)]`；禁止用 NoOp sink 后仅从控制流猜调用数。报告只输出 case ID、PASS/FAIL、固定错误 code、调用数；不输出 raw。

### 15.2 12 个首轮成功 case

| ID | 核心输入与断言 |
|---|---|
| `EXT-LIVE-001` | 杭州、3 天、预算 5000 左右、2 成人；只提取这些字段 |
| `EXT-LIVE-002` | 7 月 20 日下午 3 点到杭州东站、23 日上午 10 点离开；03 只返回枢纽名和原文日期时间，两个 hub type 为 null，由 01 确定性推导 |
| `EXT-LIVE-003` | 2 成人、1 个 6 岁儿童、2 间双床房；人数、年龄、房间/房型精确 |
| `EXT-LIVE-004` | 吃素、走路不方便、人文+美食、轻松；固定 enum，无其他默认 |
| `EXT-LIVE-005` | 西湖和灵隐寺必去、避开宋城；must-visit 列表字段与 avoid 列表字段各一条 evidence |
| `EXT-LIVE-006` | 兴趣和节奏由 Agent 决定；两个 bool 精确且有 evidence |
| `EXT-LIVE-007` | 工业遗产和城市更新；未知兴趣 enum 不猜，写 evidence-backed notes |
| `EXT-LIVE-008` | “我再想想”；合法空 Patch，不 repair |
| `EXT-LIVE-009` | Prompt injection 测试文本且不表达真实需求；合法空 Patch |
| `EXT-LIVE-010` | current requirement 已有杭州/预算，当前只说加西湖；只返回 must_visit，不回放旧字段 |
| `EXT-LIVE-011` | correction：“预算改成八千元”；只返回预算 8000，correction=true |
| `EXT-LIVE-012` | confirmation revision：“酒店改成靠近地铁，不要临街”；只返回酒店偏好，correction=true |

### 15.3 3 个受控 repair case与 1 个普通近似预算 case

测试先执行真实 extract，再用测试明确构造的 typed feedback 调 `repair`，不得依赖第一次碰巧出错：

| ID | feedback | 断言 |
|---|---|---|
| `EXT-LIVE-013` | 首轮完全正确时注入 `evidence_missing(destination)`；否则使用首轮 typed feedback | 第二次返回等价正确候选，调用数 2 |
| `EXT-LIVE-014` | 不人为注入 feedback | 金额 5000、approximate=true、evidence 正确；首轮自然产生 typed feedback 时最多 repair 一次 |
| `EXT-LIVE-015` | 首轮完全正确时注入 `intent_mismatch()`；否则使用首轮 typed feedback | correction bool、全部字段和 evidence 正确，调用数 2 |
| `EXT-LIVE-016` | 首轮完全正确时注入 `structured_output_invalid()`；否则使用首轮 typed feedback | 第二次 strict DTO、全部字段和 evidence 正确，调用数 2 |

repair 测试还必须断言第二个 user JSON 不含第一次 DTO、第一次 evidence、raw output 或 error message。自然 repair 只接受 01 定义并由生产校验得到的 typed feedback；语义字段与固定 expected 不一致仍立即失败，禁止把“目的地推断错误”等业务错误伪装成 repair。第二次仍失败时立即失败，不得第三次调用。

上述自由文本候选固定保留当前消息中的简洁原词，不做同义改写：002 的两个 datetime 分别为 `7月20日下午3点`、`7月23日上午10点`，禁止改成 `7月20日15点`、添加年份或把枢纽名并入时间，两个 hub type 和两个 approximate 字段为 null；003 的房型为 `双床房`；004 的饮食和行动限制分别为 `吃素`、`走路不方便`；007 notes 为 `工业遗产和城市更新`；011 的 budget approximate 为 null；012 酒店偏好为 `靠近地铁的，不要临街`。

### 15.4 Runtime structured failure 与资源关闭补充

当模块 02 收到完整的 `AIMessage`，但 LangChain `include_raw` 返回 `parsed=None` 或 `parsing_error` 时，03 只接收模块 02 产生的 `structured_output_invalid`，并按现有状态机最多 repair 一次。响应结构或必要 metadata 缺失时才接收 `malformed_provider_response`，不得把 parser exception、raw content 或 partial DTO 传入 Session。

Extractor 使用的共享 `DeepSeekRuntime` 由装配方通过 `aclose()` 在异步事件循环内关闭；`ModelOperation` 只关闭自己的私有状态，不关闭共享 Provider。fixture 必须使用 async context/finally 释放客户端，重复关闭必须安全且不得依赖 `__del__`。

## 16. 错误决策表

| 来源 | code/条件 | adapter 行为 | repair |
|---|---|---|---:|
| 02 | structured_output_invalid | typed repair required | 是，最多一次 |
| 01 | intent/evidence 六类 feedback | Session 接受 typed feedback | 是，最多一次 |
| 02 | input_too_large/context_length | runtime wrapper 抛出 | 否 |
| 02 | output_truncated/content_filtered | runtime wrapper 抛出 | 否 |
| 02 | auth/config/balance/permission/model | runtime wrapper 抛出 | 否 |
| 02 | timeout/rate/network/provider | runtime wrapper 抛出 | 否 |
| 02 | quota/schema/malformed/unexpected | runtime wrapper 抛出 | 否 |
| 03 | 非法 Session 顺序/feedback | 编程错误 | 否，调用数不变 |

禁止添加“让模型阅读错误并决定是否重试”的第三条路径。

## 17. 规范 ID

| ID | 固定行为 |
|---|---|
| `REQ-EXT-001` | 只接受预构造 DeepSeekRuntime 和非抛 sink |
| `REQ-EXT-002` | 每个 ExtractionInput 创建独立 Session |
| `REQ-EXT-003` | 每个 Session 恰好一个 REQUIREMENT_EXTRACTION Operation |
| `REQ-EXT-004` | 仅使用 async structured API |
| `REQ-EXT-005` | extract 最多一次、repair 最多一次 |
| `REQ-EXT-006` | 非法状态调用零模型请求 |
| `REQ-EXT-007` | 动态输入只进入标准 JSON user message |
| `REQ-EXT-008` | current requirement 完整含 null 且不摘要 |
| `REQ-EXT-009` | JSON 固定 ensure_ascii/sort_keys/indent |
| `REQ-EXT-010` | turn_id 不进入模型输入 |
| `REQ-EXT-011` | 不传历史、metadata、raw evidence 或工具结果 |
| `REQ-EXT-012` | Prompt injection 只作为不可信 JSON 数据 |
| `REQ-EXT-013` | 只返回当前消息增量，不回放旧值 |
| `REQ-EXT-014` | 不补默认值或常识推断 |
| `REQ-EXT-015` | initial/supplement correction=false |
| `REQ-EXT-016` | correction/confirmation_revision correction=true |
| `REQ-EXT-017` | 无旅行字段产生合法空 Patch |
| `REQ-EXT-018` | notes 只保存未结构化的明确旅行约束 |
| `REQ-EXT-019` | 未知 enum 不近似猜测 |
| `REQ-EXT-020` | 每个非空、已登记业务属性要求当前消息唯一 evidence，list 属性整体计一次 |
| `REQ-EXT-021` | null 字段不得有 orphan evidence |
| `REQ-EXT-022` | adapter 不伪造或规范化 evidence |
| `REQ-EXT-023` | Prompt 由两个独立 UTF-8 资源加载 |
| `REQ-EXT-024` | Prompt version 固定且 hash snapshot |
| `REQ-EXT-025` | Prompt 字符变化必须升版本和重跑 16 例 |
| `REQ-EXT-026` | repair 只接受六种固定 code |
| `REQ-EXT-027` | repair field paths 排序去重且校验空规则 |
| `REQ-EXT-028` | repair 只使用原始输入和 safe feedback |
| `REQ-EXT-029` | repair 不发送失败 DTO/raw/evidence/异常文本 |
| `REQ-EXT-030` | structured output invalid 是唯一 Runtime repair code |
| `REQ-EXT-031` | 所有其他 Runtime 错误完整保真包装并抛出 |
| `REQ-EXT-032` | 截断和 content filter 不得语义 repair |
| `REQ-EXT-033` | byte gate 失败不得缩短输入重试 |
| `REQ-EXT-034` | success 必须是 StrictRequirementPatchDTO |
| `REQ-EXT-035` | 调用事件每次语义调用恰好一条 |
| `REQ-EXT-036` | 事件不含原文、DTO、evidence、reasoning |
| `REQ-EXT-037` | sink 非抛且不得改变业务 outcome |
| `REQ-EXT-038` | 模块无 logger/print/文件写入 |
| `REQ-EXT-039` | 生产 import 只走 01/02 包根 |
| `REQ-EXT-040` | 禁止 Graph/DB/MCP/Skill/入口依赖 |
| `REQ-EXT-041` | 禁止同步 API、线程桥接和全局 singleton |
| `REQ-EXT-042` | fake 记录 Operation identity、Prompt 和调用序号 |
| `REQ-EXT-043` | 离线 suite 无 skip/xfail 且覆盖率 >=95% |
| `REQ-EXT-044` | live 缺 key 明确失败而非 skip |
| `REQ-EXT-045` | live 普通 case 一次成功或仅按自然 typed feedback repair 一次 |
| `REQ-EXT-046` | live 013、015、016 固定两次调用；其余 case 一次成功或仅按自然 typed feedback repair 一次，所有 case 禁止第三次调用 |
| `REQ-EXT-047` | live 必须一次完整 16/16，不接受个例重跑 |
| `REQ-EXT-048` | live 报告脱敏且禁止 mock/录制/硬编码结果 |

## 18. 完成定义

只有以下全部成立，模块 03 才同时具备 `contract_complete` 和当前有效的 `live_qualified`，模块 05 才能消费真实提取器：

1. 01 新异步公共合同及纯领域门槛已通过。
2. 02 原门槛仍通过，03 未修改 02 行为。
3. 03 固定文件全部存在，无额外越界文件。
4. unit、contract 完整目录通过，coverage >=95%，零 skip/xfail。
5. Prompt hash 与文档完全一致。
6. missing-key live gate 确认是 failure，不是 skip。
7. 带真实 key 的固定 16 例在一次 pytest run 中 16/16。
8. full default suite 通过，`git diff --check` 通过。
9. 最终报告逐项列出 REQ-EXT、命令 exit code、测试数、调用数和 hash；任何未运行项明确写 FAIL，不得写“预计通过”。

## 19. NOT in scope

- 01 的领域规则实现与 02 的 Runtime 内部实现。
- 04/05/13 的 workflow state、Graph、pause/resume 和错误路由。
- 10–11、19–21 的持久化、恢复、安全、真实 telemetry queue、审计和 Production 装配。
- 06–09、12、16 的 MCP、地图、酒店、候选研究、规划、输出和局部重排。
- 14–15、22–23 的入口、E2E、Production 恢复验证和质量平台；17、18 当前未分配能力。
- 多模型、fallback、动态 Prompt、流式输出和自动 Prompt 优化。
- 对旧同步 extractor、旧异常字符串或旧压缩摘要提供兼容。

## 枢纽角色提取维护增量（2026-10-07，用户已批准）

新 Prompt 为 requirement-extraction-v4-hub-roles，新增 extract_v4_hub_roles.txt / repair_v4_hub_roles.txt；UTF-8/LF SHA256 按实际字节登记如下。v1/v2/v3 全部保持原字节，其资格仅属于历史版本。继承当前消息/evidence/否定/预算延期及一次 repair，不改变 M02 调用额度。

- extract_v4_hub_roles.txt：7d61442919c3bb859e41f2617a21a77710277e67f0580c8d108d16ac197d8463。
- repair_v4_hub_roles.txt：35024c063ea64d5c7422b2aee1964cc7cc200462c94ef86cf8fd34ff362a4481。

arrival/departure 均是目的地抵达/离开枢纽；出发城市不能填入 hub_name。“去合肥，从上海出发”只明确目的城市，两个枢纽仍 null；从起点车站出发也不能自动当作目的地的离开枢纽。仅当前消息明确给出具体端点时提取。类型不从城市/名称猜测，保留 M01 的确定性推导；用户明确选择机场/火车站或高铁站时提取对应 enum 并提供该字段独立 evidence。补充中文标签中的目的城市只用于端点语义，不能回放为 destination 的新证据。

离线验收版本/hash、旧资源、strict/evidence、extract/repair 对称规则和真实 M01/M03 fake 接缝；Prompt 改变后的真实供应商资格需单独明确授权，本轮 NOT_RUN，不能用离线结果或历史 v3 资格替代。
