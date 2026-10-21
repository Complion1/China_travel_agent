# China Travel Agent 实施路线图

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次维护沿原模块文档修订： [M09阶段/预算/版本§12～15/20/25](09_itinerary_planning_loop.md)、[M02完整采集§19](02_llm_runtime_and_model_profiles.md)、[M10存储/导出§23](10_artifact_persistence.md)；实施边界归 [原M09执行规范§29](09_itinerary_planning_loop_vibe_coding.md)。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档属性：职责、依赖与交付工作包的唯一真源
> 产品和跨模块不变量：[`00_agent_master_design.md`](00_agent_master_design.md)
> 当前验证状态：[`IMPLEMENTATION_STATUS.md`](VALIDATION.md)

## 1. 三类工程单元

- **Component**：拥有单一代码职责、公共合同和隔离测试。
- **Work package**：一次可交付的设计与实施单元，可以包含多个保持独立的 Component。
- **Gate**：只验证组合系统，不拥有生产代码能力。

编号用于追踪历史和依赖，不代表每个编号都必须拥有两份大型文档。01–03 已发布编号保持稳定；后续如需拆分或合并工作包，先修改本路线图并取得用户确认。

## 2. Demo 职责索引

| ID | 类型 | 职责 | depends_on | 交付门槛 | 工作包 |
|---:|---|---|---|---|---|
| 01 | Component | 需求领域、合并、evidence、追问与确认 | — | 纯领域合同完成 | WP1 |
| 02 | Component | DeepSeek Runtime、Profile、结构化输出和错误 | — | 离线合同及 live capability 资格 | WP1 |
| 03 | Component | 连接 01/02 的真实提取 Session 和一次 repair | 01, 02 | 离线合同及固定 live 资格 | WP1 |
| 04 | Component | 工作流状态、ArtifactRef/Repository 端口与 Demo 进程内参考实现、预算和安全事件端口 | 01 | DTO 可序列化、版本化；进程内仓储与幂等语义冻结 | WP2 |
| 05 | Component | 需求子图、InMemorySaver、interrupt/resume 和节点重放 | 01, 03, 04 | 同进程暂停恢复且不重复副作用 | WP2 |
| 06 | Component | 百度地图 typed adapter、MCP Gateway、规范化和技术限流 | 04 | Demo 七项核心能力合同及 live 资格；地图调起为可选能力 | WP3 |
| 07 | Component | 酒店候选、UID 去重和人工选择 | 01, 04, 06 | 三次搜索门槛与安全失败 | WP4 |
| 08 | Component | 受控候选研究循环 | 01, 02, 04, 06, 07 | 白名单、执行预算、去重和停止条件 | WP4 |
| 09 | Component | 受控行程草案、路线优化循环和确定性提交校验 | 01, 02, 04, 06, 07, 08 | 日程规模、Direction 执行预算、停止条件和完整提交 Gate | WP4 |
| 10 | Component | PostgreSQL Artifact／evidence 持久化、专用交互诊断表和迁移 | 02, 04 | 业务 Artifact 合同保留；固定诊断 DTO、独立容量、追加/导出和失败不干扰业务；各自幂等及重启读取通过 | WP5 |
| 11 | Component | PostgreSQL checkpoint、run 协调、执行预算、operation journal 和跨进程恢复 | 04, 05, 08, 09, 10 | interrupt／崩溃恢复不重置预算且不重复已留有确定证据的外部副作用 | WP5 |
| 12 | Component | 结构化结果与中文攻略；缺少可信价格来源时明确标记费用未核验 | 01, 04, 07, 08, 09, 10, 11 | 从持久 Artifact 生成不新增事实的结果；不用模型估算未知费用 | WP6 |
| 13 | Component | 完整 Demo 主图、跨能力路由、执行预算和终态映射 | 01, 03–12 | PostgreSQL 装配下的 fake 集成主链、恢复和幂等重放通过 | WP6 |
| 14 | Component | Application Service、依赖装配、应用 API 和前端网页 | 13 | 网页经公共入口完成创建、澄清、酒店选择、取消、历史任务与安全恢复 | WP6 |
| 15 | Gate | 真实 PostgreSQL、真实网页主链和最小 Demo 质量集 | 01–14 | Demo release gate、持久化和跨进程恢复 Gate 全部通过 | G1 |

`03–12`、`01–14` 是本文中的闭区间简写；模块规范的机器元数据必须展开为明确 ID。

模块 05 对模块 01 是直接依赖，不以模块 04 对 01 的依赖代替：05 直接消费 01 的 `RequirementDomainTransition`，展示 `NeedConfirmation` 或 `RequirementInvalidated` 自带的 `summary`，并把 `RequirementDomainState` 与 `RequirementConfirmation` 交给 01 的 `confirm(state, action)`；05 不复制 01 的确认完整性规则。模块 03 提供真实提取器，模块 04 提供工作流状态合同，因此三者都必须作为 05 的直接前置合同核验。

模块 09 已在终态提交前确定性校验时间、路线、返程、候选资格、must 覆盖和事实证据，因此原模块 10 的独立 `ValidationReport` 职责取消，不再复制同一套校验。原模块 11 的旅行费用判定因当前没有酒店房价、门票、餐饮人均和交通计价等可信结构化来源而延期；在价格来源及其证据合同另行批准前，模块 12 只能明确输出费用未核验，不得推断预算合规。

模块 10、11 只把单次任务的情景数据和执行状态持久化，不实现跨任务 Semantic Memory，不允许运行时改写 Prompt、策略或代码形成 Procedural Memory。二者在本路线图只冻结职责、依赖和交付方向；数据库 schema、迁移策略、事务边界、恢复协议、文件白名单和测试矩阵必须分别通过模块 10、模块 11 的独立设计讨论后才能实施。

## 3. Demo 工作包

| 工作包 | 内容 | 交付结果 |
|---|---|---|
| WP1 Foundation | 01、02、03 | 需求与模型调用基础；已完成 |
| WP2 Workflow Foundation | 04、05 | 小状态、artifact、执行预算、需求子图和同进程恢复基础 |
| WP3 Map Gateway | 06 | 独立外部系统边界和 typed 地图能力 |
| WP4 Travel Planning Pipeline | 07、08、09 | 酒店、候选研究和行程规划；两个循环保持独立 |
| WP5 Durable Execution | 10、11 | PostgreSQL Artifact、checkpoint、执行预算、幂等和跨进程恢复 |
| WP6 Output and Application Assembly | 12、13、14 | 无新增事实的输出、唯一主图、Application Service、应用 API 和前端网页 |
| G1 Demo Release Gate | 15 | 真实 PostgreSQL、真实主链、恢复／失败路径和最小质量集 |

工作包合并的是文档与交付节奏，不取消内部 Component、DTO 和测试隔离。

## 4. 纵向集成里程碑

### M1：基础合同

01 具备 `contract_complete`；02、03 同时具备 `contract_complete` 和有效 `live_qualified`。纯领域模块 01 没有外部 live 资格。

### M2：工作流纵向骨架

05、06 完成后，使用非正式 harness 验证：需求输入 → interrupt/resume → 一次 typed 地图调用 → 安全结构化结果。下游规划允许 fake，不得标记 `demo_ready`。

### M3：规划闭环

07–09 完成后，使用同一进程内 Artifact 和 fake／专用 live harness 跑通酒店选择、候选研究、行程生成及 Module 09 确定性提交 Gate。该里程碑证明规划能力闭环，不表示数据已经持久化或完整 Demo 已交付。

### M4：持久执行与输出

10–12 完成后，使用真实 PostgreSQL 验证 Artifact 重启读取、checkpoint、执行预算、幂等和跨进程恢复，并使用 fake 外部能力生成不新增事实的结构化中文攻略。

### M5：网页 Demo

13、14 完成后执行 PostgreSQL 装配下的完整 fake 集成，15 再执行真实 PostgreSQL、真实网页主链、恢复／失败路径和最小质量 Gate。

## 5. Production 能力路线图

Production 能力在 Demo 反馈前只冻结职责，不冻结全部文件和实现模块：

| 现有 ID | 类型 | 能力 |
|---:|---|---|
| 16 | Component | 用户主动触发的局部重排与版本差异 |
| 19 | Component | 隐私、安全、删除、TTL 和密钥生命周期 |
| 20+21 | Work package | 可观测性、审计和 Production 装配 |
| 22 | Gate | 真实数据库、崩溃恢复、幂等和删除系统验证 |
| 23 | Gate | 城市泛化、质量阈值和滚动可靠性评估 |
| 24 | Work package | 前端体验与运行过程展示；直接依赖 01、04、07、08、09、10、11、12、13、14，包含全站交互、攻略阅读及必要的安全公开过程接口 |

原 17+18 的 PostgreSQL checkpoint、artifact／evidence 和 idempotency 持久化职责已提前并重新分配给模块 10、11；ID 17、18 暂不分配新能力，也不创建占位实现。模块 22 保留 Production 级真实数据库、长期运行、删除和故障恢复系统验证，不替代模块 10、11 在 Demo 路线中的组件验收。

网页和应用API已纳入M14；已确认FastAPI、原生HTML/CSS/JS、PostgreSQL及单机Docker Compose的公开受限匿名Demo设计范围，见 [M14设计](14_web_application.md) 与 [执行规范](14_web_application_vibe_coding.md)。框架选择不表示已实施或授权真实部署；交易及完整公众多用户产品仍在范围外。

## 6. 依赖与并行规则

本表的 `depends_on` 是唯一手工依赖真源，不再维护第二份 Mermaid DAG 或固定 P1–P16 批次。

- 前置公共合同和 acceptance gate 满足即可开工，不要求完成所有较小编号。
- 并行实施必须由用户明确授权，并确保不修改同一公共 DTO、Prompt 或文档真源。
- 单个 Agent 默认按工作包和依赖顺序串行。
- Gate 不反向替代 Component 的隔离测试。

## 7. 拆分与合并信号

必须优先拆分的情况：拥有两个无关外部系统、出现编译依赖环、领域与基础设施无法隔离、拥有两个独立状态机或具有独立安全/数据生命周期。

允许归入同一工作包的情况：共享业务目标和变更原因、输入输出边界相邻、交付时始终联调，但内部代码仍能独立测试。

任务数量、目录数量和文档长度只是审查信号，不是机械的架构门槛。

## 8. 测试分类

| test_class | Component / Gate | 含义 |
|---|---|---|
| pure-domain | 01、04、12 | 不启动网络、数据库、Graph 或供应商 SDK |
| adapter-contract | 02、03、06、10 | 核心使用 fake；只对本模块拥有的外部边界执行单独 live 资格，10 必须另有真实 PostgreSQL 合同 Gate |
| workflow-contract | 05 | 使用 fake 外部能力验证 Graph、interrupt/resume 和节点重放 |
| skill-contract | 07、08、09 | 使用 fake adapter 验证受控循环预算、停止条件、去重和输出合同 |
| durable-workflow | 11 | 使用真实 PostgreSQL 验证 checkpoint、执行预算、租约、幂等和跨进程恢复；模型和地图可以使用 fake 隔离 |
| assembly | 13、14 | 使用 PostgreSQL 和 fake 外部能力验证完整主图、应用入口、恢复与终态映射 |
| system-evaluation | 15 | 使用真实 PostgreSQL、DeepSeek、百度地图和浏览器 执行跨模块发布 Gate，不替代模块单测 |

16、19–23 的具体测试分类在各自权威规范获批时冻结；本路线图不提前替其选择 adapter、assembly 或 system test 形态。

M24 采用 adapter-contract 与 assembly，并包含独立 PostgreSQL、浏览器及收尾离线回归；详见 [M24 设计](24_frontend_experience.md) 和 [执行规范](24_frontend_experience_vibe_coding.md)。编号不表示必须先完成 M16～M23；它基于现有 M14 开展，不改变原组件的规划和执行职责。

## 9. M13 设计衔接修订

D-13-12/17：网页取代完整 CLI 用户流程成为 M14 正式交付和 M15 验收入口；CLI 仅可选诊断。M13 不实现 HTTP 或网页，也不复制 M07–12 的能力。

M13 实施之前必须完成并验证 M11 §22 的持久取消、公开任务查询和受保护暂停凭证交接扩展；M04 §15 的摘要例外不修改既有 RunSummary DTO。阶段及门槛见 13_demo_workflow_vibe_coding.md §4–8。M11 既有延期只在未触发的范围保留；M13 实际采用的原生酒店暂停、取消、恢复路径必须取得本轮证据。

历史任务复用 M11 runs 与 M10 Artifact；M14只增加必要匿名归属/配额/HTTP幂等/密文及最终摘要元数据，不复制业务状态或另建任务真源。单个后端同时执行一个 run、无队列，等待输入不占执行名额；这不改变 M11 active/stored 容量。M14采用普通HTTP公网IP、FastAPI及原生网页，凭证AES-256-GCM、同库真实摘要保存后释放事件，合同见其§5–9。M14 depends_on仍为[13]；M15真实浏览器/供应商/云容量资格仍需另行授权，本次文档不表示实现或发布完成。
M13 直接消费 M01 命令和领域服务类型，依赖元数据显式补入01；这不增加新的需求领域能力或M01修改权限。
