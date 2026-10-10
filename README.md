# China Travel Agent

中国境内旅游攻略 Agent，提供 FastAPI 和原生网页入口：需求澄清、确认、人工选择酒店、完整攻略、我的任务及运行统计。2026-10-10 已在腾讯云完成一个真实 DeepSeek／百度地图网页场景，以及同进程暂停续接和终态重启读回；实际范围与收据见 [M15 实施状态](docs/IMPLEMENTATION_STATUS.md#m15-云端部署与单场景交付2026-10-10)。

“补充旅行需求”按本轮缺失信息显示表单：兴趣多选、节奏单选、日期时间与数量控件，房型及限制提供选项，特殊情况可填写其他说明。选项不预选；儿童人数确定后显示对应年龄；自动读取任务进度时保留未提交选择。提交后仍经过原需求校验和完整需求确认。最新澄清交互维护资格与双审状态见[实施状态](docs/IMPLEMENTATION_STATUS.md)。

当前前端优化分支已实现六节点流程画布，活动阶段来自主图实际节点入口的有界观测，等待输入、恢复、取消及异常均有对应提示；阶段不可读时明确显示未知。攻略改为可读总览、每日导航与时间线，完整保留返程、费用未核验、来源及复核提醒；复制和下载保留原始 Markdown。定向离线主链及18项浏览器检查已通过；整体验收与双审以同步后固定源码的机器收据和本次会话最终报告为准。首版尚未提交或换版，下面的云端体验地址仍指向 M15 已交付版本；验证与交付状态见[前端首版记录](docs/IMPLEMENTATION_STATUS.md#前端流程与攻略阅读首版2026-10-10未提交)。

2026-10-06 的真实网页测试发现，抽取保留“行动限制：无”整行时，派生规则误显示需要行动辅助；随后已作最小修复，保留原约束文本及证据。当前否定识别仍有表达范围限制，建议明确填写“行动限制：无”“饮食禁忌：无”，并核对需求确认页。历史修复与本次 M15 云端单场景分别记录在[实施状态](docs/IMPLEMENTATION_STATUS.md)。

**快速体验：[开始旅行规划](http://203.0.113.10:8080)。** 也可查看[公开模拟样例](http://203.0.113.10:8080/samples)，或按[本机启动步骤](#m14-本机启动)复现。云端使用 HTTP；域名和 HTTPS 留待后续部署。首次使用请核对日期，按页面完成澄清、需求确认和酒店选择。

匿名身份可查看当前浏览器的历史任务，Cookie 固定有效 30 天。全站 200 新任务/日、每访客 20/日、每 IP 5/小时，同时执行 1 个任务且无队列；最终运行摘要同库保存后释放完整事件内存。M14 初始实现已进入本地 `main`（`b3438ca`），当前源码还包含通用工具参数修复、枢纽澄清维护及 M08/M09 额度调整。2026-10-09 的维护代码已获授权提交并本地整合；本次 M15 不执行 Git 提交、push 或 PR。逐阶段验证见[实施状态](docs/IMPLEMENTATION_STATUS.md)，网页规范见[M14设计](docs/14_web_application.md)与[执行规范](docs/14_web_application_vibe_coding.md)。

规划工具参数的缺失、类型、枚举、额外字段、范围/数量及非法 JSON 使用统一严格校验。出现参数错误时整批不执行，由模型重新生成；正常规划仍最多 17 次，另有同一 run 共享的 2 次格式修正储备，总计 19 次。补研返回不重置额度，修正后仍须通过行程业务校验。两次修正后仍不合格会停止。设计与实施入口见[M09 §26](docs/09_itinerary_planning_loop.md#26-通用参数修正与共享储备2026-10-06用户已批准)、[执行规范 §30](docs/09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)，实际离线、数据库、浏览器门禁和独立审查见[实施状态](docs/IMPLEMENTATION_STATUS.md)。此前本机18080已获授权更换为经过离线验证的128K镜像；本次 M15 真实云场景采用当前源码。

M08 候选研究的固定生成上限已按 2026-10-07 批准调整为 131,072（128K），Action 和 Observation Judgment 共用；high、120 秒读取超时及原调用预算保持不变。定向、M02/M08 和全项目离线回归结果见[实施状态](docs/IMPLEMENTATION_STATUS.md)。此前本机18080已在用户授权后更新，运行中Profile实查为131,072，首页、就绪接口及JS/CSS均200，原数据库、卷与暂停凭证保留；其入口为 http://127.0.0.1:18080。该次更新没有执行真实模型测试；本次 M15 另外记录当前源码的一个真实云场景。设计和维护入口见[M02 §6](docs/02_llm_runtime_and_model_profiles.md#6-固定-profile-矩阵)与[执行规范](docs/02_llm_runtime_and_model_profiles_vibe_coding.md#m08-生成额度维护入口2026-10-07用户已批准)。

模块 01–13 的当前批准范围实现已进入本地 `main`，包括 M11 网页控制扩展、M13 生产主图、公交换乘展示、M09 分阶段规划及完整交互诊断；实现提交为 `2ee962e`，本地合并记录由说明文档提交 `71767d3` 固化（2026-09-30）。记录见[规划改进报告 §7、§9、§10](docs/m09_phase_budget_and_interaction_records_review.md)。Module 11 保留演示范围验收及既有延期边界。生产 LLM Profile 使用 DeepSeek V4 Flash；Module 10/11 提供 PostgreSQL 产物和执行状态持久化，当前 M14 复用同库。M15 本次按[批准范围 §3.1](docs/15_demo_release_gate.md#31-待批准的范围决定)验证云部署、一个真实网页场景和实机资源；原完整质量集、M03 新16例及 M11 历史覆盖率欠账仍单列，不以此清除。push 和 PR 未执行。

## M15 云端体验与维护

入口为 [http://203.0.113.10:8080](http://203.0.113.10:8080)，腾讯云上海 2 核 2GB，独立项目 `cta-m15-cloud`。本次真实场景完成两天攻略、酒店人工选择、枢纽接驳与来源展示，完整统计已保存；同进程暂停续接及终态 Web 重启后的结果、统计和调用元数据一致。资源采样最大值为 Web 243,691,520 bytes、PG 101,867,520 bytes、最多15连接，OOM/非预期重启0；这是一场景实测。

先核对需求确认页，再接受需求。费用、天气、酒店实时房态/房型，以及部分补充文本偏好属于正文明确标注的未核验范围；地图链接请求尚未取得当前支持资格。真实规划会消耗公共 API 配额，运行统计中的计数不代表供应商账单。当前匿名身份限同一浏览器，Cookie 固定30天；换设备、清除Cookie或到期后无法找回。

以下命令在该云实例终端执行，使用现有私有配置。配置权限为0600；Key为0600、UID/GID 10001，只读挂载；独立Key备份在维护者受限目录 `<private-backup-directory>`。实际数据卷为 `cta-m15-cloud_m14_demo_pg_data`，网络为 `cta-m15-cloud_m14-demo`；PG无公网映射。发行目录保留初始主链镜像归档和收尾镜像归档，当前镜像引用以私有配置及实施状态收据为准。

```bash
M15_RELEASE_DIR=/srv/china-travel-agent/releases/20261009-aa9740d1b2b9-a
M15_CONFIG_FILE=/srv/china-travel-agent/private/m15/config.env
m15_compose() {
  sudo -n docker compose --env-file "$M15_CONFIG_FILE" --project-name cta-m15-cloud \
    -f "$M15_RELEASE_DIR/compose.yaml" -f "$M15_RELEASE_DIR/compose.cloud.yaml" \
    --profile m14-local "$@"
}

# 服务状态与真实就绪；回环请求也需要配置的公网 Host。
m15_compose ps
curl --fail --silent --show-error --max-time 10 \
  -H 'Host: 203.0.113.10:8080' http://127.0.0.1:8080/health/ready

# 日常启动，沿用现有配置、Key与数据卷；Web不自动恢复任务。
m15_compose up --detach --wait --wait-timeout 120 --no-build --pull never m14-postgres m14-web

# 日常停止，沿用Compose关闭宽限（Web 65秒），保留数据与Key。
m15_compose stop m14-web m14-postgres

# 仅汇总固定安全错误代码，避免输出日志正文。
m15_compose logs --tail 100 --no-log-prefix m14-web 2>&1 \
  | grep -oE 'WebApplicationStartupOrShutdownError|summary_not_saved|summary_finalization_failed|dependency_unavailable|service_closing' \
  | sort | uniq -c
```

新环境首次部署须先按 [M15设计 §5—7](docs/15_demo_release_gate.md) 与[执行规范 C01—03](docs/15_demo_release_gate_vibe_coding.md)准备同一发行物、独立新配置、新Key及独立备份。仅对已核验的新空库执行下面的首次迁移顺序；现有实例已完成这三条迁移，日常启动使用上面的命令。

```bash
sudo -n docker load --input "$M15_RELEASE_DIR/runtime-closeout-c.tar"
m15_compose up --detach --wait --wait-timeout 120 --no-build --pull never m14-postgres
m15_compose run --rm --no-deps --pull never m14-web python -m alembic -c app/artifact_persistence/alembic.ini upgrade head
m15_compose run --rm --no-deps --pull never m14-web python -m app.durable_execution.migrate
m15_compose run --rm --no-deps --pull never m14-web python -m app.web_application.migrate
m15_compose run --rm --no-deps --pull never m14-web python -m app.web_application.migrate --check
m15_compose up --detach --wait --wait-timeout 120 --no-build --pull never m14-web
```

维护停止前先等待任务结束且统计已保存；有活动任务时停止后的恢复结果未在本批验证。已完成结果和保存统计可以跨进程读取。本次只验证终态重启；暂停跨进程、任意节点崩溃恢复及多人并发容量仍按原资格单列。磁盘可用空间低于5GiB会拒绝新任务。到期和续费由维护者在腾讯云控制台确认；本批保留现有服务、卷、Key和8080规则，后续备份自动化、域名与HTTPS另行设计。

## M14 本机启动

在项目根目录使用 PowerShell 7、Docker 和 Docker Compose。以下是应用实际入口的初始化顺序，供取得真实 API 验收授权后使用；离线 Closeout 不读取真实密钥或调用供应商。默认只绑定 `127.0.0.1:8080`，本机 Demo、隔离测试及未来云项目使用独立卷。

首次创建私有配置并自行填写 `DEEPSEEK_API_KEY`、`BAIDU_MAPS_API_KEY`、`M14_LOCAL_PG_PASSWORD`（数据库密码至少 16 字符），保留精确的 `WEB_PUBLIC_ORIGIN=http://127.0.0.1:8080`。配置和密钥目录已被 Git 与构建上下文排除。已有配置时直接复用，不覆盖：

```powershell
New-Item -ItemType Directory -Path .m14-local/keys -Force | Out-Null
if (Test-Path -LiteralPath .m14-local/config.env) { throw '配置已存在，请复用并自行编辑' }
Copy-Item -LiteralPath .env.example -Destination .m14-local/config.env
```

从当前源码构建 runtime，并仅在首次初始化时生成暂停密钥。生成命令断网、只输出创建结果，目标文件已存在会拒绝覆盖；以后启动复用同一密钥，不重新生成：

```powershell
$M14DockerContext = docker context show
docker --context $M14DockerContext buildx build --builder $M14DockerContext --load --target runtime --tag china-travel-agent:m14-local .
$M14KeysDir = (Resolve-Path -LiteralPath .m14-local/keys).Path
docker run --rm --network none --mount "type=bind,source=$M14KeysDir,target=/keys" china-travel-agent:m14-local python -m app.web_application.keygen --output /keys/pause-key.json
```

显式启动独立 PostgreSQL，再依次执行 M10、M11、M14 迁移。Web 启动只验证 head，不自动迁移。命令明确选择服务，项目名固定为本机 Demo 的 `cta-m14-local`：

```powershell
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local up --detach --wait m14-postgres
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m alembic -c app/artifact_persistence/alembic.ini upgrade head
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.durable_execution.migrate
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.web_application.migrate
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local up --detach --wait m14-web
```

启动后浏览器访问 `http://127.0.0.1:8080`，按页面完成澄清、确认和酒店选择。任务页显示完整攻略并支持复制/下载 Markdown；运行统计默认折叠，读取数据库中保存的最终摘要。`/history` 查看本人任务，`/samples` 查看独立合成样例。刷新或关闭页面不会取消执行；清除 Cookie、换设备或身份到期后无法找回匿名任务。

停止时保留数据卷和原密钥：

```powershell
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local stop m14-web m14-postgres
```

再次启动沿用同一配置、密钥、项目名和卷。已保存结果和摘要可重读；进程重启不自动恢复业务。`compose.cloud.yaml` 使用同代码配置（Web 1GiB、PG 512MiB、最多20连接），本次云项目采用独立新卷及精确公网 Origin，实际收据见上方 [M15云端维护](#m15-云端体验与维护)。

模块 01–12 修复收尾时的全量 Docker unit/contract 回归为 **2317 passed、8 warnings**（历史快照）。第 **18/20** 轮复用第 15 轮已通过的真实需求、酒店及 M08 候选检查点，从 M09 开始真实调用，获得 Completed；M12 三个日块、汇总索引及 PostgreSQL 重启后的读取/哈希一致性均通过。成功后停止，剩余两轮未使用。这是复用真实上游的集成链验证，不是第 18 轮重新调用全部上游；这份历史结果不能替代后续 M13/14 或完整 M11 恢复闭环验收，也不证明行程质量最优。历史问题见 [联调修复记录](./docs/12_live_pipeline_fixes.md)，模块历史验收见 [实施状态快照](./docs/IMPLEMENTATION_STATUS.md)；[首次真实联调报告](./docs/12_live_pipeline_acceptance.md) 保留当时的失败记录。

当前规则：旅行预算金额功能延期，费用仍标未核验；抽取输出上限为 32,768 tokens，规划输入上限为 2 MiB。Module 09 使用 planning-v6 / ModelAction 6.0，行程产物为 3.0，Module 12 格式化结果仍为 2.0；旧版本按各模块兼容合同独立读取。备用点可选，缺项不阻止主行程完成、不触发备用点专用补研，已提供的备用点仍需通过资格、距离和证据校验。

## Module 11 显式迁移与导入自检

已进入本地 `main` 的网页控制扩展提供持久取消、run 查询/分页、暂停凭证交接端口、原生酒店恢复、节点只读证据视图及终态预算读取；新环境须显式应用既有迁移 `m11_0002_web_controls`。它复用现有 PostgreSQL runs 和预算记录，不另建任务索引库。扩展审查及收尾证据见 [实施状态](docs/IMPLEMENTATION_STATUS.md)；应用端凭证加密、网页调度与浏览器交互留给 M14/M15。

Module 11 提供持久执行库。当前默认 runtime 启动 M14 Web，采用上面的独立本机 profile；旧模块的导入诊断需显式指定命令。

仅需断网核验 Module 11 包导入时执行：

```powershell
docker run --rm --network none china-travel-agent:m14-local python -c "import app.durable_execution"
```

旧 `production` profile 的 `durable-migrate` 仍等待 PostgreSQL 健康与 `artifact-migrate` 成功，然后执行 `python -m app.durable_execution.migrate`。M14 使用自己的 Demo 库和显式三迁移顺序；其隔离测试清理只针对本次唯一前缀，保留日常库与 Demo 卷。

Module 11 已按用户授权提交并合并到本地 main（实现提交 `985dcde`）；演示范围验收和原覆盖率/延期条件的区别见 [T07 验收记录](docs/11_t07_demo_acceptance.md)。现有通用 test target 的 M11 contract 需要隔离 PostgreSQL，不能将无数据库环境的全套运行当成有效验收。当前 M13 主图使用其能力完成业务编排。

## Module 12 结果生成与保存交接

`app.result_formatting` 提供 `ResultFormattingService`，只处理 Module 09 的 `completed` 行程，并只读已保存的 Module 07/08/09 公共证据。输入需要已确认的工作流 `state` 和显式 `itinerary_ref`；输出为按日分块的结构化结果、中文 Markdown 快照及汇总索引，不提供部分行程预览，不重新调用模型或供应商。

调用顺序为 `prepare` → 调用方保存全部日块 → `build_index` 核验已保存日块 → 调用方保存索引 → `load` 完整读取。Module 12 负责生成和校验；当前 M13 通过每个日块及索引独立的 journal 保护节点保存结果，完整索引校验后才形成交付终态。

快照保留短来源引用、就地风险说明及末尾来源目录，同时显示路线原耗时与预留时间。费用标为未核验，已确认的补充文本回显并标为未验证；天气查询与地图展示延期。天气不影响本期完整达标判定。

## Module 13 生产主图

`app.demo_workflow.create_demo_workflow_definition` 返回供 M14 装配的 contract、命令 codec、预算 provider、未编译 graph factory、取消状态工厂和业务上下文清理入口。它串联真实 M05/M07/M08/M09/M12 能力，使用 M10/M11 持久化；不新增数据库、模型策略、天气交互或网页服务。

酒店必须人工选择；研究合法 Incomplete 且仍有合格候选时继续尝试规划；补研最多五次且共享原预算。只有完整行程及完整结果索引才可交付：主行程营业未知或请求了本期未提供的地图时标为 `degraded`，其余适用门槛通过标为 `demo_ready`（完整达标）。备选缺省、天气及研究曾 Incomplete 不单独降级。

设计与执行合同分别见 [M13 设计](docs/13_demo_workflow.md)、[M13 执行规范](docs/13_demo_workflow_vibe_coding.md)。已提交状态见 [规划改进报告 §7、§9、§10](docs/m09_phase_budget_and_interaction_records_review.md)。当前 M14 分支的统一离线入口见下方 Docker 开发与测试；它包括 M13 原覆盖率 Gate，原 M13 独立脚本保留其历史分支/CMD 前置。

M14 仍负责 HTTP/浏览器交互、原暂停凭证保护、单执行名额及断线后的后台任务管理。M13 的离线模型/地图替身测试不等同于 M15 真实网页发布验收。

早期 C 批验证了真实 PostgreSQL integration `3 passed`、同一专属卷重建 PostgreSQL 后新进程读取 `3` 个日块且摘要一致，以及完整 unit/contract `2218 passed, 7 warnings`；当时的 M10/M11 测试库迁移与资源清理通过。这些是历史阶段证据，最新资格见上述规划改进报告。

## 架构基线

```text
前端网页（M14：FastAPI + 原生 HTML/CSS/JS）
        → 应用 API
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
- PostgreSQL：Module 10 已提供显式创建的 Artifact Repository、Runtime 和 Alembic 迁移；Module 11 负责 checkpoint、执行账本和跨进程恢复，采用独立的显式迁移入口。数据库不通过 MCP；M13 生产主图由 M14 注入 M10 公开仓储，PostgreSQL 故障时不自动回退或双写。进程内 Repository 仅用于独立能力测试。

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

## 模块边界与历史资格

模块 01–05 已完成当前实现与离线验收：模块 05 unit `232 passed`、contract `65 passed`，line `97.7777777778%`、branch `95.0000000000%`、combined `97.0491803279%`；完整 Docker 离线 unit/contract 回归为 `935 passed`。模块 01 coverage 为 line `98.43%`、branch `96.83%`、combined `98.03%`，模块 04 coverage 为 line `97.50%`、branch `95.39%`、combined `96.98%`；模块 01、03、04 的本轮受影响回归分别为 `182`、`78`、`235 passed`。模块 02 的 DeepSeek V4 Flash capability live 已通过，模块 03 的 Flash live 资格为 `16/16`。这些数字是各模块早期验收快照，最新全量回归见 [规划改进报告 §7](docs/m09_phase_budget_and_interaction_records_review.md#7-批准后的实际实施快照2026-09-30)，不应复制到长期设计合同；模型、Prompt、依赖或能力边界变化后需重新进行 live 资格。

模块 06 提供百度地图 typed adapter、七项核心 MCP Gateway 能力、技术限流、生命周期和安全边界。本次 closeout 最终离线快照为：Module 06 + M2 offline `222 passed`；coverage `221 passed`，line `1945/2002`（`97.15%`）、branch `544/570`（`95.44%`）、combined `2489/2572`（`96.77%`）；Module 04 回归 `235 passed`；完整 unit/contract `1156 passed`。当前源码七项核心 live 和 M2 live 已通过，地图调起当前为 `NOT_AVAILABLE/NOT_IN_MANIFEST`；不在此记录地址、坐标、UID、天气、路线、指令或 Secret。

模块 08 提供受控候选研究循环、initial/supplemental、候选证据、执行预算、Artifact、replay、并发与失败边界；候选费用状态固定为 `not_evaluated`。原 closeout 后的结构化覆盖与候选一致性修复已随 Module 09 进入 `main`；当前统一 coverage 门槛为 line、branch、combined 各 70%，旧 80% 一次性例外只保留为历史记录。第 15 轮真实研究产出 56 个 eligible 候选（26 景点、30 餐厅），第 18 轮按原 Ref/hash 复用后完成规划。

模块 09 提供结构化输入范围内的受控行程规划、initial/resume、六工具循环、真实路线证据、完整确定性提交校验、Artifact、执行预算和 replay。生产 LLM Profile 锁定为 `deepseek-v4-flash`；Provider 仅把服务端当前返回的 `deepseek-flash` 归一化为该公共规范名。早期离线 Module 09 Gate 为 `284 passed`，互补 unit/contract 为 `1686 passed`，coverage line／branch／combined 为 `88.5387% / 74.2077% / 84.8764%`，compileall 通过；随后独立 Module 09 真实运行和 Module 06→09 单次真实联调均通过。文本作息、活动时刻/时长、交通限制等未被上游结构化的功能继续延期，不宣称已满足；当前 M13 已装配持久化业务主图，M14 继续负责网页/API 与资源生命周期。

模块 10 提供符合 Module 04 三方法公共接口的 PostgreSQL Artifact Repository、显式生命周期 Runtime、固定 `artifact_store` schema、Alembic `m10_0001` 迁移、幂等写入、容量与并发控制、断线单次重试和真实重启持久性。AP-T06 最终资格为 Module 10 unit `64 passed`、contract `7 passed`、迁移 `15 passed`、普通 PostgreSQL integration `31 passed`、Module 04 映射／并发／故障 `20 passed`、全量 unit/contract `2041 passed`；coverage line／branch／combined 为 `87.7127% / 74.6479% / 84.9478%`。用户追加的真实 API 验收已证明 Module 10 可保存并在重连后读取真实行程产物；两次上游 Module 09 规划未得到 Completed 终态，但用户已明确决定该问题不阻断 Module 10。Module 10 实现提交 `cc1e6ef545db52f07cc15af64ea12ed589d24dda` 已 fast-forward 合并到本地 `main`，当前保持冻结。Module 10 不实现删除、TTL、checkpoint、自动迁移、生产 Registry 组合或生产部署。

模块 02 不拥有 LangGraph 或 MCP：它只返回结构化结果或已验证 tool call。模块 05 实现需求子图、同进程 interrupt/resume 和严格 checkpoint；百度地图 MCP Gateway 归模块 06；候选研究、规划和局部重排分别归模块 08、09、16；模块 13 负责 Demo 主链装配。当前模块 14 实现 Application Service、FastAPI 与原生网页，并通过公开端口复用这些能力；显式 CLI 启动同一生产组合根。

安装、环境变量、运行命令和 live probe 命令必须以对应模块文档为准，不允许从旧原型 README 推断。

## 分支策略

本地 `main` 只保存已验收的稳定基线。后续每个新功能都必须先从最新 `main` 创建独立的 `feature/<short-kebab-name>` 分支，在该分支完成实现、测试和文档；验收通过后再合并回 `main`。具体工作区和 Git 规则见 [`docs/SHARED_IMPLEMENTATION_RULES.md`](./docs/SHARED_IMPLEMENTATION_RULES.md)。

## Docker 开发与测试

runtime 包含模块 01–14，默认 CMD 为 `python -m app.web_application`；缺少配置时安全退出 2。`COPY app` 同时打包六份静态资源和两份合成样例，生产镜像不含 tests/Playwright；test target 增加测试，browser-test 使用锁定的官方 Playwright 1.63.0 镜像。pytest 继续使用 importlib 与 no:cacheprovider。M11 数据库 contract 连接专属隔离 PG，所有 Python 均在 Docker 中执行。

基础镜像默认使用 `docker.m.daocloud.io/library/python:3.12-slim`，可通过 `.env` 中的 `PYTHON_IMAGE` 覆盖；能够访问 Docker Hub 时可改为 `python:3.12-slim-bookworm`。可选 PostgreSQL 服务同样允许通过 `POSTGRES_IMAGE` 覆盖镜像来源。

业务修改后的完整离线关闭验收使用下列入口。每次使用未用过的前缀和不存在的绝对证据目录；M15本次无业务修改，按批准范围执行runtime和云交付复核，不重复这套历史门禁：

```powershell
./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-z-local-001 -EvidenceDir D:/m14-evidence/z-local-001
```

Closeout 核验 runtime 实际默认 CMD、依赖与静态资源，覆盖全仓 unit/contract、M14 真 PG、三个新解释器读回、全部生产 Python 的三项 70% coverage、实际 Chromium、非终态 SSE 关闭及资源容量；同时执行 M10/M13 原覆盖率 Gate、M11 完整功能回归、M12 诊断覆盖率/PG/同卷真重启读回。脚本不读取真实 `.env`，不调用供应商，仅清理本次精确归属资源，保留报告和镜像。M11 历史 coverage 欠账及原接受边界保持；M12 未新增覆盖率数字门槛。其他 Set 与停止条件见 M14 执行规范 §11；旧 M12 脚本保留其历史关闭前置。

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

Module 10 使用可选的 PostgreSQL 服务，但迁移必须显式执行，应用启动不会自动修改数据库结构。当前 M13 主图通过 M11 受控能力使用该 Runtime 的公开仓储。需要初始化本地空库时按以下顺序运行：

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

## 枢纽澄清维护（2026-10-07）

当前分支增加目的城市的抵达、离开枢纽检查。名称、类型或当地时间缺失，以及名称与类型冲突时，确认前继续追问；网页提供机场、火车站或高铁站的类型选项，不预选答案。有问题的已填字段仍可核对并凭当前回答修正，历史需求记录保持可读。可核验的枢纽输入失败展示固定字段提示，并引导核对后新建任务。

实现依据为 [M01 枢纽澄清规则](docs/01_requirement_and_dialogue.md)、[M03 端点角色规则](docs/03_requirement_extraction_adapter.md)、[M13 输入失败映射](docs/13_demo_workflow.md) 和 [M14 表单与安全提示](docs/14_web_application.md)，阶段门槛见 [E01 维护执行入口](docs/01_requirement_and_dialogue_vibe_coding.md)。本次离线快照、保留的失败及同步后交付物验证入口见 [实施状态](docs/IMPLEMENTATION_STATUS.md#枢纽澄清维护离线快照2026-10-07)。

该维护阶段未替换当时的18080网页镜像。新 `requirement-extraction-v4-hub-roles` Prompt 的独立16例真实供应商资格仍为 `NOT_RUN`；本次M15云端一个场景验证不能替代它。后续本机换版和提交前验收按各自收据记录，见实施状态。

## M09 单次生成额度（2026-10-08）

当前修复分支的 M09 行程规划单次输出上限为 **128K（131,072 tokens）**，首次规划、续调及参数修正统一使用；思考仍为 `enabled/high`，总逻辑调用仍为17次普通规划加2次共享参数修正。设计依据为 [M02 Profile矩阵](docs/02_llm_runtime_and_model_profiles.md#6-固定-profile-矩阵) 和 [M09额度维护](docs/09_itinerary_planning_loop.md#27-m09-单次生成额度128k2026-10-08用户已批准)，执行及验证快照见 [实施状态](docs/IMPLEMENTATION_STATUS.md#m09-单次生成128k维护快照2026-10-08)。

该源码维护阶段未替换当时的18080网页镜像，真实API资格当时为NOT_RUN。后续本机换版、提交前离线回归与本次M15云端单场景按各自收据记录，不以历史快照代替新资格。
