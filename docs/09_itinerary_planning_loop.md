# Module 09：受控行程草案与路线优化循环

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 当前抵离日改进见 §30，执行按配套执行规范 §32；其余硬约束和历史记录语义继续有效。

> 本次阶段与预算直接修订本文及原设计§12～15/20/25；代码实施按 [原M09执行规范§29](09_itinerary_planning_loop_vibe_coding.md)。完整采集归 [M02§19](02_llm_runtime_and_model_profiles.md)，专用存储归 [M10§23](10_artifact_persistence.md)，没有新增平行设计规范。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 规范状态：用户已批准并冻结结构化输入 Demo 范围及本版合同；Module 09 已实现并进入本地 `main`，当前状态见 `IMPLEMENTATION_STATUS.md`。2026-09-14 下游职责修订已把原独立校验归入 Module 09，并把持久化提前到 Module 10/11；既有补研关联合同保留。
>
> 本文是 Module 09 的业务不变量、公共合同、受控 ReAct、路线证据、Artifact、预算、终态与验收真源。实现、测试、后续提示词和审查不得以当前代码便利、模型自由发挥或历史讨论中的中间方案覆盖本文。

## 1. 文档地位与权威层级

发生冲突时按以下顺序处理：

1. 用户对 Module 09 的最新明确决定；
2. 本文；
3. `docs/00_agent_master_design.md`；
4. `docs/IMPLEMENTATION_ROADMAP.md`；
5. `docs/SHARED_IMPLEMENTATION_RULES.md`；
6. Module 01、02、04、06、07、08 已批准公共合同；
7. 已执行的公共合同测试；
8. README、实施状态页和当前实现。

本文不保存当前 HEAD、日期、测试数量、镜像 ID 或未发生的提交。发现需要改变上游冻结合同、扩大依赖、增加真实供应商能力或修改本文决定时必须停止并取得用户授权。

本文中的“必须”“不得”“只能”“固定”为规范性要求；“可以”“建议”为允许但非强制的实现空间。附录中的字段、枚举、容量、排序和判别器同样具有规范性。

## 2. 模块目标

Module 09 消费已确认需求、用户已选酒店和 Module 08 候选集，在严格工具白名单、阶段逻辑调用额度、Direction预算和确定性控制器约束下，由规划模型决定每日主题、候选选择、日期分配、访问顺序、时间、交通语义、路线模式与路线方案，产出可审计的静态行程草案。

核心目标：

- 让大模型承担需要语义判断和组合优化的规划工作；
- 让控制器承担权限、预算、输入完整性、硬约束、证据绑定、幂等、停止条件和终态；
- 每个主路线地点来自已选酒店、已解析枢纽或 Module 08 的 eligible 候选；
- 每个相邻主地点之间都有与提交选择精确匹配的百度 Directions 证据；
- 不能完整规划时保存最后一致的合法草案并返回类型化缺口；
- 不把 Route Matrix、模型常识或自由文本冒充 Directions、营业事实、活动时刻、价格或实时服务事实。

## 3. 范围与明确非目标

### 3.1 范围

- 单一核心城市以及从同一酒店当天往返的周边游；
- 从抵达机场或火车站到酒店、每日酒店闭环、酒店到返程枢纽；
- 景点、午餐、晚餐、多角色地点、must/avoid、兴趣和备选；
- 分钟排程、抵离安全余量、跨午夜和部分日期；
- Route Matrix 比较、逐相邻地点 Directions 验证和模型选路；
- initial 规划、一次性候选 supplemental 请求与同 run 恢复；
- Completed、Incomplete、Failed 及取消传播；
- Demo 进程内 Artifact，同时保持可迁移到数据库的不可变版本结构。

### 3.2 非目标

Module 09 不负责：

- 跨城市串联、中途换酒店或多酒店优化；
- 天气查询、天气判断或天气导致的重排；
- Citywalk 专用活动类型、步行专属时间/距离上限或连续步行限制；
- 实时导航、逐转向指令展示、地图调起或当前位置导航；
- 票务、预约、库存、排队、营业政策或活动时刻的外部查询；
- 饮食适配、过敏原、清真、纯素、后厨交叉污染或菜品安全判断；
- 价格推断、交通费、景点费、餐费或总预算判断；
- 天气、价格或预约缺失导致的最终 `degraded` 映射；
- 修改候选资格、酒店选择、已确认需求或持久化／展示层结果；
- 把候选 supplement 直接编排为 Module 08 调用；
- 写 `demo_ready/degraded/failed/cancelled` 交付状态；
- MCP、供应商 SDK、HTTP、私有 Gateway、数据库、长时 Memory 或后台监控。

首版默认酒店可寄存行李。该默认只用于不因行李阻止抵达日或离开日活动，不是百度核验的酒店服务事实，输出不得宣传“酒店已确认可寄存”。

### 3.3 首版输入能力边界

首版只实现上游已经结构化、且现有证据能够验证的规划能力。Module 01～08 的字段、版本及传递方式不改；不再要求把 Module 09 的规则解释提前到 Module 01/03。

| 输入或能力 | 首版处理 |
|---|---|
| 目的地、游览日期、抵离枢纽/时刻及近似标志 | 使用 01 已规范化字段，沿用枢纽 geocode 和固定安全余量 |
| 节奏、兴趣及 Agent 代选标志 | 沿用枚举、代选、节奏锁定、默认密度和交通上限；兴趣仍为软目标 |
| 必去/避开地点 | 消费 08 完整 requirement-group、匹配结果和 pending gaps；不从地点项再提取活动时间 |
| 酒店、候选、营业状态、路线 | 消费 07/08/06 的现有公共证据，保留相关 Gate |
| 人数、年龄、房间、预算 | 保留结构化值；不自动产生行动限制，不重选酒店，不证明总费用满足预算 |
| 文本作息、交通限制、活动/餐次日期时刻与时长、用户额外安全余量 | 转换为执行规则的能力延期，不能从备注或 must/avoid 文本绕道启用 |
| 无障碍、饮食、价格等既有非目标 | 继续延期，不声称已满足 |

源需求保留不变。Prompt 仅投影首版支持内容，不把 daily_time_preferences、transport_preferences、mobility_constraints、free_text_notes 等延期文本交给模型作为排程指令或软偏好。must/avoid 仍沿用 08 结构化分组及匹配，不靠新的关键词解析器判别活动要求；现有候选搜索和营业证据处理不属于本次移除范围。

Completed 只表示本节支持范围和 §16.1 均满足。呈现结果时须使用固定说明：“本次完成的是首版支持范围内的行程规划；文本中的作息、活动时间或时长、交通限制等额外要求未纳入本次规划验证。”不得声称所有原始要求均已满足。此说明不依赖对用户文本的再次识别，不新增自由文本 Artifact 字段。实际 Module 12/14 呈现接入不在本次白名单内，须在产品接入时验证；不得把本次文档修订报告为界面已实现或 demo_ready。

延期文本不单独阻止 Completed，也不自动产生阻塞 gap。结构化 must/avoid、路线、默认时间窗和安全余量仍必须通过；不得借范围收敛掩盖已保留功能的缺陷。旧会话不迁移，新旧运行隔离按 §20 执行。

## 4. 依赖与职责边界

### 4.1 精确直接依赖

Module 09 的直接依赖精确为 01、02、04、06、07、08：

| 模块 | 只允许消费的公共能力 |
|---|---|
| 01 | `ConfirmedTravelRequirement`、`TravelPace`、`InterestTag`、`HubType` 及严格需求子树 |
| 02 | `ModelRole.ITINERARY_PLANNING`、`DeepSeekRuntime.start_operation()`、`ModelOperation.ainvoke_tools()` / `acontinue_tools()`、`ToolContract`、`ToolResult`、typed outcome/failure |
| 04 | `ArtifactRef`、`ArtifactRepository`、`OperationId`、`ExecutionBudgetManager`、预算 DTO、时钟及 `WorkflowPhase.ITINERARY_PLANNING` |
| 06 | `BaiduMapAdapter`、Geocoding、Route Matrix、Directions 的公共 request/outcome、`TravelMode`、`MatrixTravelMode`、`BaiduCoordinate`、typed error |
| 07 | `hotel.selection/1.0` Artifact 及 `HotelSelectedDecision` |
| 08 | `candidate_research.candidate_set/1.0`、`CandidateRecord`、候选状态/角色/兴趣/餐次/must/avoid/evidence/gap 语义；公共 `CandidateResearchOperationPlan`、`CandidateResearchSupplementalRequest` 和 `SupplementalGap` 分支，仅用于 §15.1 本地关联核验 |

禁止依赖 Module 02/04/06/07/08 的私有模块、ProviderAdapter、MCP 类型、供应商 payload 或运行时内部历史。公共 facade 不足时必须停止，不得通过私有导入绕过。

### 4.2 下游边界

| 模块 | Module 09 提供什么 | 下游不得反向要求什么 |
|---|---|---|
| 10 | 行程 Artifact、路线 Observation Ref、营业状态和时间证据 | 只通过 Module 04 `ArtifactRepository` 端口持久化；不得改行程、候选、校验结果或 unknown 状态 |
| 11 | planning state Ref、预算快照、OperationId 和 terminal Ref | 只用于 checkpoint、执行账本和跨进程恢复；不得重置预算、重发已有确定结果的外部调用或改变规划语义 |
| 12 | 已完成行程和展示标签 | 只格式化；不得新增地点、路线、价格、政策、主题或隐藏模型判断；当前费用统一标记未核验 |
| 13 | initial/resume 端口、候选 supplement 请求、typed outcome、ArtifactRef | Module 13 是跨模块图、OperationId、supplemental 闭环和最终状态映射的唯一所有者 |
| 14 | 无直接依赖；经 Module 13/Application Service 间接使用 | Module 14 提供应用服务、应用 API 和前端网页，不复制规划规则 |

Module 09 的 `Completed` 表示本模块已完成生成和确定性提交校验，仍不等于完整产品 `demo_ready`。Module 10/11 的持久化／恢复状态、Module 12 输出和其他请求内能力仍由 Module 13 汇总。

## 5. 已确认设计决定

### 5.1 用户逐项确认

以下决定来自需求澄清中的逐项确认；后出现的决定覆盖早期中间方案。

| ID | 最终决定 |
|---|---|
| D-09-01 | 排程使用整数分钟；用户和供应商原始时间/秒数保留精度，规划估算不得冒充精确事实。 |
| D-09-02 | 不完整时保留 `last_consistent` 合法草案；只保存可证明的合法部分和类型化缺口。 |
| D-09-03 | 完整游览日景点数量：轻松 1–2、标准 3、紧凑 4；午餐和晚餐另计；主路线候选地点最多 6。 |
| D-09-04 | 完整游览日恰好一顿午餐和一顿晚餐；营业冲突阻止选择，营业 unknown 可选但必须标为待核验。 |
| D-09-05 | 抵达/离开影响的日期允许较少活动、较少餐次或 `transit_only`；不得为凑数量越过可用时间。 |
| D-09-06 | 每个 must 语义组选择一个 eligible UID；同一 UID 可同时满足等价 must 组；备选不自动满足或替换 must。 |
| D-09-07 | `effective_interests` 只是选择偏好，不是每天或整趟必须覆盖的硬配额。 |
| D-09-08 | 一个地点有证据支持时可同时承担 attraction 与一顿 meal；只占一个主路线地点，但不能同时算午餐和晚餐。 |
| D-09-09 | 普通候选 UID 整趟主行程最多使用一次；酒店锚点例外。 |
| D-09-10 | 固定路线骨架为抵达枢纽→酒店、每日酒店→地点→酒店、酒店→离开枢纽；抵达活动在首次到酒店后，离开活动在最终离店前。 |
| D-09-11 | 采用模型主导的受控 ReAct；模型负责主题、分组、选择、顺序、时间、模式、工具顺序、权衡、修订和提交。 |
| D-09-12 | 模型只能调用六个 Module 09 工具；成功只能来自合法 `submit_itinerary`，普通文本不能成功。 |
| D-09-13 | Route Matrix 支持多点笛卡尔积；Directions 工具支持批量腿，但每个底层腿仍独立计 Direction 预算。 |
| D-09-14 | 每条相邻主路线腿在 Completed 前必须有匹配的 Directions；Matrix 只作比较，不能替代。 |
| D-09-15 | 首版只执行 §3.3 的结构化输入和默认 Gate；自然语言硬/软限制解释延期，不按关键词执行。 |
| D-09-16 | 普通日期窗口 07:30–22:00；午餐开始 11:30–13:30，晚餐开始 17:30–19:30；文本不覆盖默认。 |
| D-09-17 | 默认景点 1–4 小时、餐次 1–2 小时、组合地点 1.5–4 小时；用户文本时长解释延期。 |
| D-09-18 | 机场抵达处理 60 分钟、机场离开提前 120 分钟；铁路抵达 30 分钟、铁路离开提前 60 分钟；近似抵离时间再加 30 分钟。 |
| D-09-19 | 同一 supplement gap fingerprint 整趟最多自动请求一次；Module 13 调 Module 08 后恢复同一规划 run、预算、缓存和迭代。 |
| D-09-20 | Module 09 在终态提交前完整执行时间、路线、返程、候选资格、must 覆盖和事实证据的确定性准入；不再设置重复这些规则的独立下游 Validator。 |
| D-09-21 | 准备/交通按§12.4连续两次真实无进展停止；修正仅连续两次同reason/field、同业务方案且无被采用相关新证据的拒绝停止；无合法Action立即Incomplete。 |
| D-09-22 | ReAct 只保存安全 Action/Observation/Artifact，不保存原始 Prompt、完整用户对话、模型私有思维链或供应商 body。 |
| D-09-23 | Module 09 自身继续只依赖 Module 04 端口；Module 10/11 以 PostgreSQL Adapter 替换进程内 Repository、Saver 和执行账本，且不得改变 Module 09 DTO、不可变版本链、OperationId/hash 或显式 Ref。 |
| D-09-24 | 备用点可选，每日允许 0～3 个、角色为当天实际角色的有序子集；缺项不阻止完整主行程 Completed。已有备选保持资格/距离/唯一性校验，不进主路线且不做 Directions。 |
| D-09-25 | “附近”使用 Route Matrix 路网距离不超过 3000 米；酒店+最多 6 个主地点+最多 3 个备选正好不超过 10 点/100 cells。 |
| D-09-26 | Module 08 Incomplete 但 eligible≥1 时可尝试规划；只有所有当前规划硬条件满足时 Module 09 才可 Completed，并保留上游 gaps。 |
| D-09-27 | 营业状态只允许 `compatible/conflicts/unknown`；不得用任意脆弱 parser 把 unknown 提升为已验证。 |
| D-09-28 | 枢纽 geocode 的 success/ambiguous/no-result/typed failure 分开处理；用户修正产生新规划上下文并使旧派生路线失效。 |
| D-09-29 | 本次 D-03～D-08 替代旧六轮规则：准备2、交通5、提交/修正5、独立补充5，最多17次逻辑调用；首次提交包含在5内，无额外终态调用。 |
| D-09-30 | 不提供 `save_draft`；只有控制器准入的合法草案进入 Artifact，非法提交只返回类型化反馈。 |
| D-09-31 | 新 Directions 批次超过剩余预算时整批不发；缓存命中和精确 replay 不重复消费。 |
| D-09-32 | Directions 可请求 1–3 个方案；提交必须引用 Observation 和供应商原序中的方案索引。 |
| D-09-33 | Directions 无出发时刻，只能标为供应商路线估算；不验证用户文本班次，也不声称满足。 |
| D-09-34 | 每日窗口表示允许安排的边界，不要求填满；空闲区间不是景点、餐次或 must 的替代品。 |
| D-09-35 | 备选只作信息提示，不自动切换；真实替换需要重新规划或未来 Module 16。 |
| D-09-36 | Module 09 不处理路线价格；当前 Module 12 只标记费用未核验，不声称满足用户总预算。可信费用判定等待另行批准的价格来源，也不得因费用结果自动回到 Module 09 删改行程。 |
| D-09-37 | 完整游览日全部主路线 Directions 原始耗时总和上限：轻松 120、标准 180、紧凑 240 分钟；抵离日不套用总量上限。 |
| D-09-38 | 所有模式的单条路线硬上限：轻松 60、标准 90、紧凑 120 分钟；冲突必须重排或 Incomplete，不自动放宽。 |
| D-09-39 | 取消 Citywalk 和所有步行专属限制；普通步行只是 `TravelMode.WALKING`。 |
| D-09-40 | 儿童/老人只作软提示，不自动生成数值限制；用户文本行动限制解释延期。 |
| D-09-41 | Module 09 完全不考虑天气。 |
| D-09-42 | 默认酒店可寄存行李，不因行李限制抵离日活动；不得宣传为已核验服务。 |
| D-09-43 | 全部时间按 `Asia/Shanghai` 连续 aware datetime 计算，允许跨午夜；跨日占用真实压缩相邻日期。 |
| D-09-44 | 用户选择“由 Agent 决定”时，模型为整趟选择一个全局 `effective_pace`，首次结构合法终止尝试即锁定。 |
| D-09-45 | 每日主题由 Module 09 保存；普通游览日由模型基于实际安排概括，纯交通日使用固定标签，且不参与硬校验。 |
| D-09-46 | 文本作息解释延期；使用默认窗口及结构化抵离占用；policy 保留 pace 锁定，文本规则为空。 |
| D-09-47 | 活动时间锚点授权延期；不据此突破默认窗口；POI 访问不能冒充参加定时活动。 |
| D-09-48 | 首版不评估饮食适配、不据此筛选或阻断，也不声称餐厅满足饮食要求。 |
| D-09-49 | 保存模型选择的交通展示语义和百度路线模式；文本交通限制延期，不宣称提供叫车、班次或无障碍服务。 |
| D-09-50 | “逐段路线”保留静态摘要；公交乘车顺序按本文“公交乘车步骤扩展”保存供应商说明，不增加实时导航。 |

### 5.2 从上位合同直接继承

以下不是本轮偏好选择，而是仓库真源已经冻结、Module 09 必须原样继承的约束：

- Module 09 属于 WP4，直接依赖精确为 01、02、04、06、07、08；
- Module 08 与 Module 09 各自拥有独立局部 ReAct，Module 13 是跨模块确定性编排和 supplemental 闭环所有者，Module 09 不成为第二套主工作流；
- `planning.model_invocation=17`、`planning.direction_call=8×sightseeing_days+2`，分类额度及调用前准入按 §12.2，独立地图实际调用并发上限为 3；
- 每日最多 4 个景点、1 个午餐地点、1 个晚餐地点，进入主路线的候选地点最多 6；
- Module 09 不直接访问 MCP、供应商 SDK 或 HTTP，只使用 Module 06 typed 公共地图能力；
- Module 10 持久化 Artifact／evidence，Module 11 持久化 checkpoint、执行预算并负责跨进程恢复，Module 12 只格式化且标记费用未核验，Module 13 管理主图/OperationId/终态映射，Module 14 提供应用服务、应用 API 和前端网页；
- 默认模块测试是离线 skill-contract，使用 fake adapter；正式验收不使用真实凭据或网络；生产包 line、branch、combined coverage 三项默认均为 70%。

### 5.3 未决与延期登记

本次能力延期按 §3.3 和延期记录 DEF-09-010 执行，是用户明确收敛 Demo 范围，不是已修复/PASS 或低概率缺陷。既有 DEF-09-001～009 保持各自记录，不由本次自动关闭或转入实施。天气、Citywalk/步行专属限制、饮食适配、价格和实时导航继续为非目标。未来回收文本规则须另行批准结构化输入及实施范围。

## 6. 公共输入与前驱准入

### 6.1 Initial 输入

`ItineraryPlanningInitialRequest` 精确包含：

- `schema_version="2.0"`；
- `confirmed: ConfirmedTravelRequirement`；
- `hotel_selection_ref: ArtifactRef`；
- `candidate_set_ref: ArtifactRef`；
- `operation_plan: ItineraryPlanningOperationPlan`。

行为边界必须按 Module 04 两段式 fresh strict JSON 算法复验整个请求。启动前按固定顺序验证：

1. requirement outcome 精确为 confirmed、schema `1.0`，目的地、日期、`sightseeing_days`、抵离枢纽/时间和必要字段齐全；`sightseeing_days` 必须为 1..60，与 Module 08 已消费的候选研究范围一致；
2. `hotel_selection_ref` 精确指向同 run 的 `hotel.selection/1.0`，读取后 decision 必须是 selected；
3. `candidate_set_ref` 精确指向同 run 的 `candidate_research.candidate_set/1.0`，record 只能是 completed 或 incomplete；
4. candidate-set 内的 requirement digest、hotel selection Ref 与本次输入一致；
5. candidate-set 至少一个 `CandidateState.ELIGIBLE`；eligible 为零时 Module 13 不应调用，若仍调用则在 OperationPlan 可安全持久化时返回 `ItineraryPlanningFailed(ControlPlanningFailure(input_invalid))`，且模型/地图调用为零；
6. OperationPlan 的全部 ID 同 run、同 `WorkflowPhase.ITINERARY_PLANNING`、唯一、容量与天数/预算相符；
7. Prompt 资源版本和 hash 完整。

Module 08 Incomplete 不自动使 Module 09 Incomplete。Module 09 必须保留 predecessor gaps，并仅对当前规划实际依赖的未满足条件作业务判断。

### 6.2 Resume 输入

`ItineraryPlanningResumeRequest/3.0` 按字段声明顺序精确包含：

- `schema_version: Literal["3.0"] = "3.0"`；
- `predecessor_planning_ref: ArtifactRef`；
- `supplemented_candidate_set_ref: ArtifactRef`；
- `operation_plan: ItineraryPlanningOperationPlan`；
- `supplemental_operation_plan: CandidateResearchOperationPlan`，required，无默认值。

`operation_plan` 是本次新 Module 09 调用的规划计划；`supplemental_operation_plan` 是 Module 13 实际调用 Module 08 补研时使用的完整原计划。两者必须属于相同 run，但分别服从 `itinerary_planning` 与 `candidate_research` 的 phase、slot 和唯一性合同。Module 09 不执行后者的 slot、不调用 Module 08 服务、不消费其预算。旧 Resume `1.0/2.0` 或缺少新字段的请求按 §18.1 fresh strict 边界拒绝；具体版本兼容规则见 §20.1。

resume 只允许 predecessor 为同 run 的 `awaiting_candidate_supplement` itinerary record。新 candidate-set 必须：

- 与 predecessor 的 requirement digest、hotel Ref 和 run 一致；
- 是 Module 08 completed/incomplete 的合法可消费 record；
- 保留既有 UID 的 `admission_ordinal` 与 handle 身份，并只按 Module 08 supplemental 合同追加新 UID 或更新既有 UID 的合法字段；
- 按 §15.1 将 awaiting 中已保存的 pending gap 与实际补研 OperationPlan 重建为完整 Module 08 请求，并核对后继的前驱 Ref、terminal OperationId 和 `invocation_request_digest`；
- 不能是 Failed。

合法 Incomplete、无新增 UID 或仅更新旧 UID 的补研结果，只要满足关联合同且 eligible≥1，均可进入恢复后的重验；不得以“目标已完全满足”“必须新增 UID”或结果 `pending_gaps` 与原请求相似替代关联证据。获准 resume 不等于规划 Completed。

控制器必须逐个重验全部既有 UID 的最新 `state`、role、meal、must/avoid、evidence 和坐标。失去 eligible/角色资格或变为 avoid/conflicted 的主访问/备选及依赖它们的合法前缀立即失效，不得继续安排；旧 ordinal/handle 仍保留但标为不可用。坐标、端点、mode 和 alternatives 完全未变的 Directions 可保留为路线事实缓存，但不能证明该候选仍有规划资格。

resume 继续使用相同 `effective_pace`、时间策略、交通策略、方向预算、current_stage/四项已用次数、缓存、拒绝基线 和仍有效的路线证据。不得新建 planning run、重置预算或重新解释已锁定策略。

### 6.3 前驱变化和失效

确认需求、酒店选择或目的地枢纽被用户修订时，Module 13 必须创建新的规划上下文和新 OperationId 计划。旧 itinerary Artifact 保留不可变历史，但不得继续写入或与新输入混用。候选 supplemental 不是需求修订，按 §6.2 恢复同一 run。

## 7. 公共 API 与包边界

生产包固定为 `app/itinerary_planning`，包根只导出附录 A 标记为 public 的名称。禁止 wildcard export，禁止从包根泄露私有 runtime port、Prompt loader helper、内部状态、锁、canonical helper 或测试 fake。

`ItineraryPlanningPort` 只声明：

```python
class ItineraryPlanningPort(Protocol):
    async def plan_initial(
        self, request: ItineraryPlanningInitialRequest
    ) -> ItineraryPlanningOutcome: ...

    async def plan_resume(
        self, request: ItineraryPlanningResumeRequest
    ) -> ItineraryPlanningOutcome: ...

    async def close_run(self, run_id: UUID) -> None: ...
```

`ItineraryPlanningService` 构造签名固定为：

```python
def __init__(
    *,
    runtime: DeepSeekRuntime,
    map_adapter: BaiduMapAdapter,
    artifact_repository: ArtifactRepository,
    budget_manager: ExecutionBudgetManager,
) -> None: ...
```

服务另有 `async def aclose(self) -> None` 作为兜底。`close_run`/`aclose` 只关闭 Module 09 自己创建的 `ModelOperation` 和私有 run context，不关闭注入的 runtime、地图 adapter、Repository 或 Budget Manager。

内部最小 runtime port 只声明 `start_operation(ModelRole)`；operation port 只声明 `ainvoke_tools`、`acontinue_tools` 和同步 `close()`，以便测试使用结构化 fake。生产必须以 `ModelRole.ITINERARY_PLANNING` 启动一个同 run operation。

## 8. 时间、日期与排程合同

### 8.1 时区和分钟排程

- 所有日历日期属于目的地 `Asia/Shanghai`。
- 抵达、离开、访问和路线占用在内部使用 aware datetime 连续计算，不在午夜截断。
- 模型提交的活动和路线开始/结束使用整数分钟，秒和微秒均为零，分钟可以为 00–59。
- 上游已结构化的抵离精确/近似时刻和供应商 `duration_s` 保留原值，不从文本提取活动时刻。
- 路线排程占用为 `((duration_s + 599) // 600) * 10` 分钟；`duration_s=0` 时占用为 0，0秒不增加最低占用；非零耗时按10分钟向上取整作为总交通预留。
- 抵离时刻保持原始精度，最早可排活动只能向上取整到一分钟；最晚截止时间不得向后取整。首版不启用用户文本活动锚点。
- 访问区间是半开区间 `[start_at, end_at)`；任意重叠非法。

### 8.2 普通窗口和每日策略

普通游览窗口为 07:30–22:00，并按结构化抵离、已有跨午夜占用缩小。存在文本作息也不覆盖默认。

沿用 EffectivePlanningPolicy 和 proposal 格式，首版业务取值为 daily_time_rules=()、time_sensitive_must=()、transport_rules=()；仅启用 effective_pace。空 tuple 表示能力未启用，不代表用户没有原文要求。

显式 pace 在 run 创建时锁定；Agent 代选时，首次结构合法 submit/supplement 终止尝试中的合法 pace 在业务检查前锁定。控制器建立三空 tuple 的 policy；即使该次因非空延期规则或非法草案被拒绝，锁定 pace 也不得修改。不保存模型延期规则，也不得静默删除规则后接受同一次提交。

非空延期规则、访问 time_sensitive_anchor_ids 或 duration_source=user_explicit 均为业务拒绝。submit 依 §18.4 消耗迭代并允许修订；supplement 按既有业务拒绝终止流程处理。再次更改 pace 属业务拒绝，真正锁定快照损坏才为 policy_contract_invalid。首版不运行中文关键词、否定词、日期段、餐次或时长解析器。纯 DTO 格式仍保留，结构可解析不等于首版服务授权。

### 8.3 抵达与离开

固定安全处理：

| 枢纽 | 抵达后处理 | 离开前到达枢纽 |
|---|---:|---:|
| 机场 | 60 分钟 | 120 分钟 |
| 火车站 | 30 分钟 | 60 分钟 |

`is_approximate=true` 时在对应方向再增加 30 分钟：抵达侧推迟可用时间，离开侧提前最晚离店时间。首版固定使用上述处理时间与近似余量；上游没有额外处理余量的结构化字段，不从文本增减。

`ItineraryDraft.days` 只覆盖 `sightseeing_start_date..sightseeing_end_date`。抵达与离开各由一个独立 `BoundaryTransfer` 承载，因此 Module 01 合法的“前一日抵达”或“后一日离开”不需要伪造额外游览日：

- arrival transfer 固定 `arrival_hub → hotel`，路线区间开始不得早于抵达时刻加处理/近似余量；
- departure transfer 固定 `hotel → departure_hub`，路线区间结束不得晚于离开时刻减处理/近似余量；
- transfer 各自需要匹配 Directions、受单腿上限约束，但不计 full-day 酒店闭环总交通；
- transfer 日期在游览区间外时，该日期不安排 Module 09 候选访问，也不创建空 `DayPlan`；
- transfer 与首/末 `DayPlan` 同日时，arrival transfer 必须先于该日全部访问，departure transfer 必须后于该日返回酒店，形成 `arrival_hub → hotel → optional visits → hotel → departure_hub`；
- 跨午夜 Directions 和处理时间占用下一日期真实时间，并压缩相邻 `DayPlan` 的开放区间。

每个 `DayPlan` 自身仍固定为 `hotel → zero-or-more visits → hotel`。因此完整路线骨架由 arrival boundary transfer、连续游览日酒店闭环和 departure boundary transfer 三部分共同组成。

### 8.4 时间型 must（首版延期）

上游没有可直接消费的活动日期、时刻与时长结构；地点详情和营业时间也不是事件时间证据。首版不生成或授权 TimeSensitiveMustAnchor，user_provided 标签不能启用能力。

must/avoid 地点匹配继续消费 08 结构化结果。本次只说明地点已排入，不证明参加升旗、预约场次或准时在场；不猜时刻、不突破默认窗口、不请求补研活动时间。08 未解决的地点匹配缺口仍必须保留，不能按延期丢弃。

此前确认的提前整点/半点到场、按原始时刻加时长后向上取整、日期例外和交通适用范围，统一留作后续更新参考，不是当前 Demo 验收项。

### 8.5 活动时长与餐次

首版统一使用 planning_estimate，模型只可在下列范围按整数分钟选择规划估算：

| 类型 | 默认允许范围 |
|---|---:|
| attraction | 60–240 分钟 |
| lunch / dinner | 60–120 分钟 |
| attraction+lunch / attraction+dinner | 90–240 分钟 |

文本时长和指定餐次时刻延期，不覆盖默认。完整游览日午餐开始在 11:30–13:30、晚餐开始在 17:30–19:30。一个组合地点只能承担一顿餐。

完整游览日恰好一顿午餐和一顿晚餐。受抵离或跨日占用影响的 partial 日期，午餐和晚餐分别允许 0..1 顿，由模型根据真实可用窗口、用户要求和节奏决定；控制器只校验已安排餐次的角色、窗口和路线合法，不设置 partial 餐次硬配额，也不得为凑餐次越过不可移动边界。`transit_only` 无餐次配额。

### 8.6 日期类型和密度

- `full`：该日期不受抵达、离开或上一日跨午夜占用压缩；按完整日景点/餐次规则。
- `partial`：被上述不可移动边界压缩但仍有至少一个合法非酒店访问。
- `transit_only`：只有枢纽/酒店转移，没有候选访问。

完整日景点数量：`TravelPace.RELAXED` 1–2、`NORMAL` 精确 3、`INTENSE` 精确 4。partial 为 0..该档上限，不设利用率或“填满时间”门槛，但 must、已触发餐次和所有硬约束仍必须满足。空闲时间以 `open_interval` 表示，不可计作活动或要求满足。

## 9. 地点、候选与每日结构

### 9.1 地点引用

主路线地点使用封闭 union：

- `HotelLocationRef`：只指向已选酒店；
- `CandidateLocationRef`：以 candidate-set 内 `admission_ordinal + uid` 双重绑定；
- `HubLocationRef`：arrival/departure，加 hub geocode Observation index。

行程 Artifact 不复制酒店或候选的完整地址/坐标；只保存稳定引用和必要展示快照。控制器每次读回时重新核验 UID、ordinal、Ref 和坐标一致。

### 9.2 候选资格

Module 09 只能选择 `CandidateState.ELIGIBLE`，且所用角色必须在 `CandidateRecord.roles` 中为 supported。withheld、excluded、conflicted 均禁止进入主计划或备选。

`effective_interests` 只影响模型偏好和主题，不是硬覆盖率。Module 09 不修改 interest、role、must/avoid 或 details verdict。

普通候选 UID 在整趟主行程最多出现一次；组合角色仍只出现一次。酒店 UID 不适用此限制。任何备选 UID：

- 不得与任一主行程 UID 相同；
- 不得被多个备选槽复用；
- 必须在当前 candidate-set 中 eligible 且支持目标角色。

### 9.3 must 与 avoid

- must 全集来自 08 state 的完整 requirement_group_states，并核验 item IDs 覆盖 confirmed must 列表；不得仅从已匹配候选反推全集。每组至少由一个主行程 eligible UID 的 confirmed match 满足，未匹配组及相关 pending gap 不得消失；
- 同一 UID 可以同时满足多个等价 must 组；
- must 候选同样遵守整趟只访问一次；
- 已确认 avoid 或未解决 avoid 的候选不得使用；
- 无可排 must、must 与时间/交通/容量冲突时返回 Incomplete；
- 备选不计 must 完成，不要求属于相同 must 组，也不得声称可替代 must。

### 9.4 每日主地点和组合角色

每日期最多 4 个 attraction role、1 个 lunch、1 个 dinner；进入主路线的候选 UID 最多 6。酒店和枢纽不计入 6。

只有同一 eligible CandidateRecord 同时具备 attraction supported 与对应 meal intent/restaurant supported，且时间、营业和路线合法时，才允许 `attraction_lunch` 或 `attraction_dinner`。组合地点同时计一个景点和一顿餐，只占一个主地点，不能计两顿餐。

### 9.5 营业状态

每个已排访问保存：

- `compatible`：上游已支持且无冲突，或模型对可见百度营业文本作出的带证据解释；
- `conflicts`：上游证据明确冲突；禁止安排；
- `unknown`：缺少、不可解析、互相冲突或只存在未验证推断。

控制器不实现任意中文营业时间 parser。模型不得把 Module 08 的 `business_hours_unknown`、缺失 Details 或冲突 evidence 提升为 compatible。unknown 可以进入计划，但保存 `requires_verification=true`，并由 Module 09/12 保持未核验标签；Module 10 只持久化该状态。

### 9.6 附近备选

每个非 `transit_only` 日期可以按实际存在的核心角色建立 0～3 个备选，允许全部省略或只提供部分。角色保持 attraction、lunch、dinner 的有序子集且每类至多一个：

- 当天至少一个景点时，模型标记一个 `core_attraction`，可为其提供一个 attraction 备选；
- 当天有 lunch 时，可为 lunch 提供一个 restaurant/lunch 备选；
- 当天有 dinner 时，可为 dinner 提供一个 restaurant/dinner 备选。

备选须满足 eligible、角色、餐次/营业兼容规则、全程 UID 唯一且不进主路线。距离使用 `MatrixTravelMode.WALKING` 的 Route Matrix 路网距离，必须 `<=3000m`；这只是统一的附近度量，不是步行推荐或步行限制。备选不调用 Directions，不计路线腿，不自动替换。缺少备选不产生阻塞缺口、不触发备用点专用 supplement，也不导致 Incomplete；主规划成立即可 Completed。提供了非法备选仍拒绝，由模型明确修正或移除后重新提交。

单日 Matrix 点集为已选酒店、最多 6 个主候选和最多 3 个备选，去重后最多 10 点，完整 `N×N` 不超过 Module 06 的 100-cell 上限。抵离枢纽不加入该日比较矩阵；枢纽只做必要 Directions。

## 10. 路线语义与地图工具

### 10.1 路线双层表示

每条 `RouteLeg` 同时保存：

- `transport_semantic`：模型在首版合法模式内选择的交通展示语义，不表示已执行用户文本限制；
- `route_mode: TravelMode`：实际传给 Module 06 的四种模式。

固定映射：

| 用户语义 | route mode | 可声称事实 |
|---|---|---|
| walking | walking | 步行路线距离/耗时估算 |
| cycling | riding | 骑行路线距离/耗时估算 |
| public_transit | transit | 公交路线距离/耗时估算，不保证只含地铁 |
| ride_hailing | driving | 驾车路线距离/耗时估算，不证明叫车可用或价格 |
| private_car | driving | 驾车路线距离/耗时估算 |

首版不执行“地铁优先”“全程只能地铁”等文本要求。模型选择的模式仍须符合上表，非法模式或错误映射必须拒绝；不能因文本功能延期跳过路线 Gate。

### 10.2 允许的模型工具

工具白名单精确为：

1. `get_candidate_evidence`；
2. `geocode_hub`；
3. `calculate_route_matrix`；
4. `plan_directions`；
5. `submit_itinerary`；
6. `request_candidate_supplement`。

禁止 search、place details、reverse geocode、weather、map launch、价格、通用 HTTP/MCP、任意文件/代码/命令工具。Module 09 自己把白名单注册为 Module 02 `ToolContract`，Module 02 只验证模型返回的名称和参数，不执行工具。

### 10.3 工具业务语义

`get_candidate_evidence`：一次读取 1..16 个当前 candidate handle 的已存在聚合/Observation 证据；只读 Artifact，不调用外部系统、不消费地图或 Direction 预算。重复读取返回同一安全投影。

`geocode_hub`：只允许 `arrival` 或 `departure`，查询文本必须来自 confirmed requirement，city 必须是目的地；同一规范 query 每 run 最多一次外部调用。success 固定坐标；ambiguous/no-result 形成用户可处理 gap；typed exception 为 Failed。

`calculate_route_matrix`：每个 tool call 只绑定一个日期、一个 `MatrixTravelMode` 和 1..10 个已知地点，执行完整同序 `N×N`。同一业务迭代每个日期最多一条 Matrix 请求；一个 batch 可覆盖多个日期，底层以最多 3 个在途调用执行。Matrix 只支持 driving/riding/walking，不支持 transit，不可替代 Directions。

`plan_directions`：一个 tool call 可批量提交 1..本次规划 `planning.direction_call` 总上限个相邻腿意图；控制器对每腿构造一个 Module 06 `DirectionsRequest`，`alternatives_limit` 1..3。批次总长度受总上限约束，只有 cache miss 数必须不超过当前剩余额度；每个新底层请求消费一个 Direction 单位，缓存/replay 不消费。执行按模型腿顺序分成并发最多 3 的 wave。

`submit_itinerary`：携带完整候选草案、`EffectivePlanningPolicy` 和 Observation handle/alternative index。参数结构合法即锁定未锁策略；控制器完成全部准入后才写 Completed。拒绝形成安全 typed feedback，可在剩余迭代内继续；不保存非法整体。

`request_candidate_supplement`：终止当前规划调用，携带当前草案、锁定策略和可映射为 Module 08 `SupplementalGap` 的严格缺口。控制器只保存最长合法部分并返回 `candidate_supplement_requested`；Module 13 决定是否调用 Module 08。

### 10.4 Directions 是唯一完成证据

每个相邻主地点对必须精确匹配一个成功 Directions Observation：

- origin/destination 坐标与当前 Ref 相同；
- route mode 相同；
- alternative index 在供应商原序范围；
- `distance_m/duration_s` 与 Observation 摘要相同；
- Observation 来自同 run、同未失效上下文；
- `verified=true` 且 provenance capability 是 DIRECTIONS。

Directions Observation 保存各 alternative 的 index、distance_m、duration_s、step_count 和 provenance；公共交通额外按“公交乘车步骤扩展”保存 instruction 文本。行程仍只复制选中方案摘要并引用 Observation，模型不接收或重写步骤。

Directions 没有 departure time；所有耗时必须标为百度在获取时给出的路线估算。公交路线不等于时刻表。用户文本班次要求不纳入本次验证，按 §3.3 固定说明呈现范围。

## 11. 节奏和交通负担

### 11.1 全日总交通上限

完整游览日将酒店闭环中所有已选 Directions 的原始 `duration_s` 求和，不使用排程向上取整值：

| pace | 上限 |
|---|---:|
| `TravelPace.RELAXED` | 120 分钟 |
| `TravelPace.NORMAL` | 180 分钟 |
| `TravelPace.INTENSE` | 240 分钟 |

抵达/离开影响的 partial/transit_only 日期不套用总量上限，但仍受可用窗口和单腿上限约束。用户自定义交通时长上限延期；Module 09 不自行放宽既有节奏上限。

### 11.2 单腿上限

所有路线模式及枢纽腿都适用：

| pace | 单腿上限 |
|---|---:|
| `TravelPace.RELAXED` | 60 分钟 |
| `TravelPace.NORMAL` | 90 分钟 |
| `TravelPace.INTENSE` | 120 分钟 |

超过上限必须重排/换候选/换合法模式。must 或周边游导致无法满足时返回 Incomplete 请求用户修改，不自动提升 pace 或忽略硬约束。

### 11.3 儿童、老人和行动限制

人数与年龄只作模型软提示，不自动产生步行公里数、连续步行时长、休息次数或景点时长。mobility_constraints 的文本解释及无障碍适配延期，不构造硬/软交通规则、不据此筛选或单独阻断 Completed，不声称满足。

## 12. 受控 ReAct 状态机

### 12.1 角色分工

模型可以：

- 提议每日主题和分组；
- 选择 eligible 候选、核心点和备选；
- 决定顺序、时间、时长估算和交通语义；
- 决定先看哪些候选证据、何时用 Matrix、对哪些腿做 Directions；
- 比较 1..3 个 Directions alternative；
- 根据 typed Observation/拒绝反馈修订；
- 提交完整行程或请求候选 supplement。

控制器必须：

- 构建有界安全上下文和 handle 目录；
- 校验 tool/schema/handle/权限、有序目标结构、预算、重复和证据绑定；
- 执行 Artifact lookup-first、外部调用和安全投影；
- 计算日期、网格、路线闭合、数量、must、重复、备选和交通上限；
- 决定进展、停止、Completed/Incomplete/Failed；
- 持久化安全业务记录；供应商实际返回的思考仅经 M02 专用诊断保存，不能进入业务上下文或成为提交证据。

### 12.1.1 代码控制的阶段

内部阶段值为 `preparation`、`transport`、`submit_repair`。不改变 M04 的 WorkflowPhase 枚举；三个内部阶段都处于 itinerary_planning。

| 当前阶段 | 普通逻辑额度 | 实际允许工具 | 自动退出条件 |
|---|---:|---|---|
| preparation | 2 | get_candidate_evidence、geocode_hub、符合 §12.2 条件的 request_candidate_supplement | arrival/departure 均已有可信可用坐标；否则最多 2 次后明确缺口。 |
| transport | 5 | get_candidate_evidence、calculate_route_matrix、plan_directions、submit_itinerary、符合 §12.2 条件的 request_candidate_supplement | 第一次合法 submit 调用或交通额度耗尽，进入 submit_repair。 |
| submit_repair，尚未首次提交 | 5，共用本行程整个修正额度 | submit_itinerary | 执行现有完整 Gate；拒绝后按剩余额度进入修正。 |
| submit_repair，已拒绝且剩余 ≥2 | 共用上述 5 | get_candidate_evidence、calculate_route_matrix、plan_directions、submit_itinerary、符合 §12.2 条件的 request_candidate_supplement | 成功、业务不可行、无进展或剩余仅 1。 |
| submit_repair，剩余 =1 | 共用上述 5 | 仅 submit_itinerary | 成功或明确未完成，不再继续查询。 |

表中工具还必须通过既有 batch、handle、policy、缓存、OperationId 和地图余额校验。阶段工具只是子集，不允许绕过完整 Gate。准备成功立即切换，不为了用满 2 次继续读取；前驱目录中的酒店/候选可信坐标无需再次 geocode。候选证据可以在交通和修正阶段读取。

交通额度耗尽不表示路线已经足够，也不证明能提交完整行程。首次修正调用只给 submit，要求模型提交已有完整方案以获得确定性反馈；若已有 typed 事实证明核心条件不可满足，则直接保存 Incomplete 和具体 gaps，不能编造坐标、证据或路线。没有这种证明时，不建立“代码推算全部证据是否齐备”的新 Gate。

成功 submit 立即 Completed；结构/技术失败继续按既有 Failed 合同停止，普通文本继续为 no_terminal_submission。需要人工澄清、地图无合法剩余动作等停止条件仍适用。未使用的阶段次数不转移、不补发；17 是上界，不是运行必须完成的次数。

### 12.2 逻辑调用与阶段额度

### 12.2.1 唯一总账

新增 `ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY`，name=`planning.model_invocation`、scope=phase、phase=itinerary_planning、limit=17。替代当前运行的 planning.iteration，不同时保留两套消费。M02 itinerary profile semantic limit=17；每逻辑调用仍至多两次 provider attempts，理论最多 34 次实际尝试。M02 其他角色、输入/输出上限、超时和思考设置不因本修订改变。

M09 四个分类计数是总账的分解：preparation_used 0..2、transport_used 0..5、submit_repair_used 0..5、supplement_request_used 0..5。已确定快照中它们之和等于 model_invocations_consumed 0..17。M11 保存通用 planning.model_invocation 总额度；分类计数放入 M09 Artifact，不增加 SQL 列或四套 M11 账本。

每个新逻辑调用在 M02 完整 byte/quota 预检通过后、第一次 provider attempt 前，以本 invocation slot 的 action OperationId 消费一次总账。M02 的 async invoke/continue 增加可选的单次准入回调：只提供安全的 call_id/逻辑索引，不暴露历史正文。该回调由 M09 提供，复用现有 consume/replay/失败关闭规则；失败不发供应商请求。无回调的既有 M02 调用继续原行为。预检拒绝零 provider 请求、不消费总账；网络重试不得重复调用准入回调。

不能让 M09 复制 M02 私有历史来预计算 byte，也不能先执行模型再补业务预算。M02 在其调用锁内完成额度检查、回调与 semantic claim，避免并发越界；回调异常向消费方传播为现有控制失败，不伪造 LLM 技术错误。

### 12.2.2 调用结束后的唯一归属

| 模型结果 | 额度归属 |
|---|---|
| 合法且唯一的 submit_itinerary，无论接受或业务拒绝 | submit_repair；交通阶段首次提交不再计 transport。 |
| 合法且唯一的 request_candidate_supplement，无论业务接受或拒绝 | supplement_request；不再计普通阶段。 |
| 其他合法查询 batch | 发起时的普通阶段；同 batch 多工具仍只算一次。 |
| 已准入但发生 LLM/参数/结构错误或普通文本 | 发起时的普通阶段，保留失败；不获得额外调用。 |

总账只消费一次，归属分类不是退还 M11 预算。混合 submit/supplement batch 按旧结构失败处理，不执行合法子集，不靠原始工具名猜测其类别。请求记录的发起阶段与调用完成后的计费分类分开保存，不能为提前 submit 改写历史请求阶段。

补充必须符合现有主行程 candidate_supply gap、唯一 fingerprint、有效 evidence、M08 剩余研究/Place 预算，以及返回后可继续规划的条件；同时 supplement_request_used<5。额度独立不意味着任何时刻都必须允许申请：D-08 的最后仅提交规则仍有效，修正剩 1 时不能用补充绕过它。非法 supplement 继续按既有明确 Incomplete/Failed 停止，不自动重试申请。

M13 调 M08 的实际补研仍消费 M08 自己的余额，不属于 M09 的 17 次。回来后保留普通阶段、所有分类计数、M02 同进程 Operation、地图余额、缓存、草案和拒绝基线；不得重置阶段或额度。accepted supplement fingerprint 数与申请逻辑次数分别记录，前者仍至多 5。



回调准入成功后才进入归属结算。若业务预算消费失败或控制取消使准入结果不确定，保持既有失败关闭及 budget_snapshot_complete=false；不得填零证明总账与分类齐全。准入后产生的确定技术错误计发起阶段一次，没有观察到响应不能伪造 usage。

### 12.3 批次和终止工具

- 结构合法的非终止 batch 总调用数最多为 `sightseeing_days + 11`，在 1..60 天输入范围内即最多 12..71。组成上限精确为：`get_candidate_evidence` 0..8 次且全批 candidate handle 唯一、`geocode_hub` 0..2 次且 endpoint 唯一、`calculate_route_matrix` 0..sightseeing_days 次且 date 唯一、`plan_directions` 0..1 次并在该调用内合并全部腿。
- 同 batch 每个调用只能引用该轮开始时已存在的 handle，不能依赖 sibling 调用尚未产生的坐标、Matrix 或 Directions handle；需要依赖 Observation 时必须下一轮继续。
- `submit_itinerary` 或 `request_candidate_supplement` 出现时，必须是 batch 唯一调用；混合 batch 是 `action_batch_invalid` 结构失败，不执行合法子集，也不返回可继续的业务拒绝 Observation；已准入逻辑调用仍计入发起阶段。
- 合法 accepted submit 和 accepted supplement 都终止当前公共调用。
- schema-valid 的 submit 无论业务接受或拒绝，都占一次提交/修正额度；拒绝后按剩余额度和 §12.4 继续。
- Module 02 返回 unknown tool、tool arguments invalid、结构非法或 LLM failure 时为技术 Failed，不做 JSON repair 或近似工具名匹配。
- `ToolTurnCompleted` 普通文本永远不是成功；保存已有 last-consistent 并返回 `Incomplete(no_terminal_submission)`。

### 12.4 目标优先级和进展

控制器每轮构造有序目标提示：输入/策略澄清、未解析枢纽、未满足 must、缺餐次/景点密度、路线不可达、Direction 缺失、时间/交通冲突、可选备选建议、软优化。该顺序用于 Prompt 解释、进展比较和最终 gap 排序，不是强制工具顺序。模型可以选择任何当前合法且能推进规划的白名单 Action；控制器只能因真实 schema、handle、预算、重复或硬约束冲突拒绝，不能仅因模型没有先处理列表首项而 `policy_rejected`。

首次业务拒绝保存 reason、field、提交业务指纹及当前相关证据指纹，作为基线，不因此停止。修正查询消耗修正逻辑额度；当剩余 1 时实际 provider schema 与本地 gate 同步收紧至 submit。候选补充不能改变此最后门槛。

连续两次提交业务拒绝满足全部条件才因 no_progress 停止：相同 reason/field；业务规划指纹相同；没有被新提交采用的相关新证据。查询本身不清除拒绝基线；重复缓存或无关证据不算改进。新的拒绝位置、实际业务变化或相关新证据使基线更新，允许在剩余 5 次内继续。

业务指纹复用确定性 canonical JSON：比较日期/日类型、地点与角色、时间窗/时长、访问顺序、route leg ID/起终点/模式/alternative/证据绑定、营业判断及硬策略；排除 theme、notes、description、解释文字与拒绝原因措辞。相关证据仅指新提交实际引用的候选证据、Matrix/Directions/枢纽事实；不增加通用语义相似度模型或“自动判断修正意图”的模块。

准备与交通仍可复用已有真实进展判断；两个阶段的普通额度独立封顶。改变主题、重复读取或重复缓存不作为进展。修正的重复拒绝基线替代旧统一“任何两轮无进展”截断，避免新相关查询或有效修改被提前终止。

准备/交通沿用既有连续两次真实无进展停止：新候选证据或新枢纽/Matrix/Directions Observation、合法前缀满足新硬目标是进展，精确 replay/无关读取/纯文字变化不是。当前不存在合法白名单动作时立即 no_legal_action。修正阶段只采用上述连续重复业务拒绝规则，不混用准备/交通 streak。

### 12.5 参考流程

```text
fresh-strict validate request; derive inline digest and terminal ID only
lookup terminal successor; exact hit replays without predecessor reads
on terminal miss, validate predecessors and full planning semantics
load/create same-run context and last-consistent state
for each remaining logical invocation slot (total <= 17):
  advance preparation/transport/submit_repair using code rules
  derive effective tool subset, objectives and safe handle context
  invoke/continue Module 02 with byte/quota preflight and one budget-admission callback
  validate tool/schema/batch and terminal exclusivity if a tool result exists
  classify the admitted invocation exactly once, including failure/final text
  persist immutable safe model-action with request_stage and final budget_category
  if LLM failure or structural failure: Failed without executing any subset
  if ordinary final text: Incomplete(no_terminal_submission)
  validate handles, business policy and duplicates
  if valid submit:
      freeze effective policy before business validation
      validate whole draft and every selected Direction
      if accepted: persist terminal Completed and return
      else return typed rejection; retain stronger legal prefix; compare repair rejection baseline
  if valid supplement:
      freeze effective policy
      validate typed/evidenced gap and fingerprint
      persist awaiting-supplement state and return request
  apply business-policy admission; rejected batch returns typed observation
  reserve/execute admitted nonterminal tools in deterministic order
  persist typed observations, update evidence/cache/last-consistent
  derive progress, gaps and stop conditions
when submit_repair remaining == 1: only submit_itinerary is sent to provider
when completed or stop condition applies: persist terminal and stop, without extra invocation
```

## 13. 预算、并发、缓存与最长合法前缀

### 13.1 固定预算

| key | scope/phase | limit |
|---|---|---:|
| `planning.model_invocation` | phase/itinerary_planning | 17 |
| `planning.direction_call` | phase/itinerary_planning | `8 × confirmed.time_window.sightseeing_days + 2` |

公式中的 `days` 精确指 Module 01 的 `confirmed.time_window.sightseeing_days`，不含游览区间外的 arrival/departure transfer 日期。Direction 上限由 Module 13 在创建 `ExecutionBudgetPlan` 时用该已确认值求出。Module 09 不创建、更改或扫描 BudgetPlan。Geocode/Matrix 没有独立 Module 04 预算键，但受 OperationPlan 槽位、每轮/每日规则和 Artifact replay 约束。

### 13.2 Direction 批次预占

处理 `plan_directions` 时先分离 cache/replay 和新请求。若新请求数超过 `planning.direction_call.remaining`，整个新批次返回 `not_admitted_budget_exhausted`，零新 consume、零地图调用；缓存项仍可返回但不能掩盖整批拒绝。

足够时按模型腿顺序，以每个 Direction Observation OperationId 分别 `amount=1` 消费。所有新槽完成预占屏障后才发出任何 Directions。屏障中途非取消基础设施失败：不发地图，已 applied 不退款，为能安全对应的槽写 `not_issued_reservation_barrier_failed` 后 Failed。取消时立即停止后续 consume/write/call，不补写 not-issued。

Directions 一经发出，无论 success、no-result、timeout、限流、失败或取消均不退款。精确 cache/replay 不消费。

### 13.3 地图并发和有序聚合

所有独立地图调用共享“最多 3 个在途”的业务波次限制，不能假设 Module 06 的技术 semaphore 等同业务调度。模型顺序决定 ordinal；结果按 ordinal 聚合，不按完成先后排序。

同一 wave 任一非取消技术错误后，不启动后续 wave；已发 sibling 使用 `gather(return_exceptions=True)` 收束，成功结果可在取消信号出现前按规则持久化。主失败按 §18 全序选取。

### 13.4 缓存和失效

正文中的安全指纹分类如下；每个 payload 的精确字段、顺序语义和 domain version 以附录 B.6 为唯一实现合同：

- geocode：目的地、endpoint kind、规范 hub 文本；
- Matrix：日期、mode、同序地点 Ref/坐标；
- Directions：origin/destination Ref/坐标、mode、alternatives limit；
- candidate evidence：candidate-set Ref、candidate handle、Observation Ref 集；
- submit：requirement digest、hotel Ref、candidate-set Ref、锁定策略、完整草案和证据 handle 选择。

同 fingerprint 同上下文复用；同 OperationId 不同内容冲突。需求、酒店、枢纽纠正使依赖它的 Matrix/Directions/草案失效；candidate supplement 只使被替换/新增候选相关选择失效，坐标和模式完全相同的既有 Directions 可按指纹复用。

### 13.5 最长合法前缀

控制器可以从 rejected submit 或 supplement draft 中提取按日期、访问顺序和路线证据闭合的最长合法前缀。前缀必须：

- 只含 eligible、未重复、角色合法地点；
- 从合法锚点开始并以当前已证明的锚点闭合；
- 每条已包含腿有匹配 Directions；
- 时间不重叠、不越过硬边界；
- 不声称尚未满足的 must、餐次、备选或完整日密度。

无法证明的尾部全部丢弃并形成 gap。非法原提交本身不得持久化，且不得为了延长前缀伪造路线或自动换候选。

## 14. OperationId、幂等与 replay

### 14.1 OperationPlan

所有 OperationId 由 Module 13 在公共调用前提供。Module 09 不接收 factory、不生成 UUID、不构造新逻辑步骤。

每个公共 initial/resume call 的 invocation_slots 从已消费逻辑索引连续分配到 16，action/state 每个 slot 各一 ID。删除旧 terminal_action_operation_id 和独立 terminal-only 分支；terminal_successor_operation_id 保留，用于公共调用终态 Artifact。所有实际发生的模型调用，包括 accepted submit/supplement 和失败，使用其统一 slot。

hub 总可用新调用 2、Direction 总预算 8×days+2、地图并发 3 保持。Matrix OperationId 可按该公共 call 剩余逻辑 slots×days 保守分配，不代表新增 Matrix 调用预算；unused ID 不创建消费。resume slots、地图 IDs 仍使用 M13 的公共调用编号隔离，跨补研全局计数和剩余额度不变。

每个 PlanningInvocationOperationIds 含 schema_version=2.0、invocation_slot_index 0..16、action_operation_id、planning_state_operation_id。全部 ID 同 run、phase=itinerary_planning，跨整个计划唯一。工具 Observation 按模型 batch/ordinal 取对应目录连续前缀；未使用 ID 永不消费。

### 14.2 lookup-first 与中间状态

公共入口首先 lookup terminal successor。命中 terminal itinerary record 时，在创建私有 context 或调用模型/地图前校验请求摘要、Ref 链和终态 payload，并逐字段重建原 public outcome。

terminal 不存在时按 state/action/observation 固定顺序读取。Action 已持久化但其声明为“可能已发”的外部 Observation 缺失，或预算已 consumed 但 Observation absent，返回 `operation_result_indeterminate`，不得重发。只在能证明调用尚未发出时继续。

每个已结算逻辑调用以 slot 的 state OperationId 保存 `in_progress` 快照。Completed、Incomplete、Failed、candidate-supplement-requested 使用 terminal successor 保存可重建 record。返回 public outcome 必须从刚读回的 fresh terminal Artifact 与其 Ref 构造，不从另一份内存对象返回。

### 14.3 singleflight 和 run 锁

- terminal successor OperationId 是一次公共调用的 singleflight key；
- 同 key 同请求并发只执行一次，waiter 得到相同 outcome；
- 同 run 不同 terminal ID 使用 run 执行锁串行；
- 不同 run 可并发，但仍共享地图并发上限 3；
- resume predecessor 竞争中只有与当前 last-consistent Ref 匹配者可执行；其他请求失败关闭；
- `close_run` 与业务调用共用 run 锁，等待进行中调用完成或取消传播后清理。

### 14.4 terminal 写失败

唯一末跳例外是 terminal itinerary Artifact 自身写入失败：不得递归写第二个 Failed Artifact。服务返回仅含安全 `ControlPlanningFailure(artifact_error)` 的内存 Failed，`retryable=false、possible_duplicate=true`；同进程 singleflight waiter 复用。之后 terminal 仍 absent 且存在已消费预算或部分 Artifact 时，后续调用必须返回 `operation_result_indeterminate`。

## 15. 候选 supplemental 闭环

Module 09 只能提出请求，Module 13 才能调用 Module 08。

合法 supplement 需满足：

- 当前 candidate-set 为 completed/incomplete 且至少一名 eligible；
- gap 可精确映射为 Module 08 公共 `SupplementalGap` 分支；
- 每个 gap 携带 1..8 个相关 `candidate_research.place_observation/1.0` evidence Ref；
- anchor 是已选酒店或已有 eligible candidate；
- 不请求搜索天气、价格、活动时刻、饮食适配、无障碍认证或任意自由文本任务；
- fingerprint 在本 planning run 尚未请求；
- supplement_request_used<5，且 accepted supplement fingerprint 少于 5；独立申请额度与普通 2+5+5 合计最多 17，非法/业务拒绝申请也按 §12.2 唯一结算；
- 返回后仍有合法可继续规划的普通额度，且通过 §12.1.1 的阶段工具门槛；修正剩 1 次时只能提交，不能申请补充；
- 当前有 last-consistent 合法草案或可证明的零草案状态。

Module 09 gap 到 Module 08 的映射精确为下表；`gap_id` 和同序 `evidence_refs` 原样传递，Module 13 只做类型转换，不重新选择分支或补造字段。按 D-09-69，新范围仅准入主行程的 candidate_supply；表中 BackupUnavailableGap 两行仅保留历史结构定义，备用点专用请求不再接受：

| Module 09 来源 | Module 08 `SupplementalGap` |
|---|---|
| `CandidateSupplyGap.target=CandidateCountTarget` | `CandidateCountShortfallGap`，同 role/current_count/target_count |
| `...=AreaCoverageTarget` | `AreaCoverageMissingGap`，同 role/radius；hotel Ref→`HotelAnchorRef`，candidate Ref→含同 UID 的 `CandidateAnchorRef` |
| `...=MealCoverageTarget` | `MealCoverageMissingGap`，同 meal_intent/radius 和上述 anchor 转换 |
| `...=MustResolutionTarget` | `MustVisitResolutionMissingGap`，同 requirement group/item IDs |
| `...=AvoidResolutionTarget` | `AvoidResolutionMissingGap`，同 requirement group/item IDs/candidate UIDs |
| `...=CandidateDetailsTarget` | `CandidateDetailsMissingGap`，同序唯一 candidate UIDs |
| `...=ReplacementTarget` | `ReplacementCandidateNeededGap`，同 rejected UID/role/radius 和上述可空 anchor 转换 |
| `BackupUnavailableGap.backup_kind=attraction` | `AreaCoverageMissingGap(role=ATTRACTION, anchor=core visit candidate, radius_km=3)` |
| `BackupUnavailableGap.backup_kind=lunch|dinner` | `MealCoverageMissingGap(meal_intent=对应餐次, anchor=core visit candidate, radius_km=3)` |

`InterestCoverageMissingGap` 没有 Module 09 映射，因为兴趣只作偏好。每个 target 必须先由 confirmed、当前 candidate-set 与 evidence 确定性重建并逐字段相等；不满足 Module 08 分支 validator 时拒绝 supplement，而不是选择“最接近”的分支。

accepted 后返回 `ItineraryPlanningCandidateSupplementRequested`。Module 13：

1. 复验 gap、fingerprint 和证据；
2. 同 fingerprint 最多调用 Module 08 一次；
3. 传入新 Module 08 OperationPlan；
4. 得到 Completed/Incomplete 且 eligible≥1 时，以 Resume `3.0` 调用 `plan_resume`，同时传入新的 Module 09 `operation_plan` 和第 3 步实际使用的 `supplemental_operation_plan`；
5. Module 08 Failed、eligible=0、非法空证据或重复 fingerprint 时不恢复，保持/映射业务或技术终态。

补充不会重置planning的阶段/四项次数/拒绝基线、Direction预算、缓存或锁定策略。没有合法 evidence 的业务缺口只能留在 Module 09 Incomplete 或由 Module 13 请求用户处理，不能伪造 Ref。

### 15.1 Resume 补研关联核验

本节只扩展 Module 09 Resume 输入，不改变 Module 08 公共 DTO、Artifact、写入逻辑或摘要算法。Module 13 必须使用上表冻结映射构造原补研请求，并保留实际使用的 OperationPlan；不得重新生成一个语义近似的计划作为 resume 参数。

terminal lookup miss 后，控制器按以下顺序核验；B.6 的 Resume 请求摘要及 exact replay 必须先完成，不为计算该摘要提前读取前驱：

1. fresh 解码 `predecessor_planning_ref` 指向的同 run awaiting record，确认它是当前 run 的 last-consistent awaiting 状态；其已保存 `gap_fingerprint`、pending call ID 与 Module 09 自己保存的当前 pending 上下文精确相符，且原 ModelOperation 仍开放。不得为核验读取 Module 02 operation 的私有历史或字段。私有 operation 丢失按 §18 的 `operation_result_indeterminate` 处理，不从 Artifact 伪造会话恢复。
2. 从 awaiting record 的 Ref 目录取得当时最后一个 candidate-set Ref，作为原补研请求的 `predecessor_candidate_set_ref`；从保存的 gap snapshot、证据目录和必要 draft 还原完整 Module 09 gap。按 §15 表转换为公共 Module 08 `SupplementalGap`：保持 gap tuple、`gap_id`、evidence Ref 和各分支 typed tuple 的原顺序，保留全部目标标量；backup anchor 从该 awaiting draft 的对应 date/core visit 取得 UID，不从 successor 或新草案反推。
3. 使用公共 `CandidateResearchSupplementalRequest` 构造 `schema_version="1.0"`、上述 `predecessor_candidate_set_ref`、同序 `gaps` 和 Resume 携带的实际 `supplemental_operation_plan`，执行上游要求的 fresh strict JSON 复验。两份 OperationPlan 的 run 必须与 awaiting/run 一致；对实际 Module 08 计划只做其公共 DTO 的结构、静态容量、顺序、唯一性和 phase 校验，不套用 Module 09 的剩余迭代数或 phase。Module 08 调用开始时的 remaining-slot 和零 slot/no-op 动态准入属于 Module 08；本地不重新执行其历史预算准入，不查询补研后的当前预算来否定已执行计划，执行关联由第 5 步合法 successor 与完整摘要核验确认。
4. 原请求摘要严格服从 `docs/08_candidate_research_loop.md` 附录 B.1/B.6：对 fresh 公共请求执行 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)`，再以 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` 编码，计算 UTF-8 bytes 的 SHA-256，使用小写十六进制和 `sha256:` 前缀。摘要包含完整 Module 08 OperationPlan；不增加 Module 09 fingerprint envelope，不用 Module 08 语义 gap fingerprint 或 Module 09 gap fingerprint 替代。实现只依赖上游公共 DTO 和公开文档规范，不导入上游私有 fingerprint 函数。
5. 对 supplied candidate-set Ref 及实际读取的 Artifact 执行 type=`candidate_research.candidate_set`、version=`1.0`、run、完整性与 fresh DTO 校验；要求 Artifact mode 为 supplemental、Artifact 的 `predecessor_ref` 与第 2 步 Ref 七字段完全相同、Ref 的 `operation_id` 与实际补研计划的 `terminal_successor_operation_id` 完全相同、`invocation_request_digest` 与第 4 步完全相同。这里 Artifact 的 `predecessor_ref` 对应原请求的 `predecessor_candidate_set_ref`，不得混淆字段名。然后执行 §6.2 的可消费 record、上下文和候选资格重验。

核验前不替换当前候选集、不清除 pending、不推进 run、不失效或重建缓存、不调用模型/地图或消费预算。前驱读取及关联失败的 terminal 处理仍服从 §18，不以修改运行态“尝试恢复”。核验成功也不要求原目标全部满足或出现新 UID；结果中的剩余 `pending_gaps` 只作为补研结果，不是原请求证明。

错误使用现有枚举：旧版本、缺字段或 fresh request 失败属 `service_usage_error`；fresh DTO 合法但计划 run/slot 语义不合法属 `operation_plan_invalid`；awaiting、还原来源/映射或后继 record 不可消费属 `predecessor_invalid`；eligible=0 属 `input_invalid`。已通过完整性和结构校验的 successor，其原前驱 Ref、terminal OperationId 或完整请求摘要不匹配时属 `request_mismatch`，按 replay 冲突返回 Failed，不写新 Artifact、不产生后续副作用。Artifact IO/完整性异常、结果不确定与取消仍按 §18 原分类和优先级处理，不降级为普通候选供给缺口。

## 16. 完成、停止和公共 outcome

### 16.1 Completed 必要且充分条件

控制器只能在以下全部成立时接受 submit：

1. `days` 从 sightseeing start 到 end 连续、唯一、同序且数量一致；arrival/departure `BoundaryTransfer` 均存在，可位于游览区间外；
2. 每日 kind、窗口、两个 boundary transfer、跨日占用和酒店闭环骨架合法；
3. full/partial/transit_only 的景点和餐次规则满足；
4. 主地点最多 6、景点最多 4、午餐/晚餐各不超过 1；
5. 所有主/备选候选 eligible、角色合法、UID 规则满足；
6. 每个 must 组已由主行程满足，avoid 不进入；
7. 未启用延期文本规则或活动锚点，访问使用 planning_estimate，不声称已完成定时活动；
8. 每个相邻主地点有匹配 Directions success 和合法 alternative；
9. 时间网格、原始/取整路线占用、访问间隔和跨日计算合法；
10. 单腿和 full-day 总交通上限满足；
11. 营业 conflicts 为零，unknown 均显式待核验；
12. 不要求备选齐全；已提供备选须为当天实际角色的有序子集且 Matrix 路网距离 `<=3000m`；
13. 主题满足安全边界，`transit_only` 使用固定标签；普通主题的文学质量或与行程的语义概括质量不作 Completed 硬 Gate；
14. effective policy 的 pace 已锁定且符合结构化需求，三类延期规则 tuple 为空；
15. 所有 Ref、index、provenance、预算和 Artifact 大小合法；
16. 没有未解决且与当前行程硬要求相关的 predecessor gap。

`Completed` 不以“模型说完成”为依据，只覆盖 §3.3 首版能力，并需使用该节范围说明。不要求时间窗口填满、所有兴趣覆盖、天气/饮食/价格或延期文本要求完成。

### 16.2 Incomplete

业务不可完成但没有技术故障时返回 `ItineraryPlanningIncomplete`。典型原因：

- 迭代或 Direction 预算耗尽；
- 按§12.4阶段规则判定无进展或无合法Action；
- 普通文本结束、终止机会耗尽；
- must、餐次、密度、营业或交通硬约束无解；
- Directions no-result 经合法重排仍不可达；
- geocode ambiguous/no-result；
- 模型反复提交未启用规则或锚点，业务拒绝后仍无法形成合法草案；
- 结构化抵离时刻、默认窗口和路线占用冲突；
- candidate supplement 不合法、重复或仍不足。

Incomplete 包含 `itinerary_ref | None`、摘要、预算、非空有序 gaps 和 stop reasons。存在合法前缀时必须返回其 Ref；没有可证明部分时允许 None。Incomplete 不是 Failed，不得伪装正式完整攻略。

### 16.3 Failed

public call 内的技术或合同失败返回 `ItineraryPlanningFailed`：Module 02 typed failure、unknown tool/args invalid、Artifact/预算基础设施、地图 typed exception、OperationId/replay 不确定、前驱损坏、控制器不变量或容量失败。构造阶段的 Prompt version/hash 失败按 §18.1 抛固定 configuration marker，不进入 public Failed。Failed 不自动变成 Incomplete，也不触发整阶段重启。

Failed 含 failure union、`last_consistent_itinerary_ref | None`、`failure_artifact_ref | None`、`budgets: PlanningBudgetSummary | None`、`retryable` 和 `possible_duplicate`。只有 resume terminal miss 且 predecessor 无法读取、因而无法诚实恢复 `sightseeing_days`/历史预算，或预算后端与任何已保存 snapshot 都不可用时允许 `budgets=None`；其余路径必须给出带 completeness flags 的预算摘要。顶层标记必须从具体 failure 派生。

### 16.4 Candidate supplement requested

该 outcome 表示当前 public call 已安全终止并等待 Module 13 跨模块编排，不是 Completed/Incomplete/Failed。它必须含 awaiting-supplement planning Ref、predecessor candidate-set Ref、typed gaps、唯一 fingerprint 和预算。

### 16.5 停止原因顺序

`PlanningStopReason` 固定顺序：

1. `user_clarification_required`；
2. `candidate_supply_insufficient`；
3. `schedule_infeasible`；
4. `direction_budget_exhausted`；
5. `iteration_limit_reached`；
6. `no_progress`；
7. `no_legal_action`；
8. `no_terminal_submission`。

同时成立时全部保留，去重并按上序排列。具体业务内容放 gap，不扩张 stop reason 为任意文本。

## 17. Artifact 合同

### 17.1 注册类型

ModelAction为9.0/planning-v9，itinerary为6.0；hub/matrix/directions Observation 外层仍为 1.0。类型数量仍为五，不注册旧 ModelAction 作为新版 fallback。公共 Resume 和版本隔离见 §20.1。注册项精确为：

| artifact type | model | 用途 |
|---|---|---|
| `itinerary_planning.model_action` | `ItineraryPlanningModelActionArtifact` | 安全模型 metadata、工具名、handle 参数摘要、接受/拒绝/failure |
| `itinerary_planning.hub_geocode_observation` | `ItineraryPlanningHubGeocodeObservationArtifact` | 规范化 geocode success/ambiguous/no-result/failure/not-issued |
| `itinerary_planning.route_matrix_observation` | `ItineraryPlanningRouteMatrixObservationArtifact` | 同序点目录和规范化 Matrix cells/provenance |
| `itinerary_planning.directions_observation` | `ItineraryPlanningDirectionsObservationArtifact` | 腿请求和 alternative 摘要/provenance，仅非公交摘要不含instruction，公交分支按末节保留typed步骤 |
| `itinerary_planning.itinerary` | `ItineraryPlanningArtifact` | in-progress、awaiting-supplement、completed、incomplete、failed record |

`ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS` 是不可变 tuple，必须由 Module 13 显式组合进全局 Registry，不自动扫描。

### 17.2 安全 Ref 目录

Itinerary state 顶层保存按首次产生顺序去重的：

- predecessor requirement digest、hotel selection Ref、candidate-set Ref 链；
- model-action Ref；
- hub geocode Observation Ref；
- Matrix Observation Ref；
- Directions Observation Ref；
- upstream candidate evidence Ref。

日程、路线和 gap 只保存有界整数 index，避免重复大型 Ref。所有 index 必须落在同类型目录内；Ref type/version/run/phase、创建顺序和 OperationId 唯一性必须由上下文 validator 复验。

### 17.3 Itinerary record

`ItineraryPlanningArtifact.record` 是封闭 union：

- `InProgressItineraryRecord`；
- `AwaitingCandidateSupplementRecord`；
- `CompletedItineraryRecord`；
- `IncompleteItineraryRecord`；
- `FailedItineraryRecord`。

state snapshot 包含锁定策略、日期计划、候选/证据目录、预算、gaps、已请求 supplement fingerprints、iteration index/no-progress streak 和请求摘要。terminal record 必须足以在 `close_run` 后精确重建 public outcome。

### 17.4 大小和内容边界

每个 Artifact 使用 Module 04 canonical JSON/hash，单项 `<=1 MiB`。Directions 只保存最多 3 个 alternative 摘要而不保存最多 128 条 instruction；candidate/酒店/地图完整对象不在 itinerary 重复复制。

Artifact 禁止保存：Secret、认证头、system/developer Prompt、完整用户消息、原始模型文本、reasoning、tool arguments 原文、供应商/MCP/HTTP body、完整 RouteStep 对象（公交说明文本的严格投影除外）、异常字符串/对象/堆栈、任意 metadata dict、二进制或 Base64。

允许内容只包括严格 DTO 的明确字段、短安全摘要、规范地点/路线事实、来源时间、验证状态、模型 usage/finish metadata 和 ArtifactRef/index。

## 18. 错误优先级、取消与生命周期

### 18.1 同步检查全序

服务构造时先一次性加载并验证 Prompt version/hash；失败抛 `ItineraryPlanningConfigurationError(prompt_resource_invalid)`，不进入 public call。public call 首先对整个 request 做 fresh strict JSON 复验，以便安全取得 request kind、run、terminal successor 和 Ref 身份；调用对象类型错误、无法 fresh 复验或已显式关闭 service 属 API misuse，抛 `ItineraryPlanningUsageError(service_usage_error)`，此时没有可信 terminal ID，不能写 Artifact。

完成上述无副作用边界复验后，同一检查点多个 operational 问题按以下优先级；较高项成立后不执行未开始的低优先级副作用：

1. `asyncio.CancelledError`、`KeyboardInterrupt`、`SystemExit` 原样传播；
2. replay/OperationId/result indeterminate；
3. 输入、前驱、run、OperationPlan 不合法；
4. Prompt/模型/tool schema 合同失败；
5. Artifact lookup/read/write/integrity/capacity；
6. Budget plan/consume/snapshot；
7. 地图 typed failure；
8. 正常业务 gap、no-result、无进展和预算耗尽。

同层多个地图或持久化错误先按模型 action ordinal，再按 capability 固定顺序 `geocode、matrix、directions`，再按稳定 error-code 顺序。错误 public payload 不含异常正文。

terminal replay 必须先于完整前驱读取与业务语义校验：fresh request 可计算的 request digest 与 terminal record 匹配时，仅凭 terminal 内保存的摘要、Ref 目录和 record 重建原 outcome；不因后来缺失/损坏的前驱改变已完成结果。terminal miss 后才读取并校验前驱。`request_mismatch` 或同 OperationId 不同 digest 仍按 replay 冲突处理，不回退为新请求。

### 18.2 取消

`CancelledError` 不包装成 Module 09 outcome。取消后：

- 取消未完成 sibling；
- 只等待必要资源清理；
- 不启动新模型、地图、预算、Artifact、聚合或事件副作用；
- 已发 Directions 预算不退；
- 已 confirmed-applied 但 Observation absent 的槽在 replay 时 indeterminate；
- 不补写 not-issued 或“取消记录”。

应用取消控制及交互映射由 Module 14 依 Module 13 §13 和 Module 11 §22 执行；可选诊断 CLI 的 `Ctrl+C` 和退出码属于应用层。Module 09 只按上述规则传播 `CancelledError`。

### 18.3 资源所有权

一个 planning run 持有一个私有 `ModelOperation`、锁、handle/cache/index 和 last-consistent Ref。Graph state 只保存小状态和 ArtifactRef。任何 public outcome 返回都不隐式关闭 run context；尤其 `candidate_supplement_requested` 必须保留同一个 operation 及其 pending call，`plan_resume` 用匹配 call ID 的 typed supplemental ToolResult 调用 `acontinue_tools()`，不得二次 `start_operation()`。Module 13 在最终 Completed/Incomplete/Failed 被消费后显式调用 `close_run`；`close_run` 幂等。关闭后 exact terminal replay 仍可用，可信 terminal ID miss 的新非 replay 请求返回 `ItineraryPlanningFailed(ControlPlanningFailure(run_context_closed))`；整个 service 已 `aclose` 的 API 调用才抛 §18.1 usage marker。服务 `aclose()` 清理所有未关闭 run context，但不拥有注入依赖。Module 10/11 的数据库后端只恢复安全规划状态，不序列化或恢复 Module 02 私有对话历史；以新模型 operation 继续的能力必须另行版本化设计，不能伪称同一 operation replay。

### 18.4 异常、outcome、迭代与副作用映射

| 场景 | 结果 | 消费 iteration | 写 terminal | 允许继续模型 |
|---|---|---:|---:|---:|
| 构造时 Prompt 缺失/version/hash 错 | 抛 `ItineraryPlanningConfigurationError(prompt_resource_invalid)` | 否 | 否 | 否 |
| public 参数类型错误、fresh strict JSON 失败、service 已关闭 | 抛 `ItineraryPlanningUsageError(service_usage_error)` | 否 | 否 | 否 |
| fresh DTO 合法但 request/predecessor/OperationPlan/eligible 前置不合法 | `ItineraryPlanningFailed(ControlPlanningFailure)`，分别用 `input_invalid/predecessor_invalid/operation_plan_invalid` | 否 | 可信 terminal ID 存在时是 | 否 |
| terminal 命中且 request digest 相同 | 精确 replay 原 outcome | 否 | 否 | 否 |
| terminal/OperationId 命中但 digest 不同 | `ItineraryPlanningFailed(ControlPlanningFailure(request_mismatch))` | 否 | 否 | 否 |
| §15.1 已合法解码的补研 successor 与原请求前驱 Ref/terminal OperationId/完整摘要不匹配 | `ItineraryPlanningFailed(ControlPlanningFailure(request_mismatch))` | 否 | 否 | 否 |
| Module 02 `LLMInvocationFailure`，含 unknown tool/arguments invalid | `ItineraryPlanningFailed(LLMPlanningFailure)` | 否 | 是 | 否 |
| 空 `ToolCallsReady` | `ItineraryPlanningFailed(ControlPlanningFailure(model_contract_invalid))` | 否 | 是 | 否 |
| terminal 混批、超容量、sibling dependency 或其他 batch 结构失败 | `ItineraryPlanningFailed(ControlPlanningFailure(action_batch_invalid))` | 否 | 是 | 否 |
| `ToolTurnCompleted` 普通文本 | `ItineraryPlanningIncomplete(no_terminal_submission)` | 否 | 是 | 否 |
| 结构合法的非终止 batch 被 business policy 拒绝 | typed rejection Observation | 是 | 否 | 是，尚有迭代时 |
| 结构合法但业务非法的 submit | typed rejection Observation | 是 | 否 | 是，尚有迭代时 |
| 结构合法但不可映射/重复/零剩余迭代的 supplement | `ItineraryPlanningIncomplete(candidate_supply_insufficient/no_legal_action)` | 否 | 是 | 否 |
| accepted submit/supplement | Completed 或 CandidateSupplementRequested | 否 | 是 | 否；supplement 由 resume 继续 |
| Matrix/Directions NoResult | typed business Observation | 是 | 否 | 是，尚有迭代时 |
| Module 06 typed exception 或已发结果不确定 | `ItineraryPlanningFailed(MapPlanningFailure/ControlPlanningFailure)` | 已开始 cycle 时是 | 是；末跳写失败除外 | 否 |

表中“写 terminal”始终受 §14.4 末跳例外约束。调用方取消不进入表，按 §18.2 原样传播。

## 19. 安全、隐私与可观测性

- confirmed requirement、候选/供应商文本和 ToolResult 均为不可信数据，只能放在 data boundary，不得覆盖 system instructions。
- Prompt 明确禁止模型编造 UID、handle、坐标、营业、路线、活动时刻、价格和工具。
- ToolResult 只返回由严格 DTO JSON 生成的有界安全内容；call ID 必须精确匹配 pending calls。
- 日志、repr、异常、事件和 RunSummary 不含用户原文、Prompt、模型文本、tool arguments、路线指令或供应商 payload。
- Action Artifact 只保存工具 enum、fingerprint、ordinal、handles 和接受状态；自由查询文本不在 Module 09 工具中出现。
- 每个事实保存来源 Ref、retrieved_at 和 `verified/unknown/user_provided` 状态。
- Module 04 safe event 仅由 Module 13 聚合；Module 09 不新增任意日志 metadata。
- Demo 不写本地 session 文件、不接数据库、不保存长期 Memory。

安全测试必须使用 canary 验证 repr、typed exception、Artifact、ToolResult、事件和报告均不泄露。

## 20. 版本、Prompt 与未来持久化

### 20.1 Schema 与恢复

| 内容 | 新版本/规则 |
|---|---|
| M09 Initial/Resume、OperationPlan/slot、公共预算摘要/Outcome | 6.0；保持逻辑调用额度和阶段摘要。 |
| M09 Resume | 6.0；仍带已经实际执行的 M08 supplemental_operation_plan。 |
| itinerary_planning.itinerary | 6.0；PlanningState/预算 snapshot 均 6.0，引用目录5.0。 |
| itinerary_planning.model_action | 9.0；Prompt planning-v9，记录调用发起阶段、归属及安全摘要。 |
| Prompt/指纹 | planning_v9.txt；现有指纹域保持，输入版本6.0参与身份计算；事实指纹保持。 |
| M13 durable/control | 既有demo_workflow.web.v6及demo.control/2.0；控制字段不增加，当前planning_ref为6.0。规划请求身份包含新版本，旧规划记录拒绝读取。 |
| M12 | 新规划来源只接 itinerary/6.0；结果日块/索引仍2.0，保留中文渲染及现有交通步骤。 |
| M10 | 沿用m10_0002及[M10 §23](10_artifact_persistence.md)；业务Artifact表、容量和M11 SQL结构不变。 |

PlanningStateSnapshot 6.0 保存 current_stage、阶段分类计数、model_invocations_consumed、格式修正计数、last_rejection（reason/field/业务及相关证据指纹，可空）、纠错目标和进展、accepted supplement fingerprints、pending supplement call ID、既有 policy/draft。预算摘要保存正常调用上限17、格式修正上限2、总上限19，provider attempts上限38；保留known usage、complete flags和地图摘要。所有字段required；完整未知状态不能补零充完整。

移除 iterations_consumed/terminal_only_available/terminal_only_consumed 的旧控制语义。此处不要求重命名未改变业务内容的 day/route/tool args 或 Observation；正在产生的非公交和公交 Directions snapshot 1.0/1.1 仍是现行事实格式，不因“无历史兼容”误删。

不存在旧运行自动迁移或旧 schema fallback；旧版本读取/恢复明确不支持。不会因本设计自动删除旧数据库或测试证据。只有新记录参与新规则验收。

同进程补研恢复可读取新版阶段 snapshot 校验私有 Run 一致性；M02 私有 Operation/history 仍不可跨进程恢复。M11 non_recoverable 分类不变：诊断中有完整消息也不能升级为可恢复节点、退还预算或重发未知外部调用。网页的需求/酒店安全暂停沿新合同继续既有恢复规则。

本次固定capability_scope=module09-format-recovery-v1；请求/context的范围标识和版本进入B.6指纹，不污染Module 08重建请求或摘要。ITINERARY_PLANNING_SCHEMA_VERSION=6.0用于当前Initial、Resume、OperationPlan、slot、BudgetSummary和Outcome。ModelAction为9.0，活动时间和路线时间为2.0，路线及路线快照为3.0；未改变的proposal/tool/location/day DTO及Observation保持各自版本，禁止用该常量全局替换全部版本。当前入口及嵌套计划、槽位必须一致为6.0，旧版本在任何模型、地图、预算或仓储副作用前拒绝。拒绝未知类型、版本、判别、枚举、额外字段及forged model。

### 20.2 Prompt

当前资源、逻辑版本、固定摘要及历史字节保护以§31 RC-04为准。Prompt变化必须版本化，不能原位静默修改；loader、实际模型请求、动作记录和镜像打包必须保持一致。

### 20.3 PostgreSQL 持久化衔接

Module 10/11 已提供 PostgreSQL 业务持久化。阶段状态按新版 Artifact 保存；M11 通用预算表可保存新 key，无需增加阶段 SQL 列。业务持久化继续遵守：

- Artifact 不可变，Ref 含 type/version/hash；
- OperationId 是稳定幂等键，同键不同内容冲突；
- predecessor/candidate-set/itinerary Ref 形成显式版本链；
- resume 状态来源只用显式 Ref 与当前 run/pending 上下文，另用实际补研 OperationPlan 核验请求关联，不扫描“最新记录”；
- gap fingerprint 和工具 fingerprint 稳定规范化；
- 扩展所有权、TTL、删除和并发字段必须版本化；
- 业务 Artifact 不放原始 Prompt、模型/供应商正文或思考；专用诊断按 [M02 §19](02_llm_runtime_and_model_profiles.md) 采集并由 [M10 §23](10_artifact_persistence.md) 保存，不作为业务恢复许可。

数据库 schema、迁移和事务边界必须在 Module 10/11 的独立设计中批准。隐私生命周期属于 Module 19；队列、指标、Trace 和审计 adapter 属于 Module 20+21；Production 级恢复/灾备验证保留在 Module 22。

## 21. 测试与验收矩阵

默认 Module 09 测试使用 Module 02/04/06/07/08 公共 Protocol/DTO 的 fake；补研关联另须按执行契约 §13.7 使用真实公共 Module 08 服务与离线 fake 依赖生成摘要证据。全部测试不访问网络、MCP 或真实凭据。

| 类别 | 必须覆盖 |
|---|---|
| public/import | 包根精确导出、Protocol 签名、禁止私有/上游私有/MCP/HTTP 导入 |
| strict DTO | fresh strict JSON、extra/宽松/forged/非法 enum/index/ref/version 全拒绝 |
| predecessors | confirmed、selected hotel、completed/incomplete candidate-set、eligible=0、ref/digest/run 冲突 |
| operation plan | initial精确17个invocation slots、resume为剩余逻辑调用，索引连续至16；两份计划同run且分别满足phase/唯一性/容量；Module08 slot不作为新规划slot消费，无独立terminal_action |
| pace/policy | 显式 pace、Agent 代选、首次终止尝试锁定、重试/恢复不可换档；三类文本规则为空，非空提议业务拒绝 |
| calendar | 连续日期、Asia/Shanghai、跨午夜、抵离余量、approx+30、首末日和同日抵离 |
| minute/duration | 任意整数分钟、抵离/路线原值、分钟向上取整、默认时长、半开区间；拒绝用户文本锚点和 user_explicit |
| density/meals | 三档景点数量、full/partial/transit-only、午晚餐窗口、组合角色、主地点≤6 |
| candidate | eligible only、role、整趟 UID 一次、must 等价组、avoid、兴趣只偏好 |
| business hours | compatible/conflicts/unknown、不提升 unknown、unknown 待核验 |
| backups | 按实际角色最多3个、全程唯一、不进主路线、walking Matrix≤3000m、无 Directions |
| tools | 六工具白名单、terminal exclusive、batch、handle、unknown/args invalid、普通文本非成功 |
| matrix | N×N≤100、单日单轮最多1、跨日 batch、三并发、有序结果、transit 拒绝、不能替 Direction |
| directions | 批量腿、1..3 alternatives、每腿预算、全批超限零调用、匹配 Ref/index/mode/metrics |
| route output | 静态 RouteLeg 摘要、公交步骤仅作持久化展示证据且不进入模型、驾车不冒充叫车服务/费用、transit 不冒充纯地铁 |
| ReAct | 2/5/5/5、最多17、提前提交/最后真实submit-only、唯一归属、修正重复拒绝、no legal action、有序目标不强制工具顺序 |
| outcomes | Completed 每项门槛、Incomplete 合法前缀/gaps/stops、supplement request、Failed 三分支 |
| supplemental | §15 全映射和 §15.1 原请求摘要关联；合法 Incomplete/无新增 UID/仅旧 UID 更新均可恢复；typed evidence1..8、fingerprint一次、Module13边界、预算/缓存/策略不重置 |
| resume version | Resume3.0 required实际补研计划、拒绝旧1.0/2.0/缺字段；B.6 新范围指纹隔离、旧会话不恢复、同范围 exact replay 不读前驱 |
| replay | lookup-first、exact terminal replay、partial/consumed-absent/同ID异内容、关闭后 replay |
| artifact | 五注册、Ref目录/index、1MiB、terminal可重建、末跳失败、禁止 raw/secret/reasoning |
| error priority | 取消→replay→input→LLM→Artifact→budget→map→business 的相邻碰撞矩阵 |
| concurrency | terminal singleflight、run锁、不同run、地图≤3、wave失败收束、resume/close竞态 |
| cancellation | 预占和 wave 各位置取消、无后续副作用、预算不退款、原异常传播 |
| downstream | M10 持久化 Artifact／evidence、M11 持久化 checkpoint／执行账本并负责跨进程恢复、M12 只格式化并标记费用未核验、M13 编排、M14 无规则复制 |
| scope exclusions | 无天气、Citywalk、步行专限、饮食判断、价格、实时导航、MCP/HTTP |
| security | canary 不进 repr/异常/Artifact/ToolResult/事件/报告；不信任候选/provider文本 |

生产包 line、branch、combined coverage 三项分别不得低于 70%。必须使用同一 source `app.itinerary_planning`、单进程采集、固定分母；零测试、skip、xfail、rerun、采集错误或任一门槛不足均失败。

正式离线验收在从当前源码新建的 Docker test target 中运行，固定包含：

- `--rm`；
- `--network none`；
- `--read-only`；
- `/tmp` tmpfs；
- `no-new-privileges`；
- 不传 `.env`、API key 或其他 Secret；
- 不调用 live provider/MCP；
- 检查项目命名空间残留容器。

设计文档阶段不运行 Docker、pytest、coverage 或 compileall，报告必须是 `NOT_RUN — 需求澄清与设计文档阶段不执行代码验收`。

## 22. 实施、审查与 closeout

- 具体文件树、阶段白名单、测试命令和报告模板由配套 Vibe Coding 文档冻结。
- 原则上一次实施相邻两个阶段，两个阶段完成后统一执行相应 Gate；迁移/删除安全门可以单独阶段，但 Module 09 首版不含迁移。
- 实施模型、修复模型和 closeout 模型不得 stage 或 commit；只有独立审查者在用户另行明确授权的提交/合并阶段才可执行指定 Git 写操作。
- 初次实现完成后必须独立代码审查，逐条对照本文而非只看测试通过。
- 代码、模块测试、coverage、Docker Gate 和独立审查通过后，才能另行授权 `MODULE-CLOSEOUT`。
- closeout 必须检查 README、IMPLEMENTATION_STATUS、Dockerfile、compose、`.env.example` 和真实使用说明的实际影响；未完成不得称 Module 09 COMPLETE。
- commit、merge、push、PR、deploy 均需用户对独立审查者的对应阶段明确授权；设计、实施或 closeout 授权不自动包含这些操作。

## 23. PASS、STOP 与状态语义

### 23.1 设计/实施 PASS

PASS 必须逐条证明本文相关章节、阶段目标、白名单、正反测试、Docker 和 coverage 门槛；“测试通过”不能代替设计对照。

### 23.2 STOP

以下任一情况立即停止：

- 需要修改已确认决定或 Module 01–08 公共合同；
- 需要直接访问 MCP、HTTP、供应商 SDK/私有实现或新增依赖；
- 需要把天气、价格、饮食、实时导航或跨模块编排塞入 Module 09；
- OperationId、Artifact、预算、replay、取消或 1 MiB 合同无法满足；
- 白名单不足、出现异常 dirty 文件或需要 migration/config；
- Prompt/hash、工具白名单、终态或错误优先级无法形成可测试合同；
- 任一 P0/P1 或高概率影响主演示/公共合同的 P2 未清零。

### 23.3 允许状态

- 阶段实现完成且等待独立审查：`READY_FOR_INDEPENDENT_REVIEW`；
- 未执行的代码验收：`NOT_RUN` 并写明前置阻断；
- 低概率且不涉及安全、数据破坏、主演示崩溃或公共合同破坏的延期项：`DEFERRED_DEMO_NONBLOCKING`，必须记录概率、影响、理由和回收模块；
- 未完成 MODULE-CLOSEOUT 前不得输出 `COMPLETE`。

## 24. 规范性决定目录

| 决定 | 所属章节 |
|---|---|
| D-09-01..05 时间网格、密度、餐次、部分日 | §8、§9 |
| D-09-06..10 must、兴趣、多角色、UID、酒店骨架 | §9 |
| D-09-11..15 ReAct、工具、路线证据、交通约束 | §10、§12 |
| D-09-16..18 窗口、时长、抵离余量 | §8 |
| D-09-19..23 supplemental、校验、停止、存储、DB | §14、§15、§17、§20 |
| D-09-24..27 备选、上游 Incomplete、营业状态 | §6、§9 |
| D-09-28..33 geocode、迭代、预算、alternative、时刻表 | §10、§12–14 |
| D-09-34..38 空闲、备选、预算边界、交通硬上限 | §8、§9、§11 |
| D-09-39..43 Citywalk/天气/行李/跨午夜最终覆盖 | §3、§8、§11 |
| D-09-44..50 pace、主题、时间策略、活动、饮食、模式、静态路线 | §3、§8–12、§17 |
| D-09-67 实际补研计划与原请求关联 | §6.2、§15.1、附录 B.1/D |
| D-09-68 Resume 局部版本与兼容边界 | §20.1、附录 B.6/D |

## 附录 A：公共符号目录

### A.1 公共枚举精确值

枚举成员名使用大写 snake case，值使用下表小写字符串；顺序即公共定义和稳定排序顺序：

| Enum | 成员名=`value`，按定义顺序 |
|---|---|
| `PlanningDayKind` | `FULL="full"`、`PARTIAL="partial"`、`TRANSIT_ONLY="transit_only"` |
| `VisitKind` | `ATTRACTION="attraction"`、`LUNCH="lunch"`、`DINNER="dinner"`、`ATTRACTION_LUNCH="attraction_lunch"`、`ATTRACTION_DINNER="attraction_dinner"` |
| `BusinessHoursState` | `COMPATIBLE="compatible"`、`CONFLICTS="conflicts"`、`UNKNOWN="unknown"` |
| `PlanningTool` | `GET_CANDIDATE_EVIDENCE="get_candidate_evidence"`、`GEOCODE_HUB="geocode_hub"`、`CALCULATE_ROUTE_MATRIX="calculate_route_matrix"`、`PLAN_DIRECTIONS="plan_directions"`、`SUBMIT_ITINERARY="submit_itinerary"`、`REQUEST_CANDIDATE_SUPPLEMENT="request_candidate_supplement"` |
| `PlanningStopReason` | `USER_CLARIFICATION_REQUIRED="user_clarification_required"`、`CANDIDATE_SUPPLY_INSUFFICIENT="candidate_supply_insufficient"`、`SCHEDULE_INFEASIBLE="schedule_infeasible"`、`DIRECTION_BUDGET_EXHAUSTED="direction_budget_exhausted"`、`ITERATION_LIMIT_REACHED="iteration_limit_reached"`、`NO_PROGRESS="no_progress"`、`NO_LEGAL_ACTION="no_legal_action"`、`NO_TERMINAL_SUBMISSION="no_terminal_submission"` |
| `TransportSemantic` | `WALKING="walking"`、`CYCLING="cycling"`、`PUBLIC_TRANSIT="public_transit"`、`RIDE_HAILING="ride_hailing"`、`PRIVATE_CAR="private_car"` |

`PlanningFailureCode` 的精确值和顺序只在附录 C 定义，附录 C 同样是本公共枚举表的一部分；不得在两个位置维护不同副本。gap 使用各分支固定 `gap_type` literal，不再设置无消费者的通用 `PlanningGapReason` 枚举。

### A.2 包根 `__all__`

包根必须导出以下名称，且只从 Module 09 自有稳定模块导出：

```text
ITINERARY_PLANNING_SCHEMA_VERSION
ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY
ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY
ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS

PlanningDayKind
VisitKind
BusinessHoursState
PlanningTool
PlanningStopReason
TransportSemantic
PlanningFailureCode

PlanningInvocationOperationIds
ItineraryPlanningOperationPlan
ItineraryPlanningInitialRequest
ItineraryPlanningResumeRequest

CandidateLocationRef
HotelLocationRef
HubLocationRef
PlanningLocationRef
TimeInterval
RouteTimeInterval
DailyTimeRule
TimeSensitiveMustAnchor
TransportPolicyRule
EffectivePlanningPolicy
ScheduledVisit
RouteAlternativeSummary
RouteLeg
BoundaryTransfer
BackupOption
DayPlan
ItineraryDraft

PlanningGap
PlanningGapSummary
PlanningBudgetSummary
PlanningSummary
LLMPlanningFailure
MapPlanningFailure
ControlPlanningFailure
PlanningFailure
ItineraryPlanningCompleted
ItineraryPlanningIncomplete
ItineraryPlanningCandidateSupplementRequested
ItineraryPlanningFailed
ItineraryPlanningOutcome

GetCandidateEvidenceArgs
GeocodeHubArgs
CalculateRouteMatrixArgs
PlanDirectionsArgs
SubmitItineraryArgs
RequestCandidateSupplementArgs

ItineraryPlanningModelActionArtifact
ItineraryPlanningHubGeocodeObservationArtifact
ItineraryPlanningRouteMatrixObservationArtifact
ItineraryPlanningDirectionsObservationArtifact
ItineraryPlanningArtifact

ItineraryPlanningError
ItineraryPlanningConfigurationError
ItineraryPlanningUsageError
ItineraryPlanningPort
ItineraryPlanningService
```

禁止包根导出具体 Artifact record/snapshot 分支、内部 handle directory、runtime port、fingerprint/canonical/prompt loader helper、锁、run context 或 fake。

## 附录 B：规范 DTO 与 Artifact 合同

公共、proposal 和 tool DTO 使用 `ConfigDict(extra="forbid", strict=True, frozen=True, validate_default=True)`；当前 Initial、Resume、OperationPlan/slot、预算摘要/Outcome 为6.0；时间区间为2.0、路线为3.0、三类规划工具参数为2.0；未改变的 public/proposal/tool DTO 第一字段仍为具有冻结默认值的 schema_version=1.0，各类型按 §20.1 精确声明。Artifact-local DTO 图必须服从 Module 04 `ArtifactSchemaRegistry` 的更严格合同：每个可达 `BaseModel` 的 `model_config` 精确等于 `ConfigDict(extra="forbid", strict=True, frozen=True)`，不得多出 `validate_default`；包括 `schema_version`、判别字段和可空字段在内的每个字段都必须 required，不得声明默认值、alias、serializer 或 computed field。Artifact-local DTO 不得直接嵌入未通过该注册合同的公共 DTO；必须转换为本附录 B.5 明定的本地 snapshot。共同标量固定为：`StableId/Handle` 是去首尾空白的 1..128 code points 安全文本；`CandidateUid` 与 Module 08 `CandidateRecord.core.uid` 等域，是去首尾空白的 1..512 code points 文本；`SafeLabel` 为 1..80；`SourceField` 为 1..256；digest/fingerprint 匹配 `^sha256:[0-9a-f]{64}$`。所有 tuple 的唯一性、顺序、索引和交叉字段必须由 fresh context validator 重算；validator 只拒绝，不替调用方排序、截断、取整或补造值。

### B.1 Operation 和请求

| DTO | 字段顺序、类型与容量 |
|---|---|
| `PlanningInvocationOperationIds` | `schema_version:Literal["6.0"]; invocation_slot_index:int[0..18]; action_operation_id:OperationId; planning_state_operation_id:OperationId` |
| `ItineraryPlanningOperationPlan` | `schema_version:Literal["6.0"]; run_id:UUID; invocation_slots:tuple[0..19,PlanningInvocationOperationIds]; hub_geocode_operation_ids:tuple[0..2]; matrix_operation_ids:tuple[0..remaining_model_invocations×sightseeing_days]; direction_operation_ids:tuple[0..direction_remaining]; terminal_successor_operation_id:OperationId` |
| `ItineraryPlanningInitialRequest` | `schema_version:Literal["6.0"]; confirmed:ConfirmedTravelRequirement; hotel_selection_ref:ArtifactRef; candidate_set_ref:ArtifactRef; operation_plan:ItineraryPlanningOperationPlan` |
| `ItineraryPlanningResumeRequest` | `schema_version:Literal["6.0"]="6.0"; predecessor_planning_ref:ArtifactRef; supplemented_candidate_set_ref:ArtifactRef; operation_plan:ItineraryPlanningOperationPlan; supplemental_operation_plan:CandidateResearchOperationPlan`；最后一项 required，无默认值 |

Module 09 operation_plan.invocation_slots初始精确19，普通17与格式修正2分开准入；resume精确等于同run剩余逻辑调用数，slot index从model_invocations_consumed开始连续递增到18。该规划计划的所有 OperationId 同 request run、phase=`itinerary_planning` 且跨其全部 tuple/terminal 字段全局唯一。动态 tuple 上限和 `planning.direction_call` snapshot 由 request/context validator 校验。每个 public call 都有新的 Module 09 OperationPlan；resume 不复用 predecessor public call 的 Action ID。Resume 的 `supplemental_operation_plan` 引用上游公共类型，沿用其 `candidate_research` phase、静态容量与唯一性合同，且同 request run；它保存已经实际执行的 Module 08 计划，用于 §15.1 terminal Ref/摘要核验，不属于本次待消费的规划 slot，其历史动态预算准入不由 Module 09 重跑。

### B.2 核心行程

| DTO | 字段顺序、类型与容量 |
|---|---|
| `CandidateLocationRef` | `ref_type:Literal["candidate"]; admission_ordinal:int[0..127]; uid:CandidateUid` |
| `HotelLocationRef` | `ref_type:Literal["hotel"]; hotel_selection_ref_index:Literal[0]`，索引唯一酒店 Ref 目录 |
| `HubLocationRef` | `ref_type:Literal["hub"]; endpoint_kind:Literal["arrival","departure"]; geocode_observation_index:int[0..1]` |
| `PlanningLocationRef` | 以上三分支，以 `ref_type` 判别 |
| `TimeInterval` | `start_at:datetime; end_at:datetime`，均为 `Asia/Shanghai` aware、`start_at<end_at` |
| `RouteTimeInterval` | `start_at:datetime; end_at:datetime`，均为 `Asia/Shanghai` aware、`start_at<=end_at`；只用于路线腿，允许零耗时相等边界 |
| `DailyTimeRule` | `rule_id:StableId; date:date|None; interval:TimeInterval; strength:Literal["hard","soft"]; source_field:SourceField; source_digest:ContentDigest` |
| `TimeSensitiveMustAnchor` | `anchor_id:StableId; requirement_group_id:StableId; date:date; event_at:Asia/Shanghai aware datetime; source:Literal["user_provided","verified_evidence"]; source_ref_index:int[0..127]|None`；user source 必须 None，evidence source 必须非 None |
| `TransportPolicyRule` | `rule_id:StableId; semantic:TransportSemantic; strength:Literal["hard","soft"]; numeric_limit_minutes:int[1..1440]|None; source_field:SourceField; source_digest:ContentDigest` |
| `EffectivePlanningPolicy` | `effective_pace:TravelPace; daily_time_rules:tuple[0..64,DailyTimeRule]; time_sensitive_must:tuple[0..100,TimeSensitiveMustAnchor]; transport_rules:tuple[0..32,TransportPolicyRule]; locked:Literal[True]` |
| `ScheduledVisit` | `visit_id:StableId; location:CandidateLocationRef; visit_kind:VisitKind; interval:TimeInterval; duration_source:Literal["user_explicit","planning_estimate"]; must_group_ids:tuple[0..100,StableId]; time_sensitive_anchor_ids:tuple[0..8,StableId]; business_hours_state:BusinessHoursState; business_evidence_indexes:tuple[0..8,int[0..127]]; requires_verification:bool` |
| `RouteAlternativeSummary` | `alternative_index:int[0..2]; distance_m:int>=0; duration_s:int>=0; step_count:int[1..128]` |
| `RouteLeg` | `leg_id:StableId; origin:PlanningLocationRef; destination:PlanningLocationRef; interval:RouteTimeInterval; transport_semantic:TransportSemantic; route_mode:TravelMode; directions_observation_index:int[0..481]; alternative_index:int[0..2]; distance_m:int>=0; duration_s:int>=0; scheduled_minutes:int>=0` |
| `BoundaryTransfer` | `transfer_kind:Literal["arrival","departure"]; hub:HubLocationRef; hotel:HotelLocationRef; route_leg:RouteLeg; processing_minutes:int>=30; approximate_extra_minutes:Literal[0,30]` |
| `BackupOption` | `backup_kind:Literal["attraction","lunch","dinner"]; core_visit_id:StableId; candidate:CandidateLocationRef; matrix_observation_index:int[0..1019]; matrix_mode:MatrixTravelMode; distance_m:int[0..3000]; business_hours_state:BusinessHoursState; business_evidence_indexes:tuple[0..8,int[0..127]]; requires_verification:bool` |
| `DayPlan` | `date:date; day_kind:PlanningDayKind; theme:str[1..40]; open_intervals:tuple[0..16,TimeInterval]; visits:tuple[0..6,ScheduledVisit]; route_legs:tuple[0..7,RouteLeg]; backups:tuple[0..3,BackupOption]` |
| `ItineraryDraft` | `schema_version; requirement_digest:ContentDigest; hotel_selection_ref:ArtifactRef; candidate_set_ref:ArtifactRef; effective_policy:EffectivePlanningPolicy; arrival_transfer:BoundaryTransfer; days:tuple[1..60,DayPlan]; departure_transfer:BoundaryTransfer; upstream_gap_ids:tuple[0..128,StableId]` |

交叉不变量：

- 访问 interval.start_at/end_at 使用整数分钟。锚点 DTO 的原有精确时刻/半开覆盖结构校验保留，但首版 Service 不接受含锚点的草案。
- `RouteLeg.scheduled_minutes=((duration_s+599)//600)*10`；`duration_s=0` 时为 0。`interval` 长度必须精确等于 `scheduled_minutes`，正值两端使用整数分钟，零值允许 `start_at=end_at`，不得发明最低交通时间。
- DayPlan 的 route legs 从酒店开始、按 visits 同序连接并回到酒店；有访问时腿数精确为 `len(visits)+1`。`transit_only` 无候选访问时禁止制造酒店到酒店的零距离虚拟腿，此时 `route_legs=()`。
- DayPlan 中第一腿结束不晚于首访问开始；中间腿开始不早于前一访问结束且结束不晚于下一访问开始；末腿开始不早于最后访问结束并在开放区间内回到酒店。访问和正耗时路线 interval 不重叠且全部落在当日开放 interval 的并集内；等待空档允许存在但不计活动。零耗时腿可以落在相邻边界同一时刻。
- arrival transfer 的 leg 为 hub→hotel，departure transfer 为 hotel→hub；`route_leg.interval` 与对应 raw input、固定余量、同日 DayPlan 和跨日占用相容。两 transfer 可位于 sightseeing 区间外，但 `days` 只覆盖正式游览日期。
- 首版 processing_minutes 固定为机场 arrival60/departure120、铁路 arrival30/departure60；近似余量另记，不从文本增减。
- full/partial 的 `open_intervals` 至少一个；`transit_only` 可为空，禁止为了满足 DTO 容量伪造可用游览时间。visit/must/anchor/evidence、route/Observation、backup/Matrix 的索引必须落在 §B.5 对应目录。普通 UID、备选 UID、visit/leg/rule ID、日期和 open interval 都按正文规则唯一且稳定有序。

`theme` 去首尾空白，不允许 Markdown/HTML/URL。普通主题只做安全校验；`transit_only` 按上下文精确为“抵达与入住”“返程日”或“抵达与返程”。

上述 DTO 和 snapshot 格式及 strict 校验保留，不因首版停用删除类或枚举。首版 Service、合法前缀和终态另执行 §8.2 的三空规则、无访问锚点和 planning_estimate 限制；不可把纯 DTO 测试通过当作功能已启用。

### B.3 模型工具参数与 proposal 层

模型工具只使用当前 context 的 opaque handle，不直接构造上述 Artifact Ref/index DTO。以下 nested proposal DTO 定义在 `models.py`，不从包根导出，沿用既有 ToolContract 字段格式；planning-v9的业务准入另按 §8.2，不能借结构合法启用延期能力：

| DTO | 字段顺序、类型与容量 |
|---|---|
| `ProposedDailyTimeRule` | `rule_id:StableId; date:date|None; start_at:Asia/Shanghai aware datetime; end_at:Asia/Shanghai aware datetime; strength:Literal["hard","soft"]; source_field:SourceField` |
| `ProposedTimeSensitiveMustAnchor` | `anchor_id:StableId; requirement_group_id:StableId; date:date; event_at:Asia/Shanghai aware datetime; source:Literal["user_provided","verified_evidence"]; source_handle:Handle|None`；user source 必须 None，evidence source 必须是当前 evidence handle |
| `ProposedTransportPolicyRule` | `rule_id:StableId; semantic:TransportSemantic; strength:Literal["hard","soft"]; numeric_limit_minutes:int[1..1440]|None; source_field:SourceField` |
| `ProposedEffectivePlanningPolicy` | `effective_pace:TravelPace; daily_time_rules:tuple[0..64,ProposedDailyTimeRule]; time_sensitive_must:tuple[0..100,ProposedTimeSensitiveMustAnchor]; transport_rules:tuple[0..32,ProposedTransportPolicyRule]`，无 `locked/source_digest`，由控制器按 §8.2 锁定 pace、拒绝非空延期规则 |
| `ProposedScheduledVisit` | `visit_id:StableId; candidate_handle:Handle; visit_kind:VisitKind; start_at:Asia/Shanghai aware datetime; end_at:Asia/Shanghai aware datetime; duration_source:Literal["user_explicit","planning_estimate"]; must_group_ids:tuple[0..100,StableId]; time_sensitive_anchor_ids:tuple[0..8,StableId]; business_hours_state:BusinessHoursState; business_evidence_handles:tuple[0..8,Handle]` |
| `ProposedRouteLeg` | `leg_id:StableId; origin_handle:Handle; destination_handle:Handle; start_at:Asia/Shanghai aware datetime; end_at:Asia/Shanghai aware datetime; transport_semantic:TransportSemantic; route_mode:TravelMode; directions_handle:Handle; alternative_index:int[0..2]`，start 可等于 end |
| `ProposedBoundaryTransfer` | `transfer_kind:Literal["arrival","departure"]; hub_handle:Handle; hotel_handle:Handle; route_leg:ProposedRouteLeg` |
| `ProposedBackupOption` | `backup_kind:Literal["attraction","lunch","dinner"]; core_visit_id:StableId; candidate_handle:Handle; matrix_handle:Handle; matrix_mode:MatrixTravelMode` |
| `ProposedDayPlan` | `date:date; day_kind:PlanningDayKind; theme:str[1..40]; open_intervals:tuple[0..16,TimeInterval]; visits:tuple[0..6,ProposedScheduledVisit]; route_legs:tuple[0..7,ProposedRouteLeg]; backups:tuple[0..3,ProposedBackupOption]` |
| `ProposedItineraryDraft` | `arrival_transfer:ProposedBoundaryTransfer; days:tuple[1..60,ProposedDayPlan]; departure_transfer:ProposedBoundaryTransfer; upstream_gap_ids:tuple[0..128,StableId]`；不得自报 requirement/hotel/candidate Ref 或 completeness |
| `ProposedCandidateCountTarget` | `target_type:Literal["candidate_count"]; role:ResearchRole; current_count:int>=0; target_count:int>=1`，且 current<target |
| `ProposedAreaCoverageTarget` | `target_type:Literal["area_coverage"]; role:ResearchRole; anchor_handle:Handle; radius_km:Literal[1,3,5,10]` |
| `ProposedMealCoverageTarget` | `target_type:Literal["meal_coverage"]; meal_intent:MealIntent; anchor_handle:Handle; radius_km:Literal[1,3,5,10]` |
| `ProposedMustResolutionTarget` | `target_type:Literal["must_resolution"]; requirement_group_id:StableId; requirement_item_ids:tuple[1..100,StableId]` |
| `ProposedAvoidResolutionTarget` | `target_type:Literal["avoid_resolution"]; requirement_group_id:StableId; requirement_item_ids:tuple[1..100,StableId]; candidate_handles:tuple[1..128,Handle]` |
| `ProposedCandidateDetailsTarget` | `target_type:Literal["candidate_details"]; candidate_handles:tuple[1..128,Handle]` |
| `ProposedReplacementTarget` | `target_type:Literal["replacement"]; rejected_candidate_handle:Handle; role:ResearchRole; anchor_handle:Handle|None; radius_km:Literal[1,3,5,10]|None`，anchor/radius 同空同非空 |
| `ProposedSupplementTarget` | 以上七分支，以 `target_type` 判别；`interest_coverage_missing` 不允许，因为兴趣仅是偏好 |
| `ProposedSupplementGap` | `source_gap_type:Literal["candidate_supply","backup_unavailable"]; date:date|None; target:ProposedSupplementTarget; evidence_handles:tuple[1..8,Handle]`；backup source 要求 date 非空 |

六个 public tool args：

| DTO | 字段顺序、类型与容量 |
|---|---|
| `GetCandidateEvidenceArgs` | `candidate_handles:tuple[1..16,Handle]`，唯一 |
| `GeocodeHubArgs` | `endpoint_kind:Literal["arrival","departure"]`，不得携带 query/city |
| `CalculateRouteMatrixArgs` | `date:date; mode:MatrixTravelMode; location_handles:tuple[1..10,Handle]`，唯一，控制器构造同序 N×N |
| `DirectionLegIntent` | `leg_id:StableId; origin_handle:Handle; destination_handle:Handle; transport_semantic:TransportSemantic; route_mode:TravelMode; alternatives_limit:int[1..3]` |
| `PlanDirectionsArgs` | `legs:tuple[1..planning.direction_call limit,DirectionLegIntent]`，`leg_id` 在 batch/run draft 内唯一，逐腿请求指纹唯一；batch cache miss 数不得超过 remaining |
| `SubmitItineraryArgs` | `effective_policy:ProposedEffectivePlanningPolicy; draft:ProposedItineraryDraft` |
| `RequestCandidateSupplementArgs` | `effective_policy:ProposedEffectivePlanningPolicy; draft:ProposedItineraryDraft|None; proposed_gaps:tuple[1..32,ProposedSupplementGap]` |

每个 tool args 按 §B.5 的提取算法计算的 Handle 出现总数不得超过 6000；该上限是 Action Artifact 的安全容量，不放宽各字段自身更小上限。工具参数中类型为 `Handle` 的字段只允许引用该轮开始时当前 context 已发放的 opaque handle。`visit_id/leg_id/rule_id/anchor_id` 是模型生成的 `StableId`，不走预存在 handle 校验，但必须满足格式、作用域唯一和引用一致性。`plan_directions` 的 origin/destination 必须是已有 handle；控制器为每个结果按 OperationId+leg ordinal 生成新的 directions handle，在 Observation 后才加入下一轮目录，模型不得预造。控制器把 proposal 解析为 §B.2 Ref/index DTO，并重新派生距离、耗时、营业、完成声明和 summary；模型不得提供 UID、坐标、地址、距离、耗时、ArtifactRef 或供应商对象。supplement `draft=None` 只在控制器证明当前确无可保存的合法访问/路线前缀时允许；只要已有合法前缀，就必须提交与之相符的非空 draft。

### B.4 Gap、摘要、failure 与 public outcome

`CandidateSupplementTarget` 是下列七分支 union；每个分支先含 `schema_version:Literal["1.0"]、target_type:下表 literal`，字段与 Module 08 SupplementalGap 的 target 部分等义，但使用 Module 09 location Ref；它不重复 `gap_id/evidence_refs`：

| target 分支 / `target_type` | 字段 |
|---|---|
| `CandidateCountTarget` / `candidate_count` | `role:ResearchRole; current_count:int>=0; target_count:int>=1`，且 current<target |
| `AreaCoverageTarget` / `area_coverage` | `role:ResearchRole; anchor:HotelLocationRef|CandidateLocationRef; radius_km:Literal[1,3,5,10]` |
| `MealCoverageTarget` / `meal_coverage` | `meal_intent:MealIntent; anchor:HotelLocationRef|CandidateLocationRef; radius_km:Literal[1,3,5,10]` |
| `MustResolutionTarget` / `must_resolution` | `requirement_group_id:StableId; requirement_item_ids:tuple[1..100,StableId]` |
| `AvoidResolutionTarget` / `avoid_resolution` | `requirement_group_id:StableId; requirement_item_ids:tuple[1..100,StableId]; candidate_uids:tuple[1..128,CandidateUid]` |
| `CandidateDetailsTarget` / `candidate_details` | `candidate_uids:tuple[1..128,CandidateUid]` |
| `ReplacementTarget` / `replacement` | `rejected_uid:CandidateUid; role:ResearchRole; anchor:HotelLocationRef|CandidateLocationRef|None; radius_km:Literal[1,3,5,10]|None`，anchor/radius 同空同非空 |

`PlanningGap` 精确为下列 15 分支的 discriminated union，不得增加其他分支。每个分支的共同字段为 `schema_version:Literal["1.0"]、gap_type:下表 literal、gap_id:StableId、evidence_refs:tuple[0..8,ArtifactRef]`；具体字段如下：

| 分支 / `gap_type` | 额外 typed target 字段 |
|---|---|
| `HubResolutionGap` / `hub_resolution` | `endpoint_kind:Literal["arrival","departure"]; status:Literal["ambiguous","no_result"]; candidate_count:int[0..10]` |
| `DailyTimePolicyClarificationGap` / `daily_time_policy_clarification` | `source_field:SourceField; reason:Literal["ambiguous","conflicting","unsupported_expression"]; rule_ids:tuple[0..16,StableId]` |
| `TimeSensitiveMustUnverifiedGap` / `time_sensitive_must_unverified` | `requirement_group_id:StableId; date:date|None; candidate_uid:CandidateUid|None; reason:missing_time|hard_conflict` |
| `MustVisitUnscheduledGap` / `must_visit_unscheduled` | `requirement_group_ids:tuple[1..100,StableId]; candidate_uids:tuple[0..8,CandidateUid]; reason:no_eligible|capacity|time|route|business_hours|transport` |
| `MealSlotUnfilledGap` / `meal_slot_unfilled` | `date:date; meal:Literal["lunch","dinner"]; reason:Literal["no_candidate","time","route","business_hours"]` |
| `DayCompositionGap` / `day_composition` | `date:date; day_kind:PlanningDayKind; actual_attraction_count:int[0..4]; required_min:int[0..4]; required_max:int[0..4]; reason:attraction_count|transit_has_visit`，且 actual 不在要求范围或 transit 非法含访问 |
| `PaceCapacityGap` / `pace_capacity` | `date:date; scope:Literal["day_total","single_leg"]; actual_seconds:int>=0; limit_seconds:int>=1; leg_id:StableId|None` |
| `RouteUnavailableGap` / `route_unavailable` | `date:date; origin:PlanningLocationRef; destination:PlanningLocationRef; route_mode:TravelMode` |
| `ScheduleConflictGap` / `schedule_conflict` | `date:date; visit_ids:tuple[0..8,StableId]; rule_ids:tuple[0..8,StableId]; reason:Literal["overlap","outside_window","boundary_transfer","cross_midnight"]` |
| `TransportConstraintGap` / `transport_constraint` | `date:date|None; leg_id:StableId|None; semantic:TransportSemantic; rule_ids:tuple[1..8,StableId]; reason:Literal["unsupported_mode","hard_conflict","unverified_service"]` |
| `OpeningHoursConflictGap` / `opening_hours_conflict` | `date:date; candidate:CandidateLocationRef; visit_kind:VisitKind` |
| `BackupUnavailableGap` / `backup_unavailable` | `date:date; backup_kind:Literal["attraction","lunch","dinner"]; core_visit_id:StableId; reason:Literal["no_eligible","no_nearby","business_hours"]` |
| `CandidateSupplyGap` / `candidate_supply` | `date:date|None; target:CandidateSupplementTarget`；转 supplement 时 evidence_refs 必须 1..8且全部为 candidate-research place Observation |
| `UpstreamResearchGap` / `upstream_research` | `upstream_gap_id:StableId; relevance:Literal["hard_blocking"]` |
| `PlanningProcessGap` / `planning_process` | `process_reason:Literal["direction_budget_exhausted","iteration_limit_reached","no_progress","no_legal_action","no_terminal_submission"]` |

除 `CandidateSupplyGap` 和可映射的 `BackupUnavailableGap` 外不得转 Module 08 supplemental；后者转 proposal 时同样必须有 1..8 个 candidate-research evidence Ref。`PlanningProcessGap.process_reason` 必须在 `stop_reasons` 中存在；仅由过程终止造成且没有更具体业务缺口时，以它保证 Incomplete 的 typed gaps 非空，不得伪造成候选或路线事实。`PlanningGapSummary` 是以上 15 分支的同构 union：`summary_type` 精确等于对应 `gap_type`，保留全部 scalar/typed target，把 `evidence_refs` 唯一替换为 `evidence_indexes:tuple[0..8,ArtifactEvidenceIndex]`；不得丢 target 或加入自由 detail。

| DTO | 字段顺序、类型与容量 |
|---|---|
| `PlanningBudgetSummary` | `schema_version:Literal["6.0"]; model_invocation_limit:Literal[19]; model_invocations_consumed:int[0..19]; preparation_limit:Literal[2]; preparation_used:int[0..2]; transport_limit:Literal[5]; transport_used:int[0..5]; submit_repair_limit:Literal[5]; submit_repair_used:int[0..5]; supplement_request_limit:Literal[5]; supplement_request_used:int[0..5]; direction_call_limit:int>=2; direction_calls_consumed:int>=0; budget_snapshot_complete:bool; semantic_invocation_limit:Literal[19]; semantic_invocations_consumed:int[0..19]; known_provider_attempts:int[0..38]; provider_attempts_complete:bool; known_usage:LLMUsage; usage_complete:bool; cache_hits:int>=0; normal_invocation_limit:Literal[17]; format_repair_limit:Literal[2]; format_repair_used:int[0..2]`；direction consumed≤limit，LLMUsage 一致性继承 Module 02 |
| `PlanningSummary` | `effective_pace:TravelPace|None; sightseeing_days:int[1..60]; full_days:int>=0; partial_days:int>=0; transit_only_days:int>=0; attraction_count:int>=0; lunch_count:int>=0; dinner_count:int>=0; unique_main_candidates:int[0..128]; boundary_transfers:int[0..2]; route_legs:int>=0; backups:int>=0; unknown_hours:int>=0` |
| `LLMPlanningFailure` | 见附录 C |
| `MapPlanningFailure` | 见附录 C |
| `ControlPlanningFailure` | 见附录 C |
| `PlanningFailure` | 以上三分支，以 `failure_type` 判别 |
| `ItineraryPlanningCompleted` | `schema_version; outcome_type:Literal["completed"]; itinerary_ref:ArtifactRef; summary:PlanningSummary; budgets:PlanningBudgetSummary` |
| `ItineraryPlanningIncomplete` | `schema_version; outcome_type:Literal["incomplete"]; itinerary_ref:ArtifactRef|None; summary:PlanningSummary; budgets; gaps:tuple[1..128,PlanningGap]; stop_reasons:tuple[1..8,PlanningStopReason]` |
| `ItineraryPlanningCandidateSupplementRequested` | `schema_version; outcome_type:Literal["candidate_supplement_requested"]; planning_ref:ArtifactRef; candidate_set_ref:ArtifactRef; gaps:tuple[1..32,PlanningGap]; gap_fingerprint:ContentDigest; budgets` |
| `ItineraryPlanningFailed` | `schema_version; outcome_type:Literal["failed"]; failure:PlanningFailure; last_consistent_itinerary_ref:ArtifactRef|None; failure_artifact_ref:ArtifactRef|None; budgets:PlanningBudgetSummary|None; retryable:bool; possible_duplicate:bool` |

`ItineraryPlanningOutcome` 以上四分支以 `outcome_type` 判别。Completed/Incomplete/supplement Ref 必须为同 run 的 `itinerary_planning.itinerary/6.0`；Failed evidence Ref 只能指向 §17.1 五种 Module 09 Artifact，按各自版本检查（model_action9.0、itinerary6.0、三种Observation1.0）。顶层 retryable/possible_duplicate 必须与 failure 分支相等。`PlanningSummary` 的计数只描述当前合法 draft；无 draft 时全为 0（`sightseeing_days` 仍取 confirmed），Completed 额外要求 `boundary_transfers=2` 且 route leg 数与全部日闭环/transfer 精确相等。budget_snapshot_complete=false时各consumed字段是服务已收到 confirmed-applied 的可证明下界，不能声称为后端全量；provider/usage 的 complete flag 同理控制已知聚合语义。

既有 DailyTimePolicyClarificationGap、TimeSensitiveMustUnverifiedGap 等类型保留结构合同，但首版不因延期文本存在而生成它们。功能关闭反馈按 §8.2 业务拒绝；终止时沿用真实 process gap，不虚构候选/地图事实。

### B.5 五种 Artifact 与 record

Artifact Repository envelope 继续由 Module 04 保存 UTC `created_at`、type/version/hash/OperationId。Module 09 content model 精确为：

本节全部 `*Snapshot`、五种 Artifact content model、五种 record、`ArtifactRefDirectories`、`ArtifactEvidenceIndex` 和 `ModelCallSummary` 均为 Artifact-local DTO，不加入包根公共导出。它们及其完整可达图必须满足本附录开头的 Module 04 注册合同。允许直接复用的外部 `BaseModel` 仅限经真实 registry 测试证明兼容的 `ArtifactRef`、`OperationId`、`LLMUsage` 和 `InvocationMetadata`；枚举、`UUID`、`date/datetime` 等非 `BaseModel` 类型可直接复用。Module 09 公共 DTO、Module 06 的 `BaiduCoordinate/MapProvenance/RouteMatrixCell` 以及 Module 02 的 `LLMRuntimeError` 均不得直接进入 Artifact 图，必须在 fresh strict 校验后转换为下列本地 snapshot。

| Artifact model / type | content 字段顺序与分支 |
|---|---|
| `ItineraryPlanningModelActionArtifact` / `itinerary_planning.model_action/9.0` | `schema_version:Literal["9.0"]; request_digest; invocation_index:int[0..18]; request_stage:preparation|transport|submit_repair; budget_category:preparation|transport|submit_repair|supplement_request|format_repair; prompt_version:Literal["planning-v9"]; prompt_hash:ContentDigest; action_kind:tool_calls|final_text|llm_failure; call_summaries:tuple[0..71,ModelCallSummary]; invocation_metadata:InvocationMetadata|None; disposition:accepted|policy_rejected|controller_rejected|terminal|failed` |
| `ItineraryPlanningHubGeocodeObservationArtifact` / `itinerary_planning.hub_geocode_observation/1.0` | `schema_version; request_digest; model_action_ref:ArtifactRef; action_ordinal:int[0..70]; endpoint_kind:arrival|departure; request_fingerprint; observation_status:success|ambiguous|no_result|failure|not_issued; success_candidate:HubGeocodeCandidateSnapshot|None; ambiguous_candidates:tuple[0..10,HubGeocodeCandidateSnapshot]; provenance:MapProvenanceSnapshot|None; map_failure:MapPlanningFailureSnapshot|None`，分支互斥 |
| `ItineraryPlanningRouteMatrixObservationArtifact` / `itinerary_planning.route_matrix_observation/1.0` | `schema_version; request_digest; model_action_ref:ArtifactRef; action_ordinal:int[0..70]; date; mode:MatrixTravelMode; request_fingerprint; ordered_locations:tuple[1..10,PlanningLocationRefSnapshot]; cells:tuple[0..100,RouteMatrixCellSnapshot]; observation_status:success|no_result|failure|not_issued; provenance:MapProvenanceSnapshot|None; map_failure:MapPlanningFailureSnapshot|None`，success cells 精确 N×N 同序 |
| `ItineraryPlanningDirectionsObservationArtifact` / `itinerary_planning.directions_observation/1.0` | `schema_version; request_digest; model_action_ref:ArtifactRef; action_ordinal:int[0..70]; leg_ordinal:int[0..481]; leg_fingerprint; origin:PlanningLocationRefSnapshot; destination:PlanningLocationRefSnapshot; transport_semantic:TransportSemantic; route_mode:TravelMode; alternatives_limit:int[1..3]; observation_status:success|no_result|failure|not_issued; alternatives:tuple[0..3,RouteAlternativeSummarySnapshot]; provenance:MapProvenanceSnapshot|None; map_failure:MapPlanningFailureSnapshot|None`，不含 RouteStep |
| `ItineraryPlanningArtifact` / `itinerary_planning.itinerary/6.0` | `schema_version; request_digest; parent_planning_ref:ArtifactRef|None; directories:ArtifactRefDirectories; record:ItineraryRecord` |

`ArtifactRefDirectories` 字段顺序固定为：`hotel_selection_refs:tuple[1,ArtifactRef]`、`candidate_set_refs:tuple[1..6,ArtifactRef]`、`model_action_refs:tuple[0..19,ArtifactRef]`、`hub_geocode_refs:tuple[0..2,ArtifactRef]`、`matrix_refs:tuple[0..1020,ArtifactRef]`、`directions_refs:tuple[0..482,ArtifactRef]`、`candidate_evidence_refs:tuple[0..128,ArtifactRef]`。每个目录按首次产生顺序去重，Ref 必须同 run、精确 type/version；model_action_refs 及各 Observation 的 model_action_ref 精确指向 model_action/9.0，其余目录沿用对应 1.0；非空目录之间也必须共享同一 run。`ItineraryPlanningArtifact` validator 还必须证明 `parent_planning_ref` 精确为同 run 的 `itinerary_planning.itinerary/6.0`，并重验 record 内全部 Ref。各 DTO 的 `*_index` 只索引其命名目录，禁止跨目录全局索引。

Artifact 使用的其他 nested DTO 精确为：

| DTO | 字段顺序、类型与容量 |
|---|---|
| `ModelCallSummary` | `schema_version; call_id:str[1..256]; ordinal:int[0..70]; tool:PlanningTool; arguments_fingerprint:ContentDigest; handle_count:int[0..6000]; handles_digest:ContentDigest; handle_preview:tuple[0..16,Handle]` |
| `CandidateLocationRefSnapshot` / `HotelLocationRefSnapshot` / `HubLocationRefSnapshot` / `PlanningLocationRefSnapshot` | 字段与 B.2 对应 public 分支同序，但 `schema_version`、判别字段及全部业务字段均 required；联合类型仍以 `ref_type` 判别 |
| `BaiduCoordinateSnapshot` | `schema_version; coordinate_system:Literal["bd09ll"]; latitude; longitude`，数值域与 Module 06 public DTO 一致 |
| `MapProvenanceSnapshot` | `schema_version; source:Literal["baidu_maps_mcp"]; capability; retrieved_at_utc:UTC aware datetime; verified:bool`，枚举和值域与 Module 06 public DTO 一致 |
| `RouteMatrixCellSuccessSnapshot` / `RouteMatrixCellNoResultSnapshot` / `RouteMatrixCellSnapshot` | 字段与 Module 06 对应分支同序，全部字段 required，联合类型按原 `status` 判别；success/no-result 不变量不得削弱 |
| `HubGeocodeCandidateSnapshot` | `schema_version; formatted_address:str[1..512]; coordinate:BaiduCoordinateSnapshot; confidence:float[0..1]|None` |
| `ArtifactEvidenceIndex` | `schema_version; directory:Literal["hotel_selection","candidate_set","candidate_evidence","hub_geocode","matrix","directions"]; index:int>=0`；index 必须落入指定目录 |
| `PersistedTimeInterval` | `schema_version; start_at_utc:UTC aware datetime; end_at_utc:UTC aware datetime; timezone:Literal["Asia/Shanghai"]; local_start_date:date; local_end_date:date` |
| `PersistedRouteTimeInterval` | 与 `PersistedTimeInterval` 字段同序，但允许 `start_at_utc=end_at_utc`；仅用于路线腿 |
| `DailyTimeRuleSnapshot` | `schema_version; rule_id; date|None; interval:PersistedTimeInterval; strength; source_field; source_digest` |
| `TimeSensitiveMustAnchorSnapshot` | `schema_version; anchor_id; requirement_group_id; local_date:date; event_at_utc:UTC aware datetime; timezone:Literal["Asia/Shanghai"]; source; source_ref_index|None` |
| `TransportPolicyRuleSnapshot` | 字段和值域与 B.2 `TransportPolicyRule` 同序，全部字段 required，location 引用改为本节 snapshot |
| `EffectivePlanningPolicySnapshot` | `schema_version; effective_pace; daily_time_rules:tuple[0..64,DailyTimeRuleSnapshot]; time_sensitive_must:tuple[0..100,TimeSensitiveMustAnchorSnapshot]; transport_rules:tuple[0..32,TransportPolicyRuleSnapshot]; locked:Literal[True]` |
| `ScheduledVisitSnapshot` | 与 `ScheduledVisit` 字段同序，location 使用 `CandidateLocationRefSnapshot`，`interval` 使用 `PersistedTimeInterval`，全部字段 required |
| `RouteLegSnapshot` | 与 `RouteLeg` 字段同序，origin/destination 使用 `PlanningLocationRefSnapshot`，`interval` 使用 `PersistedRouteTimeInterval`，全部字段 required |
| `BoundaryTransferSnapshot` | 与 `BoundaryTransfer` 字段同序，hub/hotel 使用对应 location snapshot，`route_leg` 使用 `RouteLegSnapshot`，全部字段 required |
| `BackupOptionSnapshot` | 与 `BackupOption` 字段同序，candidate 使用 `CandidateLocationRefSnapshot`，全部字段 required |
| `DayPlanSnapshot` | `schema_version; date; day_kind; theme; open_intervals:tuple[0..16,PersistedTimeInterval]; visits:tuple[0..6,ScheduledVisitSnapshot]; route_legs:tuple[0..7,RouteLegSnapshot]; backups:tuple[0..3,BackupOptionSnapshot]` |
| `ItineraryDraftSnapshot` | `schema_version; requirement_digest; hotel_selection_ref; candidate_set_ref; effective_policy:EffectivePlanningPolicySnapshot; arrival_transfer:BoundaryTransferSnapshot; days:tuple[1..60,DayPlanSnapshot]; departure_transfer:BoundaryTransferSnapshot; upstream_gap_ids:tuple[0..128,StableId]` |
| `CandidateSupplementTargetSnapshot` | B.3 可持久化的全部 target 分支的 Artifact-local 同构联合；每一分支字段均 required，location 引用改为本节 snapshot，判别与 public `target_type` 完全一致 |
| `PlanningGapSummarySnapshot` | B.3 全部 15 个 gap 分支的 Artifact-local 同构联合；字段、容量、判别和交叉 validator 与 public `PlanningGapSummary` 完全一致，`evidence_indexes` 使用 `ArtifactEvidenceIndex`，候选补充 target 使用 `CandidateSupplementTargetSnapshot` |
| `PlanningBudgetSummarySnapshot` / `PlanningSummarySnapshot` | 与 B.4 同名 public DTO 字段、容量和交叉 validator 完全一致，全部字段 required；`known_usage` 可使用已通过 registry 证明的 `LLMUsage` |
| `LLMRuntimeErrorSnapshot` | 与 Module 02 `LLMRuntimeError` 字段和值域同序；原本可空且有默认值的字段在 snapshot 中仍可空但必须显式提供 |
| `LLMPlanningFailureSnapshot` / `MapPlanningFailureSnapshot` / `ControlPlanningFailureSnapshot` / `PlanningFailureSnapshot` | 与 B.4 对应 public 分支同构；全部字段 required，LLM error 使用 `LLMRuntimeErrorSnapshot`，联合按 `failure_type` 判别，并重复附录 C 的 flag validator |
| `RouteAlternativeSummarySnapshot` | 与 public `RouteAlternativeSummary` 同序且全部字段 required，不保存 RouteStep |

`PlanningGapSummarySnapshot` 的 `evidence_indexes` 精确使用 `ArtifactEvidenceIndex`，而不是无目录含义的裸整数。`InvocationMetadata` 和 `LLMUsage` 仅在真实 registry 证明其完整可达图兼容后直接复用；其余外部或公共模型必须使用上表 snapshot，禁止为通过注册而复制宽松版本或删除业务 validator。tool-calls Action 的 call summaries 必须非空并与 validated calls 同序，final-text/LLM-failure Action 的 summaries 必须为空；只有前两类成功响应保存 `InvocationMetadata`，LLM failure 为 None。

`ModelCallSummary` 的 handle 序列不得保存全量：按工具 schema 字段顺序、嵌套 tuple 顺序提取所有类型明确为 `Handle` 的出现项；`get_candidate_evidence` 取 candidate handles，geocode 为空，Matrix 取 location handles，Directions 逐腿取 origin/destination，submit 依次取 policy source handle、boundary/日计划中的 location/directions/evidence/matrix handles，supplement 再按 proposed gap 顺序追加 target/evidence handles。模型生成的 StableId 不计入。`handle_count` 是完整出现次数，`handles_digest=sha256(canonical JSON 完整有序 tuple)`，`handle_preview` 精确为前 `min(16,count)` 项；validator 从 validated args 重算三者。超过 6000 的 Action 在写 Artifact 前以 `action_batch_invalid` 失败，不截断。

每个 Observation 的 `model_action_ref` 必须指向同 request digest/run 的 model-action Artifact。Observation 分支不变量精确为：success 才允许 success payload/cells/alternatives，ambiguous 仅 geocode 且候选 2..10，no_result payload 为空但 provenance 非空，failure 仅 map_failure 非空，not_issued 的 payload/provenance/failure 全空。

`ItineraryRecord` 精确为以下五分支，以 `record_type` 判别：

| record | 字段 |
|---|---|
| `InProgressItineraryRecord` / `in_progress` | `schema_version; record_type:Literal["in_progress"]; state:PlanningStateSnapshot; gaps:tuple[0..128,PlanningGapSummarySnapshot]` |
| `AwaitingCandidateSupplementRecord` / `awaiting_candidate_supplement` | `schema_version; record_type:Literal["awaiting_candidate_supplement"]; state:PlanningStateSnapshot; gaps:tuple[1..32,PlanningGapSummarySnapshot]; gap_fingerprint:ContentDigest; pending_call_id:str[1..256]` |
| `CompletedItineraryRecord` / `completed` | `schema_version; record_type:Literal["completed"]; draft:ItineraryDraftSnapshot; summary:PlanningSummarySnapshot; budgets:PlanningBudgetSummarySnapshot` |
| `IncompleteItineraryRecord` / `incomplete` | `schema_version; record_type:Literal["incomplete"]; draft:ItineraryDraftSnapshot|None; summary:PlanningSummarySnapshot; budgets:PlanningBudgetSummarySnapshot; gaps:tuple[1..128,PlanningGapSummarySnapshot]; stop_reasons:tuple[1..8,PlanningStopReason]` |
| `FailedItineraryRecord` / `failed` | `schema_version; record_type:Literal["failed"]; last_consistent_itinerary_ref:ArtifactRef|None; failure:PlanningFailureSnapshot; budgets:PlanningBudgetSummarySnapshot|None; retryable:bool; possible_duplicate:bool` |

PlanningStateSnapshot/6.0保存schema_version、request_kind、requirement_digest、effective_policy、draft、budgets、current_stage、model_invocations_consumed、preparation_used、transport_used、submit_repair_used、supplement_request_used、no_progress_streak、last_rejection、supplement_fingerprints、pending_call_id，以及format_repair_used、correction_targets、correction_plan_fingerprints、correction_evidence_fingerprint、correction_fact_fingerprints、correction_no_progress、last_proposal_fingerprint。正常四类消耗与格式修正消耗之和等于已确认的总消耗，并与budgets相符；上限见§12.2和§26。last_rejection可空，非空时包括reason、field、business_fingerprint、relevant_evidence_fingerprint。awaiting的pending必须非空，其他record为None；纠错状态用于实际问题进展，不得因补研清空。draft=None仅按B.2的合法前缀条件；未知预算不能填零充完整。

Artifact-local 日程 snapshot 不直接嵌入要求 `Asia/Shanghai` offset 的 public DTO：每个 datetime 保存为 `*_utc`（UTC aware），同时保存 `timezone:Literal["Asia/Shanghai"]` 和所属 local date。写入前从 public +08:00 instant 转 UTC；读回先转 Asia/Shanghai，再重验日期、网格、跨午夜和排序。这样与 Module 04 canonical JSON 统一转 UTC 的规则兼容，public outcome 仍只暴露 Asia/Shanghai aware datetime。

公共 DTO 与 Artifact-local snapshot 之间只允许使用显式转换函数：写入方向先对公共对象执行 JSON round-trip 的 fresh strict 校验，再构造 snapshot；读取方向先由 registry decode 并 fresh strict 校验 snapshot，再重建并 fresh strict 校验公共 DTO。转换必须保持字段语义和全部交叉不变量，不得直接把 snapshot 作为公共返回值，也不得依赖已构造实例绕过验证。五个 `ArtifactRegistration` 必须共同实例化真实 `ArtifactSchemaRegistry`，并对每种 Artifact 执行 repository prepare/decode round-trip；只比较 registration descriptor 不算验收。

`parent_planning_ref` 在首个 state 为 None，此后精确指向同 run/type/version 的上一 itinerary state；resume 首个新 state 指向请求中的 awaiting predecessor。成功写入的 completed/incomplete/supplement public Ref 就是该 terminal itinerary Artifact 自身；Incomplete 无 draft 时 `itinerary_ref=None`。成功写入 failed record 时 `failure_artifact_ref` 是该 terminal Artifact 自身，`last_consistent_itinerary_ref` 取 record 字段；§14.4 末跳写失败时 `failure_artifact_ref=None`。

### B.6 Fingerprint/hash tree

全部 Module 09 fingerprint 使用 `sha256(UTF8(Module04CanonicalJSON(payload)))`，digest 带 `sha256:` 前缀。payload 顶层字段顺序语义固定为 `fingerprint_version、kind、fields`；request_initial 和 planning_context 使用 planning-fp-v5，request_resume 使用 planning-resume-fp-v6，其余 kind 保持 planning-fp-v1。canonical JSON 负责 key 排序，业务 tuple 顺序不得自行排序。各 kind 的 fields 精确为：

| kind | fields |
|---|---|
| `request_initial` | `capability_scope=module09-format-recovery-v1、schema_version、run_id、confirmed fresh canonical hash、hotel ArtifactRef 七字段 canonical payload、candidate-set ArtifactRef 七字段 canonical payload、完整 OperationPlan canonical hash` |
| `request_resume` | `capability_scope=module09-format-recovery-v1、schema_version、run_id、predecessor planning Ref 七字段 canonical payload、supplemented candidate-set Ref 七字段 canonical payload、完整 operation_plan canonical hash、完整 supplemental_operation_plan canonical hash` |
| `planning_context` | `capability_scope=module09-format-recovery-v1、run_id、confirmed fresh canonical hash、hotel ArtifactRef 七字段 canonical payload、arrival hub 原文 digest、departure hub 原文 digest` |
| `candidate_handle` | `planning_context_digest、admission_ordinal、uid` |
| `candidate_evidence` | `planning_context_digest、candidate_handle、当前候选 planning projection canonical hash、同序 evidence ArtifactRef content_hash` |
| `geocode` | `planning_context_digest、destination、endpoint_kind、confirmed hub text digest` |
| `matrix` | `planning_context_digest、date、mode、同序(location Ref identity, BD09LL longitude, latitude)` |
| `directions` | `planning_context_digest、origin Ref/coordinate、destination Ref/coordinate、route_mode、alternatives_limit` |
| `directions_handle` | `directions Observation OperationId canonical payload、leg_ordinal、directions request fingerprint` |
| `gap` | `planning_context_digest、gap_type、该分支全部 typed target scalar/tuple、同序 evidence content_hash` |
| `submit` | `planning_context_digest、当前 candidate-set ArtifactRef 七字段 canonical payload、ProposedEffectivePlanningPolicy canonical payload、ProposedItineraryDraft canonical payload` |
| `terminal_record` | `request digest、outcome/record type、record canonical payload hash、terminal successor OperationId` |

凡表中写 ArtifactRef canonical payload，精确使用 Module 04 的全部七字段：`artifact_id、artifact_type、schema_version、content_hash、operation_id、created_at UTC instant、size_bytes`。Resume 请求摘要只从固定 capability_scope 与 fresh 请求内联的版本、Ref 及两份完整计划计算，不读取前驱、不把 §15.1 后续重建的 Module 08 原请求摘要加入 fields；两种摘要的算法与用途不得混用。

`planning_context_digest` 就是上表 `planning_context` 的结果，故两份 OperationPlan 和 candidate-set successor 不会无差别使已取得的地图事实失效。候选 handle 由 run 内稳定 ordinal+UID 决定；resume 更新只改变该候选的 evidence fingerprint 和包含其坐标的派生 Matrix/Directions fingerprint，未受影响候选的 handle 和精确相同路线事实可复用。同 OperationId 不同 request/fingerprint 是 `request_mismatch`。仅上述三种 kind 改变版本/输入字段，其余 kind 对相同完整输入保持算法不变；含新版 context 的实际输出自然变化，不能要求与旧范围相同；任何后续字段、排序或 domain separator 变化都需要相应的新 fingerprint version，不得静默复用旧版本。

## 附录 C：失败枚举与失败 DTO

`PlanningFailureCode` 精确且仅包含以下值，顺序也是稳定排序顺序；新增/删除/改义必须设计修订：

```text
input_invalid
predecessor_invalid
operation_plan_invalid
request_mismatch
model_contract_invalid
action_batch_invalid
policy_contract_invalid
artifact_error
artifact_capacity_exceeded
budget_error
observation_invalid
operation_result_indeterminate
run_context_closed
internal_invariant_broken
```

failure DTO 精确为：

| DTO | 字段 |
|---|---|
| `LLMPlanningFailure` | `schema_version; failure_type:Literal["llm"]; error:LLMRuntimeError; invocation_index:int[0..18]; retryable:bool; possible_duplicate:bool`，两个 bool 与 error 相等 |
| `MapPlanningFailure` | `schema_version; failure_type:Literal["map"]; capability:Literal["geocoding","route_matrix","directions"]; error_code:BaiduMapErrorCode; action_ordinal:int>=0; retryable:bool; possible_duplicate:bool`，bool 按下表由 Module 09 固定派生 |
| `ControlPlanningFailure` | `schema_version; failure_type:Literal["control"]; code:PlanningFailureCode; retryable:bool; possible_duplicate:bool`，不得携带 detail/message |
| `PlanningFailure` | 上述三分支，以 `failure_type` 判别 |

`MapPlanningFailure` 的 flags 不从 Module 06 异常读取，而由 Module 09 对只读地图调用固定派生：

| `BaiduMapErrorCode` | retryable | possible_duplicate |
|---|---:|---:|
| `request_error` | false | false |
| `configuration_error` | false | false |
| `authentication_error` | false | false |
| `rate_limit_error` | true | false |
| `timeout_error` | true | false |
| `transport_error` | true | false |
| `session_error` | false | false |
| `tool_unavailable` | false | false |
| `tool_schema_error` | false | false |
| `protocol_error` | false | false |
| `response_error` | false | false |
| `concurrency_error` | false | false |

`ControlPlanningFailure` 的固定 flags 为：

| code | retryable | possible_duplicate |
|---|---:|---:|
| `input_invalid` / `predecessor_invalid` / `operation_plan_invalid` / `request_mismatch` | false | false |
| `model_contract_invalid` / `action_batch_invalid` / `policy_contract_invalid` | false | false |
| `artifact_error` | true | false |
| `artifact_capacity_exceeded` | false | false |
| `budget_error` | true | false |
| `observation_invalid` | false | false |
| `operation_result_indeterminate` | false | true |
| `run_context_closed` / `internal_invariant_broken` | false | false |

唯一上下文例外是 §14.4 terminal Artifact 写结果不确定：仍用 `artifact_error`，但固定 `retryable=false、possible_duplicate=true`；预算 consume 结果不确定不得用 `budget_error`，统一提升为 `operation_result_indeterminate`。`LLMPlanningFailure` 两个 flags 逐字段等于 Module 02 `LLMRuntimeError`；三种 failure 的顶层和 terminal record flags 必须逐字段相等。

公共 marker exception 只有 `ItineraryPlanningError`、`ItineraryPlanningConfigurationError(code="prompt_resource_invalid")` 和 `ItineraryPlanningUsageError(code="service_usage_error")`；均使用 `__slots__`，只暴露固定 code，不携带动态 message/payload。业务方法内部可持久化的预期失败返回 typed `ItineraryPlanningFailed`，不抛 marker。不得使用 generic `unknown_error`、异常消息、任意 detail dict 或 provider body。

## 附录 D：实现验收决定

- `D-09-51`：Module 09 生产包和 facade 固定为 §7/附录 A。
- `D-09-52`：公共 input/output 只传严格 DTO 和 ArtifactRef，不 inline 大型 itinerary。
- `D-09-53`：五类 Artifact、版本和安全 Ref 目录固定为 §17。
- `D-09-54`：OperationPlan、terminal replay、singleflight、run 锁和末跳失败固定为 §14。
- `D-09-55`：六工具白名单和每工具业务边界固定为 §10。
- D-09-56：阶段/调用次数及停止机制按 §12；不再保留额外terminal-only，普通文本仍Incomplete。
- `D-09-57`：Direction 预算 all-or-none、wave3、issued 不退款和 cache 不消费固定为 §13。
- `D-09-58`：取消原样传播、错误全序和无后续副作用固定为 §18。
- `D-09-59`：Module 09 不实现天气、饮食、价格、实时导航、Citywalk 或步行专限。
- `D-09-60`：实现验收必须逐条对照本文，coverage line/branch/combined 各 70%，Docker 安全 Gate 和 MODULE-CLOSEOUT 均不可省略。
- D-09-61：补充申请独立最多 5 次；2+5+5+5=17，M02 同步 17，不占普通阶段且不绕过最后仅提交门槛。
- `D-09-62`：所有路线腿用可表达零耗时的 `RouteTimeInterval` 保存精确排程占用；full/partial 至少一个开放区间，transit-only 允许空区间。
- `D-09-63`：候选 supplement 只按 §15 固定表映射 Module 08 分支；Module 13 不推断 target 字段。
- `D-09-64`：Map/Control/LLM failure flags 按附录 C 固定派生；request fingerprint 使用 ArtifactRef 全部七字段。
- `D-09-65`：五种 Artifact 的完整可达 DTO 图必须通过 Module 04 真实 `ArtifactSchemaRegistry`；不兼容的 public/upstream DTO 只能经 fresh strict 显式转换进入 B.5 Artifact-local snapshot，持久化与公共 API 语义保持同构而非直接复用对象。
- `D-09-66`：所有 `--read-only` Docker `compileall` Gate 必须把 `PYTHONPYCACHEPREFIX` 指向 `/tmp` tmpfs；不得把预期的字节码缓存写入失败当成源码编译失败，也不得向宿主工作树写 `__pycache__`。
- `D-09-67`：Resume required 携带实际 Module 08 `supplemental_operation_plan`，按 §15.1 从 awaiting 状态与冻结映射重建原请求，并核对后继前驱 Ref、terminal OperationId 与完整请求摘要；合法 Incomplete、无新增 UID 和仅旧 UID 更新不能因结果不满足原目标而被误拒绝。Module 08 DTO/Artifact/代码不变，Module 09 不调用其服务或消费其预算。
- D-09-68：版本按 §20.1：Resume6.0、Initial/计划/预算/Outcome6.0、ModelAction9.0/planning-v9、itinerary6.0；Initial/context fp-v5、Resume fp-v6。新范围 exact replay 先于前驱读取，无旧版本兼容/转换。


### D-09-69 备用点可选修订（2026-09-28，用户明确批准）

缺少任意备用角色不阻止 Completed，保留合法前缀时不生成 BackupUnavailableGap；备用点专用补充不准入，只允许主行程候选缺口。历史 DTO 分支可以保留为结构定义，不授权自动补研。主行程硬规则及上游 M08 Artifact 不变；阶段/预算/版本采用 §12～15、§20.1 的本次更新。

## 公交乘车步骤扩展（2026-09-28）

用户批准在独立分支完善“先坐什么车、再坐什么车”的静态展示，具体执行与验收见 [实施记录](VALIDATION.md)。本节覆盖旧版禁止保存任何路线说明的范围限制，仅允许 public_transit/transit 的文本证据投影，不允许模型生成线路或实时导航。

- M06 已验证的返回 alternatives 各自保留 steps 的 instruction 原序；最多128项，每项1..1024字符且至少一个非空白字符，数量必须等于step_count。不保存vehicle、路线geometry、原始MCP body或其他未知字段；旧模式不保存步骤。
- 原 RouteAlternativeSummarySnapshot（schema_version=1.0）不变；新增严格子类型 TransitRouteAlternativeSummarySnapshot（schema_version=1.1），全部字段必填，增加transit_instructions元组。Observation外层1.0按封闭union接受两种摘要，非transit模式拒绝新类型。1.0/1.1是仍在生成的事实分支；本次不要求兼容旧itinerary运行，不能因此删除非公交1.0。
- 非持久 RouteAlternativeSummary 增加默认空元组的transit_instructions，仅在公交查询有证据时填入。空值不出现在其序列化中。持久模型无默认字段，不改变M04 Registry、哈希算法或数据库结构。
- 缓存和快照转换保留步骤；工具结果仍只投影原摘要字段，Prompt、模型草案和LLM上下文不接收步骤。结果层按directions_observation_index及alternative_index选择绑定的证据，不混用其他方案。
- 新范围Observation的exact replay不补查询、不伪造步骤；不存在步骤的非公交事实沿用正常展示。repr不展示步骤字段，日志及诊断仍不输出原文。只有受控结果正文消费这些文字。
## 2026-09-29 四项改进增量修订（用户已批准）

以 [M09四项改进设计及验收](VALIDATION.md) 为本次增量依据：M09供应商工具使用beta严格schema，原本地完整校验保留；非法工具调用整批拒绝；Directions给出代码推导时长，路线end_at可空并由代码计算（显式旧值须一致）；Prompt减少格式/计算重复；提交反馈包含稳定reason/field。仅修改模型输入端，不改变历史持久行程schema、预算、硬门槛或数据库。本文此前涉及统一非严格工具输出、路线end_at必填和泛化反馈的条款由本节限定覆盖，其余条款继续有效。实施范围、前置条件、测试矩阵及Git限制见上述文档。

2026-09-29修复重跑补充：依据用户“修复问题然后重跑，有三次重跑机会”授权，strict wire完全展开无递归本地引用；被供应商限制移除的数量/长度及日期格式保留为说明，本地验证不变。M09终结提交拒绝在返回通用状态前记录代码内reason/field与terminal_only，不输出参数或异常正文，不改公开错误/持久化合同。三次真实结果、白名单及验收详见[m09_four_improvements.md](VALIDATION.md)；最后业务提交仍未通过，不据此扩大重跑或预算。

2026-09-29轻量错误定位增量：按用户授权实现[错误定位日志](error_location_logging.md)。在JSON解析、本地工具参数校验和业务拒绝转换为通用结果之前记录脱敏字段/规则/源码位置；最后一次提交同样记录，真实测试单独保存error-locations.jsonl。仅复用日志与run_id，不增加数据库表、迁移、错误结果字段、回放系统或调用额度。详见该文档的范围、测试矩阵及限制。


## 25. 本次需求确认与验收追踪（2026-09-30）

本次对应规划阶段预算与完整交互记录两部分。用户已确认 D-01～D-16，并批准本设计及 W00～W03 实施；生产代码已落实，最终验收状态以本次报告为准。各职责写在原所属文档中，本文没有另一份独立设计真源。执行范围见 [原 M09 执行规范 §29](09_itinerary_planning_loop_vibe_coding.md)。审查证据见 [报告](VALIDATION.md)。

### 25.1 已确认决定和范围

| 决定 | 本次合同 |
|---|---|
| D-01 | 代码控制阶段切换，模型在当前允许工具内选择行动。 |
| D-02 | 不增加轻量初稿登记或第二套规划器；代码不承诺在完整提交前推算全部缺失证据、路线或规划是否完成。 |
| D-03 | 按逻辑模型调用计数；多工具 batch 算一次，查询后再次提交各算一次；供应商网络重试不增加逻辑次数。 |
| D-04 | 候选补充申请使用独立额度，M02 规划角色总上限同步提高。 |
| D-05 | 准备 2、交通 5、提交与修正 5、补充申请 5，合计最多 17；首次提交包含在修正的 5 次内，不再加旧 terminal-only 调用。 |
| D-06 | 前驱已合法，抵离枢纽均有可用坐标就结束准备；不要求读完全部候选证据；准备额度不足时明确缺口并停止。 |
| D-07 | 交通阶段允许提前提交；首次 submit 只计修正额度。交通 5 次用完则进入提交与修正；校验通过立即结束。 |
| D-08 | 修正查询必须给后续提交留至少一次；修正剩 1 次时实际工具集合仅 submit，不靠 Prompt 劝告。 |
| D-09 | 修正仅在连续两次明确重复且无实质改变的业务拒绝时提前停止；改字段、改业务方案或有相关新证据可继续。 |
| D-10 | 记录保存在现有 PostgreSQL，按运行编号导出，不建设日志平台或通用记忆系统。 |
| D-11 | 保存完整业务交互与供应商实际返回的 reasoning_content；排除凭证，仅用于诊断。 |
| D-12 | 覆盖 Demo 的 M03/M08/M09 模型调用及关联执行结果；M08 action/judgment 都记录。 |
| D-13 | SDK 解析前采集请求和响应；失败、截断和每次供应商尝试也记录；缺失值不补造。 |
| D-14 | 仅诊断写入失败不改变业务结果，尝试本地兜底，明确报告缺失。 |
| D-15 | 同一 PostgreSQL 新增一张专用诊断表及增量迁移，独立于业务 Artifact 容量。 |
| D-16 | 本次升级不做旧测试结果兼容读取、旧运行续跑或阶段计数转换；新运行使用新版本。 |

范围不包括累计 Token 限额、旅行金额预算、模型切换、调整思考强度、宽松 JSON 修补、额外技术重试、前端日志页、队列、后台同步、自动清理或跨进程模型会话恢复。可以在本范围内删减旧的六轮/额外终态分支与失效测试，不建立兼容层；其他既有改动必须保留。

### 25.2 所有权与依赖

| 所有者 | 本次职责 |
|---|---|
| M02 | 当前工具集合的真实出站绑定；逻辑调用准入回调；请求/响应采集、原始 usage 与供应商思考；轻量记录 DTO/端口和上下文。 |
| M03 | 标记 extract/repair 调用用途；不直接操作数据库、文件或供应商。 |
| M04 | 预算接线表更新；业务 Repository、事件、RunSummary 和状态 codec 不承载交互正文。 |
| M08 | 标记 action/judgment，关联动作、地图结果与拒绝反馈；其 6 轮/48 Place/12 逻辑调用不扩大。 |
| M09 | 阶段、额度、工具权限、修正进展、提交校验和新版规划 Artifact；记录工具执行与调用归属。 |
| M10 | 专用诊断表、短事务追加/读取、容量与迁移；借用现有连接池，不改变 ArtifactRepository Protocol。新增对 M02 诊断 DTO/端口的直接依赖。 |
| M11 | 既有预算/journal/租约/检查点；不把诊断记录当执行许可或恢复证据。 |
| M12 | 读取新版规划 Artifact；完整 Gate 仍属 M09；不增加模型调用。 |
| M13 | 注入 recorder、绑定 run/trace 上下文、预算及 OperationPlan、新执行合同、补研路由、导出装配。 |

M02 不导入 M09/M10/M11，不了解旅游阶段规则或 SQL。M10 只消费 M02 固定诊断类型，不启动模型。M13 不访问私有连接池或 SQL；业务层通过注入的记录端口关联结果。地图调用仍走 M06 typed adapter，诊断不新增地图请求或保存原始 MCP 响应。

### 25.3 本次验收条件

| AC | 必须证明 |
|---|---|
| AC-01 | 2/5/5/5 分类和总上限 17；多工具一次、网络两尝试一次、首次及成功提交一次、预检拒绝零消费。 |
| AC-02 | 枢纽可用立即结束准备；无证据全量阅读门槛；准备耗尽保留真实 gap。 |
| AC-03 | 提前 submit 不双计；交通 5 后首次 submit；修正最后实际出站 schema 仅 submit，禁止查询和 supplement。 |
| AC-04 | 不同错误/业务修改/相关新证据不被旧两轮规则截断；两次相同无效提交能停止；额度仍封顶。 |
| AC-05 | 补充申请独立且总计17；M08实际研究另计；补研返回不重置阶段/额度/cache/history；没有第18次。 |
| AC-06 | 新版 Artifact/Ref/M12/M13 合同一致；新同进程补研和网页安全暂停通过；旧版明确拒绝，无兼容/自动转换。 |
| AC-07 | 原始 request/response 在SDK转换前完整采集；非法JSON、截断、schema失败和两次attempt仍有资料；无响应/usage缺字段不补零。 |
| AC-08 | M03 extract/repair、M08 action/judgment、M09工具/校验可按call关联；普通日志、repr、业务Artifact和结果无正文/凭证。 |
| AC-09 | 新诊断表重启可读、同ID幂等/冲突、索引导出、独立容量；旧业务表和M11表未改，迁移无清空或回填。 |
| AC-10 | 诊断不可用/超时/满额→本地/缺失且业务结果不变；业务持久化故障仍停止；取消原样传播。 |
| AC-11 | 导出已知usage精确对账、思考不双计、缺失可见、重复保存不算重复调用；真实原文仅在专用导出。 |
| AC-12 | demo完整行程仍由现有M09Gate通过，M12公交线路/换乘步骤保留，未新增任何模型/地图重试或伪造事实。 |

测试必须用真实执行路径和有意义的失败/边界 fixture，不能只统计数量或用预制 Completed 代替主链。真实 API 资格须另行授权，未运行不能写已通过。审查只阻断主演示/公共合同/明显安全问题P0/P1，高概率演示P2立即修复；边缘P2/P3记入 [统一延期清单](VALIDATION.md)。

## 26. 通用参数修正与共享储备（2026-10-06，用户已批准）

状态：APPROVED_FOR_IMPLEMENTATION；用户批准“正常规划 17 次 + 全 run 共享 2 次格式修正 = 19 次”，并授权开始修复。此增量优先于§12.3/§18.4的参数失败立即终止，以及§13/§14/§20/附录 B 的旧 17/34/16 上限；其他业务、证据和网络重试语义保留。

| 验收 ID | 批准合同 |
|---|---|
| R-09-01 | M02 通用拒绝 DTO/显式 repair 能力按 [M02 §20](02_llm_runtime_and_model_profiles.md#20-通用工具参数拒绝与显式修正2026-10-06用户已批准) 接入；不删除/nullable 化 transfer_kind，不本地补字段，不截断参数，不新增旅行字段特判。 |
| R-09-02 | 每 run 普通四分类额度仍 2/5/5/5，合计≤17；format_repair 独立≤2，总 model/semantic≤19、物理 attempts≤38。四普通 used + format_repair_used = 总 consumed；格式调用不再次增普通阶段分类。每次修正有独立 interaction、admission、action/state slot 和一次预算消费。正常调用不能占格式储备。 |
| R-09-03 | 任一参数错整批零业务/地图执行；拒绝响应已验证 metadata 仍累计 usage/attempts；action 使用 llm_failure 且 calls 空、metadata=None，具体安全错误留诊断 DTO。坏 usage/协议/未知工具/内部异常继续原失败关闭，不重试成格式错误。 |
| R-09-04 | 同一拒绝链初始 + 最多两次修正，换 call ID、字段或错误类型不重置；整批 schema 成功才清链，但全 run 两次储备不补充。两次用完仍错误返回原 typed 失败，retryable=False；补研/resume 沿同一 run 保留计数，暂停不得带 pending 格式错误。 |
| R-09-05 | pending 格式错误时保持拒绝那次 request_stage 和精确工具集合，并重新核验权限/字节/quota/admission；阶段普通额度耗尽也允许剩余格式储备，不能先 advance 或停止。修正合法后才执行通常的全批/handle/业务 Gate，然后恢复正常阶段判断；不得增加地图权限/额度。 |
| R-09-06 | 新 OperationPlan/slot/Initial 3.0、Resume 4.0；初始槽 0..18，resume 从同 run consumed 连续到18。新预算/State 3.0 加 required normal_invocation_limit=17、format_repair_limit=2、format_repair_used；公开 outcome 和 itinerary Artifact 3.0；action 6.0 / planning-v6；failure 同字段版本2.0支持index≤18。Prompt/fingerprint 版本同步。 |
| R-09-07 | 保留旧预算/State严格结构和模型动作事实的精确版本校验；旧模型动作canonical bytes/hash原样验证，不回填、不重写或追加额度。行程来源按§20.1/§29只接受当前6.0，不转换旧行程；旧非终态沿原incompatible/indeterminate处理，不新增旧runtime路由。 |
| R-09-08 | Ref 目录 1.0（17/action5）与2.0（19/action6）条件校验；Observation 仍1.0但读取合法5/6 action ref。主图装配19模型预算并使用新 OperationPlan；M12读取2/3 itinerary、5/6 action，旧结果读取保留。所有读写边界显式核验外层Ref/Write版本等于内容版本；新主图运行合同版本隔离旧17预算，旧终态读取不触发预算重新激活，旧暂停不得透明变为19。数据库结构和迁移不变，PG资格须证明两版本读取/哈希一致。 |
| R-09-09 | 地图 direction 8×days+2、hub2、matrix总容量1020、token/字节/timeout与网络 retry 不加2。矩阵操作计划按 min(17,剩余语义槽)×days 有界发放；format rejected 批次不执行，实际合法业务调用上限仍受普通17额度约束。 |
| R-09-10 | M13原聚合终态 code 保留，M14失败文案覆盖模型调用或输出校验失败，不再把所有 workflow_provider_failed 归因供应商暂不可用。验收按文档逐条映射，不能只凭 tests 绿。 |

本增量 Prompt 真源为 `app/itinerary_planning/prompts/planning_v6.txt`，版本 `planning-v6`，UTF-8/LF 字节 SHA256 为 `sha256:3b32203ec7b0a317940bf562964d222aaf5575611d675d1615083653c1b8233a`；旧资源只读保留。

实施顺序和最小白名单见 [E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)。每相邻两阶段统一相应测试后，原两位独立子 agent 分别作架构和执行核查。完成本地修复等待提交；本次未授权 stage/commit/merge/push/PR/部署，新真实 API 调用未进入本轮预算。

## 27. M09 单次生成额度128K（2026-10-08，用户已批准）

状态：APPROVED_FOR_IMPLEMENTATION。用户明确要求“把 M09 行程规划设置为修改为128k”，本次仅调整静态单次生成额度；[M02 §6](02_llm_runtime_and_model_profiles.md#6-固定-profile-矩阵) 是 Profile 真源，执行入口为 [E09 §31](09_itinerary_planning_loop_vibe_coding.md#31-m09-单次生成额度维护2026-10-08用户已批准)。

| 验收 ID | 本次批准合同 |
|---|---|
| O-09-01 | 固定 `ITINERARY_PLANNING_PROFILE.max_output_tokens=131_072`（128K），覆盖首次规划、普通续调与显式参数修正；生产 SDK `max_tokens` 使用相同 Profile，不能只更新文案。 |
| O-09-02 | 普通阶段 2/5/5/5、普通合计 17、共享格式修正 2、总逻辑 19、物理 attempts 最多 38；direction=8×days+2、hub2、matrix1020、地图并发3和原提前停止条件均保留。 |
| O-09-03 | 保持 enabled/high、完整请求 2,097,152 bytes、读取 120 秒、连接10/写入30/池等待10秒、每次最多两次技术尝试及 SDK retries=0；其他角色与通用默认的输出额度不变。 |
| O-09-04 | 本次没有输出恢复能力；`finish_reason=length` 仍按 `output_truncated` 失败关闭，不进入共享参数修正，不执行截断工具批次，不本地补字段或放宽 schema/业务门槛。 |
| O-09-05 | 不改变 Prompt、指纹、Artifact/schema/数据库结构；历史运行、旧额度记录、旧诊断和已暂存文件保持。提高额度不追溯改写历史终态，也不自动重跑失败任务。 |
| O-09-06 | 更新现有 Profile、SDK 构造、完整请求字节边界续调和 M09 上游公共合同断言；Docker 内完成定向、M02/M09 owner Gate及全项目离线回归；两个子 agent 分别逐条对照 O-09-01～07审查，测试通过不是唯一证据。 |
| O-09-07 | 真实 API、现有网页更新、迁移、stage/commit/merge/push/PR/部署均未进入本次授权；实现、离线证据与供应商资格分开报告，现有18080服务的运行额度不会因源码修改自动变化。 |
## 28. 用途准入与目标纠错（2026-10-11，用户批准）

本增量结合 [M08 同日用途判断增量](08_candidate_research_loop.md) 和 [M13 同日接线增量](13_demo_workflow.md)，覆盖 §12.4 的粗粒度纠错进展判断；其余时间、路线、事实、安全、must/avoid 和额度硬门槛保持。

U09-01：一个确定性资格函数负责候选状态、角色、餐次/组合用途、avoid、声称满足 must 的确认、主备重复及已知营业冲突。入口仅公开已准入的用途列表，研究池待确认候选供补研按需读取。已知必要供给不足时，在零地图/零规划模型调用下返回可补研缺口；有余额则经 M13 原补研路由，否则具体 incomplete。检查基于当天必要访问数、不同主地点、必选要求；不声称路线时间可行。

U09-02：Matrix/Directions 批次带 `visit_intents`（日期、候选 handle、用途、主/备标志、可选 must 组、可选访问区间），不增加单独模型往返。所有候选端点都须有明确用途声明，主备资格统一。先做整批资格/日期/重复/已知时段预检，后读取地图预算及执行地图；无效批次反馈全部可独立确定问题，零地图调用/消费，模型调用如实记账。候选、用途或证据变化后重新检查，已有缓存不授予候选资格；返回路线后及最终提交仍执行原完整 Gate。

U09-03：反馈包含稳定 target（日期＋用途＋主备身份，不能由模型 visit_id 定义）、rule、field、candidate_handle、实际/期望安全枚举、相关证据引用和允许动作。必需用途缺失也产生目标。提交前先汇总可独立确定的候选/用途/must/重复问题，不只反馈第一餐次错误；涉及路线或时间的后续 Gate 按原顺序运行。

U09-04：纠错进展只承认原硬问题被消除，或该目标取得改变判断的相关证据；换 ID、交换错误候选、移动同类错误、修改备注、删除必需访问均不算进展。首次拒绝允许修正，重复问题进入局部修正；不能把问题总数增加当作失败（合法换点可能产生待查路线）。保留问题目标集合和同证据方案指纹，不因补研重置；连续无目标进展两次或同证据循环才停止，不在首次拒绝直接失败。

U09-05：提交允许完整草案，拒绝后允许结构化 `patch`：只替换模型明确指定的日期计划/边界接驳，未列项保持。代码不自动选候选或删除访问；应用后仍完整校验。修正阶段只提供当前问题和相关候选的增量材料；不压缩/重建 M02 私有历史，不声称旧消息已删除。必要主路线优先，可选备选/比较后置；不变化的路线事实复用，不增加供应商并发。补丁工具仍是 submit_itinerary、占原提交修正额度。

U09-06：保持 normal17＋共享格式2、Directions=8×天数＋2、研究6/地图48及现有 Profile 额度。新 Prompt planning-v7；model_action/7.0、itinerary/4.0（state/budgets4.0、directories3.0）记录新合同，旧5/6及2/3可读且字节/hash不改，不能静默恢复旧非终态。M12只做必要读取适配，M13新 contract_id 隔离旧执行，无SQL迁移。

本次规划 Prompt 真源 `app/itinerary_planning/prompts/planning_v7.txt`，版本 planning-v7，UTF-8/LF SHA256=`34893676827d5237a5d8c7b27f937c1beac6f599dffdc93bd422e61680f76ece`。旧 planning-v5/v6 字节保持。新纠错指纹只比较相关角色、餐次、must组与营业事实；证据目录换序、无关角色或新增别的目标证据不算该问题取得进展。

U09-07：效率验收统计总/阶段耗时、逻辑模型调用及 provider attempts、输入/输出 token（计数）、地图执行/缓存命中、资格拒绝和目标修正次数，并同时核对成功率及硬约束。纯代码预检不耗模型 token；补齐必要证据可增加前期成本。离线验证调用减少/零无效地图及补丁大小，真实速度/token 收益须另获限次真实 API 授权。

准入数量按完整日数累计，并服从全行程主候选UID唯一约束：景点下界=每日节奏下界×完整日数，每种餐次至少完整日数个合格UID，午晚餐并集至少两倍完整日数；组合用途不简单相加。新 `MealCountTarget/2.0` 对应M08餐次数量缺口。入口 `candidate_supply/2.0` 的 count、meal-count、must 仅引用精确前驱candidate_set/2.0，经M13转换为M08 supplemental request/2.0；不借无关地图证据补齐缺失。已满足目标不请求补研，明确余量不足时仍输出具体缺口。

工具 DTO：Matrix/Directions/Submit 参数版本为2.0；`PurposeVisitIntent/1.0` 支持可选interval、营业状态和归属证据。边界纯酒店/枢纽查询允许空声明，任何候选端点不得为空。`ProposedItineraryPatch/1.0` 用days及可选抵离接驳明确替换，不能新增基草案不存在的日期；首次没有基草案时拒绝patch并允许修正。基草案仅存在于当前有效M02操作与M09 run上下文，不从旧Artifact补造；完整私有历史仍由M02保留。state/4.0保存纠错目标、方案/相关证据指纹、连续无进展数及基草案指纹，补研恢复必须与当前上下文精确一致。

验收覆盖角色、餐次、组合、备选、avoid/must、重复、缺供给、unknown/反证、错误交换/移动/删必需项、合法换点新路线、局部修正、补研保预算、缓存绑定、回放与旧版本读取；不得仅针对两字段或本次餐次 case。

U09-02/04 的具体语义：同批不同查询工具的同一日期、候选、用途与主备声明代表同一次访问，可补充可选区间；每条声明仍独立验证资格和证据，明确区间冲突、同工具重复或同点不同用途继续拒绝。查询纠错方案指纹覆盖整个批次，不以首个工具或 call_id 代表方案。进展按主备身份及同类资格义务计数，午晚餐间移动错误不算修复；must 保留具体组身份，营业未验证与冲突属于同一营业义务。只有改变准入或实际营业判断的相关事实变化才算证据进展，单改未知原因码不算。


## 2026-10-11 批准修订：研究合同修复

状态：用户已批准并授权实施。R09：消费candidate_set3.0及单兴趣，保持已批准U09用途准入、地图前预检、目标纠错与原17+2/地图额度。旧1/2候选只用于历史读取，不静默恢复到新执行；补研typed证据精确引用新前驱，版本/Ref/schema同步测试。角色/餐次/must/avoid仍由统一资格函数派生；未知不准入，地图缓存不授权用途。

共同依据与验收见 [M08 R08](08_candidate_research_loop.md)及[执行规范 R1～R6](08_candidate_research_loop_vibe_coding.md)，覆盖冲突旧条款。

## 2026-10-12 必要资格判断接线

R09：当前执行消费 candidate_set/4.0，历史1/2/3仅按原形状读取。M08 Q08 的资格补判先于新地图查询；M09仍沿§28唯一用途准入与必要供给预检，不增加17＋2调用、Directions或token额度。缺项与证据不足不授予用途，有足够主供给时未使用unknown不阻塞规划。新Ref、状态证明和补研精确前驱贯通测试；旧非终态不升级续跑，无SQL迁移。

实施和验收共同引用 [M08 Q08-01～09](08_candidate_research_loop.md)、[Q1～Q6执行契约](08_candidate_research_loop_vibe_coding.md)及本模块执行规范。用户已批准实施及隔离真实验证，完成等待提交。

## 29. 分钟排程与时间冲突反馈（2026-10-13，用户已批准）

### 29.1 背景、范围和职责

现有路线工具已提供交通耗时和排程占用分钟数，模型负责选择地点、顺序、停留时长及具体时间，控制器在提交时推导路线结束并完整校验。本次解决合理非整点时间被拒绝及时间错误反馈定位不足的问题。保持现有 ReAct、六工具及职责；不新增排程工具，不由代码自动选择地点、移动活动或删除必需访问。

本次验收只消费新分钟记录，不要求兼容历史测试行程。不删除原数据库、数据卷或测试证据，不迁移运行中的旧任务。

### 29.2 时间与事实合同

- T09-01：活动、开放区间和路线区间按整数分钟表达，秒和微秒为零。时区、非空活动区间、零耗时路线、日期连续及跨午夜校验保留。
- T09-02：交通预留占用按10分钟向上取整，使用整数公式 `((duration_s + 599) // 600) * 10`；0秒仍为0分钟。43分钟预留50分钟，50分钟保持50分钟，50分钟1秒预留60分钟。工具反馈、公共路线和持久快照使用同一公式；原始地图秒数保持不变。预留值是该段路线的总排程占用，不再叠加原始耗时；路线出发时间和活动时间仍允许任意整数分钟。
- T09-03：活动估算允许任意整数分钟，景点60–240、午晚餐60–120、组合访问90–240分钟；交通取整规则不得用于活动停留时长。文本活动时刻和 user_explicit 仍未启用。
- T09-04：结构化抵离原始时间及固定处理、近似余量不变。最早可用时间向上取整到分钟；最晚边界保留原值，不能因取整放宽。所有交通容量继续按原始秒数核对，密度、餐次、酒店闭环、候选用途、must/avoid、营业冲突和未知待复核合同保持。

### 29.3 提交反馈与纠错

- T09-05：完整校验前先定位可独立确定的路线结束不一致、活动/路线重叠、开放窗口和边界时间冲突。反馈只含稳定 rule/target、明确 field、该日期和规划时间的 actual/expected 以及允许动作；不返回异常原文、地点名称、用户原文或供应商内容。
- T09-06：时间目标按日期、冲突类别及访问顺序定位，不采用模型自由填写的 visit_id/leg_id。汇总已经能确定的独立问题；无法安全定位的结构或引用错误仍拒绝，不猜测错误。
- T09-07：保留原纠错次数、两次连续无进展停止及同证据循环检查。消除真实时间冲突可构成进展；换 ID、移动同类冲突或仅改备注不能构成进展。代码不自动顺延活动；模型仍用已有 submit/patch 修正。
- T09-08：模型逻辑调用上限保持正常17＋格式2＝19；阶段2/5/5/5、地图/Matrix额度、模型输出131072、high思考强度和供应商重试均不变。

### 29.4 记录与读取

- T09-09：新写入itinerary/6.0、PlanningState/6.0、预算摘要/6.0、目录/5.0；路线及其持久快照使用3.0，时间区间和持久时间快照保持2.0。模型动作/9.0绑定planning-v9；固定资源SHA-256为sha256:12110f9639467fb8bbff4a4682bd9b9250f0d391f47d398cac97ebf72e867689，由同模块加载器核对。Initial/Resume、OperationPlan/slot及Outcome当前版本为6.0。其余未变的地点、地图观察、活动/日期结构和工具参数版本保持，禁止全局替换schema_version。
- 新记录只能引用同版本父记录；当前写入、resume/replay、M12来源读取及M13当前规划消费方同步核对新版本和内容hash，不允许静默把旧行程解释成新分钟规则。预算版本变化仅标识新记录，限额和消耗语义不变。没有 SQL 表结构变更。
- T09-10：结果整理沿用现有渲染，网页时间仍按北京时间显示；任意分钟和交通占用在保存后读取、页面、复制及下载中保持一致。既有营业状态和来源证据不变。

### 29.5 验收

- T09-11：定向反例覆盖00–59分钟、秒/微秒拒绝、0/1/59/60/61/599/600/601/1801/2580/3000/3001秒、跨午夜、活动时长上下界、整点及非整点重叠、营业/餐次/抵离约束、精确冲突位置、有效修正和无效循环。交通预留已满足而活动时刻或时长不是10分钟倍数时必须可通过；旧一分钟预留不能伪装为当前记录。
- 用当前新记录执行写入/读取/hash及重启验证；旧版本拒绝必须明确且发生在副作用前。浏览器用隔离离线边界跑真实规划→保存→结果页，检查任意分钟、交通占用和复制/下载一致。
- 每相邻两个阶段结束统一测试，再由两个只读子agent逐条对照 T09-01～11 和执行规范审查。完成适用closeout后保持未暂存等待提交。本轮真实模型/地图API未获授权，不以离线通过宣称供应商可靠性或实际性能提升。
## 30. 按每日实际可用时间统一规划

本节定义当前每日规划、路线准入和质量交付合同。模型负责选择活动及顺序，程序负责事实、时间、预算与持久化核验。所有日期使用同一套规则；接驳影响通过实际可用窗口体现，不以日期名称减免要求。实施与验收统一见[执行规范 §32](09_itinerary_planning_loop_vibe_coding.md)。

### UD-01 每日权威窗口

先取得确认枢纽坐标，再查询抵达枢纽到酒店、酒店到离开枢纽的成功 Directions。纯接驳查询不要求每日安排；任何包含候选端点的日内 Directions，必须等待两个接驳都有成功证据。未核验时只提供 route_pending 暂定窗口，不能把它当作执行依据。

全部游览日期均由默认北京时间07:30～22:00，与最早可到酒店、最晚必须离开酒店的时刻取交集。铁路抵达／离开缓冲30／60分钟，机场60／120分钟，近似时刻另加30分钟；交通按原始秒数向上取整至10分钟。最早时刻向上取整到分钟，最晚时刻向下取整到分钟。未选方案时使用已成功核验方案中交通占用最短的窗口；最终提交改用所选方案。不得用模型缩小的 open_intervals 或任意延迟抵达、提前返站收缩权威窗口。活动和休息还须在实际到酒店之后、实际离开酒店之前。

同日抵离取交集；跨午夜接驳影响其覆盖的每个日期。窗口为空或很短时不强塞活动、正餐。示例：铁路09:00抵达、20:00离开且各接驳30分钟时，抵达日窗口10:00～22:00，中间日07:30～22:00，离开日07:30～18:30。窗口是最大可用范围，不要求一直活动到22:00。

### UD-02 统一安排规则

day_kind 仍由实际接驳和访问派生，仅用于说明。景点硬上限按轻松／标准／紧凑为2／3／4，所有日期一致；1～2／3／4是时间宽裕时的首版规划目标，不能作为不考虑实际时长的固定最低数。模型根据可用时间、停留、候选及路线事实合理减少，并在主题或反馈中说明具体依据。正餐每种最多一次；有完整60分钟并覆盖既有开始窗口时应考虑该餐，窗口存在并不证明餐馆或路线可行。

景点60～240分钟、正餐60～120分钟、组合用途90～240分钟，以及营业、用途、must/avoid、全程主备地点互异、酒店闭环、路线来源、安全时间边界保持。所有日期均适用单腿原始耗时上限3600／5400／7200秒和城内路线总原始耗时上限7200／10800／14400秒；抵离接驳仍各自核验单腿上限，不重复计入城内总量。

规划准入不再用 full_dates 过滤抵离日，也不把“标准日恰好3景点、恰好两餐”作为全日期硬门槛。候选数量不足的补研是建议性目标或具体缺口，不得因为达不到理论全日数量而在边界路线尚未核验前直接判定规划失败。已确认 must 的候选供给和最终安排要求保持。

### UD-03 路线查询前的整份初步安排

沿用六个工具，不增加独立排程工具或额外模型调用。PlanDirectionsArgs升级3.0，新增 daily_outline，默认为空元组；legs 与 visit_intents 的原边界保持。

DailyPlanOutline/1.0包含 date、visits、planned_breaks。visits最多6项，使用现有PurposeVisitIntent/1.0，必须是主访问（is_backup=false），interval必填、日期匹配、按开始时间排列且不重叠；planned_breaks最多16项，使用既有安全PlannedBreak。daily_outline最多60项、日期不重复。首次日内查询必须提供全部游览日期，包括空日期；以后提供受影响日期的替换项，空数组复用已接受安排。合并后必须完整覆盖连续日期。只在整批准入成功后接纳替换，拒绝批次不污染已接受安排。

每批在任何地图调用或预算consume之前，按以下顺序核验：
1. 原有整批结构、工具集合、句柄及操作计划类型／归属；非法兄弟不能被容量或质量反馈掩盖。
2. 当前查询端点的visit_intents用途、证据与营业约束；它只覆盖本批端点，不能塞入整份安排。
3. 两条边界路线已核验；合并整份安排，检查全部日期、用途、must/avoid、全程重复、已知营业、分钟区间、窗口与休息。合法短窗口可以为空。窗口长度达到UD-04对应节奏空白阈值却没有任何主访问，返回unplanned_usable_day，不能通过整日休息或transit_only规避。
4. 派生全日期餐次、空档与过长休息提醒，写入当前安全planning_state；这些提醒是修订依据，不把未知营业或尚未查询的交通认定为不可行。除第3步的整日未安排外，质量提醒不拒绝地图查询，避免阻断为修复而进行的必要补查。
5. 检查剩余操作槽位及新查询容量。容量不足不执行整批地图或证据兄弟。

边界路线及初步安排不替代最终路线证据。初步安排的目的，是在昂贵的日内路线调用之前排除明显漏日和大段整日未规划；路线实测后仍由模型调整，程序不补造、删除、重排活动。最终submit/patch重新运行全日期覆盖、实际窗口及整日未安排校验，不能删除离开日来修复重复地点。

### UD-04 统一质量规则与交付

对所有日期使用同一纯函数，从实际安排重新派生质量问题，不信任模型填写的quality_issues。四个稳定规则仍为unexplained_gap、missing_lunch、missing_dinner、excessive_break；轻松／标准／紧凑空白阈值分别240／180／120分钟。

空档检查范围从权威窗口起点开始，到“当日最后一个安排的结束时刻”与“当日17:30”中较晚者，再受窗口终点限制。安排包括访问、路线和休息；超出该日窗口的部分裁剪，不包含其他日期。该范围内连续空档达到阈值即提醒；若没有单个大空档但累计空档达到两倍阈值，产生一个累计提醒。末尾普通晚间余量不要求填满22:00。累计休息达到同一阈值仍提醒，不能用整日自由活动掩盖遗漏。正餐开始窗口和最短60分钟与既有规则一致，组合用途计入相应餐次；缺餐只表示安排尚缺，不证明可行餐馆存在。

只有完整覆盖日期、通过全部硬约束，且每个达到阈值的可用日期至少有一个真实主访问，才能成为可交付基线。空窗口或小于阈值的短日期允许无访问。无法取得合格候选或路线的长空日期按已有Incomplete缺口收敛，不输出伪装完整的攻略。完整基线若仍有非致命质量提醒，最多请求一次daily_plan_quality_review；只修相关日期并复用证据。修订后仍有提醒可交付，必须逐日展示及标记降级，不能宣称问题已经解决。

### UD-05 路线需求、容量与P1收敛

根据模型初步安排中的访问顺序确定酒店→各相邻访问→酒店的地点对，形成required_daily_routes；每项列日期、端点及已有成功Directions句柄，供模型直接引用。未选模式和未查询时长保持未知，不伪造预计路线耗时。边界与主路线优先于可选模式比较及备选。既有可复用结果直接引用，不重新申请查询。

remaining_direction_slots仅表示剩余operation_id槽位；remaining_direction_queries仅表示账本允许的新指纹查询数，读取失败不能回填成零。缓存请求可不消耗账本额度，但仍占操作槽位，因此两个数分开展示。移除固定4条预留；所需相邻路线与剩余量用于建议，不能增加额度或强行拒绝仍合法的主路线。正常模型17＋格式修正2、阶段2／5／5／5、Directions为8×天数＋2、并发3及供应商重试均保持。

合法批次超过剩余方向槽位或新查询容量时，记录direction_capacity_exhausted业务反馈，整批零地图副作用，允许在剩余模型次数内缩小批次或使用已有证据提交。只有容量不足可进入该路径；hub/matrix计划缺陷、错run/phase/类型、未知工具、无效句柄、持久化或账本故障仍按原错误失败，取消继续透传。容量反馈持久化失败也必须失败，不得继续交付。

当槽位或账本实际为零时，不再鼓励无意义查询；经过完整重验的有效基线可以按既有结束逻辑交付并保留提醒。没有完整有效基线时为Incomplete；预算读取失败或部分证据失效不能借用历史基线掩盖。部分容量不足但不为零时先提供明确批次上限，后续仍按总模型预算及无进展规则终止。不可通用捕获ToolBatchError后改成Completed。

### UD-06 版本、持久化与下游

新运行合同：itinerary/8.0，InitialRequest、ResumeRequest、ItineraryPlanningOperationPlan、PlanningInvocationOperationIds、PlanningBudgetSummary及各Outcome均8.0；state8.0、directory7.0、model_action11.0、planning-v12、PlanDirectionsArgs3.0。其余未改DTO沿用原版，现有日计划及草案快照2.0、质量问题形状保持。state8.0显式保存已接受daily_outline及其逐日安全提醒，使用Artifact本地严格快照；恢复继续保留阶段、累计额度、质量修订标记和已验证来源，不重新领额度。动作与Prompt版本按§31 RC-04核验。旧6/7行程及旧动作保持只读和原规范字节／hash；旧非终态不能自动恢复至新执行合同，不改写旧行程或结果。没有数据库表结构迁移。

当前Prompt资源、完整性摘要及历史资源保护按§31 RC-04执行。资源仅在文档双审通过后写入生产目录；受hash保护并随镜像打包。

M12对8.0使用zh-guide-v4-daily-quality，day/index根版本仍2.0，沿用严格质量数据分支、逐日提醒和质量总数。旧7.0仍v3，旧6.0仍v1/v2，旧Markdown、字节、hash不变。M13当前contract_id为demo_workflow.web.v9，当前规划接线只接受8.0，历史终态可按原合同只读；质量问题仍计入degraded，其他limited机制保持。Incomplete不进入M12，不新增部分攻略网页或接口。

### UD-07 验收与授权边界

先文档双审与六文件hash冻结，再实施和离线验证，再由两名新的代码审查者逐条核对，全部通过后仅新建一次真实任务。离线证明窗口／标签等价、前置漏日拦截、晚到早走／跨午夜、合理晚间收尾、路线复用、公共入口P1各分支、版本负例及旧字节不变，并覆盖存储重启与实际页面链路。

真实输入固定为2026-10-24至26日合肥三日游，首日09:00抵达合肥南站、末日20:00离开合肥南站，标准节奏，文化、美食、拍照偏好，无必去和避开地点，酒店交通便利。驱动必须显式填入并核对全文、日期、枢纽，不得读取页面动态预设替代。仅一次创建任务；提取确认和酒店人工选择可自动完成，失败只收集记录，不新建或重复供应商调用。使用独立私有数据库及临时端口，完整记录保存在受限目录，仅报告安全汇总，不输出密钥或模型思考原文。既有本地网页、云服务器及原M09工作区保持。

## 31. 多日期路线合并调用

本节明确规划模型的路线查询方式，实施与验收见[执行规范§33](09_itinerary_planning_loop_vibe_coding.md)。模型继续按每天实际可用时间选择活动、顺序和时刻；按日期安排活动与合并路线查询可以同时进行。本次只完善提示词、路线工具说明及必要版本记录，不调整规划算法或错误恢复。

### RC-01 查询顺序与每日安排

保留UD-01～05的全部规则。先取得两个确认枢纽的坐标，再用一个plan_directions调用查询两条抵离接驳。两条接驳成功核验后，根据所有日期的daily_windows形成整份初步安排。首次日内查询仍须携带覆盖全部日期的daily_outline；之后只替换受影响日期，空数组复用已接受安排。visit_intents只声明当前查询端点实际涉及的活动，不与整份初步安排混用。

### RC-02 合并范围与额度

同一轮最多输出一个plan_directions调用。当前阶段需要查询且额度允许的多日期相邻路线，统一放入这个调用的legs，不因活动属于不同日期就拆成多个调用。抵离接驳与日内路线仍分阶段查询，不能为了合并而混在同一个调用中。

合并不要求一次查完全部路线。需要查询的路线超过剩余操作槽位或新查询额度时，只提交当前可执行的部分，保留仍待查询的路线；同一轮仍不能拆出第二个plan_directions调用。已有成功且未变化的路线直接复用。六工具、单腿来源、顺序、唯一性、并发、缓存、预算及阶段上限均保持。程序不代替模型合并、排序、截断或重写调用参数。

### RC-03 模型可见说明

当前系统提示词和实际传给模型的plan_directions工具说明必须同时明确：每轮最多一个路线调用；额度允许的多日期路线放入同一个legs列表；先查询接驳、后查询日内路线。提示词同时保留每日窗口、完整初步安排、用途、分钟排程、质量反馈及路线复用要求。不得只在文档或控制器代码中保存调用数量限制。

### RC-04 Prompt与动作记录

新增planning_v12.txt，逻辑版本planning-v12，UTF-8无BOM、LF，摘要为sha256:51ad670cc237ddb5b319ae2ef46a8cfb0f1f0f72e331aab9c0b68b4f63e5ba9b。它以planning_v11.txt为基础，仅在Directions查询段加入RC-02的明确合并要求。v1～v11资源字节保持；v11摘要仍为sha256:d340f0e8d1b52ea1414f744c7aa5b7c4519d2ed877bc7a695f407387f17b61d5。loader使用新资源、版本和固定摘要，构造失败继续按原配置错误处理。

不新增Artifact类型或字段。model_action仍为11.0，仅该版本的prompt_version允许planning-v11或planning-v12；旧5.0～10.0动作与各自Prompt的精确配对保持。新动作必须写入planning-v12及实际受保护资源的摘要；旧11.0/planning-v11动作可以按原严格模型读取，不改写旧记录或放宽其他版本。itinerary8.0、state8.0、directory7.0、PlanDirectionsArgs3.0、M12模板及M13接线版本均保持。没有SQL迁移或依赖变化。

### RC-05 范围边界

本次不新增批次错误纠正机会，不放宽每轮一个路线调用的限制，不改变ToolBatchError、容量反馈、预算、终态、研究纠错、界面或其他问题。收到同轮多个路线调用时仍按现有规则拒绝；这是本轮保留的已知限制，不能因为提示词明确了要求就宣称已有恢复能力。

### RC-06 验收

文档先由两名子agent独立审查并复核，冻结后才实施。验证实际模型请求包含新提示词和工具说明，而不只匹配文本片段；公共规划入口证明一个调用能够承载多日期路线、全日期daily_outline保留、已核验接驳复用，且调用次数、地图消耗和路线顺序准确。保留漏日、长空日、非法批次零副作用和原版本拒绝测试；证明旧11.0/v11与新11.0/v12动作均可严格保存读取，其他配对仍拒绝。不得声称离线固定响应能够证明真实模型始终合并。

两名新的代码审查者通过后，仅创建一次真实任务，使用UD-07固定输入，记录每轮路线调用数量、各调用腿数、实际终态、每日窗口与安排、耗时、token及缓存命中。失败保存记录，不修后重跑。未到规划阶段或仍拆分调用必须如实报告，不能算作合并行为已验证；完整业务交付与调用组织分别判断。

## 研究纠错来源读取合同

依据[M08设计§31](08_candidate_research_loop.md#31-研究纠错计数与已有成果交付c08)及其[执行规范§30](08_candidate_research_loop_vibe_coding.md)，当前规划显式接收candidate_set5来源，按M08公共核验函数读取Action3、Judgment4及invocation_attempt1链。拒绝attempt不提供候选资格，只有最终接受行动与判断贡献来源。旧候选1/2/3/4按各自原模型只读，原用途、必去/避开、路线和窗口校验保持。研究Incomplete不等于规划Completed；M09不代替M08继续纠错，也不重领研究/模型/地图预算。本节不改变itinerary8版本、阶段预算、规划工具、分钟排程或交付质量门槛。
