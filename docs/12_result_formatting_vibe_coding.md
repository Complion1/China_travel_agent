# Module 12：Vibe Coding 执行契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 新版来源/结果接线与验收按 [新执行规范](09_itinerary_planning_loop_vibe_coding.md) W02/G-DEMO；旧测试结果版本不兼容，渲染硬规则与当前公交步骤功能继续适用。 新增目标尚未实施，文档审查/批准状态见 [本次报告](VALIDATION.md)。

> 2026-09-26 用户批准预算金额功能延期：[当前修订与执行合同](12_budget_amount_deferral.md) 优先适用。金额不采集、不作为确认/输出门槛；API 调用额度不变。旧预算案例属于历史合同，不代表本期验收项。

> 文档属性：未来实施执行规范草案，待用户批准。当前仅完成设计工作；本文件中的代码、Docker、测试、迁移和 MODULE-CLOSEOUT 都尚未执行。
>
> 唯一业务真源：[12_result_formatting.md](12_result_formatting.md)，下称 D。通用规则：[SHARED_IMPLEMENTATION_RULES.md](SHARED_IMPLEMENTATION_RULES.md)，下称 S。
>
> 本文件拥有阶段、文件白名单、操作顺序、验证矩阵和报告要求，不重新定义 D 的字段或业务语义。实施提示词必须同时引用 D 和本文。

## 1. 开始条件与不可越界事项

1. 用户明确批准 D 与本文，并另行授权实际实施。设计写作授权不包含实施。
2. 核验本地 main、开始 HEAD、dirty、staged、目标路径归属和当前合同。不得将某次设计 HEAD 写成永久实施基线。
3. 从经核验的本地 main 创建 `feature/module-12-result-formatting`；若分支已存在，先辨别归属，不能 reset/覆盖。设计分支为 `feature/module-12-result-formatting-design`，两者不混用。设计尚未合入实施基线时，先由用户指定取得已批准文档的方式，禁止自行 commit/merge 设计。
4. 每个实施提示词写明本批次的实际分支与开始 HEAD、预计 dirty 文件、允许的新文件及是否继续上一批次。白名单中已有文件必须先读，不按新文件覆盖。
5. D §2 的已确认决定与 §3 上游合同均已读取；依赖仍以路线图 §2 为唯一真源。只对直接消费的公共符号读精确章节，不无差别复制全部上游文本。
6. 生产代码仅 D §14 的新包；不得修改上游接口、测试、SQL、迁移、依赖、Prompt、完整主图或 CLI。没有必要的新 pip 依赖。
7. 小范围且明确的问题可在当前批次白名单内修复。改已确认设计、扩大范围、大改或发现上游冲突时停止，给出精确证据并先取得用户同意。
8. 所有阶段禁止 stage/commit/merge/push/PR/deploy。提交只能由独立审查通过后的另一个明确授权任务执行；未来提示词必须保留该边界。
9. 不操作其他项目；尤其不得触碰 process_simulation-platform-dev。不得读真实 .env。所有 Python 执行、测试、compileall、coverage 均在 Docker 内，宿主只承担 Git、文本编辑与 Docker 编排。

## 2. 必读与现有证据

按 D §3 读取上游章节，并核对实际公共包根及以下证据：

| 目的 | 已存在文件 |
|---|---|
| State/Ref/Repository/Registry | app/workflow_contracts/state.py、artifacts/models.py、artifacts/repository.py、artifacts/registry.py |
| 已确认输入与酒店 | app/requirements/models.py；app/hotel_selection/models.py、artifacts.py、__init__.py |
| 候选与事实 | app/candidate_research/artifacts.py、__init__.py |
| 行程与其目录 | app/itinerary_planning/models.py、artifacts.py、__init__.py |
| 持久化/恢复接线边界 | app/artifact_persistence/runtime.py、repository.py；app/durable_execution/models.py、protocols.py |
| 公共合同证据 | tests/contract/workflow_contracts/test_public_api.py、test_artifact_repository_contract.py；tests/contract/itinerary_planning/test_public_api.py、test_artifact_registration.py、test_upstream_integration.py |
| 基础设施门面 | tests/contract/artifact_persistence/test_public_api.py；tests/contract/durable_execution/test_public_api.py |
| 环境与收尾 | Dockerfile、compose.yaml、.env.example、pyproject.toml、requirements.lock |
| 既有例外 | docs/IMPLEMENTATION_STATUS.md §1；docs/11_t07_demo_acceptance.md；docs/11_delivery_precommit_review.md；docs/deferred_findings/unresolved_findings.md |

表内 app/... 与 tests/... 以项目根目录为基准。不存在的实现目标仅见 §3、§5 白名单；引用核验失败要区分“已存在必读引用缺失”与“允许创建的新目标尚未存在”。

M09 completed 已做规划验证，不在 M12 再建 ValidationReport。不得从 M09 私有 Snapshot/转换函数导入；M08 及 M09 嵌套 DTO 通过公开 Artifact 对象按字段消费。枚举真实值必须核对，例如铁路枢纽为 railway_station、pace 为“轻松/标准/紧凑”，visit_kind 包括组合餐次，不能机械翻译后错误拒绝。

## 3. 分批实施图与验收入口

原则：相邻两个小阶段组成一批，完成两阶段后统一跑本批相关测试。调试可在批内运行针对性检查，但不可把阶段配对理解为跳过安全前置条件。

| 批次 | 阶段 | 结果 | 完成后验证 |
|---|---|---|---|
| A | RF-T01 + RF-T02 | 严格 DTO/Artifact 与源投影 | schema、projection、上游组合合同 |
| B | RF-T03 + RF-T04 | 中文模板与完整只读服务/API | 全部 M12 unit/contract、coverage 诊断 |
| C | RF-T05 + RF-T06 | PG 持久结果集成、独立实施审查与回归 | M12 integration、全项目 unit/contract、文档逐条对照 |
| 独立 | MODULE-CLOSEOUT | 文档和 Docker 自检真实同步 | 同步后重建、默认 CMD、受影响 Gate、完整离线回归 |

无迁移阶段；RF-T05 只在新建测试数据库执行既有 M10/M11 已批准迁移，不创建/修改迁移文件。真实供应商 live 不属于 M12。

未来实施可创建的完整路径以 D §14 为准；每批只允许其子集。D 与本文在实施阶段冻结，不能为了代码或测试通过改文档。本轮文档作者写入范围与未来实施白名单不是同一权限。

## 4. 关键实现顺序

以下为执行顺序，不复制 D 的完整 DTO 定义。

### 4.1 生成

```text
fresh_request = strict Python-tree preflight + 公共 M04 state codec
check_business_preconditions()
itinerary = get(explicit_ref, expected_model=public M09 artifact)
require completed
hotel/candidate/source = get 只读证据，逐项检查 D §7 关联
core, day_data = deterministic projection（有界目录、确定排序）
input_digest, bundle_digest = D §6
day_artifacts = 为每一天渲染并构造严格 Artifact
对所有日块真实 Registry prepare + canonical byte 容量校验
返回全部 days；任何错误只返回 Failed
```

所有独立对象在第一次 await 前复制请求；中途调用者修改嵌套输入不改变本次结果。回显补充字段是确定性投影，不做文本规则解析。

### 4.2 建索引

先按同请求重建 §4.1 的完整预期 core/days；验证 day_refs 数量和原序；只对完全合法的 Ref 发起 get。每个已保存日块与预期全部字段比较，包括 Markdown；全部通过才构造并检查索引。不得仅以“日块存在”“hash 不为空”或“数量等于天数”替代内容关联。

### 4.3 读取

读取显式 index_ref，读取全部 day_refs，并按照 D §10.3 重建源投影进行比对；完整通过后才拼接正文。禁止流式返回前半程、补写丢失块或在此调用 recover。写入只出现在测试 harness 或未来 M13，不能藏进 M12 的 helper/validator。

### 4.4 事实与文案复核

每个输出字段至少能落到 D §3/§7 的一个明确来源。费用、营业、备选、公交、酒店寄存、天气、地图、补充要求各使用 D §8/§9 的限定文案。审查必须人工检查模板结果，不能只看 snapshot 测试绿灯。

## 5. 逐阶段合同

### RF-T01：严格 DTO、Artifact 和写请求构造

目标：落实 D §4～6、§11 的类型与规范字节合同。

前置：§1 满足；真实 M04 Registry 可通过现有 Docker 环境导入；本阶段不启动数据库。

精确白名单（允许创建）：

- app/result_formatting/__init__.py
- app/result_formatting/models.py
- app/result_formatting/artifacts.py
- app/result_formatting/errors.py
- tests/unit/result_formatting/conftest.py
- tests/unit/result_formatting/fakes.py
- tests/contract/result_formatting/test_artifact_registration.py

允许动作：建立无默认的持久 DTO 图、两个注册项、fresh 校验、摘要及 build_result_artifact_write_request。不得用全局 mutable Registry、万能 dict 或 Pydantic serializer 隐藏字段转换。门面可先仅暴露本阶段已实现的符号，最终精确 API 在 T04 验收，不用空壳 Service 冒充完成。

测试准备：RF-SCHEMA 的 round-trip、中文 bytes、嵌套 mutation、重复 prepare 字节稳定与错误对象，不写镜像实现的逐字段无价值测试。与 T02 结束后统一执行。

PASS：类型与注册设计逐项一致，实际 Registry 成功；STOP：必须改变上游模型、字段/版本或注册规则。

### RF-T02：已保存源的读取核验和结构化投影

目标：D §3、§6、§7 的真实证据到 ResultCore/ResultDayData 映射。

前置：T01 类型可用，保持同批 dirty 归属可解释。

白名单：T01 全部文件，加：

- app/result_formatting/projection.py
- tests/unit/result_formatting/test_projection.py
- tests/contract/result_formatting/test_upstream_integration.py

允许动作：在 projection.py 实现仅接收已读取 typed 对象的纯投影、来源目录、摘要、跨字段校验；准备复用的测试 fakes。公共 Repository 的 async 读取编排到 T04，不提前引入 Graph。测试以真实上游公共 DTO 和真实 M04 进程内 Repository 构建合成证据，不能自己定义同形假 Artifact 绕开 expected_model identity。

统一测试：§6 的批次 A 命令；覆盖 RF-SCHEMA、RF-SOURCE、RF-ROUTE 中的纯投影部分，尤其 M08 incomplete/M09 completed、组合餐次、UTC+8 跨午夜及枢纽无 POI UID。

PASS：A 批全部相关测试与设计对照通过；报告未知行为；STOP：上游缺必要字段或必须重新研究/改规划才能输出。

### RF-T03：确定性中文模板

目标：D §8 的完整模板、源引用、风险提示和文本转义。

前置：A 批 PASS；未生成真实旅行文本或调用外部 API。

白名单：A 批生产文件（仅明确小修）及 fakes.py，加：

- app/result_formatting/renderer.py
- tests/unit/result_formatting/test_renderer.py

允许动作：固定模板逻辑、显式安全转义、确定性格式化；至少普通多日、组合餐次/备选、零耗时/跨午夜、补充文本与营业未知四组高收益场景。Golden 预期必须由规范逐项核对，不能简单保存当前输出作为正确答案。

正文 golden 使用合成地点和固定来源时间；不得把真实供应商输出写进 repo。与 T04 后统一测试。

PASS：模板逐项满足 D §8；STOP：需要模型润色、新事实、URL 或任意文本解释。

### RF-T04：公共服务、索引核验和完整读取

目标：D §4、§10、§11 的完整只读入口及精确门面。

前置：T03 模板可与投影组合。

白名单：A/B 已有新包与本模块测试文件，加：

- app/result_formatting/service.py
- tests/unit/result_formatting/test_service.py
- tests/contract/result_formatting/test_public_api.py
- tests/contract/result_formatting/test_import_boundaries.py

允许动作：构造、prepare/build_index/load、错误映射、单调用缓存与精确 __all__。API 测试确认只有 D §4 所列导出，无数据库/模型连接，无额外 close/recover/list/latest/put。补齐 Ref 同 ID 不同字段、错 bundle/顺序/缺块、未知 schema 与非法模板的 D §11 不同错误路径、取消、无叠加重试、源内容与保存正文矛盾。

统一测试：批次 B 命令，全部 M12 unit/contract，coverage line/branch/combined 诊断。真实 PG 留 T05。

PASS：RF-API～RF-ERROR 对应项与 D 逐条一致；STOP：任一核心事实可能伪造、部分结果误当完整、写入或恢复越权。

### RF-T05：专用 PostgreSQL 与跨进程结果读取

目标：RF-PERSIST；只验证 M12 输出存储合同对接真实 M10，不装配完整 M13。

前置：B 批 PASS；Docker 可用；§6 专属资源身份核验通过。任何数据库连通问题为环境失败，不是假测试替代理由。

白名单：全部 M12 新包（仅集成暴露的小修）、本模块 unit/contract 测试，加：

- tests/integration/result_formatting/conftest.py
- tests/integration/result_formatting/test_persisted_result.py
- tests/integration/result_formatting/restart_worker.py
- scripts/verify_module12.ps1

允许动作：脚本只编排专属 Docker；fixture 只消费合成设置，不能控制宿主 Docker。通过 M10 公共 factory/runtime.repository 构建组合 Registry，保存合成 M07/08/09 证据、日块与索引。worker 支持 seed/verify：seed 保存后退出，新容器 verify 仅用固定合成需求、run 身份及 Repository lookup/get 找回显式索引并加载；不得靠同一 Python 对象保留内存。

PG 容器重建使用本次专属卷保留数据；worker 和服务进程均重新启动。传递的摘要/Ref 为合成安全测试数据，不能把正文作为日志或宿主临时文件。对账断言相同 index/day 字节、数量与索引关联；不同内容同操作必须冲突；部分保存无索引不得加载为完整攻略。

允许执行现有 M10 migration，Regression 需要时另执行现有 M11 migration；禁止修改迁移或以本测试创建额外生产表。M11 journal 不确定窗口仍以 D §10.2 为限，报告不得称完全无损恢复。

与 T06 配对统一执行 integration + regression；环境建库和迁移本身的安全检查必须即时完成，不能延到批末。

PASS：跨进程字节一致、完整索引、精确幂等与冲突负例均满足 RF-PERSIST；STOP：环境身份、卷权限、迁移、源关联或任一持久完整性断言不符，不靠换库、删数据或 fake 替代放行。

### RF-T06：独立实施审查与回归

目标：逐条对照 D 的 RF 矩阵，关闭阻断问题，记录非阻断项。

前置：T05 环境及 harness 就绪；允许使用当前批 dirty 源码。

白名单：前述 M12 新包/本模块测试/verify_module12.ps1；允许追加本轮内容至 docs/deferred_findings/unresolved_findings.md。不得变更 D、本文或上游冻结设计。无需为本阶段新增专门报告文件。

审查顺序：公共 API/类型 → 源映射 → 字段真实性和中文文案 → 分块/索引 → 错误/安全 → 下游保存接线空间 → 实际测试命令与环境。修复小问题后只重跑受影响测试及批末要求；未改代码不反复全量跑。

统一测试：M12 全量 unit/contract + integration、项目当前全部 tests/unit 和 tests/contract。项目回归必须提供 M11 合同测试需要的 PG，不得一边 --network none 一边忽略该测试。覆盖率独立采集 M12，固定数值不阻断，但生成/解析失败必须修复。

PASS：无 P0/P1/高概率演示 P2；其他已登记；RF 矩阵逐项给出规范定位/实现位置/证据与限制。仍不宣告模块最终 COMPLETE，等待独立 closeout。

### MODULE-CLOSEOUT：未来独立收尾

目标：严格执行 S §7，属于未来生产实施最后一个独立阶段，本次设计不执行。

前置：T01～T06 通过且独立审查通过；允许尚未提交但已审查的白名单 dirty 功能分支，不要求先 commit。新失败或设计冲突先停止，不能借收尾改规范。

本阶段精确可写白名单：

- Dockerfile：仅默认 CMD 加入 app.result_formatting 导入，并把包数/说明更新为实际完成范围；COPY app 已覆盖新包，COPY tests 已覆盖新测试，不添加 SQL/联网启动动作。
- README.md：实际完成模块、生产包、可运行测试入口与 Docker 说明。
- docs/IMPLEMENTATION_STATUS.md：本次真实验证快照、命令、结果、限制。
- docs/deferred_findings/unresolved_findings.md：本模块发现及关闭证据。

Compose、.env.example、pyproject.toml、requirements.lock 预期无需变化：本模块不新增供应商、数据库配置、依赖或服务。若实际证据要求修改，先请求精确扩展白名单；不得机械新增 module12 service。

同步后从当前源码重新构建 runtime/test；新容器运行默认 CMD，断言无网络/数据库初始化；重跑 M12 全量、受影响持久 Gate 及完整 tests/unit+contract。基于最终源码核对全部测试事实，不复制旧镜像结论。

PASS 只表示本模块当前批准范围完成，提交资格仍 NOT AUTHORIZED。任何失败写 FAIL，后续项 NOT_RUN；不提前报告准备合并。

## 6. Docker 与可执行验证合同

### 6.1 通用隔离

所有命令在项目根目录执行，脚本使用 PowerShell 7。运行前检查当前源码与白名单；构建镜像必须基于本次源码。只用新建容器，均 --rm、--read-only、--tmpfs /tmp、--cap-drop ALL、--security-opt no-new-privileges。runtime/test 以 Dockerfile 的非 root 用户运行；PG 显式使用镜像 postgres 用户及 §6.3 的目录权限，不使用需要 chown/gosu 的默认 root 初始化路径。构建下载依赖允许联网；领域测试运行 --network none。数据库测试使用 --internal 网络，不注入 DeepSeek/Baidu 真实变量，不读取 .env。

当前 compose test 会继承 DEEPSEEK_API_KEY，且 production postgres 有日常持久卷，因此 M12 验收不直接复用该 service/volume。使用下面专属脚本和显式 docker run。脚本禁止打印完整环境、连接串、异常或正文。

### 6.2 A/B 批可直接执行命令

以下均为未来实施命令；本轮设计不执行。测试结果、coverage 和 Python cache 仅写容器 /tmp，coverage 数值用输出的安全计数采集。镜像标签固定为专属临时标签，每次构建覆盖本任务标签，不沿用旧结果。

```powershell
docker build --target test -t china-travel-agent:module12-test .
if ($LASTEXITCODE -ne 0) { throw 'M12_BUILD_FAILED' }

# A 批：T01/T02 完成后
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --cap-drop ALL --security-opt no-new-privileges:true china-travel-agent:module12-test python -m pytest -q -p no:cacheprovider tests/unit/result_formatting/test_projection.py tests/contract/result_formatting/test_artifact_registration.py tests/contract/result_formatting/test_upstream_integration.py
if ($LASTEXITCODE -ne 0) { throw 'M12_A_FAILED' }

# B 批：T03/T04 完成后
$M12CoverageCode = @'
import json
import sys
from pathlib import Path
import pytest

code = int(pytest.main([
    "-q", "-p", "no:cacheprovider",
    "tests/unit/result_formatting", "tests/contract/result_formatting",
    "--cov=app.result_formatting", "--cov-branch", "--cov-fail-under=0",
    "--cov-report=term-missing", "--cov-report=json:/tmp/module12-coverage.json",
]))
try:
    totals = json.loads(Path("/tmp/module12-coverage.json").read_text("utf-8"))["totals"]
    keys = ("covered_lines", "num_statements", "covered_branches", "num_branches")
    covered_lines, num_statements, covered_branches, num_branches = [totals[k] for k in keys]
    values = (covered_lines, num_statements, covered_branches, num_branches)
    if not all(type(v) is int and v >= 0 for v in values):
        raise ValueError()
    if not (0 <= covered_lines <= num_statements and num_statements > 0):
        raise ValueError()
    if not 0 <= covered_branches <= num_branches:
        raise ValueError()
    line = 100 * covered_lines / num_statements
    branch = 100 * covered_branches / num_branches if num_branches else 100.0
    combined = 100 * (covered_lines + covered_branches) / (num_statements + num_branches)
    print("M12_COVERAGE_COUNTS", *values)
    print(f"M12_COVERAGE_PERCENT line={line:.2f} branch={branch:.2f} combined={combined:.2f}")
except Exception:
    print("M12_COVERAGE_INVALID")
    sys.exit(code or 2)
sys.exit(code)
'@
$M12CoverageCode | docker run --rm -i --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --cap-drop ALL --security-opt no-new-privileges:true -e COVERAGE_FILE=/tmp/.coverage china-travel-agent:module12-test python -
if ($LASTEXITCODE -ne 0) { throw 'M12_B_FAILED' }

docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --cap-drop ALL --security-opt no-new-privileges:true -e PYTHONPYCACHEPREFIX=/tmp/pycache china-travel-agent:module12-test python -m compileall -q app/result_formatting tests/unit/result_formatting tests/contract/result_formatting
if ($LASTEXITCODE -ne 0) { throw 'M12_COMPILE_FAILED' }
```

B 命令中的 --cov-fail-under=0 仅为 D-12-13 对本模块显式诊断策略，不改项目默认 95 或其他模块历史门槛。上面的容器内 wrapper 在 pytest 结束后、容器移除前输出安全计数；T05 脚本复用同一逻辑。JSON 不存在/非法、计数越界或无有效语句均失败；不得用后置输出覆盖测试非零退出码。A 批无 renderer/service 时只测已经完成部分；B 批必须完整执行。

### 6.3 T05 脚本接口与资源契约

新脚本 `scripts/verify_module12.ps1` 参数：

```powershell
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('Build','Module','Integration','Regression','Closeout')]
  [string]$Mode,
  [Parameter(Mandatory=$true)]
  [ValidatePattern('^[a-z0-9][a-z0-9-]{2,30}$')]
  [string]$RunTag
)
```

脚本必须拒绝空/非法参数，不自动复用同 RunTag 的已有资源。前缀 `china-travel-agent-m12-$RunTag`；Docker label 为 `china-travel-agent.module=12` 与 `china-travel-agent.run=$RunTag`。网络名 suffix=-net，PG 容器=-postgres，卷=-pgdata；网络 alias 固定 postgres。所有清理都先 inspect 名称和两个 label，只清理本次自己成功创建且身份一致的资源，不调用全局 prune。

数据库名用前缀把 '-' 转 '_' 后追加 _main；专用合成用户名 m12_test，密码用新随机合成值仅通过当前脚本内存传入子容器，报告不输出密码。PG 使用 postgres:16-alpine 对应项目镜像源；不发布宿主端口。客户端变量精确为 POSTGRES_HOST=postgres、POSTGRES_PORT=5432、POSTGRES_DB、POSTGRES_USER、POSTGRES_PASSWORD；另带 MODULE12_TEST_PREFIX 和 MODULE12_TEST_IDENTITY_TOKEN=前缀-identity。fixture 验证上述身份，拒绝默认日常库、空凭据或外部 host。

PG 启动前固定本次解析得到的镜像 ID，并用该镜像的短命断网只读容器查询 postgres 的实际数值 UID/GID，不能把某次镜像的 70:70 当永久合同。PG 容器显式 --user postgres；socket tmpfs 为 `/var/run/postgresql:rw,nosuid,nodev,uid=<实际UID>,gid=<实际GID>,mode=3775`，/tmp tmpfs 为 mode=1777。新专属 named volume 挂载 `/var/lib/postgresql/data`，使用默认 copy-up，禁止 volume-nocopy；其初始权限应来自镜像内 postgres 所有的数据目录。首次启动前，以相同镜像 ID、用户、卷挂载及安全选项的短命检查容器验证数据目录归属/可写/初始为空；不符则 ENVIRONMENT_BLOCKED，不给其他卷改权限。重启同一卷时只检查归属/可写与既有 PG 版本，不再要求空目录。

该非 root 初始化选择依据 [PostgreSQL 官方入口脚本](https://raw.githubusercontent.com/docker-library/postgres/master/docker-entrypoint.sh) 与 [PostgreSQL 16 Alpine Dockerfile](https://raw.githubusercontent.com/docker-library/postgres/master/16/alpine3.23/Dockerfile) 的目录/用户行为；本轮仅只读查证，未来以实际镜像检查和启动结果为验收依据。

脚本流程：

1. 验证 cwd/git root、白名单与 Docker 可用；从当前源码 build runtime 和 test 专属标签（包含 RunTag）。构建失败即停。
2. Module 模式按 §6.2 B 批完整命令及 compileall；同容器输出 coverage 诊断。不需要 PG。
3. Integration/Regression/Closeout 创建专属 internal network、带 label 的卷，并按上述非 root 及权限预检合同启动新 PG 容器（--rm；数据卷本次创建）；等待 pg_isready，有总超时 60 秒，超时失败。重建 PG 也使用同一镜像 ID、用户、tmpfs 和本次卷。
4. 仅通过 runtime 容器在新库执行 `alembic -c /workspace/app/artifact_persistence/alembic.ini upgrade head`。Regression/Closeout 另外执行 `python -m app.durable_execution.migrate`，供现有 M11 contract 使用。任何迁移失败 STOP，无重建库掩盖错误。
5. Integration 执行 M12 integration；随后 seed worker 保存、退出，停止并移除本次 PG 容器，使用同一个本次卷新建 PG 容器，再用新 test 容器 verify。只传合成 run ID/OperationId 等标识，所有查找走公共 Repository。
6. Regression 执行 `python -m pytest -q -p no:cacheprovider tests/unit tests/contract`，连接隔离主库；Module12 自己的领域测试仍另外断网执行。不得追加 --ignore 或 -k 排除失败模块。
7. Closeout 包含 Module、Integration、Regression，另用最终 runtime 镜像断网运行默认 CMD。普通入口不应连接 PG/MCP 或迁移。
8. finally 核对本次对象 label 后清理本次容器、网络、卷；镜像可保留且在报告列名，不因名字相似删除其他镜像。清理失败作为残留报告，不把验证失败改为成功。

Integration 命令：`python -m pytest -q -p no:cacheprovider tests/integration/result_formatting`。worker 使用 `python -m tests.integration.result_formatting.restart_worker seed|verify`；若当前 tests namespace 不支持模块执行，在本阶段显式使用文件路径 `python /workspace/tests/integration/result_formatting/restart_worker.py seed|verify` 并确保项目根 Python path，不新增生产 CLI。fixture/shared fakes 只构造真实公共模型的合成数据，不复制上游生产服务。

未来调用：

```powershell
pwsh -NoProfile -File ./scripts/verify_module12.ps1 -Mode Integration -RunTag <本次唯一小写标签>
pwsh -NoProfile -File ./scripts/verify_module12.ps1 -Mode Regression -RunTag <另一个唯一小写标签>
# 仅独立 MODULE-CLOSEOUT 获准进入后：
pwsh -NoProfile -File ./scripts/verify_module12.ps1 -Mode Closeout -RunTag <又一个唯一小写标签>
```

尖括号值必须替换，不能原样运行。脚本实现后的审查要逐条核对上述流程，不允许只创建一个同名空脚本。容器退出后检查本次标签残留；Docker 故障是 ENVIRONMENT_BLOCKED，测试未执行是 NOT_RUN，不写 PASS。

## 7. 停止、报告与后续提示词

通用停止引用 S §8；另有：schema/模板/API 需改变已确认设计，出现白名单外未知修改，索引可绕过完整性，输出新增事实，源枚举/摘要合同冲突，所需数据库拓扑不可用，coverage 采集失败，新增真实 API 要求，均停止对应阶段。

每批报告至少包含：

```text
任务/批次：
分支：
开始 HEAD / 结束 HEAD：
用户已有 dirty 与本批变更归属：
修改文件 / 白名单外异常：
逐条规范对照（D 章节、RF ID、实现位置、证据）：
迁移（仅测试库既有迁移，或 NOT_RUN）：
测试（精确命令、环境/镜像、退出码、结果/未运行原因）：
coverage（line/branch/combined、安全计数；诊断，不自授数字门槛 PASS）：
Docker 资源（本次身份、重建、残留）：
独立审查、阻断项及延期项：
commit / merge / push / PR / deploy：NOT AUTHORIZED，未执行
当前阶段状态：
MODULE-CLOSEOUT 状态：
模块最终完成资格与限制：
```

必须区分“日块生成”“已持久化”“索引完整”“可加载”“主图交付终态”；本模块不能自授 demo_ready。未来实施/修复/续执行提示词一律放在一个完整代码框内，只引用 D/本文精确章节，不复制大段已成文合同；仍保留阶段目标、前置条件、分支、精确文件白名单、允许动作、测试矩阵、停止条件及报告格式。

## 8. 本次文档审查记录

此节记录 2026-09-26 的设计审查，不是代码验收。两个 Agent 均只读，未写文件、运行 Python、Docker、测试或迁移；修订均由主模型在授权四文件范围内完成。

第一轮：Agent A 检查架构、源字段/摘要、证据关联和下游边界；Agent B 检查阶段执行性、Docker/PG、测试和决定追踪。第二轮互换重点，并复核各自第一轮发现。全部发现及关闭如下：

| 轮次/发现 | 等级 | 修订与关闭证据 |
|---|---|---|
| A1：正常续规划复用前轮观测被最终摘要条件拒绝 | P1 | D §7.3 比较 Observation 与所属公共 Action 摘要，RF-SOURCE 增加合法复用正例；A2/B2 关闭 |
| A1：M01 无前缀摘要被误称旧路径、算法不精确 | 高概率 P2 | D §7.1 明确当前常规路径和完整 confirmed JSON 算法；A2/B2 关闭 |
| A1：M07 来源字段及 M09 酒店日期字段误写 | 高概率 P2 | D §5.2 分三类来源映射，§7.1 酒店日期仅取 confirmed；A2/B2 关闭 |
| A1：Literal 模板版本与 Repository 错误码不相容 | 高概率 P2 | D §11 区分 Ref 未知 schema 与正文非法模板；T04 同步；A2/B2 关闭 |
| B1：PG 默认 root 初始化与 cap-drop ALL 冲突 | P1 | §6.3 明确非 root 用户、实际 UID/GID、tmpfs 与专属卷权限预检；A2/B2 关闭 |
| B1：B 批 coverage JSON 在临时容器退出后丢失 | 高概率 P2 | §6.2 同容器解析并输出计数，保留失败码；A2/B2 关闭 |
| B1：T05 缺显式 PASS/STOP | P3 | RF-T05 补齐持久完整性及环境停止条件；A2/B2 关闭 |
| A2/B2：临时 logical_step 含点，不符合真实 M04 正则 | P1 | D §6 改为 result_formatting_validate；双方复读关闭 |
| A2：Registry.prepare 只算大小，不实施容量限额 | 高概率 P2 | D §6 要求 M12 显式比较 size_bytes 后抛公共异常；双方复读关闭 |

共 3 项 P1、5 项高概率 P2、1 项 P3，均已修订并复核关闭；未发现 P0，无遗留 P0/P1/高概率演示 P2。没有为了审查改变 D-12-01～14，没有把测试未执行写成 PASS。主模型另核对真实 railway_station、中文 pace、组合餐次枚举、来源时间、单件容量、取消和 M13/M11 所有权边界。

主模型总审：D §2 的 14 项关键决定均已取得用户明确答复，无已知关键分歧；含独立写作授权共 15 题，低于 50 题。此统计只表示已识别关键决定达成一致，不代表草案批准或实现正确率。两份文档互相追踪到 RF 验收矩阵与阶段；引用存在性、四文件白名单、冻结文件未变、staged 为空及文档空白检查已核验。

用户设计批准：待批准；代码实施、Docker、pytest、coverage、迁移、MODULE-CLOSEOUT：NOT_RUN。延期记录见 [UF-12-001～003](VALIDATION.md)：天气、地图、孤立日块长期清理；历史 UF09/UF11 决定保持原状。

## 9. M12-DESIGN01 纯设计任务交付报告

本节按附件 A §二十一记录本次事实，不授予未来实施权限。所有 NOT_RUN 均因本轮仅授权文档设计与静态审查，不是省略应执行的代码验收。

```text
任务：M12-DESIGN01
最终状态：草案完成，待用户批准
实现状态：NOT_RUN；未创建生产包或测试
模块收尾状态：MODULE-CLOSEOUT NOT_RUN，属于未来独立实施阶段

Git：
- 开始分支：main
- 结束分支：feature/module-12-result-formatting-design
- 开始 HEAD：f30adf51e05a99eb7df58e406caf3636276e7929
- 结束 HEAD：f30adf51e05a99eb7df58e406caf3636276e7929
- HEAD subject：docs: record module 11 local merge
- 工作区开始状态：clean
- 工作区结束状态：仅四份授权文档 dirty，两新建、两修改
- staged 文件：无

引用审计：
- 指定引用：用户任务、附件 A、grilling、主设计/路线图/共享规则、01/04/07/08/09/10/11、状态与验收记录及真实公共代码/合同测试
- 实际读取：用户任务、附件 A、grilling 全文；其余规范按指定及相关章节，代码按公共符号/字段/调用链/对应测试审计
- 缺失必读引用：无；新文件目标与必读引用已区分
- 允许创建目标：docs/12_result_formatting.md、docs/12_result_formatting_vibe_coding.md，已创建
- AGENT.md/AGENTS.md：已确认本地不存在，按用户要求不重复搜索、不因此停止；会话所给指令已遵守

冻结设计文档：
- 文件：下方哈希清单及其他既有冻结规范
- 是否修改：否；路线图只执行用户明确授权的 M12 depends_on 加 08
- 前后 SHA-256：下方统一 Git blob 口径，前后一致
- 与实现一致性：本次消费的公共合同已静态对照；未宣称全仓实现资格通过
- 未经批准的既有设计变更：无；M12 新草案仍待批准

代码变更：
- 新建：docs/12_result_formatting.md、docs/12_result_formatting_vibe_coding.md
- 修改：docs/IMPLEMENTATION_ROADMAP.md（仅 M12 依赖一行）、docs/deferred_findings/unresolved_findings.md（仅追加本轮）
- 白名单外文件：无
- 迁移：NOT_RUN；没有新增/修改迁移
- 依赖变化：仅路线图声明增加 M08；pip/lock 文件未改

模块完成同步：
- MODULE-CLOSEOUT：NOT_RUN
- README / IMPLEMENTATION_STATUS / Dockerfile / compose.yaml / .env.example：未改，本轮未授权；未来影响与白名单见 §5
- 其他说明文件：仅上述四份授权文档
- 过期内容扫描：本轮草案及关联边界已核对；不据设计完成改写模块实现状态

Docker：
- runtime 构建 / test 构建 / 镜像 ID / Python / Pydantic / 关键依赖运行版本：NOT_RUN
- runtime 默认 CMD：仅只读检查现有配置，未运行
- 容器自动移除 / 残留容器：本轮没有创建容器或 Docker 资源

测试：
- 定向 unit / contract / integration / 当前模块完整测试 / 全量离线回归：NOT_RUN
- failed / skipped / xfail：不适用，未执行测试
- 未运行 Gate：全部实施 Gate 与 MODULE-CLOSEOUT

coverage：
- line / branch / combined：NOT_RUN

静态检查：
- trailing whitespace：通过
- git diff --check：tracked 变更无错误；两新文档另以 no-index --check 和逐行空白核验，无错误
- compileall：NOT_RUN
- pycache：本轮无 Python 执行，无新增
- 禁止依赖：规范与导入白名单已静态核对，未创建生产代码
- 文件白名单 / staged / 冻结文档变更扫描：通过

文档逐条对照：
- 主设计 / 权威设计 / Vibe Coding / 路线图 / 公共合同：完成本轮设计范围静态追踪
- 合同差异：已发现九项审查问题均修订；无已知关键未决

独立审查：
- P0：0
- P1：3，全部关闭
- P2：5，全部关闭
- P3：1，已关闭
- 修复及重跑：完成两轮只读交叉复核；没有运行或声称重跑代码测试

外部操作：
- commit / commit hash / merge main / push / PR / deploy：未执行，NOT AUTHORIZED

其他项目：未操作；没有访问或修改 process_simulation-platform-dev
剩余问题：M12 草案待批准；UF-12-001～003 见延期清单；未来 M13 保存/恢复接线待其自身设计
提交资格：NOT AUTHORIZED
模块最终完成资格：未达到——本轮未实施代码
```

冻结文件 SHA-256 校验口径：先比较当前文件经 Git 规范化后的 blob ID 与开始 HEAD 的 blob ID，再对相同 blob 原始字节计算 SHA-256；不混用工作区 CRLF 与仓库 LF。下表每行的值同时是开始基线与结束内容的 SHA-256。

| 既有文件（docs/） | 前后相同 SHA-256 |
|---|---|
| 00_agent_master_design.md | 81ab8605db6edc95f42409ab2c735e5a0b51f7fbd7d46418712d7520131648dd |
| 01_requirement_and_dialogue.md | ce392c47966212ba3001dee3ad9fe29e6ee3593b4e786dbdfff98a1a146299f5 |
| 04_workflow_state_and_artifacts.md | 4bfcd561a1e885b9f769cd13038374f290e84cd0bc771a0c9a61656a3ca3ee61 |
| 07_hotel_candidate_selection.md | eae97dee41b61010ab3fe5d6b21451b7c51d2ba6bbe0de8b76a08fc685504ee5 |
| 08_candidate_research_loop.md | 8e13316788535c95224d86db851f62584c483c0151a62af472a72e4dbed79c7c |
| 09_itinerary_planning_loop.md | 7a7bc1375fa507f33151e71240af2954b64a620e5a63be0f31871a048673afc3 |
| 10_artifact_persistence.md | 9c23035df78dcee0a7ec4337dc57139561946f42ce813fb0f0fe43ae3d3544b4 |
| 11_durable_execution_and_recovery.md | 54a211c24bcc1792eef667267c5aea963b57b699a3837043d40323742e9d63e1 |
| 11_durable_execution_and_recovery_vibe_coding.md | bb3770dfcffea17a7b1c32155273f0007f5c0307f2e24ff137887b03175050e2 |
| SHARED_IMPLEMENTATION_RULES.md | 18cf847525d6fcd57476bf0c126921e72d6c7e5b717b09e83db82e8ce02bfeac |

2026-09-28 用户批准 M09 备用点可选：按 D §7.3 读取 ModelAction9.0，只展示实际备选，0/部分备选不拒绝输出。补充三天无备选日块/索引保存与读取回归，保留已有备选证据反测。无其他输出 schema 或 SQL migration 变化。

## 公交乘车步骤增量执行规范（2026-09-28）

用户已批准独立分支实现静态公交乘车顺序。以同模块设计文档末节“公交乘车步骤扩展”和 [实施记录](VALIDATION.md) 为本增量权威范围；旧文禁止保存/展示全部instruction的要求仅对非公交步骤及未批准字段继续生效。持久模型继续全部必填、strict/frozen/extra-forbid。必须核验旧数据兼容、选中方案绑定、步骤原序、缓存与落库后读取、危险文本转义及工具结果不含步骤。不新增模型调用，不自动提交、合并或发布。


## 本次新版来源与结果维护入口（2026-09-30）

原设计§4～7及“本次新版规划消费”要求itinerary/6.0和result日块/索引2.0；原M09执行规范§29/W02白名单内同步Ref/Registry/DTO及fixture。没有旧结果兼容或转换；保留中文/公交线路步骤和原Gate。测试按AC-06/12，不能仅改版本fixture然后声称主链通过。


## M09参数修复消费执行入口（2026-10-06）

设计为M12本次版本消费增量及M09 §26；阶段、白名单、允许操作、测试、STOP和报告按[E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)，不得更改结果模板或版本。
## 2026-10-11 必要版本读取执行增量

按[本模块同日设计增量](12_result_formatting.md)、[M08 A契约](08_candidate_research_loop_vibe_coding.md)及[M09 B契约](09_itinerary_planning_loop_vibe_coding.md)的分支/白名单/操作/测试/STOP/报告实施，仅扩展已批准来源版本读取。新旧结果分别验证字节/hash、费用未核验和来源完整；不改UI或新增事实。


## 2026-10-11 批准执行入口：研究合同修复

执行本设计同日R条款及 [M08 R1～R6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约拥有阶段目标、前置/分支、联合文件白名单、允许操作、测试矩阵、停止条件和最终报告。R1/R2、R3/R4、R5/R6每组结束两个子agent逐条对照文档审查。未经新的提交授权保持未暂存；禁止push/PR/部署。

## 2026-10-12 必要资格判断执行入口

执行本设计“必要资格判断接线”和 [M08 Q08合同](08_candidate_research_loop.md)、[Q1～Q6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约明确分支/HEAD、既有变更、每组目标/前置、生产及测试白名单、允许操作、统一测试、停止条件和报告字段。每两阶段统一测试后两个子agent逐条对照文档；全部离线通过后隔离三城各一次真实API，记时间及token。不得提交、推送、PR、部署或更新既有服务。

在同一Q5/Q6白名单内落实精确来源读取，并复用M08纯来源核验入口。增加缺失Judgment3、当前4引用旧模型版本、删除判断或行动目录、地图与行动不一致、肯定标签缺来源、错步骤、错自身摘要、伪造覆盖及recovered最终接受的反例；兼容性fixture保存完整合法行动和判断来源，不以空资格目录代替来源证明。原1/2/3字节与精确读取回归保持。

补跨候选搜索及详情归属、首次接收和初始身份反例，同步重算判断、证据摘要、覆盖及Ref/hash后仍须由公共来源核验和M12拒绝。另验证合法自身详情、无结果和失败详情的行动UID绑定、容量拒收观察及后续真实冲突；不能用候选自行声明的observation_indexes代替地图实际身份。

## 2026-10-13 分钟规划读取合同

依据[M09设计§29](09_itinerary_planning_loop.md)与[执行规范T5＋T6](09_itinerary_planning_loop_vibe_coding.md)，新规划消费itinerary/6.0、model_action/9.0、路线快照3.0与分钟时间快照2.0，预算和已有职责保持。新父链、来源目录、类型/version/hash及实际字段必须一致；当前来源须满足十分钟交通预留及分钟时刻规则，禁止混用不同版本记录。结果层沿用北京时间和原始地图耗时/按10分钟向上取整的交通占用的分别展示，工作流不增加预算或排程工具。历史测试行程兼容不纳入本次验收，无数据库表迁移。

## 每日规划质量结果执行合同

引用[D12“每日规划质量结果合同”](12_result_formatting.md)、[D09 §30](09_itinerary_planning_loop.md)及[E09 §32](09_itinerary_planning_loop_vibe_coding.md)。分支、前置、联合精确白名单、允许操作、UD3～UD6阶段、测试矩阵、STOP和报告以E09为准。本模块不独立授权提交、发布或追加真实调用。

实现来源8.0与v4模板精确映射，允许读取action11.0，严格保持旧7/v3及6/v1/v2的数据和模板字节。仅新模板使用所有日期的提醒措辞；M12不计算质量、不放宽源核验、不消费Incomplete。定向证明版本混配拒绝、已有公交步骤及安全转义保持、新质量提醒和计数、旧golden不变；使用全新专属PostgreSQL执行既有迁移并保存、跨进程重读一致；浏览器明确区分结果展示夹具与真实主图。所有Gate及两名新代码审查通过后才进入E09的唯一一次真实任务。

## 研究纠错来源读取执行合同

依[D12“研究纠错来源读取合同”](12_result_formatting.md)、[D08 §31/E08 §30](08_candidate_research_loop_vibe_coding.md#30-c1c6-研究纠错与成果交付执行规范)执行C1～C6的必要来源接线；目标、前置、分支、白名单、允许操作、矩阵、STOP与报告归E08。按Ref版本读取旧四版和新候选5并核验attempt来源、主记录最终一致及预算，不新增格式化模型/地图调用，不改Markdown模板和发布门槛。验证新来源保存/另进程打开、伪造链拒绝、旧bytes/hash/Markdown不变。当前授权的唯一真实任务由C6统一执行。
