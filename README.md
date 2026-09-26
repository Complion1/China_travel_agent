# China Travel Agent

中国境内旅游攻略 Agent。模块 01–10 已完成并进入本地 `main` 稳定基线；Module 09 的结构化输入 Demo 已通过离线 Gate、独立真实规划和历史 Module 06→09 单次真实 DeepSeek Flash／百度地图联调，最新真实联调发现的问题已按所属模块登记并由用户决定暂缓。生产 LLM Profile 继续使用 DeepSeek V4 Flash。Module 10 已通过 PostgreSQL Artifact 持久化、迁移、并发、真实重启、真实 API 产物重读和回归资格门；完整 Demo 主链仍等待 Module 13 显式装配，因此现有 Module 01–09 生产组合尚未自动切换 Repository。Module 11 的 checkpoint、执行预算和恢复状态持久化已提交并快进合并到本地 `main`，并提供显式迁移和 Docker 导入自检；演示优先验收与原严格资格的区别见实施状态快照，不代表完整 Demo 已装配。Module 10 本地 commit 和 fast-forward merge 已执行，push、PR 和 deploy 均未执行。旧原型接口不构成兼容要求。

Module 12 的确定性结果格式化已实现，当前源码包含模块 01–12；独立收尾验证结果见 [实施状态快照](./docs/IMPLEMENTATION_STATUS.md)。三天真实需求业务链的单轮测试停在需求抽取修复阶段，尚未通过完整业务链验收，具体证据及后续修复边界见 [真实联调报告](./docs/12_live_pipeline_acceptance.md)。

## Module 11 显式迁移与导入自检

Module 11 提供持久执行库，不提供完整旅游 CLI。当前默认 runtime 只导入 Module 01–12，不连接数据库、不自动迁移；启动 runtime 不会自动拉起迁移服务。

配置本地 `.env` 的 PostgreSQL 变量后，显式执行：

```bash
docker compose --profile production build runtime
docker compose --profile production run --rm durable-migrate
docker compose run --rm --no-deps runtime
```

`durable-migrate` 等待 PostgreSQL 健康和 `artifact-migrate` 成功，再执行 `python -m app.durable_execution.migrate`；可显式重复执行。`production` profile 包含数据库和迁移服务，因此启用该 profile 后无目标地执行 `up` 也会启动这些服务，应使用上述明确目标命令。不要把测试迁移指向正式数据库；不要删除含有需保留数据的卷。

Module 11 已按用户授权提交并合并到本地 main（实现提交 `985dcde`）；演示范围验收和原覆盖率/延期条件的区别见 [T07 验收记录](docs/11_t07_demo_acceptance.md)。现有通用 test target 的 M11 contract 需要隔离 PostgreSQL，不能将无数据库环境的全套运行当成有效验收。完整业务装配继续留给 Module 13。

## Module 12 结果生成与保存交接

`app.result_formatting` 提供 `ResultFormattingService`，只处理 Module 09 的 `completed` 行程，并只读已保存的 Module 07/08/09 公共证据。输入需要已确认的工作流 `state` 和显式 `itinerary_ref`；输出为按日分块的结构化结果、中文 Markdown 快照及汇总索引，不提供部分行程预览，不重新调用模型或供应商。

调用顺序为 `prepare` → 调用方保存全部日块 → `build_index` 核验已保存日块 → 调用方保存索引 → `load` 完整读取。Module 12 负责生成和校验，保存与主图交付由后续 Module 13 显式接线；当前持久化测试 harness 仅证明 Module 10 与 Module 12 的保存/读取合同，不代表完整 Module 11/13 恢复链已装配。

快照保留短来源引用、就地风险说明及末尾来源目录，同时显示路线原耗时与预留时间。费用标为未核验，已确认的补充文本回显并标为未验证；天气仅有占位，天气查询与地图展示延期。完整 Demo 主链仍未装配。

本轮 C 批已验证真实 PostgreSQL integration `3 passed`、同一专属卷重建 PostgreSQL 后新进程读取 `3` 个日块且摘要一致，以及完整 unit/contract `2218 passed, 7 warnings`；既有 M10/M11 测试库迁移与本次资源清理通过。这些是收尾同步前的阶段证据，最终源码的独立 Closeout 结果另见实施状态快照。

## 架构基线

```text
CLI Demo
        → Application Service
        → LangGraph workflow
        → Runtime Agent Skill / deterministic domain service
        → typed domain port
        → MCP Gateway（仅外部 Agent 工具能力）
        → trusted MCP Server
```

- LangChain：DeepSeek 调用、结构化输出、tool schema 和 MCP 工具适配。
- LangGraph：主工作流、状态转换、受控循环、interrupt/resume 和 checkpoint。
- Runtime Agent Skill：单一业务能力、Prompt、工具白名单、调用预算、停止条件与失败策略。
- MCP：外部 Agent 工具协议。首版百度地图能力由模块 06 的 typed adapter 和 MCP Gateway 接入。
- PostgreSQL：Module 10 已提供显式创建的 Artifact Repository、Runtime 和 Alembic 迁移；Module 11 工作区实现负责 checkpoint、执行账本和跨进程恢复，采用独立的显式迁移入口。数据库不通过 MCP，现有 Module 01–09 Demo 组合在 Module 13 接线前仍使用进程内 Repository，也不会在 PostgreSQL 故障时自动回退或双写。

外部工具型 Skill 在项目已指定 MCP Server 时必须走 MCP；纯领域计算和确定性校验不使用 MCP。Skill 不得直连供应商 HTTP、直接管理 MCP session 或把 MCP 原始 payload 写入 Graph state。DeepSeek 由模块 02 通过 LangChain 专用集成调用，不经过 MCP。

## 规范入口

- [`docs/00_agent_master_design.md`](./docs/00_agent_master_design.md)：产品边界、跨模块架构和全局不变量。
- [`docs/IMPLEMENTATION_ROADMAP.md`](./docs/IMPLEMENTATION_ROADMAP.md)：组件职责、依赖、工作包和 Gate 的唯一索引。
- [`docs/IMPLEMENTATION_STATUS.md`](./docs/IMPLEMENTATION_STATUS.md)：当前实现、提交基线和验证结果快照。
- [`docs/SHARED_IMPLEMENTATION_RULES.md`](./docs/SHARED_IMPLEMENTATION_RULES.md)：所有模块执行契约共享的工作区、测试、安全和报告规则。
- [`docs/01_requirement_and_dialogue.md`](./docs/01_requirement_and_dialogue.md)：模块 01 纯需求领域合同。
- [`docs/01_requirement_and_dialogue_vibe_coding.md`](./docs/01_requirement_and_dialogue_vibe_coding.md)：模块 01 精确执行契约。
- [`docs/02_llm_runtime_and_model_profiles.md`](./docs/02_llm_runtime_and_model_profiles.md)：模块 02 LangChain / DeepSeek 运行时合同。
- [`docs/02_llm_runtime_and_model_profiles_vibe_coding.md`](./docs/02_llm_runtime_and_model_profiles_vibe_coding.md)：模块 02 精确执行契约。
- [`docs/03_requirement_extraction_adapter.md`](./docs/03_requirement_extraction_adapter.md)：模块 03 需求提取适配合同。
- [`docs/03_requirement_extraction_adapter_vibe_coding.md`](./docs/03_requirement_extraction_adapter_vibe_coding.md)：模块 03 精确执行契约。
- [`docs/04_workflow_state_and_artifacts.md`](./docs/04_workflow_state_and_artifacts.md)：模块 04 工作流状态、Artifact Repository、预算与安全事件合同。
- [`docs/04_workflow_state_and_artifacts_vibe_coding.md`](./docs/04_workflow_state_and_artifacts_vibe_coding.md)：模块 04 精确执行契约与 RSC-T00–RSC-T06 验收路由。
- [`docs/05_requirement_subgraph.md`](./docs/05_requirement_subgraph.md)：模块 05 需求子图、interrupt/resume、checkpoint、预算重放与运行时合同。
- [`docs/05_requirement_subgraph_vibe_coding.md`](./docs/05_requirement_subgraph_vibe_coding.md)：模块 05 精确执行契约与 T00–T05、MODULE-CLOSEOUT 验收路由。
- [`docs/06_baidu_map_adapter_and_mcp_gateway.md`](./docs/06_baidu_map_adapter_and_mcp_gateway.md)：模块 06 百度地图 typed adapter、MCP Gateway、规范化、限流和资格合同。
- [`docs/06_baidu_map_adapter_and_mcp_gateway_vibe_coding.md`](./docs/06_baidu_map_adapter_and_mcp_gateway_vibe_coding.md)：模块 06 精确执行契约、live 资格和 MODULE-CLOSEOUT 路由。
- [`docs/07_hotel_candidate_selection.md`](./docs/07_hotel_candidate_selection.md)：模块 07 酒店候选发现、证据聚合与人工选择合同。
- [`docs/07_hotel_candidate_selection_vibe_coding.md`](./docs/07_hotel_candidate_selection_vibe_coding.md)：模块 07 精确执行契约与 MODULE-CLOSEOUT 路由。
- [`docs/08_candidate_research_loop.md`](./docs/08_candidate_research_loop.md)：模块 08 受控候选研究循环、证据、预算、Artifact 与 replay 合同。
- [`docs/08_candidate_research_loop_vibe_coding.md`](./docs/08_candidate_research_loop_vibe_coding.md)：模块 08 精确执行契约、Docker Gate 与 MODULE-CLOSEOUT 路由。
- [`docs/09_itinerary_planning_loop.md`](./docs/09_itinerary_planning_loop.md)：模块 09 结构化输入、受控规划循环、路线证据、Artifact 和 replay 合同。
- [`docs/09_itinerary_planning_loop_vibe_coding.md`](./docs/09_itinerary_planning_loop_vibe_coding.md)：模块 09 精确执行契约、Docker Gate、真实联网资格和 MODULE-CLOSEOUT 路由。
- [`docs/10_artifact_persistence.md`](./docs/10_artifact_persistence.md)：模块 10 PostgreSQL Artifact 持久化、迁移、Repository、事务和安全合同。
- [`docs/10_artifact_persistence_vibe_coding.md`](./docs/10_artifact_persistence_vibe_coding.md)：模块 10 精确执行契约、真实 PostgreSQL Gate 和 MODULE-CLOSEOUT 路由。
- [`docs/12_result_formatting.md`](./docs/12_result_formatting.md)：模块 12 结构化结果、中文 Markdown、来源、日块与索引完整性合同。
- [`docs/12_result_formatting_vibe_coding.md`](./docs/12_result_formatting_vibe_coding.md)：模块 12 精确执行契约、独立 PostgreSQL 验证脚本和 MODULE-CLOSEOUT 路由。

权威层级和冲突处理以主设计第 1 节为准。后续模块必须按路线图中的 `depends_on` 和模块 acceptance gate 开工。`_vibe_coding.md` 专门面向能力较弱的编码模型，必须保留精确文件白名单、符号、算法、阶段 Gate、测试矩阵、停止条件和报告模板，不能压缩成需要模型自行推导的摘要。

## 当前边界

模块 01–05 已完成当前实现与离线验收：模块 05 unit `232 passed`、contract `65 passed`，line `97.7777777778%`、branch `95.0000000000%`、combined `97.0491803279%`；完整 Docker 离线 unit/contract 回归为 `935 passed`。模块 01 coverage 为 line `98.43%`、branch `96.83%`、combined `98.03%`，模块 04 coverage 为 line `97.50%`、branch `95.39%`、combined `96.98%`；模块 01、03、04 的本轮受影响回归分别为 `182`、`78`、`235 passed`。模块 02 的 DeepSeek V4 Flash capability live 已通过，模块 03 的 Flash live 资格为 `16/16`。这些数字是当前状态快照，不应复制到长期设计合同；模型、Prompt、依赖或能力边界变化后需重新进行 live 资格。

模块 06 提供百度地图 typed adapter、七项核心 MCP Gateway 能力、技术限流、生命周期和安全边界。本次 closeout 最终离线快照为：Module 06 + M2 offline `222 passed`；coverage `221 passed`，line `1945/2002`（`97.15%`）、branch `544/570`（`95.44%`）、combined `2489/2572`（`96.77%`）；Module 04 回归 `235 passed`；完整 unit/contract `1156 passed`。当前源码七项核心 live 和 M2 live 已通过，地图调起当前为 `NOT_AVAILABLE/NOT_IN_MANIFEST`；不在此记录地址、坐标、UID、天气、路线、指令或 Secret。

模块 08 提供受控候选研究循环、initial/supplemental、候选证据、执行预算、Artifact、replay、并发与失败边界；候选费用状态固定为 `not_evaluated`。原 closeout 后的结构化覆盖与候选一致性修复已随 Module 09 进入 `main`；当前统一 coverage 门槛为 line、branch、combined 各 70%，旧 80% 一次性例外只保留为历史记录。最新 Module 06→09 真实联调已从 Module 08 输出真实景点和餐厅候选。

模块 09 提供结构化输入范围内的受控行程规划、initial/resume、六工具循环、真实路线证据、完整确定性提交校验、Artifact、执行预算和 replay。生产 LLM Profile 锁定为 `deepseek-v4-flash`；Provider 仅把服务端当前返回的 `deepseek-flash` 归一化为该公共规范名。最新离线 Module 09 Gate 为 `284 passed`，互补 unit/contract 为 `1686 passed`，coverage line／branch／combined 为 `88.5387% / 74.2077% / 84.8764%`，compileall 通过；随后独立 Module 09 真实运行和 Module 06→09 单次真实联调均通过。文本作息、活动时刻/时长、交通限制等未被上游结构化的功能继续延期，不宣称已满足；完整持久化 Demo 主链仍需 Module 11–14，尤其是 Module 13 组合根的显式装配。

模块 10 提供符合 Module 04 三方法公共接口的 PostgreSQL Artifact Repository、显式生命周期 Runtime、固定 `artifact_store` schema、Alembic `m10_0001` 迁移、幂等写入、容量与并发控制、断线单次重试和真实重启持久性。AP-T06 最终资格为 Module 10 unit `64 passed`、contract `7 passed`、迁移 `15 passed`、普通 PostgreSQL integration `31 passed`、Module 04 映射／并发／故障 `20 passed`、全量 unit/contract `2041 passed`；coverage line／branch／combined 为 `87.7127% / 74.6479% / 84.9478%`。用户追加的真实 API 验收已证明 Module 10 可保存并在重连后读取真实行程产物；两次上游 Module 09 规划未得到 Completed 终态，但用户已明确决定该问题不阻断 Module 10。Module 10 实现提交 `cc1e6ef545db52f07cc15af64ea12ed589d24dda` 已 fast-forward 合并到本地 `main`，当前保持冻结。Module 10 不实现删除、TTL、checkpoint、自动迁移、生产 Registry 组合或生产部署。

模块 02 不拥有 LangGraph 或 MCP：它只返回结构化结果或已验证 tool call。模块 05 实现需求子图、同进程 interrupt/resume 和严格 checkpoint；百度地图 MCP Gateway 归模块 06；候选研究、规划和局部重排分别归模块 08、09、16；模块 13 负责 Demo 主链装配。当前开发没有客户端、FastAPI 或 Web；模块 14 只规划薄 CLI 和 Application Service。

安装、环境变量、运行命令和 live probe 命令必须以对应模块文档为准，不允许从旧原型 README 推断。

## 分支策略

本地 `main` 只保存已验收的稳定基线。后续每个新功能都必须先从最新 `main` 创建独立的 `feature/<short-kebab-name>` 分支，在该分支完成实现、测试和文档；验收通过后再合并回 `main`。具体工作区和 Git 规则见 [`docs/SHARED_IMPLEMENTATION_RULES.md`](./docs/SHARED_IMPLEMENTATION_RULES.md)。

## Docker 开发与测试

当前容器包含已经实现的模块 01–12，完整 Graph/CLI 主链尚未装配。runtime 默认 CMD 配置为导入模块 01–12，并输出 `modules 01-12 import successfully`；该检查只验证包可加载，不会自动连接或迁移数据库。`COPY app` 与 test target 的 `COPY tests` 已覆盖 Module 12。pytest 通过 `pyproject.toml` 的 `addopts = "--import-mode=importlib"` 消除全仓同名测试模块冲突，并继续使用 `no:cacheprovider`。M11 的数据库合同测试需要隔离 PostgreSQL；默认 test 服务不替代该编排。Docker 构建使用 Python 3.12；`.env`、本机 `.venv` 和 IDE/测试缓存不会进入镜像。

基础镜像默认使用 `docker.m.daocloud.io/library/python:3.12-slim`，可通过 `.env` 中的 `PYTHON_IMAGE` 覆盖；能够访问 Docker Hub 时可改为 `python:3.12-slim-bookworm`。可选 PostgreSQL 服务同样允许通过 `POSTGRES_IMAGE` 覆盖镜像来源。

复制本地配置并填写需要的密钥：

```powershell
Copy-Item .env.example .env
```

构建并验证当前运行时包：

```powershell
docker compose build runtime
docker compose run --rm runtime
```

在项目根目录使用 PowerShell 7 执行 Module 12 完整验证与全项目 unit/contract 回归：

```powershell
pwsh -NoProfile -File ./scripts/verify_module12.ps1 -Mode Closeout -RunTag local-m12-001
```

每次执行须将示例 `RunTag` 改为本次未使用的小写标签。脚本从当前源码构建专属 runtime/test 镜像，不读取 `.env`，仅使用合成测试凭据；模块测试断网，数据库测试使用新建的 internal 网络和专属 PostgreSQL 卷。Closeout 依次覆盖 Module 12 unit/contract、coverage 诊断、compileall、既有 M10/M11 迁移、Module 12 integration、同卷重启后的新进程读取、全部 `tests/unit` 与 `tests/contract`，最后断网验证 runtime 默认 CMD。脚本退出时核验资源身份并清理本次容器、网络和卷，保留并报告镜像；不调用真实供应商 API。参数、停止条件和其他模式见 Module 12 执行契约 §6.3。

模块 06 百度地图 live、地图调起和 M2 live 资格命令及独立授权要求只以 [`docs/06_baidu_map_adapter_and_mcp_gateway.md`](./docs/06_baidu_map_adapter_and_mcp_gateway.md) 和配套执行契约为准；README 不提供读取或输出 Secret 的命令。

真实 DeepSeek 测试会产生外部 API 调用，只在明确需要时运行：

```powershell
docker compose --profile live run --rm live-test
```

Module 09 的真实 E2E 会同时调用 DeepSeek 和百度地图，只用于重新资格，不能作为默认离线测试运行：

```powershell
docker compose --profile live run --rm module09-live-test
```

Module 06 地图 Adapter、Module 07 酒店、Module 08 候选和 Module 09 行程的单次真实联调入口为：

```powershell
docker compose --profile live run --rm --no-deps module070809-live-test
```

Module 10 使用可选的 PostgreSQL 服务，但迁移必须显式执行，应用启动不会自动修改数据库结构。当前完整 Demo 主链尚未在 Module 13 接入该 Runtime。需要初始化本地空库时按以下顺序运行：

```powershell
docker compose --profile production up -d postgres
docker compose --profile production run --rm artifact-migrate
docker compose --profile production down
```

`down` 不会删除数据库卷；本模块不提供清空、删除或自动重建日常数据库的命令。只有需要真实持久化的应用组合层才应通过 `create_artifact_persistence_runtime(...)` 显式取得 PostgreSQL Repository。

本地开发默认密码只用于隔离开发环境；任何共享或生产环境都必须通过 Secret 或外部环境变量覆盖。百度地图默认使用远程 Streamable HTTP MCP 地址，不需要额外启动 `baidu-mcp` 容器。

模块 06 设计提交为 `3412dcc5760e072ba549a8e8cfd997d60294df04`，最终实现与收尾提交为 `b8d1dc0493add91ed150d3fb1f8778f8fe22de09`；用户决定免除该模块的独立 closeout 审查，并已明确授权提交及合并到本地 `main`。push、PR 和 deploy 均未执行。

## Module 07 完成快照

Module 07 的确定性酒店候选发现与人工选择实现已完成 07-1～07-6；实施后独立审查和独立 closeout 审查的 P0/P1/P2/P3 均为 0。设计提交为 `3e1397c587bc9cf1e7b960f4c4bfabba88387012`，最终实现与收尾提交为 `173401caa9624424d17ea63bac23db253ca37d71`；本地 `main` 已从 `c85e66ca79200d289f7c85ab936a96f7592f078d` fast-forward 到最终实现提交，没有产生 merge commit。

Module 07 统一 Gate：`204 passed`；line `655/655 = 100.00%`、branch `194/194 = 100.00%`、combined `100.00%`；互补完整 unit/contract 回归 `1156 passed`，允许 7 条既有上游 Pydantic warnings，compileall 通过。Module 07 无新增依赖、无数据库 migration，不记录业务 query、目的地、UID、地址、坐标、Secret 或供应商原始 payload。

收尾镜像：runtime `china-travel-agent:module07-closeout-runtime`（`sha256:06ccfaaae4cc61f7b34840e800fa3c338e6d22e01bcf5bdc5593e67c055a5a4a`）；test `china-travel-agent:module07-closeout-test`（`sha256:7bc3a39e1ff1aabdaa7ad717f0ebb2d35c4a5a03253bc49128fa1334e7eb54b4`）。Module 07 commit 和本地合并已执行；push、PR、deploy 均未执行。

## Module 08 完成快照

Module 08 的受控候选研究实现已完成 08-1～08-6、生产缺陷修复、独立代码审查和 MODULE-CLOSEOUT。最终代码保持四类 `1.0` Artifact、双 Prompt hash、6/48/12 预算、最多 3 并发、128 候选容量、initial/supplemental/replay/singleflight/close 生命周期及上游公共边界；无新增依赖、migration、live provider、HTTP/SDK 私有访问或 Secret。

MODULE-CLOSEOUT 只同步两份 Module 08 设计中的用户验收例外、README、实施状态和 Docker runtime import 自检；没有修改 Module 08 生产代码或测试。最终实现与收尾提交为 `da2d1eadc09d5abac8f86054054c58d637de22db`，本地 `main` 已从 `7285fc2489b6a490bd38a65f1fb16b371159b094` fast-forward 到该提交，没有产生 merge commit。当前状态为 `MODULE08_COMPLETE_ON_LOCAL_MAIN`；push、PR 和 deploy 均未执行。
