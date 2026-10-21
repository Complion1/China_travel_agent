# 06 百度地图 Gateway Vibe Coding 精确执行契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 状态：已批准并冻结（2026-08-19）。本文件与 <code>docs/06_baidu_map_adapter_and_mcp_gateway.md</code> 一起冻结。
> 本文件规定未来如何实施和验收，不授权当前文档任务实施代码、运行 live、提交或合并。

## 1. 必读引用与冲突规则

每个实施、修复、续执行、复核和 MODULE-CLOSEOUT 提示词都必须引用并逐条对照：

1. <code>docs/00_agent_master_design.md</code> 第 4、6、7、8、9、10 节；
2. <code>docs/IMPLEMENTATION_ROADMAP.md</code> 模块 06、WP3、M2；
3. <code>docs/06_baidu_map_adapter_and_mcp_gateway.md</code>，尤其第 1 节权威顺序；
4. <code>docs/SHARED_IMPLEMENTATION_RULES.md</code>；
5. <code>docs/04_workflow_state_and_artifacts.md</code> 第 5、6.4、8～13、16、18～21 节；
6. <code>docs/04_workflow_state_and_artifacts_vibe_coding.md</code>；
7. <code>docs/05_requirement_subgraph.md</code> 第 11、12、15、19 节；
8. 路线图 M2、<code>docs/05_requirement_subgraph.md</code> 第 19 节，以及模块 06 权威设计第 23.3 节；模块 05 Vibe Coding 只作为其自身实现约束，不声称含有独立 M2 章节；
9. <code>requirements.lock</code> 中 Python 3.12、Pydantic 2.13.4、MCP 1.28.1 和 langchain-mcp-adapters 0.3.0 的事实；
10. 百度地图和 MCP SDK 官方一手资料。

本清单只表示必读顺序，不另立权威层级；发生冲突时唯一采用模块 06 权威设计第 1 节顺序。验收必须逐条对照设计，不得只看测试是否通过。不得复制设计文档的大段正文；实施步骤引用精确章节和公共符号。

未来生成的每一份实施、修复、续执行、独立审查或下一阶段提示词必须完整放在同一个 Markdown 代码框中，不得输出依赖旧提示词的补丁或“其余沿用上一版”。

## 2. 执行模式

| 模式 | 适用范围 | 必须运行 | 精确 NOT_RUN |
|---|---|---|---|
| <code>full_rebuild</code> | 首次实现或结构性重建 | T00 → T01/T02 → CONFIG-MIGRATION → PROVIDER-CONTRACT-DISCOVERY → T03/T04 → T05 → LIVE-QUALIFICATION → 独立审查 → MODULE-CLOSEOUT | 无；缺失网络授权时分别停在 READY_FOR_PROVIDER_CONTRACT_DISCOVERY_AUTHORIZATION 或 READY_FOR_LIVE_AUTHORIZATION |
| <code>maintenance</code> | 已批准设计下的小修 | 受影响 Task、受影响定向测试、模块 06 全量 offline、模块 04 回归、完整 offline 回归；触发失效规则时重跑 live | 未触发的 live 标记 NOT_RUN，并说明为何既有资格未失效 |
| <code>requalification_only</code> | 代码和冻结文档不变，只重跑供应商资格 | Git/依赖/镜像核验、七项 live、需要时地图调起和 M2 live | unit/contract/coverage 只有存在同一基线的有效结果时可 NOT_RUN；否则停止，不得用历史未知结果 |

任何 NOT_RUN 必须写明 Gate、原因、前置证据和是否影响最终资格。NOT_RUN 不能写成 PASS。

## 3. Git 前置和续执行规则

目标分支固定为：

<code>feature/module-06-baidu-map-gateway</code>

每个阶段开始必须报告：

- 当前目录、分支、HEAD、HEAD subject；
- <code>git status --short</code>；
- <code>git diff --cached --name-only</code>；
- 本阶段允许的已有 dirty 文件；
- 冻结文档 hash；
- 目标文件是否存在。

规则：

- 本次 full_rebuild 的“干净基线”精确定义为：目标功能分支上只保留五份已批准但未提交的文档差异（主设计、路线图、模块 04 所有权措辞和两份模块 06 文档），除此之外无 dirty，且 staged 为空；文档批准不自动授权先提交；
- maintenance/续执行只允许看到前一已授权阶段报告中列出的预期 dirty；
- staged 必须为空；
- 未知 dirty、同名分支归属不明或白名单外文件立即停止；
- 禁止 reset、checkout 覆盖、clean、stash、删除用户文件或强制接管分支；
- 不得切回 main 实施；
- 不得修改冻结设计来匹配代码；
- 实施模型禁止 git add、stage、commit、merge、push、PR、deploy。

小范围、原因明确且完全处于当前阶段白名单的问题可直接修复；需要改变设计、扩大范围、升级依赖或大改时必须停止并征求用户同意。

## 4. 固定未来文件树

### 4.1 生产代码

~~~text
app/baidu_map/
├── __init__.py
├── adapter.py
├── capabilities.py
├── errors.py
├── limits.py
├── runtime.py
├── settings.py
├── _mcp_gateway.py
├── _response_parsing.py
├── _tool_registry.py
└── models/
    ├── __init__.py
    ├── common.py
    ├── geocoding.py
    ├── places.py
    ├── routes.py
    ├── weather.py
    └── map_launch.py
~~~

### 4.2 测试

~~~text
tests/support/
├── baidu_map_fake_mcp.py
└── run_module06_coverage.py
tests/unit/baidu_map/
├── test_models.py
├── test_settings.py
├── test_tool_registry.py
├── test_response_parsing.py
├── test_gateway_lifecycle.py
├── test_gateway_concurrency.py
└── test_security.py
tests/contract/baidu_map/
├── test_adapter_contract.py
├── test_public_api.py
└── test_import_boundaries.py
tests/integration/baidu_map/
├── test_m2_offline_vertical.py
└── test_m2_live_vertical.py
tests/live/baidu_map/
├── test_live_capabilities.py
└── test_live_map_launch.py
~~~

不得创建生产 fake、共享根 conftest、第二套 adapter、兼容层、生成器、供应商 schema snapshot、未来模块 stub 或原始响应 fixture。

### 4.3 配置和 closeout 文件

按阶段才可修改：

- T03/T04：<code>pyproject.toml</code>，仅把已锁定 <code>mcp==1.28.1</code> 声明为直接生产依赖；
- CONFIG-MIGRATION：<code>compose.yaml</code>、<code>.env.example</code>，并强制创建专用 live service；
- MODULE-CLOSEOUT：<code>README.md</code>、<code>docs/IMPLEMENTATION_STATUS.md</code>、<code>Dockerfile</code>；
- <code>requirements.lock</code> 全程只读，不升级依赖；
- <code>scripts/run_agent.py</code>、<code>scripts/probe_deepseek_runtime.py</code> 全程只读；没有模块 06 接线职责。

冻结且禁止修改：

- 主设计、路线图、共享规则；
- 模块 01～06 的权威设计和 Vibe Coding 文档；
- 模块 01～05 的代码和测试。

## 5. 全局实施约束

- Python、pytest、coverage、compileall 和正式依赖核验全部在 Docker 中执行；
- 每次构建使用从当前源码生成的唯一新 tag，记录不可变 image ID；
- 每次测试使用新容器、<code>--rm</code>、只读根、tmpfs、no-new-privileges、cap-drop ALL；
- offline 使用 <code>--network none</code> 且不注入 Secret；
- live 必须单独授权、独立命令、独立容器、只注入 BAIDU_MAPS_API_KEY；
- 不启动 PostgreSQL、本地 MCP Server 或其他项目服务；
- 不操作 <code>process_simulation-platform-dev</code> 的文件、容器、网络、卷或镜像；
- 不使用宿主机 Python 结果作为验收证据；
- 不复用旧容器、旧镜像测试结果或历史 coverage；
- 不得 skip、xfail、自动 rerun、弱化 strict/断言或使用 sleep 证明并发。
- 模型声明顺序只用于 Pydantic/schema/model_dump 展示；模块 06 禁止创建 canonical serializer 或计算 Artifact bytes/hash，持久化时只能由下游使用模块 04 第 9 节 <code>sort_keys=True</code> canonical 算法和 1 MiB 边界。

PowerShell 任务变量使用模块专用名称，例如 <code>$module06Tag</code>、<code>$module06ImageId</code> 和 <code>$module06Container</code>；不得复用 HOME、home 或 CODEX_HOME。

## 6. T00：只读事实与实现前停止门

### 6.1 目标

在修改代码前重新证明仓库、依赖、SDK、官方能力和上游合同仍与设计兼容。

### 6.2 前置条件

- 用户已批准两份模块 06 冻结文档；
- 当前位于目标功能分支；
- 工作区精确包含第 3 节定义的五份已批准、未提交文档差异，staged 为空；不得为满足“clean”自行提交文档；
- 未取得 live 授权也可执行 T00，但不得连接百度。

### 6.3 必读

- 第 1 节全部引用；
- <code>app/workflow_contracts/__init__.py</code>；
- 模块 04/05 与 M2、OperationId、Artifact、预算和 UtcClock 相关代码/测试；
- <code>pyproject.toml</code>、<code>requirements.lock</code>、Dockerfile、compose、env example；
- 锁定 <code>mcp==1.28.1</code> 安装包源码中的 ClientSession、initialize、list_tools、send_request/CallToolRequest、streamable HTTP 和 CallToolResult；
- 百度官方快速开始、功能介绍、更新日志和官方 GitHub。

### 6.4 文件白名单

只读；不得创建或修改任何文件。

### 6.5 允许操作

- Git、rg、文件读取、hash；
- 从当前源码构建一次临时 test image；
- 在无 Secret、无需连接百度的容器中打印 Python/依赖精确版本并读取安装包源码；
- 不得运行 live、不得读取 <code>.env</code>。

### 6.6 必须证明

- Python 为 3.12.x，Pydantic 2.13.4，MCP 1.28.1；
- 无需升级依赖；
- 官方托管 endpoint 和 Streamable HTTP 仍有一手依据；
- 七项核心公开 tool name 仍有官方依据；
- 托管 schema 尚需 provider discovery 的部分被识别为后续安全门，不被猜测；
- 模块 04 包根公开 UtcClock，模块 05 不是直接依赖；
- 当前无模块 06 旧实现需要迁移；
- 遗留 URL 只作为待删除配置事实存在。

### 6.7 Docker 命令基线

~~~powershell
$module06T00Tag = "china-travel-agent:module06-t00-<唯一标识>"
docker build --target test --tag $module06T00Tag .
docker image inspect $module06T00Tag --format "{{.Id}}"
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T00Tag python -c "import sys,pydantic; from importlib.metadata import version; print(sys.version); print(version('mcp')); print(pydantic.__version__); assert version('mcp') == '1.28.1'"
~~~

### 6.8 PASS

所有事实与冻结设计兼容，未发生外部调用，工作区未变化。

### 6.9 STOP

版本不符、需升级依赖、官方事实冲突、核心 tool name 无官方依据、模块 04 合同变化、未知 dirty/staged 或任何 Secret/live 需求。

### 6.10 报告

报告分支、前后 HEAD、镜像 ID、版本、完整读取、官方 URL、差异、文件变化（应为零）、Docker、live=NOT_RUN、commit/push/PR/deploy=未执行，状态只能是 READY_FOR_T01_T02 或 BLOCKED。

## 7. T01/T02：公共模型、错误、limits、Protocol 与 settings

T01 和 T02 必须在一份实施提示词中连续完成，然后统一构建和测试。

### 7.1 阶段目标

- T01：实现第 7～14、18 节的 strict/frozen DTO、枚举、错误和 limits；
- T02：实现第 15、17 节的核心/可选 Protocol、settings，以及不依赖尚未创建 runtime/registry 的阶段性包根投影；最终包根 API 在 T03/T04 收口。

### 7.2 前置条件

- T00 为 READY_FOR_T01_T02；
- 分支和冻结文档 hash 未变化；
- 工作区只有五份已批准文档差异：主设计、路线图、模块 04 所有权措辞和两份模块 06 文档；
- staged 为空。

### 7.3 允许创建

~~~text
app/baidu_map/__init__.py
app/baidu_map/adapter.py
app/baidu_map/capabilities.py
app/baidu_map/errors.py
app/baidu_map/limits.py
app/baidu_map/settings.py
app/baidu_map/models/__init__.py
app/baidu_map/models/common.py
app/baidu_map/models/geocoding.py
app/baidu_map/models/places.py
app/baidu_map/models/routes.py
app/baidu_map/models/weather.py
app/baidu_map/models/map_launch.py
tests/unit/baidu_map/test_models.py
tests/unit/baidu_map/test_settings.py
tests/contract/baidu_map/test_adapter_contract.py
~~~

其他文件只读。不得提前创建 Gateway、registry、parser、runtime、fake、live 或 M2 文件。

### 7.4 实施要求

- 逐个实现本阶段白名单可承载的精确符号、字段顺序、union discriminator、容量和错误码；不得为未到阶段的 runtime/registry 创建 stub 或把符号错放到其他文件；
- 本阶段 <code>app.baidu_map.__all__</code> 是权威设计第 6 节最终有序 tuple 删除 <code>MAP_BINDING_MANIFEST_VERSION</code>、<code>BaiduMapRuntime</code>、<code>create_baidu_map_runtime</code> 后的精确有序投影；这些名字不得提前导出。其余已实现公共导入只从包根暴露；
- <code>test_adapter_contract.py</code> 本阶段只验证七方法 Protocol、可选 Protocol、请求/结果类型和错误端口，不得要求 runtime；最终精确 <code>__all__</code>、完整包根导入与 import-boundary 测试固定推迟到 T03/T04；
- settings 与 DeepSeek 模式一致，但错误类型归模块 06；
- 显式 Mapping 测试不得触发真实 env/.env；
- 遗留 URL 键存在即失败，真实 Secret 不进入 repr/str/args；
- Protocol 不接收 OperationId、ArtifactRepository 或业务预算；
- 禁止宽泛 Any、可变集合和供应商对象。

### 7.5 统一测试

~~~powershell
$module06T02Tag = "china-travel-agent:module06-t02-<唯一标识>"
docker build --target test --tag $module06T02Tag .
docker image inspect $module06T02Tag --format "{{.Id}}"
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T02Tag python -m pytest -q -p no:cacheprovider tests/unit/baidu_map/test_models.py tests/unit/baidu_map/test_settings.py tests/contract/baidu_map/test_adapter_contract.py
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL --env PYTHONPYCACHEPREFIX=/tmp/module06-pycache $module06T02Tag python -m compileall -q app/baidu_map
~~~

### 7.6 PASS

定向测试零 failed/skipped/xfail，compileall 通过，阶段性 <code>__all__</code> 与上述精确投影一致且三个延后名字不存在，安全扫描无 Secret/raw/MCP 泄漏。不得把本阶段 PASS 报告为最终公共 API 已完成。

### 7.7 STOP

需要改变字段/版本/坐标/错误，需新增依赖，需导入 MCP 或模块 05，测试需访问网络/.env，或白名单不足。

### 7.8 报告

包含分支、前后 HEAD、修改文件、公共符号、测试命令/计数、镜像 ID、异常文件、迁移=NOT_RUN、provider discovery=NOT_RUN、live=NOT_RUN、commit/push/PR/deploy=未执行，状态 READY_FOR_CONFIG_MIGRATION 或 BLOCKED。

## 8. T03/T04：binding、解析、Gateway、runtime 与完整 offline 合同（固定在第 10 节发现门之后执行）

T03 和 T04 必须在一份实施提示词中连续完成，然后统一运行全部模块 06 offline 测试。

### 8.1 阶段目标

- T03：实现权威设计第 16、19 节的 binding manifest、tools/list schema 校验和响应解析；
- T04：实现第 20、21 节的官方 SDK Gateway、runtime、session、timeout、retry、取消、并发和资源释放。

### 8.2 前置条件

- CONFIG-MIGRATION 为 READY_FOR_PROVIDER_CONTRACT_DISCOVERY_AUTHORIZATION，且 PROVIDER-CONTRACT-DISCOVERY 为 READY_FOR_T03_T04；
- 已有 dirty 与 T01/T02、CONFIG-MIGRATION 报告完全一致；
- 冻结文档 hash 不变、staged 为空；
- 已有 discovery 的纯结构证据；实现和全部测试仍必须离线完成。

### 8.3 允许创建或修改

~~~text
app/baidu_map/_tool_registry.py
app/baidu_map/_response_parsing.py
app/baidu_map/_mcp_gateway.py
app/baidu_map/runtime.py
app/baidu_map/__init__.py
pyproject.toml
tests/support/baidu_map_fake_mcp.py
tests/support/run_module06_coverage.py
tests/unit/baidu_map/test_tool_registry.py
tests/unit/baidu_map/test_response_parsing.py
tests/unit/baidu_map/test_gateway_lifecycle.py
tests/unit/baidu_map/test_gateway_concurrency.py
tests/unit/baidu_map/test_security.py
tests/contract/baidu_map/test_adapter_contract.py
tests/contract/baidu_map/test_public_api.py
tests/contract/baidu_map/test_import_boundaries.py
tests/integration/baidu_map/test_m2_offline_vertical.py
tests/integration/baidu_map/test_m2_live_vertical.py
tests/live/baidu_map/test_live_capabilities.py
tests/live/baidu_map/test_live_map_launch.py
~~~

<code>pyproject.toml</code> 只允许增加直接且精确的 <code>mcp==1.28.1</code> 项；不得改变其他依赖、Python 范围、pytest 或 coverage 配置。<code>requirements.lock</code> 不修改。

### 8.4 实施要求

- manifest 1.7 只包含七项官方公开 core tool name，并固定七项工具与实际参数名的绑定；
- registry 必须区分四项只发送 <code>is_chinese_mainland</code> 的 capability、天气只发送 <code>is_china</code>，reverse/matrix 两者均不发送；不得双发、别名、运行时猜测或动态切换；
- reverse schema 必须接受 latitude/longitude property 存在且为 number-compatible、但可不在顶层 required；只允许权威设计第 16.2 节精确受限的根 anyOf：根键封闭为 type/properties/可选 required/anyOf，两个互异非空分支均只能含 required，required 只能引用四个 number-compatible 坐标 property，且至少一个分支精确要求 latitude/longitude；lat/lng 不成为输入或 outbound alias；缺 property、类型冲突、未知 required、未批准根约束或畸形分支必须失败；DTO 仍强制有限值和范围；
- matrix/directions 的 model property 必须存在且为 string-compatible；enum 完整时逐项校验固定 mode，enum 完全缺失时由独立 PROVIDER-CONTRACT-DISCOVERY Gate 负责固定 mode reinforcement；Gate 通过后，生产 registry 对已资格化 binding 直接兼容，不产生 pending/reinforcement 状态；
- 创建 registry/runtime 后把包根 <code>__all__</code> 从 T01/T02 阶段投影原位补入 <code>MAP_BINDING_MANIFEST_VERSION</code>、<code>BaiduMapRuntime</code>、<code>create_baidu_map_runtime</code>，达到权威设计第 6 节精确最终 tuple；本阶段创建并运行完整 <code>test_public_api.py</code>/<code>test_import_boundaries.py</code>，禁止保留 stub 或额外导出；
- 未知工具忽略；地图调起首版没有官方稳定名称时报告 unavailable，不猜测；
- 每个 startup attempt/ClientSession 到达发现阶段后恰好一个无分页 tools/list、nextCursor=None，七项核心唯一兼容才 ready；正常 startup initialize=1、tools/list=1、tools/call=0，enum 缺失不得触发 route probe；正常路径跨创建过程总计 1，只有首次 list 阶段发生获准瞬时错误/timeout 时，全新第二 session 可使总计为 2，但每 session 仍各 1，重试不得执行资格化 tools/call；
- fake 必须模拟 SDK 边界和内容块，不能直接返回项目结果 DTO；
- parser 逐项实现权威设计第 16.4 节经 discovery 确认的路径，严格执行 structuredContent 优先、唯一完整 JSON 文本回退和 block 拒绝；
- parser 只启用 search/matrix/directions 三个权威设计第 16.4 节 no_result allowlist；geocode/reverse/details/weather 的 <code>result=null</code>、缺失、空 object 或其他空形状必须响应错误，四个公共 no_result DTO 只作为版本化端口保留；search 必须先拒绝原始 results>50，再执行本地 result_limit；Place v3 search category 只读 results[].tag，Place v2 details category 只读 result.detail_info.tag；reverse POI 只读 uid/name/addr/point.x/point.y/tag，point.y→latitude、point.x→longitude，禁止 address/location alias；必须验证全部原始项后按原序投影前 20 个合格项，并证明第 21 项之后的畸形字段仍整体失败；nearby POI 全被丢弃时保留有效地址 success 和空 tuple；
- reverse binding 1.7 测试必须覆盖原始 POI 0、20、21 项及大于 20 的合格数组；完整项映射、缺核心证据逐项丢弃、旧 address/location alias 不启用、point x/y 仅 JSON number、x/y 经纬方向和范围、tag 可空、原序前 20 投影；必须先验证每项所有已存在白名单字段再决定是否因缺证据丢弃，同项“一个核心字段缺失、另一个已存在字段畸形”仍整体失败；被投影区间中的任一对象/type/text/坐标畸形必须整体失败，证明实现不是先切片再验证；
- Directions 的 walking/driving/riding 只接受单层 <code>routes[].steps[]</code>；transit 只接受严格双层 <code>routes[].steps[][]</code>。四种 mode 的供应商指令字段都精确为单数 <code>instruction</code>，拒绝复数 <code>instructions</code> 和其他 alias。walking/driving/riding 的 instruction 必须非空；transit 必须先按外层后内层原序验证全部原始 step、原始展平容量 1..128 和 instruction string 类型，随后才显式丢弃 strip 后为空的 step，剩余项保持原序并映射到公共 tuple，丢弃后为空则响应错误而非 no_result；不得先过滤再做容量/其他字段验证，不得伪造文本；四种 mode 都直接读取 route distance/duration，拒绝单层/双层混合、空洞、第三层及任一其他字段畸形；step 的 start/end location 坐标只在 Directions 路径接受 JSON number 或严格 ASCII 十进制 string 并规范化为有限、范围合法的 float，其他坐标路径不得复用；
- Weather 必须先对供应商原始 <code>forecasts</code> 0..7 项逐项验证字段、类型、日期严格递增且唯一、high>=low，再按供应商原序显式投影前 5 项；超过 7 项或第六/七项畸形均整体响应错误，不得先截断后验证；公共 DTO 容量仍为 0..5；
- 原始 response、schema、异常正文和 Secret 不得保存；所有底层异常只在 except 内分类并释放，退出 active exception 后才 raise 公共异常；除 CancelledError 外测试 <code>__cause__ is None</code>、<code>__context__ is None</code> 且 traceback/local 不可达底层对象；
- 工具调用只走官方 session 的受控 <code>send_request(ClientRequest(CallToolRequest(params=CallToolRequestParams(...))), CallToolResult, ...)</code>，证明不触发 SDK 隐式第二次 list/outputSchema 预解析；
- outbound 必须覆盖唯一 <code>ak</code> query、四项 <code>is_chinese_mainland</code>、天气 <code>is_china</code>、reverse/matrix 不带任一地域字段和 matrix 1×N/N×1/N×M 的 tuple 原序、<code>lat,lng</code>、ASCII U+007C 连接断言；
- 显式 <code>follow_redirects=False</code>，3xx 失败关闭；按权威设计第 16.5 节以 logger name 或锁定包 <code>record.pathname</code> predicate 覆盖 mcp/httpx/httpcore、root/client/raw message 的引用计数 guard，两个 runtime 重叠关闭和 DEBUG/INFO/异常 canary 不泄漏，同名应用路径反例不误杀且不覆盖应用配置；
- 每 runtime 一个 session；一个 Gateway 共享容量 3 semaphore；
- 启动整体只对瞬时连接/timeout 重试一次；必须测试正常 initialize=1、list 总计1、tools/call=0，以及首次 list 失败后两个 session 各 initialize=1/list=1、总 tools/call=0；每次 call 零重试；
- 每个 startup attempt 必须创建一个专用 SDK owner task；该 task 独占 transport/session context 的创建、enter、initialize/list、等待 close signal 和 exit，ready 只经私有 one-shot future 发布，owner 不执行 tools/call。retry 或 startup 取消必须先在原 owner 内同任务清理并 join，严禁创建者 task enter 后由 close task exit；必须用真实 AnyIO task group context 做任务亲和回归，精确断言 enter/exit 为同一 owner task 且不是调用方 task；
- 每能力总 timeout 包含 semaphore 等待和解析；
- CancelledError原样传播；<code>close_timeout_s</code>只作为 <code>aclose()</code>调用方的 public wait上限。首个 close原子建立唯一 absolute monotonic deadline并缓存唯一私有 close task；Gateway强引用且回收该 task异常，所有 waiter只 shield同一 task并等待同一 deadline的剩余时间，晚到 waiter不得重新获得完整期限，取消一个 waiter不得取消清理或其他 waiter；
- 唯一 close task在 public deadline前等待 in-flight自然归零并请求 owner正常退出；deadline到达时取消仍登记调用和 owner，同时让当前及清理期内后续 <code>aclose()</code>稳定抛 timeout，但不取消 close task。close task可在公开调用已经返回后继续等待 in-flight finally、semaphore恢复、owner同 task context exit和 join；禁止第二个 cleanup/context-exit task和跨 task <code>__aexit__</code>；
- 延迟清理期间必须保持 closing、拒绝新调用且零网络、继续持有日志 guard，不得提前 closed。只有 in-flight为空、semaphore全恢复、owner/context exit完成后才 closed并精确释放 guard；随后重复 close立即成功。永久拒绝协作的第三方 task只能使唯一 tracked close task保持 pending和 Gateway保持 closing，不能宣称强杀、零 pending或提前释放 guard；
- 测试必须覆盖清理跨过 public deadline、两个不同时进入的 waiter共享 deadline、waiter取消、owner task affinity、body异常/取消优先级、延迟完成后重复 close成功，以及 close task异常回收；在 deadline内完成的 owner close failure仍映射 session error，deadline后的延迟底层错误只被回收且不得覆盖已报告 timeout或产生未回收异常；
- 失败 session 按冻结分类标记 broken，不自动重连；正常 closing 时已登记 waiter 继续，broken 时已登记但未发送 waiter 原子重查后以 session error、零网络退出；
- runtime 工厂不得暴露 URL 或任意 tool call；
- M2 offline 使用真实模块 05/06 和 fake MCP，完成一次 typed call，不写 demo_ready；
- offline adapter-contract 覆盖 matrix 三 mode 与 directions 四 mode，并与 discovery 的 mode-specific 结构投影一致；matrix 三 mode 彼此同型，directions 的 walking/driving/riding 为单层加非空单数 instruction 同型，transit 为独立严格双层、单数 instruction string、全量验证后显式空白过滤合同；这些是正常能力调用合同，不是 runtime startup qualification；fake/registry 必须覆盖完整 enum、无 enum 的 binding 1.7 schema 直接兼容且无 pending/reinforcement、enum 值/类型冲突、reverse direct 与受限 anyOf 两种合格 schema、optional-required 合同和 unknown required；
- outbound 测试必须逐项断言 GEOCODING/PLACE_SEARCH/PLACE_DETAILS/DIRECTIONS 的 <code>is_chinese_mainland</code>、WEATHER 的 <code>is_china</code>，以及 REVERSE_GEOCODING/ROUTE_MATRIX 两者均不存在；
- 必须保留 future coverage：manifest version 精确为 1.7；reverse direct schema 与受限根 anyOf 正向均通过；anyOf 分支数量、分支键、required 类型/非空/去重、分支互异、坐标 property allowlist/number-compatible 以及精确 latitude/longitude outbound 分支逐项负向；其他 capability 或其他组合关键字仍失败关闭；reverse latitude/longitude property 存在但不在顶层 required 可通过、缺 property/类型冲突/未知 required 失败，lat/lng 不作为 reverse 输入别名；异常测试必须证明 code/capability/args 仍只读，同时 BaseException 原生 <code>__traceback__</code> 回填不会被业务不可变规则阻断；reverse adcode 仅在该路径接受非 bool integer 或完全匹配 <code>0|[1-9][0-9]*</code> 的 ASCII 十进制 string 并规范化为 0..999999 int，拒绝空白、正负号、小数、指数、前导零、越界，其他整数路径不得复用；Directions step 坐标 string 采用权威设计第 16.4 节唯一正则和规范化，拒绝空白、加号、指数、NaN/Infinity、范围外以及其他坐标路径 string；walking/driving/riding 覆盖单层 steps、单数非空 instruction 正向/负向；transit 覆盖严格双层、仅单数 instruction string、原始 1..128 容量和全部字段先验证、首/中/尾多个空白按原序过滤、全部空白失败、原始超限但过滤后合规仍失败、复数 instructions/混合/第三层/其他畸形负向；Weather 覆盖原始 0、1、5、6、7、8 项，验证全部 0..7 项后只投影前五项，并证明第六/七项畸形不会被截断掩盖；model enum 完整通过、无 enum 的已资格化 binding 直接兼容且不产生 pending/reinforcement、缺值或类型冲突失败；独立 discovery Gate 或最终 live 必须满足 matrix 三 mode 同型、directions 三个非 transit mode 单层/非空单数 instruction 同型、transit 独立双层/单数 instruction/显式空白过滤合同，任一 mode 失败/空结果/偏离其结构或未授权的外部 mode 均失败；
- live 文件可在本阶段创建并做 collection/静态检查，但不得执行 live。

### 8.5 统一 offline 测试

~~~powershell
$module06T04Tag = "china-travel-agent:module06-t04-<唯一标识>"
docker build --target test --tag $module06T04Tag .
docker image inspect $module06T04Tag --format "{{.Id}}"
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T04Tag python -m pytest -q -p no:cacheprovider tests/unit/baidu_map tests/contract/baidu_map tests/integration/baidu_map/test_m2_offline_vertical.py
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL --env PYTHONPYCACHEPREFIX=/tmp/module06-pycache $module06T04Tag python -m compileall -q app/baidu_map
~~~

必须另外用事件/屏障测试跨 capability、跨 run 的在途调用始终不超过 3；不得使用 wall-clock sleep 作为并发证明。

### 8.6 PASS

全部模块 06 offline 测试零 failed/skipped/xfail，七项行为和所有错误路径覆盖，compileall 通过，只有白名单文件变化。

### 8.7 STOP

托管 schema 必须靠猜测才能实现、需要连接百度才能让 offline 通过、官方 SDK 不支持已批准生命周期、直接依赖版本不符、需要修改上游或设计、或 raw payload/Secret 无法隔离。

### 8.8 报告

报告分支、前后 HEAD、全部文件、直接依赖变化、测试/镜像、session/list/call 计数、并发与关闭竞态证据、M2 offline、provider discovery 证据引用、live=NOT_RUN、异常文件、commit/push/PR/deploy=未执行，状态 READY_FOR_T05 或 BLOCKED。

## 9. CONFIG-MIGRATION：删除遗留 URL 的独立安全门

删除配置是独立 Gate，不与 T03/T04 混做。

### 9.1 前置条件

- T01/T02 为 READY_FOR_CONFIG_MIGRATION；
- 用户明确授权该删除 Gate；
- 真实 <code>.env</code> 不在 Git diff，且不得读取；
- staged 为空。

### 9.2 精确白名单

- <code>compose.yaml</code>；
- <code>.env.example</code>。

代码、测试、冻结文档和用户 <code>.env</code> 全部只读。

### 9.3 必须修改

- 从所有 Compose environment 删除 <code>BAIDU_MAPS_MCP_URL</code>；
- 从共享 <code>x-agent-environment</code>、offline <code>test</code> 和非百度 <code>live-test</code> 删除 <code>BAIDU_MAPS_API_KEY</code>，不得让它们继承或展开百度 Secret；
- 只有实际装配地图能力的 <code>runtime</code> 在其自有 environment 中显式加入 <code>BAIDU_MAPS_API_KEY=${BAIDU_MAPS_API_KEY:-}</code>；不得顺带改变 DeepSeek/PostgreSQL 的键名、默认值、service 或安全合同；
- <code>.env.example</code> 保留 <code>BAIDU_MAPS_API_KEY</code> 占位符；
- 从 env example 删除 URL 和“key/URL 分离”说明；
- 必须在 compose 新增 <code>baidu-map-live-test</code> service，合同固定为：<code>profiles: ["live"]</code>；build context 为项目根、target=test；image 为 <code>china-travel-agent:module06-live-${MODULE06_LIVE_IMAGE_TAG:-not-authorized}</code>；environment 只能含 <code>TZ=Asia/Shanghai</code>、<code>HOME=/tmp/module06-home</code>、<code>XDG_CACHE_HOME=/tmp/module06-cache</code>、<code>BAIDU_MAPS_API_KEY=${BAIDU_MAPS_API_KEY:-__BAIDU_MAPS_API_KEY_REQUIRED__}</code>；<code>read_only: true</code>；<code>tmpfs: /tmp:rw,nosuid,nodev,size=256m</code>；<code>security_opt: no-new-privileges:true</code>；<code>cap_drop: [ALL]</code>；
- 该 service 不得继承通用 environment/env_file，不得含 DeepSeek、PostgreSQL、<code>BAIDU_MAPS_MCP_URL</code> 或其他 Secret/URL；每次网络 Gate 必须从当前源码设置唯一 <code>MODULE06_LIVE_IMAGE_TAG</code> 并记录 image ID；
- 普通 <code>docker compose</code> 解析、offline service 和下游模块不得要求 live tag 或百度 Secret；网络 Gate 必须在 PowerShell 中先断言 tag 已设置、非 <code>not-authorized</code> 且只含 <code>[a-z0-9-]</code>，容器内 settings 再对缺失/占位 key 失败关闭；preflight 不读取或输出 key；
- 不得把 AK 写入镜像、命令、文档或 compose 展开结果；
- 报告用户应手工从真实 <code>.env</code> 删除遗留 URL，但不得代替用户修改。

### 9.4 验证

~~~powershell
$module06ConfigTag = "china-travel-agent:module06-config-<唯一标识>"
$module06ConfigForbiddenEnv = @(
    "DEEPSEEK_API_KEY",
    "BAIDU_MAPS_API_KEY",
    "MODULE06_LIVE_IMAGE_TAG",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_IMAGE",
    "PYTHON_IMAGE"
)
foreach ($module06ConfigEnvName in $module06ConfigForbiddenEnv) {
    if (Test-Path -LiteralPath "Env:$module06ConfigEnvName") {
        throw "BLOCKED_CONFIG_SHELL_ENV_PRESENT:$module06ConfigEnvName"
    }
}
docker compose --profile live --env-file .env.example config --quiet
docker build --target test --tag $module06ConfigTag .
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06ConfigTag python -m pytest -q -p no:cacheprovider tests/unit/baidu_map/test_settings.py tests/contract/baidu_map
~~~

静态检查必须证明：

- 在 Compose 解析前，只用 <code>Test-Path Env:&lt;name&gt;</code> 检查上列全部插值变量是否存在，不读取、保存、清除或输出值；任一存在即 STOP，要求用户在不含这些进程变量的新 clean shell 重跑；
- 通过存在性门后，<code>config --quiet</code> 只使用含占位符的 <code>.env.example</code>，成功时不得输出展开配置；禁止省略 <code>--quiet</code>、改用真实 <code>.env</code> 或由脚本暂存/恢复宿主值；
- compose 和 env example 不再出现遗留 URL；共享 anchor、offline test 和非百度 live-test 不再出现百度 key；只有 runtime 自有 environment 与专用 baidu-map-live-test 注入该 key；
- settings 与安全测试中仅保留“检测遗留键”的必要字面量；
- 没有读取/打印 <code>.env</code>；
- 除上述固定的 <code>--env-file .env.example config --quiet</code> 语法校验外，不得执行会向终端输出展开配置的 <code>docker compose config</code>。

### 9.5 PASS/STOP

PASS：两文件完成最小迁移、专用 live service 完整符合冻结 YAML 合同、测试通过、用户真实配置未触碰。
STOP：需要修改真实 <code>.env</code>、需要暴露 AK、需要改变 endpoint、出现白名单外修改或无法做到 Secret 隔离。

### 9.6 报告

必须把迁移写为“项目样例/Compose 已迁移；用户真实 .env 未读取、需用户手工清理”，并报告前后文件、专用 service 合同、测试、镜像、discovery/live=NOT_RUN、commit/push/PR/deploy=未执行，状态 READY_FOR_PROVIDER_CONTRACT_DISCOVERY_AUTHORIZATION 或 BLOCKED。

## 10. PROVIDER-CONTRACT-DISCOVERY：parser 前只读供应商合同发现

这是独立网络和 Secret 安全门，只解决“托管 tools/list 与响应字段路径能否支持冻结 parser”，不是最终资格。写作、Docker、配置迁移或最终 live 授权均不能替代本 Gate 的单独明确授权。

### 10.1 前置条件

- CONFIG-MIGRATION 为 READY_FOR_PROVIDER_CONTRACT_DISCOVERY_AUTHORIZATION；
- T03/T04 尚未开始，不存在供应商 parser；
- 用户明确授权 provider contract discovery；
- 根 <code>.env</code> 由用户提供有效 <code>BAIDU_MAPS_API_KEY</code>，专用 service 只注入该 key；
- staged 为空，冻结文档 hash 未变化。

### 10.2 文件与允许操作

仓库完全只读。允许在容器 <code>/tmp</code> 生成一个阶段内临时、退出即销毁的 discovery probe；不得把脚本、原始 JSON、schema、fixture、snapshot 或报告值写回仓库。只允许 initialize、恰好一次无分页 tools/list，以及权威设计第 23.2 节七个能力步骤：geocode A/B 各一次；reverse/search/details/weather 各一次；matrix 三种 mode；directions 四种 mode；总计十三个 tools/call。schema 阶段按 binding manifest 1.7 验证七项唯一工具、object 根、required/property/type、reverse latitude/longitude number-compatible property、受限根 anyOf 和未知 required；响应阶段按权威设计第 16.4 节执行 reverse POI 的 addr/point.x/point.y 封闭映射、全量验证后前 20 投影、reverse adcode、Directions step 坐标、mode-specific steps/单数 instruction/全量验证后 transit 空白过滤，以及 Weather 7→5 的封闭规范化；matrix/directions model 的 enum 若存在则校验固定 mode，若完全缺失则不失败并进入后续 3+4 个固定 mode 的正向 reinforcement。地图调起和 M2 不在本 Gate。

### 10.3 执行和脱敏证据

~~~powershell
$env:MODULE06_LIVE_IMAGE_TAG = "discovery-<唯一标识>"
if ($env:MODULE06_LIVE_IMAGE_TAG -notmatch '^discovery-[a-z0-9-]+$' -or $env:MODULE06_LIVE_IMAGE_TAG -eq 'not-authorized') { throw 'invalid module06 discovery image tag' }
docker compose --profile live build baidu-map-live-test
docker image inspect "china-travel-agent:module06-live-$env:MODULE06_LIVE_IMAGE_TAG" --format "{{.Id}}"
if ($module06DiscoveryProbe -cmatch '[^\x00-\x7F]') { throw 'module06 discovery probe must be ASCII-only' }
$module06DiscoveryProbe | docker compose --profile live run --rm --no-deps -T baidu-map-live-test python -
~~~

正式实施、修复、续执行、审查和下一阶段提示词不得粘贴完整 probe 源码或其他大段代码。提示词必须引用第 10.3 节，并明确阶段目标、initialize/tools/list/call 计数、固定输入边界、安全约束、脱敏要求、PASS/STOP 和报告字段；不得只写“按文档执行”。执行代理可在获准阶段按照本节和权威设计，在容器临时空间或标准输入中构造并审查一次性 probe；临时 probe 不得写入仓库，不得保存原始 schema、响应、Secret、fixture、snapshot 或 golden file。提示词中只允许保留完成阶段所需的短命令或命令骨架，不得嵌入完整脚本正文。禁止输出形式的 <code>docker compose config</code>、<code>printenv</code>、shell tracing 或输出异常正文。

临时 probe 的算法合同必须逐行等价于以下顺序，不得由实施者另行设计：

1. 在读取 Secret 或构造 endpoint 前，保存 logging 全局 disable 值、LogRecordFactory、根 logger/现有 handler，以及 <code>mcp</code>、<code>mcp.client.streamable_http</code>、<code>mcp.shared.session</code>、<code>httpx</code>、<code>httpcore</code> 和当时已存在子 logger 的 level/disabled/propagate/filters；从锁定 import spec 规范化三个包根。临时拒绝 predicate 与权威设计第 16.5 节相同：logger name 命中 namespace，或 <code>record.pathname</code> 位于锁定 mcp/httpx/httpcore 包根，不论 name 是否为 root/client；其他应用 root record 不匹配。安装 filter/record factory 后执行 <code>logging.disable(logging.CRITICAL)</code>，使本独立 probe 的全部标准日志均不可输出；finally 只恢复本 probe 实际改过的对象和属性，不覆盖原配置。
2. 从进程环境读取 BAIDU_MAPS_API_KEY，只在内存校验非空/非占位；失败只向 stderr 写固定 <code>BLOCKED_DISCOVERY_CONFIGURATION</code> 并以非零退出。禁止 repr/长度/hash/首尾字符。
3. 只在内存执行 <code>urlencode({"ak": api_key})</code>，向无 query/fragment 的固定官方 endpoint 追加恰好一个 <code>ak</code> 参数；禁止其他 query、重复键、认证别名或 fragment。创建 <code>httpx.AsyncClient(follow_redirects=False)</code>，connect/write/pool=10 秒，read 由外层期限承担；3xx、401/403/429 只输出固定分类码，不输出 URL/header/body/异常。
4. 通过官方 Streamable HTTP transport 和同一 <code>ClientSession</code> 在 15 秒外层期限内 initialize，再在 15 秒内调用一次 <code>list_tools()</code>；nextCursor 必须 None。原始 schema 只投影为 tool name、required/property/type/enum 后立即释放。
5. 每次工具调用都使用 <code>session.send_request(ClientRequest(CallToolRequest(params=CallToolRequestParams(name=..., arguments=...))), CallToolResult, request_read_timeout_seconds=timedelta(seconds=remaining_s))</code>，禁止高层 <code>call_tool()</code>、meta 和 progress callback；按 geocode=15、reverse=15、search=20、details=15、matrix=30、directions=30、weather=15 秒外层期限，零 retry，使用权威设计第 23.2 节固定输入和前一步内存结果，逐一覆盖 matrix walking/driving/riding 与 directions walking/driving/riding/transit；若 model enum 缺失，必须以 matrix 三 mode 彼此同型成功、directions 的 walking/driving/riding 单层 steps 加非空单数 instruction 同型成功、transit 严格双层 steps 加单数 instruction string 且全量验证后空白过滤成功完成资格；不得要求 transit 与前三种 mode 的 steps 嵌套层级相同，也不得接受复数 instructions；任一失败、空结果或偏离其 mode-specific 结构/字段立即失败，不得推断其他 mode；outbound 必须遵守第 16.4 节精确地域参数映射和 matrix ASCII U+007C 编码。
6. 对每个结果先执行 isError/content 边界，只在内存解析 structuredContent 或唯一完整 JSON TextContent；输出仅为 capability、block 类型组合、字段 path 和 scalar/array/object/null 类型，任何 array 元素以 <code>[]</code> 表示，不输出标量、长度、hash、错误正文或原始 JSON。
7. 任意取消原样传播；其他异常在 except 内只映射固定阶段分类码并清空原异常/请求/响应引用，退出 active exception 后再 <code>raise SystemExit(1)</code>，确保 cause/context 均为 None。finally 清空 endpoint/AK/CallToolResult/JSON/schema 引用，按 session→transport→HTTP client 顺序在 5 秒内尽力关闭，再恢复 logger。不得注册 traceback hook，顶层不得打印异常对象。

后续提示词只需引用第 10.3 节算法合同并逐项保留其边界、调用计数、脱敏、PASS/STOP 和报告要求；不得粘贴完整 here-string、probe 源码或其他大段脚本，也不得仅写“按算法实现”。

证据只允许记录：七个 tool name；required/property/type/enum；nextCursor 是否为 None；content block 类型组合；结构化字段路径以及每个节点是 scalar/array/object/null。null 只记录类型，不记录值。不得记录任何标量值、地址、坐标、UID、POI、路线、天气、完整 URL、AK 或原始/摘要 payload。

### 10.4 PASS/STOP/报告

PASS：binding manifest 1.7 与权威设计第 16.2 节以及第 16.4、19 节中本次观察到的正向路径完全匹配；reverse direct 或受限根 anyOf 完成封闭 schema 兼容校验，且 outbound 仍只有 latitude/longitude；reverse POI 完成 provider-addr-point/all-raw-validated/projected-first20 固定结论，reverse adcode 完成限定 string/ASCII-decimal/normalized-int 的脱敏规范化，Directions step 坐标完成 number 或限定 string/ASCII-decimal/normalized-float 的脱敏规范化；Weather 原始 0..7 项全部验证后显式投影前五项；tools/list 恰好一次且 nextCursor=None，geocode=2、reverse/search/details/weather 各=1、matrix=3、directions=4、总 tools/call=13；matrix 三 mode 结构相符，directions 三个非 transit mode 单层加非空单数 instruction 结构相符，transit 独立双层、仅单数 instruction string、全部原始 step/容量先验证、空白 step 显式过滤且过滤后仍非空；内存引用和容器退出后均无原始材料，状态 READY_FOR_T03_T04。报告必须明确本 Gate 未资格化任何 no_result；意外空结果直接 STOP，不能据此扩展 allowlist。

STOP：缺 key、网络/鉴权/配额失败、任何 schema/字段路径/type/unit/block 差异、分页、Secret 风险、需要保存原始响应或需要改冻结设计。供应商/环境失败报告 BLOCKED，不得顺手修改合同或把发现写成资格通过。

报告包含分支/HEAD、唯一 image tag/ID、binding manifest version=1.7、命令、initialize/list/call 计数、地域参数映射、reverse 根 schema 只记录 direct/受限-anyOf 固定类别、reverse POI 只记录 provider-addr-point/all-raw-validated/projected-first20 固定结论，reverse adcode 与 Directions step 坐标各自只记录固定输入类别/规范化类别而不记录值、Directions 指令只记录 all-modes=instruction、transit=raw-all-validated/blank-filtered/nonempty-result 固定结论、Weather 只记录 raw<=7/all-validated/projected-first5 固定结论、model enum 是否存在及 mode-specific reinforcement 结论、纯结构脱敏矩阵、原始材料未保存、仓库零变化、最终 live=NOT_RUN、commit/push/PR/deploy=未执行；不得包含动态地图事实、指令正文、空白项数量或 forecast 数量。

## 11. T05：coverage、Docker 与完整 offline 回归

### 11.1 前置条件

- T01～T04、CONFIG-MIGRATION 和 PROVIDER-CONTRACT-DISCOVERY 完成；
- 最终 live qualification 尚未运行；
- 代码、测试和配置 diff 均在固定树内；
- staged 为空，冻结文档 hash 不变。

### 11.2 文件白名单

只读验收；不得为了 coverage 修改文件。coverage 缺口回到对应实现阶段，以新的完整修复提示词处理。

### 11.3 新镜像与模块测试

~~~powershell
$module06T05Tag = "china-travel-agent:module06-t05-<唯一标识>"
$module06T05Container = "china-travel-agent-module06-t05-<唯一标识>"
docker build --target test --tag $module06T05Tag .
docker image inspect $module06T05Tag --format "{{.Id}}"
docker run --name $module06T05Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T05Tag python -m pytest -q -p no:cacheprovider tests/unit/baidu_map tests/contract/baidu_map tests/integration/baidu_map/test_m2_offline_vertical.py
~~~

### 11.4 Coverage 三指标

<code>tests/support/run_module06_coverage.py</code> 必须在一个 Python 进程内用 coverage API 启动 branch/source=<code>app.baidu_map</code>，调用 pytest API 执行 <code>tests/unit/baidu_map</code> 与 <code>tests/contract/baidu_map</code>，停止并把 JSON 写入 <code>/tmp/module06-coverage.json</code>，再计算 line、branch、combined；pytest 非零或任一指标低于 95.00% 都以非零退出。它不得吞 pytest 参数、重跑测试、修改源码或读取网络/Secret。

公式固定读取 coverage JSON <code>totals</code>：<code>line = 100 * covered_lines / num_statements</code>，num_statements=0 直接失败；<code>branch = 100 * covered_branches / num_branches</code>，仅当 num_branches=0 时定义为 100；<code>combined = 100 * (covered_lines + covered_branches) / (num_statements + num_branches)</code>，分母为 0 直接失败。三个阈值都用未舍入的整数分子/分母交叉相乘判断是否 ≥95%，不得先格式化；只在报告时使用 Decimal <code>ROUND_HALF_UP</code> 显示两位小数。

唯一 coverage 命令为：

~~~powershell
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T05Tag python tests/support/run_module06_coverage.py
~~~

不得只报告 coverage.py 的单一 total；line、branch、combined 必须分别给出两位小数且各自至少 95.00%。

### 11.5 上游与完整回归

~~~powershell
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T05Tag python -m pytest -q -p no:cacheprovider tests/unit/workflow_contracts tests/contract/workflow_contracts
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06T05Tag python -m pytest -q -p no:cacheprovider tests/unit tests/contract
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL --env PYTHONPYCACHEPREFIX=/tmp/module06-pycache $module06T05Tag python -m compileall -q app
~~~

还必须执行：

- <code>git diff --check</code>；
- trailing whitespace；
- public API/import boundary；
- 禁止依赖和宽泛 Any 扫描；
- Secret/raw payload/URL 泄漏扫描；
- pycache、staged、白名单和冻结文档 hash；
- 本项目模块 06 验收容器残留检查；
- 不得清理或操作其他项目 Docker 资源。

### 11.7 PASS/STOP/报告

PASS：模块、上游和完整 offline 全绿；三项 coverage 均达标；provider discovery 仍与当前冻结代码/manifest 相符；静态检查通过。
STOP：任一失败/skip/xfail、coverage 不足、镜像/基线不明、冻结文档变化、白名单外文件、discovery 失效或环境阻断。

实施模型最终状态只能是 READY_FOR_LIVE_AUTHORIZATION，不得提交。

报告必须包含分支、前后 HEAD、全部文件、迁移、镜像 tag/ID、全部测试计数、三项 coverage、provider discovery 引用、live/M2 live=NOT_RUN、异常文件、容器残留、commit/push/PR/deploy=未执行。

## 12. LIVE-QUALIFICATION：七项核心与 M2 live

这是最终网络和 Secret 安全门，必须在 T05 完整 offline/coverage 之后单独取得用户明确授权。Provider discovery 不能替代本 Gate。

### 12.1 前置条件与白名单

- T05 为 READY_FOR_LIVE_AUTHORIZATION；
- 用户明确授权百度最终 live；
- 本地根 <code>.env</code> 由用户提供有效 <code>BAIDU_MAPS_API_KEY</code>；
- 专用 service 只注入该 key，代码/测试/配置冻结，staged 为空；
- 仓库完全只读；发现供应商漂移不得顺手修改 parser、manifest、测试或设计。

### 12.2 唯一镜像与执行

~~~powershell
$env:MODULE06_LIVE_IMAGE_TAG = "qualification-<唯一标识>"
if ($env:MODULE06_LIVE_IMAGE_TAG -notmatch '^qualification-[a-z0-9-]+$' -or $env:MODULE06_LIVE_IMAGE_TAG -eq 'not-authorized') { throw 'invalid module06 qualification image tag' }
docker compose --profile live build baidu-map-live-test
docker image inspect "china-travel-agent:module06-live-$env:MODULE06_LIVE_IMAGE_TAG" --format "{{.Id}}"
docker compose --profile live run --rm --no-deps -T baidu-map-live-test python -m pytest -q -p no:cacheprovider tests/live/baidu_map/test_live_capabilities.py
docker compose --profile live run --rm --no-deps -T baidu-map-live-test python -m pytest -q -p no:cacheprovider tests/live/baidu_map/test_live_map_launch.py
docker compose --profile live run --rm --no-deps -T baidu-map-live-test python -m pytest -q -p no:cacheprovider tests/integration/baidu_map/test_m2_live_vertical.py
~~~

禁止输出形式的 <code>docker compose config</code>、<code>printenv</code>、输出环境或复用 discovery 镜像。七个能力步骤必须在一个顺序用例内使用权威设计第 23.2 节的精确固定请求，调用计数为 geocode=2、reverse/search/details/weather 各=1、matrix=3、directions=4、总 tools/call=13；每个 mode 的正向结构必须再次与 binding manifest 1.7 和 discovery 相符，reverse 根 schema 必须重新满足受限 anyOf、outbound 仍只使用 latitude/longitude，reverse POI 的 addr/point.x/point.y 封闭映射、全量验证后前 20 投影、reverse adcode、Directions step 坐标、mode-specific steps/单数 instruction、transit 全量验证后空白过滤，以及 Weather 全量验证后 7→5 投影必须重复验证；model enum 缺失时仍须逐一验证 matrix 三 mode 彼此同型、directions 三个非 transit mode 单层加非空单数 instruction 同型，以及 transit 独立双层、单数 instruction string、raw-all-validated/blank-filtered/nonempty-result 合同，不能只验证一个代表 mode、要求四种 directions mode 同层或接受复数 instructions。不打印/保存动态地址、坐标、UID、路线、指令正文、空白项数量、天气、forecast 数量、URL query、原始响应或底层异常，不自动 rerun。真实缺 key 必须失败，不能 skip。该正向 live 不资格化 no_result；意外空结果必须 STOP，不能把当前响应升级为合同。

地图调起无 binding 时单独报告 NOT_AVAILABLE；有 binding 时使用 A→B walking 并只验证安全 URL。M2 必须按第 23.3 节使用真实模块 05 interrupt/resume、确定性 fake domain service 和真实模块 06，confirmed 后恰好一个真实 geocode，其他 capability 计数为 0，不读取 DeepSeek key、不产生 demo_ready。

### 12.3 PASS/STOP/报告

PASS：七项核心全真实通过，M2 live 恰好一次 geocode；地图调起单独 PASSED 或 NOT_AVAILABLE；状态 READY_FOR_INDEPENDENT_REVIEW。

STOP：凭据缺失、鉴权/配额/网络失败、schema/tool/response drift、Secret 风险、需要改代码、发生额外 call，或 discovery 与当前结果不一致。环境/供应商失败报告 BLOCKED，不得报告代码失败或伪造 live 通过。

报告记录命令、唯一 image tag/ID、Python/MCP/Pydantic、binding version=1.7、七项脱敏 PASS、reverse schema=restricted-anyOf/outbound=latitude-longitude 固定结论、reverse POI 的 provider-addr-point/all-raw-validated/projected-first20 固定结论、reverse adcode 与 Directions step 坐标的固定规范化类别、Directions all-modes=instruction 与 transit=raw-all-validated/blank-filtered/nonempty-result 固定结论、Weather raw<=7/all-validated/projected-first5、每个 mode-specific 结构的重复验证、地图调起状态、M2 各 capability 调用计数、failed/skipped/xfail、资格有效性、仓库零变化及 commit/push/PR/deploy=未执行；不得记录 Secret/raw payload/动态地图事实、指令正文、空白项数量或 forecast 数量。

## 13. 独立代码审查与修复

最终 LIVE-QUALIFICATION 后由独立审查者逐条对照权威设计、Vibe Coding、上游合同和完整 diff，不得只看测试：

- 公共 DTO/Protocol/包根；
- 供应商和 MCP 隔离；
- binding/schema/parse 失败关闭；
- 错误优先级、redaction 和 raw payload；
- session、retry、取消、并发和资源释放；
- Artifact/OperationId/业务预算边界；
- offline/provider discovery/live/M2 的真实性；
- 配置迁移和 Secret 注入；
- 下游 07～15 空间；
- 测试是否覆盖文档，而非迎合实现。

每个 finding 使用文件、行号、P0/P1/P2/P3、问题、后果、最小修复、是否改变设计。若 P0/P1/P2 为零，独立审查报告状态固定为 <code>READY_FOR_MODULE_CLOSEOUT_AUTHORIZATION</code>。若存在 P0/P1/P2，回到一份用户授权的完整修复提示词，修复后重跑受影响测试、模块全量、coverage 和完整 offline；触发失效规则时重新取得授权并重跑 live；随后必须再次进行独立审查，不能由修复者自证闭环。只有重新审查 P0/P1/P2=0 才进入 closeout 授权门。

若 finding 要改变设计或扩大范围，停止并请求用户批准。实施/修复模型仍不得提交，最终只能 READY_FOR_INDEPENDENT_REVIEW。

## 14. MODULE-CLOSEOUT：模块完成同步与最终资格

MODULE-CLOSEOUT 是独立 Task。在它完成前不得把模块 06 标记 COMPLETE、提交最终代码或合并 main。

### 14.1 前置条件

- 代码和测试阶段完成；
- 独立代码审查完成且状态为 READY_FOR_MODULE_CLOSEOUT_AUTHORIZATION；
- 全部 P0/P1/P2 已修复；
- 模块测试、三项 coverage、完整 offline 和七项 live 资格有效；
- 用户明确授权 closeout；
- 冻结文档 hash 与批准版本一致；
- staged 为空。

### 14.2 精确白名单

- <code>README.md</code>；
- <code>docs/IMPLEMENTATION_STATUS.md</code>；
- <code>Dockerfile</code>。

只有审计证明 <code>compose.yaml</code> 或 <code>.env.example</code> 的已批准迁移不完整时，必须停止并请求新的精确白名单，不能顺手修改。生产代码、测试、pyproject、lock 和冻结设计只读。

### 14.3 固定顺序

1. 保存冻结文档 SHA-256；
2. 按真实实现同步 README 的模块范围、包根、文档索引、Docker、live、限制和下一阶段；
3. 按真实证据同步 IMPLEMENTATION_STATUS 的基线、分支、测试、coverage、镜像、live、已知限制及外部操作；
4. 检查 Dockerfile COPY、依赖、test target、默认 CMD 和 runtime self-check；
5. 默认 CMD 若仍只验证模块 01～05，更新为包含 <code>app.baidu_map</code> 并同步输出文字；
6. 从同步后的源码重新构建 runtime/test 新镜像；
7. 运行 runtime 默认 CMD；
8. 重跑模块 06、模块 04、M2 offline、三项 coverage 和模块 01～06 完整 offline；
9. 若 Docker/依赖/config 变化触发 live 资格失效，取得新 live 授权并重跑；仅 README/状态文字变化不冒充 live 重跑；
10. 复核冻结 hash、白名单、staged、pycache、diff、容器残留；
11. 独立审查 closeout；通过时只报告 <code>READY_FOR_COMMIT_AUTHORIZATION</code>，它不授权任何 Git 或外部操作；
12. 用户另行授权后才由独立审查者提交；
13. 用户另行授权后才可合并 main；
14. 之后才可报告模块最终 COMPLETE。

### 14.4 同步后 Docker 命令

~~~powershell
$module06CloseRuntimeTag = "china-travel-agent:module06-close-runtime-<唯一标识>"
$module06CloseTestTag = "china-travel-agent:module06-close-test-<唯一标识>"
docker build --target runtime --tag $module06CloseRuntimeTag .
docker build --target test --tag $module06CloseTestTag .
docker image inspect $module06CloseRuntimeTag --format "{{.Id}}"
docker image inspect $module06CloseTestTag --format "{{.Id}}"
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06CloseRuntimeTag
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06CloseTestTag python -m pytest -q -p no:cacheprovider tests/unit/baidu_map tests/contract/baidu_map tests/integration/baidu_map/test_m2_offline_vertical.py
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06CloseTestTag python tests/support/run_module06_coverage.py
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL $module06CloseTestTag python -m pytest -q -p no:cacheprovider tests/unit tests/contract
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL --env PYTHONPYCACHEPREFIX=/tmp/module06-pycache $module06CloseTestTag python -m compileall -q app
~~~

Coverage 必须从 closeout test image 在同一容器会话重算三指标，不得沿用 T05 数字。

### 14.5 Closeout PASS/STOP

PASS：状态同步真实、Docker 两镜像和默认 CMD 通过、同步后全部测试/coverage 通过、资格有效；冻结文档 SHA-256 与批准基线一致，除第 3 节五份批准但未提交的文档差异外没有新增或变化的冻结文档 diff；独立 closeout 审查通过并报告 <code>READY_FOR_COMMIT_AUTHORIZATION</code>。
STOP：文档夸大、需要修改冻结设计、测试/live 失效、Docker/环境阻断、白名单外修改或尚有 P0/P1/P2。

## 15. 每份未来正式提示词的固定字段

每份提示词必须保留：

1. 任务编号和执行模式；
2. 当前阶段目标；
3. 前置条件和上一阶段证据；
4. 工作目录、目标分支、开始 HEAD 和预期工作区；
5. 必读文档及精确章节；
6. 允许创建/修改的精确文件；
7. 禁止修改的文件；
8. 冻结设计 hash 和变更门；
9. 本阶段公共符号；
10. 按章节引用的实施步骤；
11. 允许的 Git、Docker、网络和 Secret 操作；
12. 唯一镜像 tag、image ID 和新容器要求；
13. 完整测试矩阵和命令；
14. 静态/安全/白名单检查；
15. PASS；
16. STOP；
17. 最终报告格式；
18. commit、merge、push、PR、deploy 的明确禁止或独立授权状态。

不得只写“按设计实现”，不得省略前置、白名单、测试或停止条件。相邻 T01/T02、T03/T04 原则上各用一份完整提示词并统一测试；删除和 live 安全门保持独立。

## 16. 提交、合并和外部操作边界

所有实施、修复、续执行、requalification 和 closeout 模型禁止：

- git add/stage；
- commit；
- merge main；
- push；
- 创建 PR；
- deploy。

实施、修复、续执行、requalification 和 closeout 实施者的最高状态是 READY_FOR_INDEPENDENT_REVIEW，提交资格固定为 NOT AUTHORIZED。只有独立 closeout 审查者在逐项通过第 14.5 节后可报告 <code>READY_FOR_COMMIT_AUTHORIZATION</code>；该状态仍表示 commit、merge、push、PR、deploy 全部 NOT AUTHORIZED，只允许用户据此决定是否另行授权提交。

只有同时满足以下条件后，独立审查者才可在用户明确授权下提交：

1. 文档已批准；
2. 代码和测试独立审查完成；
3. 全部 P0/P1/P2 修复；
4. MODULE-CLOSEOUT 完成；
5. 用户明确给出提交授权。

提交授权不等于 merge、push、PR 或 deploy 授权。

## 17. 最终报告固定格式

每个正式阶段最终报告至少包含：

任务：
- 编号、模式、目标、最终状态。

Git：
- 开始/结束分支、开始/结束 HEAD、HEAD subject；
- 开始/结束工作区、staged、异常文件。

文档对照：
- 权威设计章节；
- 主设计、路线图、共享规则；
- 模块 04、模块 05 M2；
- 合同差异和是否改变设计。

变更：
- 新增、修改、删除文件；
- 白名单外文件；
- 迁移；
- 依赖变化；
- 公共 API 和版本。

Docker：
- 构建命令；
- runtime/test tag 和不可变 image ID；
- 容器安全参数；
- 残留容器；
- 其他项目是否触碰。

测试：
- 每条命令、passed/failed/skipped/xfail；
- unit、contract、adapter-contract、integration/M2；
- 模块 04 回归和完整 offline；
- compileall 和静态检查。

Coverage：
- line、branch、combined；
- 是否分别达到 95.00%。

Live：
- 是否授权和运行；
- 七项 core；
- 地图调起 PASSED/NOT_AVAILABLE/NOT_RUN；
- M2 live 调用计数；
- 资格是否有效；
- 不得含 Secret/raw payload。

外部操作：
- commit、hash、merge、push、PR、deploy；
- 未授权项必须明确“未执行”。

最终资格：
- READY_FOR_Txx / READY_FOR_PROVIDER_CONTRACT_DISCOVERY_AUTHORIZATION / READY_FOR_LIVE_AUTHORIZATION / READY_FOR_INDEPENDENT_REVIEW / READY_FOR_MODULE_CLOSEOUT_AUTHORIZATION / READY_FOR_COMMIT_AUTHORIZATION / BLOCKED；
- 提交资格；
- 模块是否达到最终 COMPLETE。

## 18. 全局停止条件

任一阶段出现以下情况立即停止：

- 冻结设计或上游合同冲突；
- 需要猜测百度工具/schema/响应；
- 需要改变七项核心、BD-09LL、错误、retry、session 或并发；
- 需要新增/升级依赖；
- 未知 dirty/staged 或白名单外文件；
- 需要修改模块 01～05；
- 需要读取、打印或持久化真实 Secret/raw payload；
- 未经授权运行 provider discovery/live、提交、合并、push、PR 或 deploy；
- Docker/网络/供应商环境阻断；
- 测试必须靠 skip/xfail/rerun/弱化断言；
- 需要操作其他项目资源。

BLOCKED 报告必须精确说明文件、章节、符号、环境事实、已完成检查和恢复所需授权；不得用部分结果冒充 PASS。

## 2026-09-29 公交无路径限定映射（用户批准）

本节精确修订 §16.4/§18 及执行规范中 isError 一律 Protocol 的规则。仅 DirectionsRequest.mode=transit、CallToolResult.isError=true、structuredContent=None、唯一精确 TextContent 且正文完全等于 `API response error: no bus path` 时，直接映射 DirectionsNoResult；不伪造 status=0 响应，不把原始API的1001假装为MCP字段。其余错误仍按原合同处理，不做包含匹配、strip、正则猜测或模糊分类。匹配后的业务无结果不使会话 broken，不自动重试；同批其他请求正常执行。该例外由真实同路段公交/步行及原始 API 对照证实，见 [根因报告](VALIDATION.md)。

M09 已有 no_result 反馈和公共交通不可行时查询步行/网约车的 Prompt，不放宽路线证据或预算。地图恢复默认 max_in_flight=3（产品默认原本就是3，移除真实测试worker的1覆盖）。验收覆盖精确匹配、未知错误拒绝、非公交/混合信封拒绝、同会话并行及后续成功；从M09真实测试一次，通过即止，不自动追加。
