# Module 24：前端体验与运行过程展示

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本文是 M24 的设计真源。实施顺序、文件范围和验证要求见 [执行规范](24_frontend_experience_vibe_coding.md)；动态现场、审查及验证结果仅记录于 [实施状态](VALIDATION.md)。

## 1. 目标与范围

用户希望保留现有流程画布的清爽风格，将首页、人工交互、攻略、历史与样例统一起来。运行期间应能够点击阶段，查看实际查询、候选、路线、提交校验和修复的记录，理解任务为何仍在处理，以及接下来需要自己做什么。

M24 是一个独立交付工作包。网页仍由 M14 的 FastAPI 与原生 HTML、CSS、JavaScript 提供；新增的公开过程投影及存储位于 app/frontend_experience。新增代码应按职责组织，允许重构和删减重复实现。模块不改变模型规划职责、预算、研究或规划规则，不启用暂缓收集的人数、儿童年龄、房间、饮食、行动限制和费用预算等输入。

本期包含十项：真实过程详情；稳定的任务工作区；随任务状态调整主要内容；首页输入；需求确认和酒店比较；攻略阅读；历史与样例；统一视觉；手机及键盘操作；前端职责拆分。必要的数据接口、持久记录和无损兼容属于同一交付范围。

用户已经授权直接编写两份文档、经两个独立 agent 审查后实施、由两个新的 agent 审查代码。文档阶段可修正草案；两轮审查结束后冻结。正式实施中发现需要改变本设计的业务含义，停止并提交具体变更理由。

## 2. 依赖与边界

### 2.1 直接依赖

依赖编号唯一真源为 [路线图 §5、§6](IMPLEMENTATION_ROADMAP.md) 的 M24 行。直接依赖展开为 01、04、07、08、09、10、11、12、13、14：

| 模块 | 消费内容 |
|---|---|
| M01 | 已确认旅行需求中的城市、日期和天数；沿用现有需求交互 |
| M04 | ArtifactRepository、ArtifactWriteRequest、ArtifactRef、WorkflowPhase 等公共合同 |
| M07 | 酒店搜索、候选集合、选择的公开 Artifact |
| M08 | 研究动作、地点观察、候选集合及必要判断的公开 Artifact |
| M09 | 动作摘要、枢纽和路线观察、规划状态及提交拒绝的公开 Artifact |
| M10 | 公开仓储和规范化记录；不查询其私有业务表 |
| M11 | 既有公开运行视图及合法暂停、终态读取；不在 executing 时取 state |
| M12 | 已验证结果及日块；不另行生成攻略事实 |
| M13 | 现有主图和诊断阶段投影；不建立第二份业务状态 |
| M14 | 匿名身份、任务归属、HTTP、安全 DOM、应用装配及元数据连接池 |

### 2.2 既有规范衔接

本工作包补充 [M14 §8、§9、§11 及流程画布增量](14_web_application.md) 的展示与公开只读接口。原 SSE 仍只发送运行快照。M13 SafeWorkflowEvent、RunSummary 和 M02/M10 维护者交互记录均保持原合同；公开过程记录是独立、经过字段白名单投影的展示资料，不是这些日志的公开导出。

M24 不修改 M07～M13 的领域 DTO、Prompt、调用预算、操作计划、终态或幂等语义。以功能分支所基于的已提交 main 为准，不吸收另外工作区中尚未提交的分钟排程。对合法 Artifact 的投影按公开模型读取，不硬编码一份未来行程版本。

## 3. 布局与视觉

### 3.1 全站框架

四个页面使用一致的品牌、窄侧栏导航、页面标题、内容宽度、按钮层次与状态色。桌面任务区为主内容与可关闭的详情侧栏；屏幕较窄时详情转为单列展开区域，不挤压表单或攻略。首页以旅行需求为中心，说明和样例入口为次级内容。

保留白色、浅灰和蓝色体系。颜色、字体、间距、圆角、边框、阴影与焦点样式使用统一变量。状态同时有文字和图形或边框提示，不能只靠颜色。界面使用可读中文字体，不引入远程字体、第三方脚本或分析服务。

参考 Waku 的可点击节点、详情分层和稳定工作区思想，自行实现本项目样式。不得复制其 LICENSE-BRAND 列明的 design 目录、名称或标志。参考：[前端说明](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/static/README.md)、[品牌边界](https://github.com/ShenSeanChen/waku-agent/blob/main/LICENSE-BRAND)。

用户明确允许直接复用合适的开源代码。优先采用其 MIT 范围内的通用交互实现，例如 ui.js 的键盘焦点移动、原生展开控件及 main.js 的前后台轮询管理；只保留本项目实际使用的代码，并适配安全 DOM、命名空间和当前接口。复制或改编处标注来源，THIRD_PARTY_NOTICES.md 保存确切来源提交和完整许可，不引入未使用组件。

### 3.2 随状态调整内容

执行中突出画布、当前真实阶段及最近已记录动作；等待补充或酒店选择时突出交互区；结果加载成功后突出攻略，过程区默认收起并可重新展开。不能仅凭 terminal 或 delivery_status 就显示“攻略已完成”。

失败、取消、阻断和恢复分别使用既有有限状态说明与实际允许动作。取消请求不等于已取消。连接中断显示最后一次成功更新时间和重试入口，暂停动画，不推测后台仍在执行。重新连接只读取，不恢复、不取消、不重发操作。

任务标题优先使用经授权读出的城市和旅行日期；资料缺失时使用“旅行规划任务”，不从用户原话或样例猜测。运行状态与过程资料的保存状态独立显示。

## 4. 真实过程记录

### 4.1 采集与事实含义

生产装配在 M10 公开仓储外增加透明的 ObservedArtifactRepository，并将其传入 M11；M11 原有作用域、租约、操作计划和写入验证仍在外层执行。lookup/get 原样委托。put 必须先成功调用原仓储，再按公开注册模型校验并投影当前任务的内容。观察失败不能把成功业务写入改成失败，也不能伪造 Artifact 或额外占用业务操作预算。

PublicProgressRecorder 的 operation(run_id, trace_id) 由 M14 的已验证执行任务建立。上下文使用 ContextVar 和活动标记；退出时标记失效，继承的旧子任务不能继续发布。没有匹配作用域、run_id 不同或执行任务结束时，不采集。只记录原业务实际保存的内容；不得从原始模型响应、reasoning、工具参数全文或供应商 payload 建立展示资料。

M14 在合法执行结束后，可对 awaiting_user 或 closed 终态使用已有公开读取路径投影当前需求与等待项；不能为详情读取 executing 状态。后处理先确认业务执行已停止，读取公开运行视图，再合法读取状态，复验 run_id、trace_id、生命周期、暂停代数及终态字段未变，最后建立独立的短 operation 作用域保存需求投影。不得复活已失效的执行作用域。遇到 Busy、取消竞争或视图变化时安全放弃本次投影。产生的过程资料不改变 RunSummary 的完整性或终态判断。

| 内容 | 展示含义 |
|---|---|
| 需求提取或补充后的结构化资料 | 已提取、待确认；确认接受后才称已确认 |
| 酒店搜索与候选集合 | 地点和坐标信息；不表示有房或价格可订 |
| 研究动作 | 待执行查询或查详情的计划，明确标“拟查询” |
| 地点观察 | 工具实际返回的名称、地址、坐标、结果数量及来源时间 |
| 候选集合与判断 | 当前候选数量、准入状态和未解决的缺口；语义判断不等于地图事实 |
| 规划动作摘要 | 当前调用的工具名称和动作处置，不代表工具执行成功 |
| 路线观察 | 实际返回的交通方式、时长、距离及核验状态 |
| 规划状态与拒绝 | 已提交草案的日数、明确冲突字段、固定原因说明和补研缺口 |
| 完成的规划记录 | 通过业务提交校验的草案摘要；最终攻略仍以 M12 加载为准 |
| 结果日块和索引 | 已保存的交付日块与完整结果索引 |

同一节点内采用“动作摘要—事实列表—展开详情”的层次。拟查询、已观察、待修正、已提交、已交付使用不同文字。未知、不支持、未核验、没有结果和没有保存记录分别表述。不能把未覆盖时间写成自动安排的休息，也不能虚构补研或修复原因。

### 4.2 严格公开模型

公开 DTO 均 strict、frozen、extra=forbid，字符串有上限，整数拒绝 bool，时间为 UTC，日期明确为旅行日期。统一 schema_version=1.0：

- PublicFact：label 长度 1～80，value 长度 1～512。
- PublicTrip：city 长度 1～128、start_date、end_date、days 为 1～60；日期与天数一致，缺少完整三项时不创建。
- PublicRecordBody：phase 为 WorkflowPhase；kind 为固定记录种类；status 为 proposed/observed/accepted/rejected/failed；title 为固定中文标题；facts 最多 24 项；trip 可空。种类集合由 projection.py 的固定分支定义，不接受浏览器写入。
- PublicRecord：sequence 为 1～512、occurred_at、body；返回值不含 ArtifactRef、operation_id、trace_id、原始 UID、内部摘要或存储键。
- PublicProgressPage：run_id、items 最多 50 项、next_after 可空、latest_sequence 为 0～512、coverage 为 recorded/limited/unavailable、settled 为 bool。settled 仅表示终态后处理已停止采集，不代表资料完整或业务成功；没有 M24 资料时为 false。recorded 只表示本模块的记录可读，不表示完整模型推理或业务观测完整；界面固定说明“仅展示已保存的业务处理记录”。

PublicRecordBody 只从明确列出的字段构造；禁止递归公开整个 model_dump。所有外部文本统一移除控制字符、限制长度，并遮蔽实际配置密钥、常见密钥模式、邮箱、手机和文件路径。来源仅为固定名称与已验证获取时间，不输出供应商原始地址或参数。任何 URL 继续采用 M14 http/https 和 DOM 安全规则。

固定投影目录如下。字段路径均从注册模型验证后的对象读取，集合超过公开条数时说明仅展示前若干项。中文标题固定为表中标题，不采用外部文本。status 中的 observed 表示观察到该保存记录，不隐含工具成功；无结果、未执行与失败另有固定中文事实说明。

| Artifact 类型或合法状态 | kind / phase / 标题 | 字段白名单与 status |
|---|---|---|
| TravelSessionState.requirement_outcome.requirement | requirement / requirements / 旅行需求 | destination、time_window.sightseeing_start_date、time_window.sightseeing_end_date、time_window.sightseeing_days、time_window.arrival/departure 的 hub_name/local_datetime、preferences.interests/pace；ConfirmedTravelRequirement 为 accepted，其余 proposed |
| hotel.place_search | hotel_search / hotel_selection / 酒店地点查询 | evidence.outcome_type、evidence.places 的 name/address/coordinate、retrieved_at、verified；failed 为 failed，其余 observed |
| hotel.candidate_set | hotel_candidates / hotel_selection / 酒店候选 | result.result_type、result.candidates 的 name/address/coordinate 及数量；observed |
| hotel.selection | hotel_selection / hotel_selection / 酒店选择 | decision.decision_type、decision.selected_candidate 的 name/address；selected 为 accepted，declined 为 rejected |
| candidate_research.model_action | research_action / candidate_research / 研究动作 | result（recovered 时取 final）的 record_type、output.actions 中 action_type、search 的 query/area_mode；accepted 的拟查询为 proposed，policy/controller_rejected 为 rejected，llm_failure 为 failed；不公开 details UID |
| candidate_research.place_observation | place_observation / candidate_research / 地点查询结果 | result.record_type、result.places 或 result.details 的 name/address/coordinate、result.provenance.retrieved_at/verified；map_failure 为 failed，其余 observed |
| candidate_research.model_judgment | research_judgment / candidate_research / 候选用途判断 | result（recovered 时取 final）的 record_type、patch 中各 updates 集合数量；accepted 为 accepted，controller_rejected 为 rejected，llm_failure 为 failed；固定标注模型判断 |
| candidate_research.candidate_set | research_state / candidate_research / 候选研究进展 | record.record_type、record.state.counts 中景点/餐厅/可用数量及目标、record.state.pending_gaps 数量、record.state.candidates 的 core.name 与 state；failed 为 failed，其余 observed；completed 不等于最终攻略完成 |
| itinerary_planning.model_action | planning_action / itinerary_planning / 规划动作 | request_stage、budget_category、action_kind、call_summaries.tool、disposition；accepted 的工具动作 proposed，policy/controller_rejected 为 rejected，failed 为 failed，其余 observed |
| itinerary_planning.hub_geocode_observation | hub_observation / itinerary_planning / 抵离枢纽核验 | endpoint_kind、observation_status、success_candidate.formatted_address/coordinate、provenance.retrieved_at_utc/verified；failure 为 failed，其余 observed |
| itinerary_planning.route_matrix_observation | route_matrix / itinerary_planning / 路线矩阵核验 | date、mode、observation_status、cells 数量与成功数量、provenance.retrieved_at_utc/verified；failure 为 failed，其余 observed |
| itinerary_planning.directions_observation | directions / itinerary_planning / 路线核验 | transport_semantic、route_mode、observation_status、alternatives 的 duration_s/distance_m、provenance.retrieved_at_utc/verified；failure 为 failed，其余 observed；不公开端点内部引用 |
| itinerary_planning.itinerary | planning_state / itinerary_planning / 行程提交校验 | record.record_type、record.draft 或 record.state.draft 的 days 数量、record.state.current_stage、record.state.last_rejection 的 reason/field、record.gaps 数量；仅固定原因/字段目录可展示，未知以“校验未接受，请等待后续处理”代替；completed 为 accepted，failed 为 failed，有 last_rejection 为 rejected，其余 observed |
| result_formatting.day | result_day / output_formatting / 攻略日块已保存 | data.day_no/date/theme、data.visits/data.route_legs 数量；observed |
| result_formatting.index | result_index / output_formatting / 攻略索引已保存 | days 数量；observed，必须经原结果加载验证后才能显示已交付 |

phase 一栏使用 M04 WorkflowPhase 的实际枚举值。对于同一目录内公开模型的版本差异，只有存在的白名单字段参与投影；不递归搜索替代字段。固定 kind 集合就是本表的十五项，未知类型不发布。

### 4.3 保存、顺序与容量

独立 schema frontend_experience，由 M24 单独 Alembic 迁移创建，不改变 artifact_store、durable_execution 和 web_application 的现有结构：

1. public_records：run_id、trace_id、sequence、event_key、occurred_at、content_bytes、content_hash；主键(run_id,sequence)，同 run 的 event_key 唯一，body 为规范 UTF-8 JSON，单条最多 16384 字节。
2. progress_runs：run_id 主键、trace_id、record_count、limited、settled；记录身份绑定、是否缺少部分记录及终态采集是否收束。settled 只能由核验完成的终态后处理单向设为 true，之后不再追加；暂停不收束，后续 resume 可继续追加。
3. capacity：单行 record_count，硬上限 16384 条；每 run 最多 512 条。数量修改和记录插入在一个短事务中，固定锁顺序为 capacity 后 progress_runs。

event_key 只在服务端使用，来自原 Artifact 身份及记录种类，或已验证暂停代数/终态的稳定标识。相同键与相同内容重复发布复用；同键不同内容拒绝并标记 limited，不覆盖旧记录。sequence 是每任务递增序号，不用时间排序，也不暴露全站序号。读取逐行校验身份、序号、大小、hash 和严格 DTO；损坏返回安全依赖错误，不能当成空历史。

采集保存等待最多 0.25 秒；不创建无限重试或后台队列。普通保存失败不影响业务，尽力标记 limited；数据库完全不可用时接口返回明确读取失败。全局容量或任务上限达到后保留已保存的前缀，页面说明记录不完整；不删除旧业务数据。没有 M24 资料的历史任务返回 unavailable，不从私有日志重建记录。

新 schema 的迁移是显式运维步骤；应用启动只检查，不自动建表。正式实施只在具名隔离测试库执行迁移；现有本机、云端数据库和服务不换版。启动时缺 M24 schema 必须安全失败，不能静默丢弃生产采集。原 M14 迁移、指纹和查询保持原样。

终态后处理在最终需求投影完成或安全放弃后，复验执行停止及同一终态，再以短作用域保存 settled；投影失败同时尽力标记 limited。保存失败不能阻断原终态清理。进程中断或存储不可用时允许未收束，读取不得为此补写。

### 4.4 HTTP 与读取行为

增加 GET /api/runs/{run_id}/progress?after=N，N 为十进制 0～512，默认 0，每页固定最多 50 条；非法、重复或未知查询参数拒绝。先沿 M14 验证当前 Cookie 和归属，再读 M24 表。未知任务和其他访客任务统一 404。页面、JSON 和错误均 no-store。

读取绝不调用图、模型、地图、恢复、取消、采集发布或写元数据；不改变配额。SSE 原合同保持；详情可见时最多每 5 秒增量读取。终态后只有读到 settled=true 且已读尽该响应水位才停止；未收束时最多再进行 12 次间隔读取，随后停止并显示“过程记录尚未收束，可重新读取”。等待输入与恢复状态不视为业务终态。多页顺序加载，按 sequence 去重，使用请求代数防止旧响应覆盖新任务。

浏览器刷新从 after=0 重新读取保存资料。追加记录不得重建整块已展开 DOM，不改变选中节点、表单、展开状态或阅读位置。详情关闭或页面后台时暂停轮询，重新打开或回前台时读取。新记录通过数量提示呈现，不强制滚动。

## 5. 首页与人工交互

### 5.1 首页

保留合肥三日游、09:00 抵达、20:00 离开、火车站、人文/美食/打卡、标准节奏和交通方便酒店等示例条件。提供“填入示例”和“清空”操作；示例采用未来的连续三天，明确提醒用户核对旅行日期，不保证跳过既有需求确认。

只收集当前支持的信息。主输入、日期核对、主要提交操作和错误提示位置清晰；失败或限额时保留输入。公共配额、Cookie 保存期限和样例免费阅读说明保持可找到，但不占主要内容位置。输入不得保存到 URL、localStorage 或新增长期缓存。

### 5.2 需求与酒店

沿原交互卡的真实字段分组显示旅行条件，突出本轮需要回答的信息，保留已填写内容。没有变化的快照不能重建表单。确认仍由原服务端协议决定，不能自动接受。

酒店采用可比较卡片，名称、地址、现有事实和当前选择清晰可见；缺失信息明确为未知。保留“地图核验仅涉及地点身份”的含义，不增加评分、价格、库存或推荐结论。键盘可选，整张卡点击与单选按钮行为一致。提交中显示正在提交；只有服务端接受后才更新步骤，状态未知时查询原 request_id。

## 6. 攻略与样例

### 6.1 结果层次

结果加载成功后显示紧凑概览、每日导航、活动时间线、交通详情和注意事项。日期、活动时间、地点与停留时长使用源结果，不重新排程。交通的方式和总时长常驻，原乘车顺序可展开；营业、费用、抵离和未核验事项保留在对应位置。

每日导航可定位并标记当前阅读日，桌面粘性定位不得遮挡标题；手机横向导航局部滚动，不产生整页横向溢出。来源清单和匹配来源编号默认收起，展开后可见；原文、复制、下载继续保留完整 Markdown 字节。

### 6.2 结构化展示与兼容

M14 WebResultView 兼容新增可空的结构化阅读字段，来源仅为 M12.load 验证成功后的公开日块 data。新增字段按显式白名单投影，不输出完整内部对象或重新生成事实。原 markdown、days、交付状态、版本字段和导出不变。

具体新增 WebResultDay.reading，可空且默认 None：date、theme（1～40）、visit_count（0～6）、route_count（0～7）、transport_minutes（非负安全整数）。它们分别来自已加载日块 data.date、theme、len(visits)、len(route_legs)、各 route.scheduled_minutes 之和。该字段用于日期导航、每日标题及紧凑摘要；当天全部活动、交通、备选和提醒仍由原日块 Markdown 无损显示。WebRunView 兼容新增 trip，可空且默认 None，仅来自 M24 已保存的完整 PublicTrip。历史接口同样遵循缺失不猜测。trip 属于可选标题资料，缺失或读取错误时返回 None，保留原任务状态、交互卡和合法动作；专门的 progress 接口仍应报告真实读取错误。

共用 guide.js 的安全渲染和章节组织，同时服务任务结果与样例。对有结构化数据的结果可展示日期、活动和路线摘要；旧结果或样例缺少结构化数据时使用保守 Markdown 组织，章节不明确时展示全部正文。不能为了排版依赖越来越多的正则去猜地点、费用或事实状态。样例日期、真实结果/合成标签、非实时说明和降级提醒保持。

## 7. 历史、连接与可访问性

历史以紧凑列表展示真实可用城市、旅行日期、创建时间、状态及进入入口；M24 资料缺失时使用通用标题。保留原服务端分页，筛选仅作用于已加载页面并明确范围；不把当前页没有匹配项说成全站没有任务。提供加载、错误、空记录和重新读取状态。

所有页面在 390px 与 1440px 宽度可使用，操作区可触达，正文可读。节点是键盘可操作控件，支持 Enter/Space；详情有明确标题、关闭按钮和焦点返回。移动详情作为文档内区域，避免没有焦点约束的伪模态框。保留 prefers-reduced-motion 和可见焦点。

有限状态全部有对应说明：执行、等待输入、完成、降级、失败、取消中、已取消、阻断、不兼容、待显式恢复、连接不可用、详情未保存和详情读取失败。详情读取失败不隐藏已加载攻略或取消已有表单内容。

## 8. 前端代码边界

保留无构建步骤的原生页面。ui.js 提供安全 DOM、统一时间/状态等公共展示；guide.js 拥有 Markdown 与攻略组织；process.js 拥有流程、节点选择和过程详情；app.js 保留请求幂等、身份、表单提交和页面协调。依赖使用显式命名空间及固定 defer 顺序，禁止内联事件字符串和重复全局变量。

新增静态资源仅允许固定文件名，M14 路由不开放任意路径。所有入口更新同一版本的资源 URL，避免旧缓存与新 HTML 混用。公共样式按布局、控件、流程、攻略、响应式组织，清理重复覆盖。

## 9. 验收条目

| 编号 | 必须验证的结果 |
|---|---|
| AC24-01 | 从 main 的独立分支及工作区实施，M09 原工作区和 main 不变 |
| AC24-02 | 四页统一框架、视觉变量、按钮及状态语义 |
| AC24-03 | 六节点可点选、键盘可用、当前选择与详情对应 |
| AC24-04 | 公开记录来自真实业务 Artifact 或合法结构化状态，拟执行和已完成分明 |
| AC24-05 | 不公开原文、reasoning、凭据、内部 Ref/ID 或供应商 payload |
| AC24-06 | 无匹配/过期/跨 run 作用域不能发布；观察故障不改变业务成功 |
| AC24-07 | 新 schema 显式迁移、旧 schema 保持、重启读回、幂等、冲突及容量有证据 |
| AC24-08 | 进度 API 严格分页、归属、no-store、损坏拒绝；读取无业务副作用 |
| AC24-09 | 刷新与增量去重、失败提示、后台暂停、终态停止，不丢表单与阅读状态 |
| AC24-10 | 执行、等待、交付及异常状态的主内容和合法操作正确 |
| AC24-11 | 首页示例、清空、日期核对、输入保留及停用字段不回归 |
| AC24-12 | 需求补充、确认、酒店选择、原请求查询与取消协议保持 |
| AC24-13 | 攻略概览、日导航、活动与交通层次、来源折叠及重要提醒完整 |
| AC24-14 | Markdown 安全渲染、保守回退、复制和导出字节不变 |
| AC24-15 | 样例共用阅读逻辑且保留真实/合成、日期、非实时与降级标签 |
| AC24-16 | 历史真实资料、分页、加载/空/错状态及筛选范围准确 |
| AC24-17 | 390/1440 宽度、焦点、键盘、减少动画及无全页横向溢出 |
| AC24-18 | 原生脚本拆分、固定静态资源允许名单、无新远程依赖 |
| AC24-19 | 两位文档审查者两轮复核；两位新的代码审查者核对设计和实际证据 |
| AC24-20 | MODULE-CLOSEOUT、最终新镜像、当前模块三项覆盖率及全量离线回归完成 |

## 10. 验证与授权

测试分类为 adapter-contract 与 assembly，浏览器使用真实生产业务主链配合模型/地图替身。新增纯投影测试覆盖字段来源、事实状态、脱敏和异常，存储用独立真实 PostgreSQL 验证，浏览器验证代表性状态和操作。Python、pytest、coverage、compileall 全部使用当前源码构建的具名 Docker 镜像。

M24 Python 生产包 line、branch、combined 分别不低于 70%；JS 以浏览器行为验收，不用 Python 覆盖率冒充。收尾必须使用最终源码重建 runtime/test，验证默认生产入口安全失败行为、资源及完整离线 unit/contract；旧的结果不能计入当前数字。

本期不调用真实 DeepSeek/百度地图 API，不更新原本机服务、运行库或云端，不 stage、commit、合并、push 或创建 PR。实施后保留可审查变更；可在具名隔离浏览器服务中查看效果。用户后续明确授权才更新日常网页或执行 Git 发布。
