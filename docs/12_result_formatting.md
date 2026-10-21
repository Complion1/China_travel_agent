# Module 12：结构化结果与中文攻略设计

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 新网页需求 1.1 使用 zh-guide-v2-active-inputs 模板，停用人数、房间及限制字段允许空值且不展示；旧需求仍使用 zh-guide-v1，保留原文、源绑定摘要和完整字段要求。日块与索引的 Artifact schema 2.0 保持。版本增量及验收依据见 [M14 有效输入与历史兼容](14_web_application.md#有效输入与历史兼容)。

> 本次新版结果来源只接受itinerary/6.0，公共结果及日块/索引为2.0；保留中文渲染与公共交通步骤。M12仍不调用模型，原完整Gate/交付规则不放宽。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 2026-09-26 用户批准预算金额功能延期：[当前修订与执行合同](12_budget_amount_deferral.md) 优先适用。金额不采集、不作为确认/输出门槛；API 调用额度不变。旧预算案例属于历史合同，不代表本期验收项。

> 文档属性：Module 12 规范草案，待用户批准。设计完成不代表代码实施、测试或模块交付完成。
>
> 配套执行契约：[12_result_formatting_vibe_coding.md](12_result_formatting_vibe_coding.md)。
>
> module_id：12；component_type：Component；test_class：pure-domain。直接依赖以 [路线图 §2](IMPLEMENTATION_ROADMAP.md) 为唯一真源，本文展开为 01、04、07、08、09、10、11。

## 1. 权威、范围与术语

权威顺序：用户最新明确决定 → [主设计](00_agent_master_design.md) → [路线图](IMPLEMENTATION_ROADMAP.md) → 所消费上游的冻结合同 → 本文 → 配套执行文档 → 状态说明 → 当前实现。公共实现是必须核验的事实，不能把偶然实现行为变成业务规范；冲突先列证据并停止相关变更。

本文拥有结果投影、证据关联、中文模板、按天分块、汇总索引及只读结果服务。模块 13 拥有操作标识、写入编排、主图与交付终态；模块 14 拥有应用入口和退出码。Module 12 不连接供应商、不调用模型、不重排、不重新执行 M09 的完整规划 Gate，不保存结果、不启动恢复。

“日块”保存一天的结构化数据和对应 Markdown 快照；“索引”保存全局展示数据、所有日块的有序引用和首尾 Markdown；“完整结果”必须由合法索引及其全部日块组成。“生成成功”“索引可保存”均不等于已持久化或 demo_ready。

后文中 Mxx 表示 Module xx；D-12-xx 为用户决策，RF-xx 为可追踪验收项。现有文件是读取依据；§14 的新文件是未来实施白名单，不冒称已经存在。

## 2. 已确认决策与继承要求

| ID | 本轮已确认决定 | 落点 |
|---|---|---|
| D-12-01 | 增加 M08 只读直接依赖，只经公共 DTO/Repository 读取候选及证据 | §3、§7 |
| D-12-02 | 不提供 incomplete 部分行程预览 | §4、§11 |
| D-12-03 | 天气功能及真实天气证据合同延期，仅显示“天气未获取” | §9 |
| D-12-04 | 同时保存结构化结果与确定性中文 Markdown，记录格式版本 | §5、§8 |
| D-12-05 | M12 生成及核验，M13 编排保存 | §4、§10 |
| D-12-06 | 正文短标签和来源编号，风险就地提示，文末集中列来源 | §8 |
| D-12-07 | 路线同时显示百度估算耗时和日程预留时间 | §6、§8 |
| D-12-08 | 地图链接及证据合同延期 | §9 |
| D-12-09 | 补充要求按已确认字段回显，明确未逐项验证 | §6、§8 |
| D-12-10 | 输入为已验证工作流状态及显式行程 Ref | §4、§7 |
| D-12-11 | M12 不叠加自动重试 | §11 |
| D-12-12 | 按天分块并增加汇总索引；采用成熟实现模式 | §5、§10 |
| D-12-13 | 演示优先；覆盖率用于诊断，不设数字自动阻断 | §13 |
| D-12-14 | 后续 Python 检查、实施验证和正式验收全部在 Docker 中 | 配套 §6 |

D-12-12 取代“整份结果只存一个 Artifact、整体超限直接拒绝”的备选方案。日块和索引仍分别遵守上游单件及仓库总容量规则。

直接继承、不重新选择：

- 主设计 §4.1～4.3、§6～9：不新增事实，费用分类未核验，来源时间真实，终态只由 M13 判定。
- M09 §3.3、§9.5、§10.4、§16～18：completed 仅代表首版范围；营业未知仍须复核；路线为供应商获取时的估算；备选未进入主路线；保留固定范围说明。
- M04 §8～11、§17～21：精确注册模型、fresh strict 校验、规范字节、Ref 七字段、幂等和容量。
- M10 §10～13、M11 §17：只借用公共仓储；不查 SQL、checkpoint、journal、lease 或历史，不触发 recover。
- 历史 M09/M11 演示例外继续如实引用状态记录，不重阻本轮设计，也不改称原冻结门槛 PASS。

以下实现选择从上述决定推导，尚不构成文档最终批准：不可变日块、最后写索引、版本化确定性模板、显式输入、无自动重试、按引用加载而非扫描最新记录。独立审查若要求改变已确认业务决定，必须先征询用户。

## 3. 上游读取映射与导入边界

| 目的 | 精确公共来源 | 读取与边界 |
|---|---|---|
| 确认需求 | `app.requirements.ConfirmedTravelRequirement`；`app.workflow_contracts.TravelSessionState` | 使用 confirmed requirement；不复制 metadata、NormalizationRecord、完整用户消息 |
| 酒店 | `HotelSelectionArtifact → HotelDiscoveryArtifact → HotelPlaceSearchArtifact`，均从 `app.hotel_selection` 导入 | selection 的 candidate_set_ref 指酒店候选集，不是 M08 候选集 |
| 候选身份及营业原文 | `app.candidate_research.CandidateResearchArtifact`、`CandidateResearchPlaceObservationArtifact` | 只读 record.state、core、observation_refs 及成功 observation；不调用研究服务 |
| 已完成行程 | `app.itinerary_planning.ItineraryPlanningArtifact` | record_type 必须 completed；消费公开 Artifact 的嵌套字段，不导入私有 Record/Snapshot 类或转换函数 |
| 主路线、枢纽、备选距离 | 同包公开 `ItineraryPlanningDirectionsObservationArtifact`、`ItineraryPlanningHubGeocodeObservationArtifact`、`ItineraryPlanningRouteMatrixObservationArtifact` | 只取所引用成功证据，核对索引、端点和数值 |
| 观测所属轮次 | 同包公开 `ItineraryPlanningModelActionArtifact` | 仅核对被使用 Observation 的 Action Ref 与 request_digest；不展示、持久化或记录模型元数据 |
| 严格仓储及状态 | `app.workflow_contracts` 的 ArtifactRepository、ArtifactRef、ArtifactSchemaRegistry、ArtifactWriteRequest、状态 codec | get 的 expected_model 必须是上表原公共类，不能复制同形类代替 |
| 持久运行 | M10 的 runtime.repository；M11 交给 M13 的 node-bound Repository | M12 只收到 M04 Protocol；不构造/关闭 Runtime，不直接导入两模块实现 |

必读规范：01 §3/6/11/13/16；04 §3/5～11/15/17～21/23；07 §4/6～9/17/19/21/23/25；08 §3/4/17～22、附录 A/B 中 candidate-set、place-observation 和摘要规则；09 §3～4/7/16～20/21/23、B.2/B.4/B.5；10 §5～7/10.1/10.5/10.6/12～13/18～20；11 §5/17～18/20。引用均为仓库中对应编号的规范文件。

生产包只导入标准库、Pydantic 及上述 01/04/07/08/09 公共包根；不得直接导入 M02、M03、M06、LangGraph、psycopg、HTTP/MCP 客户端、上游私有模块。不因上游 DTO 内部使用 M06 类型而把 M06 SDK 接入本模块。坐标和地图枚举值显式投影为本文本地标量。

## 4. 新公共 API

生产包定为 `app.result_formatting`，名称描述“结果格式化”的单一职责。入口不接受自由 dict、任意文件路径、配置环境或数据库连接。

### 4.1 门面及签名

包根精确导出：

```text
RESULT_FORMATTING_SCHEMA_VERSION             # "1.0"
RESULT_FORMATTING_TEMPLATE_VERSION           # "zh-guide-v1"
RESULT_FORMATTING_ARTIFACT_REGISTRATIONS     # 恰好 §5 两项
ResultFormattingRequest
ResultReadRequest
ResultFormattingPrepared
ResultIndexReady
ResultFormattingLoaded
ResultFormattingFailed
ResultFormattingOutcome
ResultIndexOutcome
ResultReadOutcome
ResultFormattingFailureCode
ResultDayArtifact
ResultIndexArtifact
ResultFormattingService
ResultFormattingUsageError
build_result_artifact_write_request
```

```python
class ResultFormattingService:
    def __init__(self, *, artifact_repository: ArtifactRepository) -> None: ...
    async def prepare(self, request: ResultFormattingRequest) -> ResultFormattingOutcome: ...
    async def build_index(
        self, request: ResultFormattingRequest, *, day_refs: tuple[ArtifactRef, ...]
    ) -> ResultIndexOutcome: ...
    async def load(self, request: ResultReadRequest) -> ResultReadOutcome: ...

def build_result_artifact_write_request(
    *, operation_id: OperationId,
    artifact: ResultDayArtifact | ResultIndexArtifact,
) -> ArtifactWriteRequest: ...
```

构造无 I/O。服务无外部资源、无 close/aclose、无恢复循环；借用 Repository，不改变其生命周期。所有读取缓存只在单次调用内存在，同一 ArtifactRef 七字段完全相同才复用；同 ID 不同 Ref 立即失败。不得跨 run 或调用复用缓存。

### 4.2 请求和 outcome

请求模型 config 为 strict/frozen/extra forbid；所有字段 required，无默认：

| DTO | 字段 |
|---|---|
| ResultFormattingRequest | schema_version:Literal["2.0"]；state:TravelSessionState；itinerary_ref:ArtifactRef |
| ResultReadRequest | schema_version:Literal["2.0"]；state:TravelSessionState；index_ref:ArtifactRef |
| ResultFormattingPrepared | outcome_type:Literal["prepared"]；schema_version；days:tuple[1..60,ResultDayArtifact] |
| ResultIndexReady | outcome_type:Literal["index_ready"]；schema_version；index:ResultIndexArtifact |
| ResultFormattingLoaded | outcome_type:Literal["loaded"]；schema_version；index_ref:ArtifactRef；index:ResultIndexArtifact；days:tuple[1..60,ResultDayArtifact]；markdown:str |
| ResultFormattingFailed | outcome_type:Literal["failed"]；schema_version；code:ResultFormattingFailureCode |

三个 Outcome 分别是 Prepared/Failed、IndexReady/Failed、Loaded/Failed 的 discriminator=outcome_type union。schema_version 均 Literal["2.0"]。payload 字段 repr=False。Failed 不携带自由 detail、底层异常、局部正文或猜测的 retryable 标志。

prepare 只生成日块；build_index 按同一请求重新确定性投影，并读取已保存日块进行逐一比较后生成索引，不依赖私有内存句柄。这样调用方无法通过自行构造 Prepared 绕过证据检查。load 先读取索引及所有日块，再按索引中的 source_itinerary_ref 对相同状态重建并比对结果；不调用研究或规划。初版只支持本文 schema/template 版本，未知版本明确失败，不用新版模板改写旧正文。

### 4.3 工作流状态要求

使用 M04 公共 serialize_travel_session_state 后 parse_travel_session_state_json 获得独立 fresh state；M04 codec 自带原始 Python-tree 复验，不先对原对象做 JSON 洗白。确认 outcome 必须为 M01 ConfirmedTravelRequirement，state 保留其合法一致性。

prepare/build_index 的 itinerary_ref、load 的 index_ref 都必须与 state.run_id 相同，且七字段完全相同地出现在 state.artifact_refs。load 还要求索引的 source_itinerary_ref 在 state 中，执行相同源请求检查。仅接收 delivery_status 为 in_progress/demo_ready/degraded，phase 为 itinerary_planning/output_formatting 的状态；其他状态返回 input_not_ready，不从 failed/cancelled/awaiting_user 包装正式攻略。M13 后续接线须满足此输入前置条件；M12 不修改状态。

状态列表不是“最新行程选择器”。同 run 可以保留多个 Artifact；只读取调用方指定且关联一致的那一份。子证据 Ref 可以只存在于父 Artifact 的有界目录中，不要求所有子 Ref 重复塞入 Graph state。

## 5. Artifact、日块与汇总索引

### 5.1 注册与基础规则

恰好注册：

| type/version | 公共模型 |
|---|---|
| result_formatting.day / 1.0 | ResultDayArtifact |
| result_formatting.index / 1.0 | ResultIndexArtifact |

所有可达本地嵌套 DTO 定义于 artifacts.py，严格遵守 M04 注册合同：config 精确 strict=True/frozen=True/extra="forbid"，每字段 required，nullable 显式 null；无默认、alias、serializer、computed field、变换型 validator、任意 metadata 或自由 dict。validator 只断言，不排序/截断/补值。公共入口创建投影可以显式转换类型，不能由 validator 偷改。

表中 tuple[a..b,T] 表示长度及元素类型；其他约束复用表中定义。schema_version 在每个 Artifact 顶层为 Literal["2.0"]，嵌套 DTO 不重复加版本字段。UTC datetime 必须有零偏移；中国日程用独立 timezone="Asia/Shanghai" 与当地 date，展示转换不能改变时刻。

常用标量：ShortText 为 1..512 Unicode code points；RequirementText 为 1..500；StableText 为 1..128；Digest 为 `^sha256:[0-9a-f]{64}$`；整数拒绝 bool，最大安全整数 2**53-1；浮点有限。所有字符串拒绝孤立 surrogate，不能借大小限制静默截断。

### 5.2 证据与地点目录

| DTO | 全部字段 |
|---|---|
| ResultCoordinate | coordinate_system:Literal["bd09ll"]；latitude:finite float[-90,90]；longitude:finite float[-180,180] |
| ResultSource | source_id:int[1..1024]；artifact_ref:ArtifactRef；source:Literal["baidu_maps_mcp"]；capability:Literal["place_search","place_details","geocoding","route_matrix","directions"]；retrieved_at_utc:datetime；verified:Literal[True] |
| ResultPlace | place_id:int[1..131]；kind:Literal["hotel","candidate","arrival_hub","departure_hub"]；uid:ShortText\|None；name:ShortText；address:ShortText；coordinate:ResultCoordinate；identity_source_id:int[1..1024]；business_hours:tuple[0..16,ShortText]；business_hours_source_id:int[1..1024]\|None |
| ResultInterval | start_at_utc:datetime；end_at_utc:datetime；timezone:Literal["Asia/Shanghai"] |
| ResultVisit | visit_id:StableText；place_id；visit_kind:Literal["attraction","lunch","dinner","attraction_lunch","attraction_dinner"]；interval:ResultInterval；duration_source:Literal["planning_estimate"]；business_hours_state:Literal["compatible","unknown"]；business_source_ids:tuple[0..8,int[1..1024]]；requires_verification:bool |
| ResultRoute | leg_id:StableText；origin_place_id；destination_place_id；interval；transport_semantic:Literal["walking","cycling","public_transit","ride_hailing","private_car"]；route_mode:Literal["walking","riding","transit","driving"]；distance_m:int>=0；duration_s:int>=0；scheduled_minutes:int>=0；source_id；alternative_index:int[0..2] |
| ResultBackup | backup_kind:Literal["attraction","lunch","dinner"]；core_visit_id:StableText；place_id；matrix_mode:Literal["walking","riding","driving"]；distance_m:int[0..3000]；source_id；business_hours_state；business_source_ids；requires_verification |
| ResultTransfer | transfer_kind:Literal["arrival","departure"]；hub_place_id；hotel_place_id；route:ResultRoute；processing_minutes:int>=30；approximate_extra_minutes:Literal[0,30] |

未重复写值域的 place_id/source_id/interval/business 字段沿本表同名定义。上述模型只保存展示所需字段，不复制 step instruction、模型判断正文、预算账本或 candidate 全量对象。营业 compatible 只表示 M09 带证据判断，不宣传“实时开门保证”；unknown 必须 requires_verification=True。

地点目录顺序：酒店 → arrival hub → departure hub → 行程实际使用的 candidate，最后一组按 M08 admission_ordinal 升序。两个枢纽即使同坐标也保留不同角色。候选以 ordinal+uid 关联，不按名称合并；酒店和候选相同 UID 也不合并角色。最多酒店 1、枢纽 2、候选 128。name/address/coordinate 来自身份事实；枢纽 name 使用 confirmed hub_name，address 使用成功 geocode formatted_address，并在展示说明名字由用户确认、位置由百度地理编码。

来源目录按 (artifact_type, schema_version, operation_id.phase.value, operation_id.logical_step, artifact_id.hex) 字典序排列，七字段相同的 Ref 只分配一个 ID；一个 Ref 的 provenance 若冲突即失败。序号从 1 连续递增。目录只包含实际用于身份、营业、主路线和备选距离的成功证据，不收集无关模型 Action。来源编号用于关联，不是外部 URL。

ResultSource 的精确字段映射如下，不能假设上游三种 Artifact 有同形 provenance：

| 上游成功证据 | source / capability / retrieved_at_utc / verified |
|---|---|
| M07 HotelPlaceSearchArtifact | 顶层 source / 固定 place_search / 顶层 retrieved_at / 顶层 verified |
| M08 CandidateResearchPlaceObservationArtifact | result.provenance.source / capability.value / retrieved_at / verified，均来自 result.provenance |
| M09 Hub/Matrix/Directions Observation | provenance.source / capability.value / retrieved_at_utc / verified，均来自 provenance |

以上均须验证成功分支及 verified=True；artifact_ref 使用该证据自身的完整 Ref。来源获取时间不从 Ref.created_at 或当前时钟补值。

### 5.3 需求和全局核心

| DTO | 全部字段 |
|---|---|
| ResultEndpoint | hub_name:ShortText；hub_type:Literal["airport","railway_station"]；local_datetime:str（M01 YYYY-MM-DD HH:MM）; is_approximate:bool |
| ResultRequirement | destination:str[1..64]；arrival:ResultEndpoint；departure:ResultEndpoint；sightseeing_start_date:date；sightseeing_end_date:date；sightseeing_days:int[1..60]；hotel_check_in_date:date；hotel_check_out_date:date；hotel_nights:int>=1；adults_count:int[0..100]；children_count:int[0..100]；children_ages:tuple[0..100,int[0..17]]；seniors_count:int[0..100]；total_people:int[1..100]；room_count:int>=1；budget_total_cny:finite float>0\|None；budget_is_approximate:bool；budget_scope:Literal["destination_only"]；mobility_constraints:RequirementText\|None；dietary_restrictions:RequirementText\|None；room_type_requirements:RequirementText\|None；hotel_preferences:RequirementText\|None；transport_preferences:RequirementText\|None；daily_time_preferences:RequirementText\|None；free_text_notes:RequirementText\|None；must_visit_places:tuple[0..100,RequirementText]；avoid_places:tuple[0..100,RequirementText] |
| ResultCore | requirement:ResultRequirement；effective_pace:Literal["轻松","标准","紧凑"]；places:tuple[3..131,ResultPlace]；sources:tuple[1..1024,ResultSource]；arrival_transfer:ResultTransfer；departure_transfer:ResultTransfer；unknown_business_visit_count:int[0..360]；unknown_business_backup_count:int[0..180]；weather_status:Literal["not_collected"]；map_status:Literal["deferred"]；cost_status:Literal["unverified"]；scope_notice_code:Literal["m09_first_release"] |
| ResultDayData | day_no:int[1..60]；date:date；day_kind:Literal["full","partial","transit_only"]；theme:str[1..40]；open_intervals:tuple[0..16,ResultInterval]；visits:tuple[0..6,ResultVisit]；route_legs:tuple[0..7,ResultRoute]；backups:tuple[0..3,ResultBackup] |
| ResultDayEntry | day_no:int[1..60]；date:date；ref:ArtifactRef |

M01 已确认必需项仍为 null 时拒绝 input_not_ready，不自行补人数、日期或房间；延期预算允许 null，不填金额占位值。nullable 文本保留 null；“无”等已经确认的文本仅按记录展示，不另做关键词判断。预算字段保留兼容，不在本期攻略展示，也不产生消费估算。M01 元数据与 inferred_scenarios 不进入 ResultRequirement；不得因同行年龄推断行动限制。

### 5.4 两种持久模型

```text
ResultDayArtifact:
  schema_version: Literal["2.0"]
  template_version: Literal["zh-guide-v1"]
  run_id: UUID
  source_itinerary_ref: ArtifactRef
  input_digest: Digest
  bundle_digest: Digest
  data: ResultDayData
  markdown: str

ResultIndexArtifact:
  schema_version: Literal["2.0"]
  template_version: Literal["zh-guide-v1"]
  run_id: UUID
  source_itinerary_ref: ArtifactRef
  input_digest: Digest
  bundle_digest: Digest
  core: ResultCore
  days: tuple[1..60, ResultDayEntry]
  markdown_header: str
  markdown_footer: str
```

Markdown 字段 repr=False，每项可为空的唯一情况是模板明定不存在的可选小节；整个 header/footer/day markdown 均非空。UTF-8 规范 Artifact 正文每件不得超过 1,048,576 bytes；规范字节按 M04 §9 产生并计算，而非 Python 字符数。每份完整结果有 N 个日块和 1 个索引，N=confirmed sightseeing_days=plan days 长度；日期原序连续，禁止缺日、重复日、重排或把只完成部分保存称为完整。

日块只引用 core 地点/来源 ID，不重复保存全局目录；bundle_digest 将它们绑定到同一个 core。汇总索引中每个 Ref 必须为同 run、phase=output_formatting、type=result_formatting.day、version=2.0；全部 artifact_id 和 operation_id 唯一。索引本身的写入也必须属于相同 run/output_formatting，不可与任何日块共用 OperationId。

若单日日块、汇总索引或仓库总容量超限，明确失败；不继续递归拆分半天、不截短名称/提醒、不提升 M04/M10 限额。这是既有容量安全边界，不取消按天分块能力。

## 6. 规范字节、版本与确定性

本模块源绑定摘要固定为：

1. 对 fresh ConfirmedTravelRequirement.requirement 执行 JSON model_dump（exclude_none=False、by_alias=False、exclude_computed_fields=True）。
2. input_digest = SHA-256({"version":"result-input-v1","run_id":UUID字符串,"requirement":第1步值,"itinerary_ref":Ref七字段规范值})，使用 M04 §9 的规范编码、UTF-8、小写 sha256: 前缀。
3. bundle_digest = SHA-256({"version":"result-bundle-v1","template_version":"zh-guide-v1","input_digest":...,"core":完整 ResultCore 规范值,"days":[所有 ResultDayData 规范值]})。

这些摘要属于 M12 新合同，不替代 M01 confirmation_summary_digest、M08 confirmed_requirement_digest 或 M09 requirement_digest。源一致性仍按 §7 校验。摘要不保存原始用户消息或 metadata，输出内容与 source_itinerary_ref 已足以绑定生成版本。

M04 Artifact hash 与上述业务摘要不同。写请求一律由本模块公共 helper 构造，内部使用本模块注册项组成的真实 ArtifactSchemaRegistry.prepare 做规范复验并计算规范字节。Registry.prepare 本身不实施容量限额；M12 必须显式比较其返回值的 size_bytes 与 1,048,576，超出即抛公共 ArtifactTooLargeError，服务映射 result_too_large。不导入 PreparedArtifact 私有类，只消费该公开调用产物的字段用于内部校验，不将其作为公共返回类型。写请求 content 是经过纯投影形成的 JsonValue tree。日块大小先在服务 prepare 完整检查，索引大小在 build_index 返回前检查。helper 复验 Artifact、OperationId 的 fresh Python-tree 与 JSON，并要求 run/phase 匹配；不会 put。

服务尚未取得 M13 的真实写入 OperationId 时，大小预检仅在内存使用同 run、phase=output_formatting、logical_step="result_formatting_validate" 的临时 OperationId 调用 Registry.prepare 并显式检查 size_bytes；该标识不进入结果 Artifact、state 或 Repository，也不分配保存许可。M13 保存时必须另传自己的真实 OperationId，helper 再次验证。helper 的非法对象/标识/字段违反合同抛 UsageError；单件超限抛公共 ArtifactTooLargeError；不包装为 outcome，不进行 I/O。

时间：持久 DTO UTC，展示统一 Asia/Shanghai，日程标题和跨日区间写明日期。日程时间区间原样转换；正路段预留量沿用 M09，0 秒/0 分钟也如实显示。估算耗时以“X 分 Y 秒（百度获取时路线估算）”表达，整分钟省略秒；超过 60 分钟允许继续显示总分钟，不四舍五入为新的事实。预留时间单列整数分钟。距离用精确米数，不引入取整误差。活动时长标为规划估算。用户近似抵离时刻保持“约”。

模板版本变更必须新版本，不静默改变已保存的 markdown。初版不做旧版迁移或多版本自动 fallback；未来版本需要新的设计及资格。相同 fresh 输入、同源 Artifact 字节和同模板必须产生相同 core、days、业务摘要及 Markdown；运行时当前时间、trace_id、random UUID、遍历 set 顺序不得参与内容。Ref.created_at 是写入时间，绝不冒充事实 retrieved_at。

## 7. 源读取和关联算法

### 7.1 入口和源上下文

固定顺序：

1. 按 §4 复验输入；类型错误/无法 fresh 验证为 UsageError，合法 DTO 但业务前置不满足为 Failed。
2. get itinerary_ref，expected_model=公开 ItineraryPlanningArtifact；record_type 不是 completed 返回 itinerary_not_completed。不得降级读取其 last_consistent 或 parent 来生成预览。
3. 比较 draft.requirement_digest 与当前 confirmed 的需求绑定摘要，并检查 days 日期和数量。酒店入住/离店日期只从 confirmed 投影，不假设 M09 draft 存在这些字段。M12 的兼容算法为：confirmation_summary_digest 匹配 `^sha256:[0-9a-f]{64}$` 时取该值，否则对完整 fresh confirmed 执行 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)`，随后 `json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`、UTF-8、SHA-256 并加 `sha256:` 前缀。当前 M01 正常确认返回无前缀的 64 位摘要，因此正常路径也走后者；这与现有 M08/M09 消费语义相容，不改变 M01 原摘要。完整 confirmed（含 metadata）仅在内存参与此兼容摘要，不进入结果 Artifact；不得调用上游私有函数或以 M12 input_digest 替代。
4. draft.hotel_selection_ref 和 draft.candidate_set_ref 必须精确出现在 itinerary 对应目录且同 run；读取酒店 selection、它引用的 discovery，以及 M08 当前候选集。候选 record 允许 completed 或 incomplete（M09 completed 是格式化准入），必须有 state，需求摘要、酒店 Ref 与 draft 相同。
5. HotelSelectedDecision 必须 selected；selected_candidate 是展示唯一酒店。discovery 的 confirmation_summary_digest 必须与当前 confirmed 原摘要相同。仅做源身份关联，不重新运行 M07 酒店筛选/人工选择规则。
6. 只收集主行程、备选、两个 transfer 用到的地点和证据，以及这些 M09 Observation 直接引用的 Action。get 每次指定精确公共模型，检查返回 ref 七字段及 fresh 内容；真实 Repository 完成 hash/size 校验，测试 fake 不能跳过公共模型复验。所有可达 Ref 都要求相同 run、正确 type/version/phase；不读 parent 链历史、模型对话或未使用候选。

不得复制 M09 的 must 完成、节奏总量、营业解释、路线重排 Gate。M12 校验的是“输出是否确实对应上游已完成结果和其来源”。发现关联矛盾返回 source_mismatch，不尝试修正规划。

### 7.2 身份证据选择

酒店按 discovery.search_refs 顺序查找第一个 success 且含与 selected_candidate 的 UID/name/address/坐标/category 全字段一致的 place；按 §5.2 使用该 Artifact 顶层来源字段。找不到完全匹配为 source_mismatch，不能按相近名称补上。

候选按 draft 中 ordinal+uid 在当前候选 state 唯一定位，state 必须 eligible。候选 core 是身份展示快照。按 candidate.observation_indexes 升序检查关联 Observation，选择第一个成功 Search places 项或 Details 的 UID/name/address/coordinate/provider_category 与 core 完全相同者作为身份来源；无一致证据为 source_mismatch。其余不匹配的成功响应不得覆盖 core，也不得把 Details 更新后的坐标嫁接到旧路线。

candidate.details.status=success 时，按 observation_index 取 Details success，UID 必须一致；只从该项复制 business_hours 原文和对应来源。not_requested/no_result 为无营业原文。缺原文不是身份失败，不自行解析营业时间或变更 M09 business_hours_state。M09 business_evidence_indexes 映射 directories.candidate_evidence_refs，读取后要求成功、包含目标 UID，provenance 有效；保留关联但不扩大“已核验”范围。

枢纽只读取 transfer 所用 HubLocationRef 的 geocode index；success、provenance.verified、capability 必须匹配，endpoint_kind 必须一致，并执行 §7.3 的观测轮次关联。使用返回的 formatted_address/coordinate，名字另由 confirmed 对应 hub_name 表示；不得生成新的 POI UID。

### 7.3 路线及备选

观测轮次关联：使用到的 M09 Hub/Directions/Matrix Observation 的 model_action_ref 必须精确存在于最终 itinerary.directories.model_action_refs，且同 run、type=itinerary_planning.model_action、version=9.0、phase=itinerary_planning。以公共 ItineraryPlanningModelActionArtifact 读取并 fresh 校验，仅将其 request_digest 与该 Observation.request_digest 比较。不要求 Observation 的摘要等于最终 itinerary.request_digest：正常补研续规划会更新最终请求摘要，但可复用前轮观测。Action 不进入来源展示目录或结果快照，不输出其调用/模型元数据。

主路线（含两个 transfer）：按 directions_observation_index 读取公开 Directions Artifact，核对同 run、该 Ref 在目录、success、source/capability/verified、origin/destination Ref、transport_semantic、route_mode、alternative_index 与 distance_m/duration_s，并执行上述观测轮次关联。展示值取 draft 中对应值并与证据相等，只增加本文“公交乘车步骤扩展”规定的静态乘车说明，不增加实时逐转向导航。

备选可选，0～3 个且按上游实际内容展示，缺少备选不拒绝主行程输出、不补造备用点。已有备选：core_visit_id 必须指当天对应角色的主访问，组合访问可分别承担其 attraction/lunch/dinner 角色。使用 matrix_observation_index，核对 success、date、mode、同 run，并执行上述观测轮次关联。按 ordered_locations 精确找到核心访问与备选 Ref，取 core→backup 有向 cell，要求 success 且 distance_m 等于 backup.distance_m 且 <=3000。不得使用反向 cell、直线距离或 Directions 缺失补造路线。展示“附近备选，未纳入主路线；距离为对应模式的路网距离”。

安全余量只复制 processing_minutes 和 approximate_extra_minutes，明确其为规划预留，不是机场/车站官方政策。不得从活动备注重新推断额外余量。酒店行李寄存只显示需自行确认，不宣传已有服务证据。

## 8. 中文 Markdown 合同

### 8.1 固定模板组织

模板逻辑版本 zh-guide-v1；文件 renderer.py 是唯一模板实现来源，不接入 LLM、Prompt 或 Markdown 插件。

最终字符串严格为：
`index.markdown_header + "\n\n" + "\n\n".join(day.markdown 按 index.days 原序) + "\n\n" + index.markdown_footer + "\n"`。
各片段内部采用 LF，无首尾空行；不得平台相关换行。日块保存的就是其片段，完整正文不另存一个超大 Artifact。

固定顺序：

1. header：城市与日期/天数、同行人数与儿童年龄、住宿日期与房间、已确认酒店名字地址、抵达枢纽到酒店路线及预留。
2. 每日日块：日期/上游主题/日类型；天气未获取；主访问与路线按现有时间顺序；营业待复核就地提醒；仅展示实际提供的备选。transit_only 明确“本日未安排游览”，不编活动。
3. footer：返程枢纽路线与处理/近似余量；费用类别；关键限制；用户补充要求；固定范围说明；来源清单；需复核声明。

主访问、交通和备选采用列表，避免长名称破坏表格。若同一时刻零耗时 route 与访问边界相邻，顺序按原路径：进入路线 → visit → 离开路线；不靠字符串排序换序。主时间线不插入备选访问。

来源引用文字固定“〔来源 N〕”。来源清单按 source_id 升序，每条写百度地图、能力中文名、获取时间（北京时间带日期）、“供应商返回已核验”；另外正文限定是地点身份/路线估算/营业文本/备选路网距离。不会把整份攻略标为“全部核验”。

### 8.2 文案与值域映射

- day_kind：full→完整游览日；partial→部分游览日；transit_only→纯交通日。
- 交通：walking→步行；cycling→骑行；public_transit→公共交通；ride_hailing→网约车（驾车路线估算，叫车可用性及费用未核验）；private_car→自驾。
- visit_kind：attraction→景点；lunch→午餐；dinner→晚餐；attraction_lunch→景点兼午餐；attraction_dinner→景点兼晚餐。组合角色保持一个访问和一个时间区间，不拆成两次活动；对应备选仍按各自角色关联。
- 营业 unknown：“营业时间未核验，请出行前确认”；compatible：“规划时营业证据相容，实时营业仍请确认”。有原文时仅转义展示供应商原文及来源，不生成解释。
- 费用固定住宿、当地交通、餐饮、景点、其他当地消费五类“未核验”；无金额、小计、余额或预算合规结论。
- 补充要求按房型、酒店偏好、交通偏好、作息、补充备注顺序，仅非 null 值展示；标题附“已记录，本版未逐项验证”。行动限制和饮食禁忌在关键限制区展示，同样不宣传适配已验证。must/avoid 按确认原序展示为用户要求，不从文本解析活动时间。
- 固定范围说明精确引用 M09 §3.3：“本次完成的是首版支持范围内的行程规划；文本中的作息、活动时间或时长、交通限制等额外要求未纳入本次规划验证。”
- 来源是查询时快照，不是当前实时保证；不设新的 TTL 或失效时长，后续生命周期归 M19。
- 新结果的预算金额及约数展示延期；允许 null，禁止使用 0 或其他默认金额占位。旧非空金额工件保持原模板和确定性十进制文案，兼容已保存结果读取。保留费用五类未核验提示。

### 8.3 不可信文本的展示

上游名称、地址、主题、营业文本、用户确认文本均当数据。结构化字段保留合法值；renderer 必须逐字符转义 Markdown 标记及 HTML 尖括号，阻止用户文本变成标题、列表、代码块、链接、图片或 HTML。反斜线先转义；至少转义反引号、星号、下划线、花括号、方括号、圆括号、井号、加减号、点、感叹号、竖线、大于号；小于/大于/与号以 HTML entity 表示。换行/tab 显示为可见“↵”/“⇥”，不产生新的块级结构。

ESC、DEL、其他 C0/C1 控制符、Unicode 双向控制字符 U+202A～U+202E/U+2066～U+2069、孤立 surrogate 拒绝为 unsafe_text；LF/CR/TAB 仅按上述显式展示转换处理，CRLF 算一个换行。不得执行正文指令、访问用户写入的 URL、加外部链接或生成地图 URL。没有通用 Secret 探测承诺；只消费白名单业务字段，严禁把配置、异常、metadata、原始消息或供应商原响应传给 renderer。

## 9. 缺失能力与终态边界

天气固定 not_collected/“天气未获取”；不定义真实天气输入、调用或证据类型。地图固定 deferred，普通正文不产生空地图章节；用户请求地图时 M13/14 必须如实说明暂不支持。两项延期不自动宣告 M06 或整套 Demo 的资格完成。

M12 的 outcome 不含 delivery_status、terminal_code 或 demo_ready 布尔值。core 的营业缺口计数、weather/map/cost 状态只是可供 M13 使用的事实描述。M13 必须继续按主设计 §4.1/§6.1 及当次已批准功能范围判断状态：不得将用户事后接受缺失当作执行前关闭可选能力，不得把本模块生成成功等同于 demo_ready。M12 不替 M13 冻结未来天气开关或终态策略。

预约、实时库存、过敏原、无障碍、临时政策没有可信输入合同；只使用固定复核声明，不编造具体政策/电话/价格。缺失的阻断身份/主路线证据必须失败，不能用“待核验”标签放行。

## 10. 分块保存、索引发布与读取

### 10.1 生成与发布交接

1. M13 在合法状态选择 itinerary_ref，调用 prepare，获得 N 个完整日块。任何源读取失败均不返回部分日块。
2. M13 为每个日块和索引规划不同且稳定的 output_formatting OperationId，不含用户文本/日期/随机重试计数。M12 helper 只构造严格写请求。
3. M13 按日序通过 M11 node-bound Repository 保存日块，并收集返回的完整 Ref。M12 不写入、不申请预算、不拥有 M13 的预算键。
4. M13 用同一格式化请求调用 build_index(day_refs=原序 N 个 Ref)。M12 重新生成完整投影，读取每个 Ref，要求 Artifact 字节语义、模板、run、源 Ref、input_digest、bundle_digest、day_no、date、data 和 markdown 全部与预期相等。不能只相信 Ref 中的 hash 或日数。
5. 全部一致才生成索引，检查索引容量并返回 IndexReady。M13 保存索引，再把已保存索引 Ref 纳入工作流状态。此时才存在可作为完整格式化结果的根引用；交付终态仍由 M13 另判。
6. 引用写入和 Graph checkpoint 的接缝由 M13/M11 按其已批准合同处理。索引最后写入提供“完整结果发布标记”，不是跨 N+1 次 put 的数据库总事务。

M13 未来必须把有 put 的节点合法声明为 journal_guarded 并通过现有许可和 Artifact 对账；不得以“格式化是纯计算”为由将写入节点标 replay_safe。M12 不在本模块新增预算占位键，也不修改 M11。M13 设计必须另行冻结真实保存操作预算和节点拓扑；本模块交接不声称该接线已经存在。

### 10.2 失败、中断与恢复

| 状态 | 行为 |
|---|---|
| 尚无日块，prepare 失败 | 返回 Failed，M13 不发布任何结果索引 |
| 已保存部分日块，后续失败 | 保留不可变产物；无索引即没有完整攻略，不删除、不回滚其他产物、不输出前几天预览 |
| 同操作同内容日块已存在 | M13 经合法恢复流程复用 Ref；M04/M10 精确幂等语义不变 |
| 同操作内容不同或 Ref 错配 | 冲突/完整性失败，不覆盖，不换随机 OperationId 绕过 |
| 索引存在但缺日块、跨 run、错版本、顺序或内容不一致 | load 失败；不补写、不跳过、不拼接剩余日块 |
| M11 已许可但没有可证明 Artifact | 按 M11 §11.2/11.3 阻断，不借确定性渲染重试突破不确定窗口 |
| 索引与全部日块合法，源状态一致 | 返回保存的原始片段拼接结果，不更新时间或重新研究事实 |

首次 save 到 checkpoint 前的精确崩溃续跑能力取决于 M11/M13，不能由单测的幂等 put 冒称跨进程恢复完整通过。无索引的孤立日块占用仓库容量；不增加 delete/TTL/GC，长期回收交 M19。

### 10.3 load 与完整性

load 先 fresh 输入和 get index，然后读取有序日块。在所有内容可证明完整前不流式输出、不返回前缀。校验源请求及重新生成的 core/days/header/footer/digests 与保存内容一致，保证注册合法但语义被伪造的 index/day 不能当作正式结果。源 Artifact 此时缺失/损坏同样失败，初版不支持脱离源证据的独立归档读取。

保存的快照以当前支持的模板复核，禁止按照新的当前时间改写正文。load 不执行 put/lookup/recover 或任何外部能力；源读取和日块 get 可使用单调用缓存，读取顺序固定，首个错误稳定。

## 11. 错误与优先级

UsageError 为零参数 Exception，str/repr 只为固定类名，无 chain；包括入口不是规定 DTO、forged/mutated 输入 fresh 失败、helper 传错误对象/不合法 OperationId、Repository 对象没有所需可调用方法。构造不触发 Repository I/O。

业务枚举固定：

| code | 含义 |
|---|---|
| input_not_ready | 已验证 DTO 合法，但未 confirmed、状态/必需字段不允许 |
| itinerary_not_completed | 源行程不是 completed |
| source_mismatch | run、需求摘要、Ref 关联、地点/路线/索引内容不匹配 |
| artifact_not_found | 需要的源或结果 Artifact 缺失 |
| artifact_integrity_error | Ref/hash/bytes/strict 内容损坏 |
| artifact_contract_error | type/version/注册模型/Repository 用法不兼容 |
| artifact_dependency_error | 公共仓储依赖错误 |
| result_too_large | 任一日块或索引超过单件容量 |
| unsupported_result_version | index/day 的 Ref 可见 schema_version 不支持 |
| unsafe_text | §8.3 禁止的文本控制字符 |
| internal_error | 非预期普通 Exception，安全收敛，不含原文 |

按 §7 读取顺序决定首个故障；入口 misuse 优先于 I/O。公共仓储映射：NotFound→artifact_not_found；Integrity/读取 fresh 校验失败→artifact_integrity_error；UnsupportedType/UnsupportedVersion/ExpectedModelMismatch/RegistryConfiguration/RepositoryUsage→artifact_contract_error；Dependency→artifact_dependency_error。result Artifact 已知 type 但 Ref 上未知 schema_version 在 get 前返回 unsupported_result_version；源未知版本为 artifact_contract_error。schema=2.0 内未知/非法 template_version 会被真实 Registry 的 Literal 校验拒绝，统一 artifact_integrity_error，不能绕过 Repository 读取原始字节区分。ArtifactTooLarge→result_too_large。其他 ArtifactRepositoryError→artifact_contract_error。

CancelledError、KeyboardInterrupt、SystemExit 原样传播，绝不转为 Failed；不吞取消后继续读取。未知普通 Exception→internal_error，不记录 stack/text/对象。M12 不增加重试，不写失败 Artifact，不在正常返回后擅自调用其他服务。M13/M14 负责稳定中文故障说明和后续操作。

## 12. 安全、性能与生命周期

- 服务不读环境变量、真实 .env、Secret、文件系统、网络或时钟；唯一 I/O 是注入 Repository 的 get。
- 需要保留的用户字段是已确认的规范业务值，不是原始对话；输出 DTO 全部白名单，不保留 normalization/evidence 原文或 arbitrary metadata。
- 输出内容可包含旅行隐私，仅交当前 run 调用者；日志、repr、异常、事件、测试报告不得输出正文。M12 不新增事件系统、RunSummary 或泄漏标识符的诊断文本。
- 每次调用有界：最多 60 日、131 地点、1024 来源；按显式目录访问，每 Ref 至多读取一次。prepare/build_index/load 的输入上限与 Artifact 限额联合适用，失败不消耗供应商调用或执行预算。
- 不增加依赖、数据库表、迁移、后台任务、缓存目录、临时正文文件或服务常驻状态。持久结果由 M10 保存，生命周期沿用上游，不延伸为长期记忆。
- 不从 M09 读取或暴露私有模型会话，不复制其服务端 context，不重新对候选资格作语义判断。

## 13. 验收矩阵与演示优先政策

所有测试必须同时对照本规范；通过测试不替代设计符合性。优先修 P0/P1 及高概率影响演示的 P2；极低概率、不影响演示的 P2/P3 记录延期并注明回收条件。coverage 采集本模块 line/branch/combined 用于诊断，不设数字门槛，不为凑数增测；覆盖率采集失败仍是验证失败。历史 M11 未通过原门槛不可改为 PASS。

| ID | 必要证明 | 关键负例 |
|---|---|---|
| RF-API | §4 精确导出、签名、import 无 I/O、无私有导入、借用资源不关闭 | 不暴露 renderer/internal DTO 或 SQL/LLM 接口 |
| RF-SCHEMA | 两注册项真实 Registry prepare/decode；完整可达图、fresh copy、canonical 稳定 | default/alias/serializer、forged bool/int、嵌套 mutation |
| RF-SOURCE | 真实 M01 确认结果与 M07/08/09 公共模型形成已保存来源链；无前缀摘要常规路径；M08 incomplete + M09 completed；补研续规划合法复用前轮观测 | 错 run/需求摘要/Action 摘要/酒店/ordinal/UID/证据 index、核心事实缺失 |
| RF-ROUTE | 主路线与两个 transfer 精确证据；原耗时和预留；备选有向 cell | 反向距离、错误 mode/alternative、拿 Matrix 替主路线、虚构公交时刻 |
| RF-TEXT | 固定模板完整覆盖 00 §4.3、D-12-06/07/09；确定性 golden 场景 | Markdown/HTML/ANSI/bidi 注入；未验证→已验证措辞；含 Secret 的内部对象误入输出 |
| RF-DAYS | 1 日、多日、跨午夜、transit_only、60 日按块；每件实际字节容量 | 少日/乱序/重复、混 bundle、中文 byte 超限、静默截断 |
| RF-INDEX | 全部日块保存并核验后索引才可生成；load 精确拼接和源复核 | 索引先发布、缺块、数据与 Markdown 不一致、源删除/损坏、错版本 |
| RF-ERROR | §11 错误分类、读取次数、无额外重试、取消透传 | 返回部分攻略、底层异常原文泄漏、失败后继续读取 |
| RF-PERSIST | M4 独立 harness：真实 M10 PG 保存日块/索引，关闭进程后重读且字节一致 | 部分日块无索引不可交付、相同操作重放不新增、冲突不覆盖 |
| RF-REGRESSION | 模块完整测试、当前全项目 unit/contract；需要 PG 的项目用内部网络 | 删测试/skip/xfail/只跑绿灯子集；未经授权调用真实 API |
| RF-CLOSEOUT | 未来独立 closeout：README/STATUS/Dockerfile 真实同步与重建复验 | 设计完成冒称实现、旧镜像冒称最终证据 |

RF-PERSIST 不装配 M13 完整主图；使用测试 harness 承担写入，仅证明 M10+M12 存储/读取合同。M11 checkpoint/journal 跨进程资格引用已接受证据并按受影响范围回归，不能将简单重启读库冒称全部 M4/M11 恢复证明。完整主图的 journal 保存接线留 M13。

## 14. 未来实现树与文件边界

以下为允许创建的新实现目标，当前设计阶段不创建：

```text
app/result_formatting/__init__.py
app/result_formatting/models.py
app/result_formatting/artifacts.py
app/result_formatting/errors.py
app/result_formatting/projection.py
app/result_formatting/renderer.py
app/result_formatting/service.py
tests/unit/result_formatting/conftest.py
tests/unit/result_formatting/fakes.py
tests/unit/result_formatting/test_projection.py
tests/unit/result_formatting/test_renderer.py
tests/unit/result_formatting/test_service.py
tests/contract/result_formatting/test_public_api.py
tests/contract/result_formatting/test_artifact_registration.py
tests/contract/result_formatting/test_upstream_integration.py
tests/contract/result_formatting/test_import_boundaries.py
tests/integration/result_formatting/conftest.py
tests/integration/result_formatting/test_persisted_result.py
tests/integration/result_formatting/restart_worker.py
scripts/verify_module12.ps1
```

models.py 拥有请求/outcome；artifacts.py 拥有本文全部持久及嵌套 DTO、注册和写请求 helper；projection.py 只做已读取对象到 DTO 的确定性投影和摘要；renderer.py 只做转义和模板；service.py 拥有公共读取顺序/关联核验/错误收敛。不得新增万能 utils.py、生产 Graph、CLI、SQL、Prompt、配置或依赖。

本模块实施不改 M01/04/07/08/09/10/11 生产及测试文件；发现真实缺陷先停止并报告，不以内联复制私有实现绕过。README、STATUS、Dockerfile 仅在配套独立 closeout 阶段按精确白名单同步。没有数据库迁移需求。

## 15. 设计追踪、批准与审查

文档编写与设计批准是不同动作。本文和配套文档在用户批准前保持草案；双 Agent 审查不替用户授予批准。实施提示词必须引用本文和执行规范，保留目标、前提、分支、精确白名单、允许动作、测试矩阵、停止条件和完整报告；不得重复粘贴已有合同大段正文。

本文审查覆盖架构/逻辑、上游兼容、下游空间、弱模型可执行性。审查状态记录于配套 §8，不夹带测试通过声明。延期项目统一登记 [unresolved_findings.md](VALIDATION.md)，仅本轮事项，不改写历史例外。

常规版本化、模板组织和索引发布细节依据用户“成熟方案优先”选择；若审查迫使改变 D-12-01～14、扩大白名单或修改上游冻结合同，应停止相关改动并重新确认。

## 公交乘车步骤扩展（2026-09-28）

本节由用户新增展示需求授权，覆盖旧版路线仅有摘要的限制，见 [实施记录](VALIDATION.md)。投影仍完整核对同run、Ref、地点对、模式、alternative_index及距离/耗时；只从最终选中的M09公交方案取得步骤。

- 保留原ResultRoute；新增严格ResultTransitRoute，固定transport_semantic=public_transit、route_mode=transit，新增必填transit_instructions（1..128项，每项1..1024字符、非全空白）。ResultTransfer.route和ResultDayData.route_legs接受两种封闭结构；公交步骤的既有业务结构保持；本次阶段改造的结果顶层/日块/索引注册2.0，不兼容旧测试运行。
- 有步骤的路线摘要下增加“乘车顺序”，按供应商顺序编号；保留供应商提供的线路/方向/上下车站/换乘文字，不自行拆分猜测缺失字段。每段提示为查询时路线说明、班次与实时运营未核验；绝不以静态路线保证首末班或实时换乘。
- 复用既有纯文本安全转义，HTML/Markdown/链接成为惰性文本，控制符仍触发UNSAFE_TEXT；不执行、不请求文本中的URL，不将步骤传给模型。
- 原始旧结果和缺步骤的旧Observation不生成占位线路、保持旧Markdown及哈希。新结果day/index同时保存步骤，重启读取无新增地图/模型调用。持久模型全部必填且extra-forbid，不放宽通用Registry。
- 原先1MiB Artifact容量门槛继续生效，不静默截断长步骤。完整合成步骤回归及真实PostgreSQL重启测试为本期验收；未执行新的真实地图/模型查询时不得宣称具体城市线路已重新资格。


## 本次新版规划消费（2026-09-30）

M09只生成itinerary/6.0及model_action/9.0，新结果Request/Outcome、ResultDayArtifact、ResultIndexArtifact为2.0；template_version与中文排版未改变。来源Ref按type/version精确检查，hub/matrix/directions外层1.0和现行Directions摘要1.0/1.1都可消费，不接受旧itinerary运行或旧result/1.0，也不自动转换。完整主行程Gate仍由M09负责，公交线路/换乘步骤、主行程营业未知影响标签及天气延期提示保持。实施仅依原M09执行规范§29/W02和AC-06/12，不复建M12。


## 通用参数修复的版本消费增量（2026-10-06，用户已批准）

按[M09 §20.1/§29](09_itinerary_planning_loop.md)精确读取当前itinerary6.0/action9.0来源。历史模型动作事实按原形状验证，不授予旧行程当前读取资格。模板、事实、价格、结果Artifact版本和读取算法保持，来源bytes/hash不回填、不重写，无SQL迁移；当前实施见[E09 T5＋T6](09_itinerary_planning_loop_vibe_coding.md)。
## 2026-10-11 用途修复的来源读取增量

消费[M09 §28](09_itinerary_planning_loop.md)的新itinerary/6.0、model_action/9.0和[M08](08_candidate_research_loop.md)的candidate_set/2.0；保留旧结果/来源只读与原字节/hash。仅来源版本接缝变化，不新增事实、改变渲染、费用或历史结果；旧非终态不能因此自动恢复。


## 2026-10-11 批准修订：研究合同修复

状态：用户已批准并授权实施。R12：只读支持新candidate_set3.0和旧1/2的精确版本；读取新终态并保留事实/费用/证据边界，不新增模型调用或地图事实。新旧canonical bytes/hash回归和新候选→规划→格式化贯通；没有SQL迁移。

共同依据与验收见 [M08 R08](08_candidate_research_loop.md)及[执行规范 R1～R6](08_candidate_research_loop_vibe_coding.md)，覆盖冲突旧条款。

## 2026-10-12 必要资格判断接线

Q12：按Ref精确读取candidate_set/4.0和model_judgment/3.0，同时保留candidate_set1/2/3及judgment1/2历史读取。新增资格工作单仅作来源核验，不生成地点事实、价格或资格，不展示内部判断目录；结果版本与渲染保持。旧字节/hash、从新候选到规划及格式化贯通必须通过；无SQL迁移。

当前4.0分支精确读取其全部行动、判断及观察来源，调用M08的validate_qualification_sources共用核验；严格要求Action2/Judgment3，逐条证明地图属于已接受行动，并核对最终用途和需求标签来自实际accepted判断。缺失来源、删除目录后仍保留地图或肯定标签、错步骤、候选窗口摘要不符及伪造覆盖均停止格式化。旧1/2/3判断按自身精确模型读取，不要求新增工作单或覆盖；判断产物不进入公开地点事实来源。

实施和验收共同引用 [M08 Q08-01～09](08_candidate_research_loop.md)、[Q1～Q6执行契约](08_candidate_research_loop_vibe_coding.md)及本模块执行规范。用户已批准实施及隔离真实验证，完成等待提交。

候选自身目录须按M08共同算法核验真实搜索结果和详情行动的UID，初始身份来自首次接收的搜索结果；不能只信保存的索引和摘要。跨候选观察、容量拒收结果和伪造初始身份均不提供资格来源，即使判断、覆盖及所有Ref/hash重新计算正确也停止格式化。后续真实同UID冲突与合法无结果详情按原合同保留。

## 2026-10-13 分钟规划读取合同

依据[M09设计§29](09_itinerary_planning_loop.md)与[执行规范T5＋T6](09_itinerary_planning_loop_vibe_coding.md)，新规划消费itinerary/6.0、model_action/9.0、路线快照3.0与分钟时间快照2.0，预算和已有职责保持。新父链、来源目录、类型/version/hash及实际字段必须一致；当前来源须满足十分钟交通预留及分钟时刻规则，禁止混用不同版本记录。结果层沿用北京时间和原始地图耗时/按10分钟向上取整的交通占用的分别展示，工作流不增加预算或排程工具。历史测试行程兼容不纳入本次验收，无数据库表迁移。

## 每日规划质量结果合同

消费[D09 §30 UD-04～06](09_itinerary_planning_loop.md)并执行[E09 §32](09_itinerary_planning_loop_vibe_coding.md)的本轮验收。新itinerary8.0使用zh-guide-v4-daily-quality；day/index根版本仍2.0，沿用严格的planned_breaks、quality_issues和quality_issue_count数据分支。模板、来源与数据分支必须精确对应：8.0→v4、7.0→v3、6.0→原v1/v2；禁止跨版本混配。旧6/7来源及旧结果只读，旧规范字节、hash、Markdown不变，不补算历史质量。

v4在所有日期按时间显示访问、路线与休息，逐日显示未解决的长空档、缺餐或过量休息，页脚使用“每日安排”措辞。它只投影M09已保存的派生质量，不重新规划，不把休息理由当地点事实。只有M09完整有效Completed进入本模块；长时间整日未规划的Incomplete没有正式攻略。新来源链／摘要／反篡改核验、保存及另进程重新打开、复制与下载保持原严格合同。无新增模型、地图、公网接口或SQL结构迁移。

## 研究纠错来源读取合同

依据[M08设计§31](08_candidate_research_loop.md#31-研究纠错计数与已有成果交付c08)，新增candidate_set5/Action3/Judgment4/invocation_attempt1的精确读取和公共来源核验。每次修正审计不成为地点事实；主行动的最终结果须与末attempt一致，全部Ref/字节/hash和计数须经M08核验。旧候选1/2/3/4及已保存M12结果保持原模型、原字节及原Markdown，新旧类型不混配。模板、行程8.0及M12 v4保持，本模块仍只消费合格规划Completed；不因研究Incomplete降低结果发布门槛。
