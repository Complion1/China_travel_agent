# China Travel Agent

中国境内旅游攻略 Agent。模块 01–06 已完成并进入本地 `main` 稳定基线；模块 06 的实现、离线验收、独立代码审查、当前源码 live/M2 资格、MODULE-CLOSEOUT、提交和本地 fast-forward 合并均已完成。模块 07 已在 `feature/module-07-hotel-selection` 完成 07-1～07-6 实现、统一 Gate、实施后独立代码审查和本次 MODULE-CLOSEOUT 收尾 Gate，当前状态为 `MODULE07_CLOSEOUT_GATES_PASSED_AWAITING_INDEPENDENT_REVIEW`；实现与测试仍为未提交的 24 个 untracked 文件，尚未 commit 或合并本地 `main`，push、PR、deploy 均未执行，独立 closeout 审查待执行。旧原型接口不构成兼容要求。

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
- PostgreSQL：Production v1 的 checkpoint、artifact 与审计基础设施，不通过 MCP；当前 Demo 不依赖数据库。

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

权威层级和冲突处理以主设计第 1 节为准。后续模块必须按路线图中的 `depends_on` 和模块 acceptance gate 开工。`_vibe_coding.md` 专门面向能力较弱的编码模型，必须保留精确文件白名单、符号、算法、阶段 Gate、测试矩阵、停止条件和报告模板，不能压缩成需要模型自行推导的摘要。

## 当前边界

模块 01–05 已完成当前实现与离线验收：模块 05 unit `232 passed`、contract `65 passed`，line `97.7777777778%`、branch `95.0000000000%`、combined `97.0491803279%`；完整 Docker 离线 unit/contract 回归为 `935 passed`。模块 01 coverage 为 line `98.43%`、branch `96.83%`、combined `98.03%`，模块 04 coverage 为 line `97.50%`、branch `95.39%`、combined `96.98%`；模块 01、03、04 的本轮受影响回归分别为 `182`、`78`、`235 passed`。模块 02 正式 live 为 `1/1`，模块 03 历史正式 live 为 `16/16`。这些数字是当前状态快照，不应复制到长期设计合同；模型、Prompt、依赖或能力边界变化后需重新进行 live 资格。

模块 06 提供百度地图 typed adapter、七项核心 MCP Gateway 能力、技术限流、生命周期和安全边界。本次 closeout 最终离线快照为：Module 06 + M2 offline `222 passed`；coverage `221 passed`，line `1945/2002`（`97.15%`）、branch `544/570`（`95.44%`）、combined `2489/2572`（`96.77%`）；Module 04 回归 `235 passed`；完整 unit/contract `1156 passed`。当前源码七项核心 live 和 M2 live 已通过，地图调起当前为 `NOT_AVAILABLE/NOT_IN_MANIFEST`；不在此记录地址、坐标、UID、天气、路线、指令或 Secret。

模块 02 不拥有 LangGraph 或 MCP：它只返回结构化结果或已验证 tool call。模块 05 实现需求子图、同进程 interrupt/resume 和严格 checkpoint；百度地图 MCP Gateway 归模块 06；候选研究、规划和局部重排分别归模块 08、09、16；模块 13 负责 Demo 主链装配。当前开发没有客户端、FastAPI 或 Web；模块 14 只规划薄 CLI 和 Application Service。

安装、环境变量、运行命令和 live probe 命令必须以对应模块文档为准，不允许从旧原型 README 推断。

## 分支策略

本地 `main` 只保存已验收的稳定基线。后续每个新功能都必须先从最新 `main` 创建独立的 `feature/<short-kebab-name>` 分支，在该分支完成实现、测试和文档；验收通过后再合并回 `main`。具体工作区和 Git 规则见 [`docs/SHARED_IMPLEMENTATION_RULES.md`](./docs/SHARED_IMPLEMENTATION_RULES.md)。

## Docker 开发与测试

当前容器承诺复现已经实现的模块 01–07，不会把尚不存在的完整 Graph/CLI 主链伪装成可运行服务。runtime 默认 CMD 会导入 `app.requirements`、`app.llm_runtime`、`app.requirement_extraction`、`app.workflow_contracts`、`app.requirement_subgraph`、`app.baidu_map` 和 `app.hotel_selection`，并输出 `modules 01-07 import successfully`；test target 包含当前 tests 树，pytest 的冻结命令通过 `pyproject.toml` 的 `addopts = "--import-mode=importlib"` 消除全仓同名测试模块冲突，并继续使用 `no:cacheprovider`。Docker 构建使用 Python 3.12；`.env`、本机 `.venv` 和 IDE/测试缓存不会进入镜像。

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

运行全部离线测试：

```powershell
docker compose --profile test run --rm test
```

模块 06 百度地图 live、地图调起和 M2 live 资格命令及独立授权要求只以 [`docs/06_baidu_map_adapter_and_mcp_gateway.md`](./docs/06_baidu_map_adapter_and_mcp_gateway.md) 和配套执行契约为准；README 不提供读取或输出 Secret 的命令。

真实 DeepSeek 测试会产生外部 API 调用，只在明确需要时运行：

```powershell
docker compose --profile live run --rm live-test
```

PostgreSQL 是 Production v1 的预留服务，当前模块不会连接它。需要单独验证数据库容器时运行：

```powershell
docker compose --profile production up -d postgres
docker compose --profile production down
```

本地开发默认密码只用于隔离开发环境；任何共享或生产环境都必须通过 Secret 或外部环境变量覆盖。百度地图默认使用远程 Streamable HTTP MCP 地址，不需要额外启动 `baidu-mcp` 容器。

模块 06 设计提交为 `3412dcc5760e072ba549a8e8cfd997d60294df04`，最终实现与收尾提交为 `b8d1dc0493add91ed150d3fb1f8778f8fe22de09`；用户决定免除该模块的独立 closeout 审查，并已明确授权提交及合并到本地 `main`。push、PR 和 deploy 均未执行。

## Module 07 当前收尾快照

Module 07 的确定性酒店候选发现与人工选择实现已完成 07-1～07-6，实施后独立只读审查 P0/P1/P2/P3 均为 0，MODULE-CLOSEOUT 收尾 Gate 已通过，当前等待独立 closeout 审查。当前分支为 `feature/module-07-hotel-selection`，HEAD 为 `3e1397c587bc9cf1e7b960f4c4bfabba88387012`；实现与测试仍是未提交的 24 个 untracked 文件，commit 和本地 `main` 合并尚未执行，push、PR、deploy 均未执行。

Module 07 统一 Gate：`204 passed`；line `655/655 = 100.00%`、branch `194/194 = 100.00%`、combined `100.00%`；互补完整 unit/contract 回归 `1156 passed`，允许 7 条既有上游 Pydantic warnings，compileall 通过。Module 07 无新增依赖、无数据库 migration，不记录业务 query、目的地、UID、地址、坐标、Secret 或供应商原始 payload。

收尾镜像 tag：runtime `china-travel-agent:module07-closeout-runtime`；test `china-travel-agent:module07-closeout-test`。最终 image ID 以本页状态快照和实际 `docker image inspect` 为准。
