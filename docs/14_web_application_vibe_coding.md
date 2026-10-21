# Module 14：网页应用精确执行规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 配套权威设计：[14_web_application.md](14_web_application.md)，下文称 W14。
> 共享执行规则：[SHARED_IMPLEMENTATION_RULES.md](SHARED_IMPLEMENTATION_RULES.md)，下文称 S；依赖唯一真源：[IMPLEMENTATION_ROADMAP.md §2、§9](IMPLEMENTATION_ROADMAP.md)，下文称 R。
> 用户于2026-10-05批准W14及本文，并明确授权按文档实施M14整个模块、每个通过阶段使用两位子Agent核查、完成后等待提交。本次授权覆盖W00–W10及其白名单内离线Docker/隔离PG迁移与测试；原§2/§14为历史设计任务范围和记录。规范业务合同冻结，stage/commit/merge/push/PR/真实API/云部署未授权。
> 用户随后批准澄清选择表单、两份文档及网页问题合同增量，授权按§15实施并由原两位审查者核查。只解冻W14 D14-18/§8.1/§11.1/AC14-20及本文相应新增条款；历史W00–W10正文hash/资格不冒充本次维护资格，其他冻结边界保持。

## 1. 使用方式与前置条件

先读主设计 §1–4、§10–12，W14 全文、本文、S 全文、R §2/§9、IMPLEMENTATION_STATUS 当前末节。M14 直接依赖仍为 [13]，必读 M13 §4–15 和其执行规范 §2–9、当前维护入口；不另建机器可读依赖索引或上下文生成器。实际消费上游时只补读 W14 §3 表所列合同：尤其 M01 §6/§8、M02 §5–7/§12/§19、M04 §11–15/§17、M10 §7–14/§23–24、M11 §22、M12 公共结果读取与版本。旧测试快照不代替本次受影响回归。

实施顺序按W14 D14-17/§12.2：M14先交付本机浏览器可用应用并完成本机离线资格；M15另获API授权后先本机真实网页验收，通过后再按单独云部署授权上云。准备cloud配置不要求使用或登录腾讯云，也不替代本机入口；阶段、依赖和文件白名单保持。

实施必须在已批准 W14/本文的正文 hash 基线上开始；记录 hash、branch、HEAD、dirty/staged、上游公共导出、依赖和验证入口。发现实现与文档冲突先修白名单内的小问题；需要改变已确认设计、上游合同、分支或仓库范围时 STOP，不能为测试通过修改规范。设计/执行条款的引用不能只写“见文档”，须标具体节、阶段和 AC。

当前计划分支 feature/module-14-web-application。未来开工重新验证其实际 HEAD，不硬编码本轮 HEAD 作为未来现状；已有分支直接复用，不自动建 worktree、切分支或清工作区。未知 dirty/staged 必须先核对来源。保留既有 ../m13-live-evidence/，不得读其正文、整理、删除、暂存或用它做新样例。

## 2. 本轮文档任务精确白名单

路径以下均相对 <PROJECT_DIR>；Git 根 <WORKSPACE>。本轮只允许以下 12 个文件，禁止创建第三份 M14 报告或更改其他文件。

| 类别 | 路径 | 允许动作 |
| --- | --- | --- |
| 新建 | docs/14_web_application.md | 权威设计、决定表、AC、澄清与审查附录 |
| 新建 | docs/14_web_application_vibe_coding.md | 本文、阶段、白名单、测试矩阵与现场记录 |
| 同步 | README.md | M14入口、真实待实现状态、未来体验说明 |
| 同步 | docs/00_agent_master_design.md | 有界匿名公开Demo、同库摘要、职责例外 |
| 同步 | docs/IMPLEMENTATION_ROADMAP.md | 不改depends_on，仅同步已选框架、受限部署/历史职责 |
| 同步 | docs/04_workflow_state_and_artifacts.md | 已保存真实摘要的读回例外，DTO/预算不变 |
| 同步 | docs/10_artifact_persistence.md | 新云profile/公共只读容量合同 |
| 同步 | docs/10_artifact_persistence_vibe_coding.md | 上述增量的未来执行入口 |
| 同步 | docs/13_demo_workflow.md | 摘要读回/终态释放公共端口，图和预算不变 |
| 同步 | docs/13_demo_workflow_vibe_coding.md | 上述增量未来白名单与逐条验证引用 |
| 同步 | docs/IMPLEMENTATION_STATUS.md | 当前纯文档状态、授权/审查/未运行事实 |
| 同步 | docs/deferred_findings/unresolved_findings.md | 真实新审查延期项、已知问题处理条件；不把未实施修复写关闭 |

仅可只读源码和无敏感内容的说明以核验符号。允许 Markdown 相对链接/结构、白名单、git diff --check、git status/HEAD/staged 和文档独立审查；不读真实 .env、不运行服务、Python、测试、coverage、Docker、PG、迁移或真实供应商；禁止任何 Git 写操作、外部发布和云控制台变更。

## 3. 后续实施文件树与唯一公共符号

### 3.1 M14 文件树

下面路径是未来阶段按用户授权启用的白名单，不能据此提前写代码。不得以目录通配获得额外文件权限。

```text
app/web_application/
  __init__.py                 # W14§4/8公共导出，无副作用
  __main__.py                 # 显式生产入口，单worker，无reload/proxy headers
  models.py                   # W14§8严格DTO、fresh与联合验证
  errors.py                   # 零正文typed marker、有限安全HTTP映射
  settings.py                 # W14§4.2变量、显式Mapping解析
  security.py                 # 身份/CSRF/Origin、Key派生、AES、cursor
  keygen.py                   # 独占创建Key，只在显式命令执行
  store.py                    # 自有pool、六表SQL、事务与完整性
  service.py                  # Dependencies、RunBoundExtractionInvocationSink、单槽/控制
  presentation.py             # 上游DTO到安全页面投影，非业务校验
  summary.py                  # 唯一终态finalizer、保存/有界重试/释放
  assembly.py                 # 真实生产组合根和资源逆序清理
  api.py                      # FastAPI/lifespan/routes/body/Host/CSRF
  alembic.ini
  migrate.py                  # 显式迁移入口，不在启动调用
  migrations/env.py
  migrations/versions/m14_0001_web_application.py
  static/index.html
  static/task.html
  static/history.html
  static/samples.html
  static/site.css
  static/app.js
  samples/manifest.json
  samples/demo_trip.md
```

公开对象和 service 签名以 W14 §4/§8 为准；models 的 union/field 限制必须逐条落实，不用 dict[str,Any] 绕开。WebApplicationError 下增加 WebApplicationConfigurationError、WebApplicationDependencyError、WebApplicationUsageError、WebApplicationConflictError、WebApplicationCapacityError；marker 无用户正文/底层异常，公开包装 raise … from None。业务错误 code 由 api 固定映射，不给 marker 增添 arbitrary details。

WebMetadataStore(*,settings:WebApplicationSettings,pool:AsyncConnectionPool,key:bytes,clock:UtcClock) 只由组合根构造，不从 HTTP 创建；持有借用 pool，不负责关闭它。其行为分成 visitors、requests/owner、quota/admission、pause、summary/cursor 六个固定查询组。store.py 内部方法不是跨模块合同，不开放任意 SQL/表名接口；固定参数化SQL、UTC严格校验、同schema固定head验证、提交不确定安全分类必须能由公开 service 行为测试。密钥不进入 repr/日志/DTO。DurablePauseCredentialSink adapter 位于 security.py，借用 store；只暴露 W14 §5.3 的async store签名给 M11。

RunBoundExtractionInvocationSink、WebApplicationDependencies和WebApplicationService均放service.py，避免models反向导入service形成循环；models.py只定义HTTP DTO。sink签名/active ContextVar/已知和内生turn规则严格引用W14§3.1，dependencies显式注入该sink，M03使用同一实例而非直接diagnostics。W05才能导出这三个符号，W02不占位；public_contract在相应批次同步阶段内冻结导出。

### 3.2 上游与基础设施白名单

| 子范围 | 未来可修改/新建的精确路径 | 约束 |
| --- | --- | --- |
| M10容量查询 | app/artifact_persistence/capacity.py（新）、runtime.py、__init__.py | 仅 W14§3.2/M10§24；不改 Repository/SQL写入/迁移 |
| M13诊断释放 | app/demo_workflow/diagnostics.py | 仅 release_run、活动operation追踪和释放；不改 finish 算法、业务预算/终态/图 |
| 依赖/镜像 | pyproject.toml、requirements.lock、Dockerfile、.dockerignore | 明确新依赖及静态资源打包；不能全量升级旧依赖 |
| 本机/云复现 | compose.yaml、compose.cloud.yaml（新）、.env.example（新）、.gitignore | 显式m14-local运行profile和云覆盖/资源/只读Key/日志；本机Demo、测试、云卷独立，不接日常开发卷 |
| 测试入口 | scripts/verify_module14.ps1（新）、tests/support/run_module14_coverage.py（新）、tests/support/module14_browser_worker.py（新）、tests/support/module14_restart_worker.py（新） | 宿主PowerShell只调Docker，Python全在容器；后两个只在test镜像 |
| 收尾文档 | README.md、docs/IMPLEMENTATION_STATUS.md、docs/deferred_findings/unresolved_findings.md | 只据实际资格记录；W14与本文规范正文只读 |

原 Dockerfile runtime/test targets 保留生产/测试区分；未来可另加 browser-test target，浏览器依赖不得进入生产镜像。FastAPI/Uvicorn 加为直接依赖；HTTP/browser所需包仅补充必要 dev 声明及准确 lock。沿当前 Python3.12/Pydantic锁定版本验证兼容；本轮未选择新包版本，未来 W02 必须提交具体版本和解析diff，禁止借“安装最新”升级M02/M06或整个lock。新增浏览器库按官方支持的匹配浏览器镜像/版本锁定，不以临时下载未锁版本作为资格。

### 3.3 精确测试白名单

```text
tests/unit/web_application/__init__.py
tests/unit/web_application/fakes.py
tests/unit/web_application/test_models_settings.py
tests/unit/web_application/test_security.py
tests/unit/web_application/test_service_control.py
tests/unit/web_application/test_summary_lifecycle.py
tests/unit/web_application/test_assembly.py
tests/contract/web_application/__init__.py
tests/contract/web_application/test_public_contract.py
tests/contract/web_application/test_http_boundaries.py
tests/contract/web_application/test_upstream_composition.py
tests/integration/web_application/__init__.py
tests/integration/web_application/conftest.py
tests/integration/web_application/test_migrations.py
tests/integration/web_application/test_admission_store.py
tests/integration/web_application/test_pause_summary_restart.py
tests/integration/web_application/test_workflow_api.py
tests/integration/web_application/test_capacity_shutdown.py
tests/browser/web_application/__init__.py
tests/browser/web_application/conftest.py
tests/browser/web_application/test_demo_pages.py
tests/unit/artifact_persistence/test_capacity_snapshot.py       # 新
tests/integration/artifact_persistence/test_capacity_snapshot.py # 新
tests/unit/demo_workflow/test_diagnostics_release.py             # 新
tests/contract/artifact_persistence/test_public_api.py           # 既有，仅同步新增导出
```

typed fake仅位于测试目录，借用已批准测试公共支持接口；不得导入旧测试私有helper或修改现有不相关fixture/测试来降低门槛。上列唯一既有测试的修改范围是M10.__all__末尾追加ArtifactCapacitySnapshot的预期，保留其他导出顺序、工厂签名/默认limits及私有符号拒绝断言；未来W01明确授权才执行。其他既有测试若必须变化仍先报告精确范围。测试新生产API行为、跨事务/并发/安全/生命周期，不写镜像实现或只断言私有字典长度的测试。

## 4. 阶段路线、相邻批次与共同报告

普通阶段按相邻两个统一验证：W01+W02、W04+W05、W06+W07；两阶段做完再运行批次测试。W03 迁移是独立安全Gate，不能为凑两阶段跳过。W08资格+W09独立审查组成验收批次；W10最终收尾是单独关闭Gate，修复导致资格失效时按影响重跑，不以阶段数代替验证。

每个批次开工都报告：阶段目标、授权范围、前置PASS、branch、开工HEAD、预期dirty/staged、必读文件/章节、实际文件白名单、允许/禁止操作、测试矩阵与命令、PASS/STOP。结束报告使用 §13。所有阶段 commit/stage/merge/push/PR/deploy 默认 NOT_AUTHORIZED；独立审查者的后续提交必须另获明确授权，编码模型不得自行暂存提交。当前文档批准不授权 W01。

### W00：只读基线与可实施性核验

- 目标：核验 §1 的依赖、公开符号、版本、现有迁移和受影响测试入口；确认已批准文档正文 hash 与现场分支。
- 前置：W14/本文正式批准、W00明确授权；已知私有证据不读；无来源不明 staged/dirty。
- 白名单：不修改文件；仅 §1 指定文档、§3 已存在文件的只读核验。
- 允许：git branch/status/rev-parse/diff，rg/读取非敏感源码、Docker版本只读查询；禁止服务/迁移/live及Git写操作。
- 矩阵：符号/入口、配置/实际锁定依赖、M10/M11 head、资源命名、所有引用文件存在。不能用历史全量测试数宣布通过。
- PASS：无设计缺口且确认后续精确白名单足够；STOP：公共入口变化、兼容性需改上游、白名单不足或异常文件；报告 §13，未运行测试写 NOT_RUN。

### W01+W02：上游窄增量与 Web 基础合同

W01 目标落实 W14 §3.2。白名单仅 M10 capacity.py/runtime.py/__init__.py、M13 diagnostics.py、§3.3三个新增上游测试及tests/contract/artifact_persistence/test_public_api.py（仅§3.3的新增导出预期）；必读 M10 §7–12/§24、M13 §12、M04 RunSummary/Event 合同。不新增数据库或部署操作。M10快照只读既有单行，校验数值/限制/关闭与跨loop；M13 release追踪活动operation，unknown/重复幂等，清理后不复造完整历史，waiting的误调用必须由应用禁止。不得改 finish 的既有缓存/专用结束记录取消窗口。

W02 目标落实 W14 §4/§5/§8 请求和加密/cursor/错误合同。白名单：M14 __init__.py/models.py/errors.py/settings.py/security.py/keygen.py；pyproject.toml/requirements.lock/.dockerignore/.gitignore；M14 unit的models_settings/security、contract public_contract、unit fakes/__init__.py、contract __init__.py；scripts/verify_module14.ps1（首次创建UnitContract-only入口）。允许安全fixtureKey/显式Mapping，不调用无参load_settings读取真实.env；keygen测试使用临时独占文件不打印Key。先完成严格模型与派生域，再实现cookie/CSRF/AES；不得把HTTPtoken/JSON反序列化当可信上游state。

共同前置：W00 PASS；未来代码阶段授权；具体新增包/版本diff可在范围内解决，否则STOP。统一验证C01、C02单元、C03仅settings/cookie/Origin/CSRF/cursor/body规则纯单元、C09仅Key/AAD/AES/tamper纯单元，以及M10/M13受影响unit/contract；此批不实现未来HTTP/store或运行integration。C02真PG延至W03，C03归属/HTTP/SSE和C09密文PG/新进程子项分别在W04+W05/W06+W07完成，报告明确PARTIAL_BY_STAGE而非整Case PASS。PASS：本批符号/无副作用/strict/key/canary/active release均证实且旧门槛不降；白名单内小修后重跑受影响项。新建脚本只跑本批实际存在的unit/contract，不提前收集未来文件或新integration测试。

### W03：新 schema 显式迁移独立 Gate

- 目标/引用：W14 §6.1、§12、AC14-17；只建 web_application 六表/约束/索引/versiontable。
- 前置：W01+W02 PASS；W03明确授权；新唯一项目测试DB，不复用日常或云库；原M10/M11 head已在该测试库显式应用。
- 文件白名单：M14 alembic.ini/migrate.py/migrations/env.py/m14_0001_web_application.py；integration __init__.py/conftest.py/test_migrations.py；scripts/verify_module14.ps1（扩展隔离PG/显式迁移和M10容量集成支持）；compose.yaml（仅新增隔离测试服务，不自动迁移正式库）；W01已创建的M10 integration/test_capacity_snapshot.py允许迁移Gate所需fixture接线的小修。
- 允许：容器内依次显式原M10迁移、原M11迁移、M14迁移；只能操作本次已核验前缀的测试资源。禁止 downgrade、修改旧迁移、旧库容量UPDATE、DROP/TRUNCATE/DELETE、挂载开发卷、自动启动迁移。
- 步骤：记录旧两schema结构/head/已有fixture内容hash → 应用M14 → 精确核验六表列、CHECK、索引与head → 再次运行同迁移确认幂等 → 新解释器启动结构检查 → 模拟迁移前结构冲突/DDL失败，确认失败回滚和旧数据不变。
- 矩阵：C04、C02真PG容量查询子项；真实PostgreSQL必需，不用SQLite或只有fakeSQL。并发迁移应明确一个成功、其他序列化/安全拒绝，无部分schema可ready。
- PASS：所有DDL/旧表不变/新进程/重跑/回滚证据齐；STOP：需要改旧结构、隐式修复、无法识别目标卷或回滚异常。
- 报告：迁移名称/head、前后结构/旧fixture对照、资源/清理对象/保留对象；不会因迁移脚本存在就写迁移已执行。

### W04+W05：持久元数据与应用服务/生产装配

W04 目标 W14 §5–6/§9 的store：身份、owner/request、原子quota预留/核对、密文、摘要完整性。白名单 M14 store.py；integration test_admission_store.py/test_pause_summary_restart.py；允许此前security.py仅接store端口的小修；不得向M10/M11裸SQL或用它们私有pool。事务内不得等待模型/地图；quota固定advisory xact lock、规范body指纹与身份绑定、closed/unknown分类必须先实现再接调度。

W05 目标 W14 §3–4/§7/§9–10：组合根、单槽/后台、RunBoundExtractionInvocationSink及依赖绑定、合法命令、取消维护和终态finalizer。白名单 M14 service.py/presentation.py/summary.py/assembly.py/__init__.py（仅本阶段已完成符号导出）；unit test_service_control.py/test_summary_lifecycle.py/test_assembly.py；contract test_upstream_composition.py/test_public_contract.py（仅补齐本阶段导出）；integration test_workflow_api.py/test_capacity_shutdown.py；restart worker；scripts/verify_module14.ps1（扩展store/service/新解释器Integration编排）；unit fakes.py按本阶段新增typed外部替身。此时通过service测试真正主图，HTTP/生产CLI入口留W06，不以导入尚未存在的api形成占位实现。

共同前置 W03 PASS及两阶段授权；必读W14 §3–10、上游表涉及的实际签名。装配不得从tests拿生产EvidenceSink、误await M06 contextmanager、访问definition.diagnostics或直接全局repo写节点。sink显式注入service/M03，并在每次执行绑定时finally失效，不能预填M01内生UUID。backgroundtask强引用/异常消费、slot所有权、独占锁失效、安全关闭以及summary先持久后释放/失败例外均须实现。读取不触发finish/recover/外部API，M11既有get_state内部元数据收束例外按W14§8.3如实描述；启动核对不恢复业务。

统一矩阵C05–C13、C16的store/service/PG子项；补齐C03归属service/C09密文新进程；HTTP/browser子项留W06+W07，C16云Compose/实测留W07/W08。真实PG+typed模型/地图替身、真M01/M03事件及M13/M11/M12链，不能fake completed替代。PASS：本批正常/拒绝/未知commit/断线/重启/关闭前终态采集与关后禁止查询证据齐；未到阶段子项明确PARTIAL_BY_STAGE。STOP：必须降预算、存私有state或放宽恢复/凭证。报告区分HTTP交接与M11转换，列资源/绑定/摘要释放。

### W06+W07：HTTP、四页面与本机入口/云复现准备

W06 目标W14 §8：全部固定routes/响应DTO、严格body/归属/错误/读回/SSE及生产CLI。白名单 M14 api.py/__main__.py/__init__.py（补齐已完成公共符号）；contract test_http_boundaries.py/test_public_contract.py（本阶段导出）；integration test_workflow_api.py；已存在models/errors/service/presentation只允许与冻结合同一致的小修。不能开放通用Artifact读取、原始诊断或token，不绕过service。session/admission/samples严格响应和claimed→confirming只读投影必须落实；socket IP只能API传入。SSE仅get_run且card=None，详情get_state和closed结果读取沿W14§8.3，no-store/CSP/Host/Origin/gzip排除SSE逐条测试。

W07目标W14§11–12：先完成四页面、本机m14-local运行入口、独立样例、普通HTTP随机request_id，再准备2GB云配置供后续复用。白名单M14 static下六文件、samples两文件；Dockerfile/compose.yaml/compose.cloud.yaml/.env.example/.dockerignore/.gitignore；tests/browser/web_application四文件；module14_browser_worker.py；verify_module14.ps1的Browser/Capacity支持。compose.yaml在本阶段提供显式m14-local运行profile，使用runtime镜像实际__main__/生产装配、回环端口、独立PG卷和Key，精确规则引用W14§12.2；测试工厂只属于隔离测试，不代替本机运行入口。样例为新离线合成，source_kind=synthetic，日期显式标注；不能读取历史私有正文或声称真实。生产镜像只加Web必需资源，test/browser-test另装浏览器。cloud覆盖禁公开PG、不自动迁移、Key只读、新卷、两容器内存和日志限额；不得连接真实服务器。初始化/启动/停止与浏览器入口的README说明在W10收尾白名单写入，不为W07扩大文件范围。

共同前置W04+W05 PASS及授权。统一C03/C10–C17，浏览器必须实际打开本机HTTP页面和交互，不以只查HTML字符串代替。PASS：本机origin/精确Host/回环端口和运行配置正确，隔离浏览器普通HTTP下合法操作闭环、reload/SSE断线无重复执行、过期/Busy/429/损坏等页面清楚；Docker配置与本机/cloud profile可重复验证，真实云/供应商资格仍NOT_RUN。STOP：真实API/云登录请求、需HTTPS改变已确认方案、额外前端框架、scope扩大。报告分别记录本机应用运行配置与测试工厂证据，不将替身测试当作实际生产供应商ready；不得填虚假公开链接。

### W08+W09：模块资格与独立逐条代码审查

W08 允许 §3 测试入口与受影响M14测试中的验证完善，不改设计。运行 §11 的UnitContract、Integration、Browser、Capacity及三coverage Gate；然后运行受影响M10/M13/M11/M12回归。审查所有AC对应的代码/事务/资源证据，即使测试为绿也检查语义。新代码需修复只限本批已授权白名单；跨模块或大改先STOP并询问。

W09 为独立审查角色，仅只读app/测试/构建配置/规范和授权的离线报告；不得stage/commit或读原始敏感调用。审查必须逐条对W14 AC14-01..18及上游合同，不以测试数量当结论；对AC14-19明确M15待授权。报告每项文件/行、级别、影响、最小建议和条款；P0/P1/高概率Demo P2关闭，边缘项登记F。修复后复核具体diff及受影响Gate；尚有阻断不得closeout。当前两个文档子Agent的审查不等于未来代码审查。

共同前置 W06+W07 PASS、独立审查已获相应阶段授权。文件写权限只为阶段内小修和F/I真实记录；审查人员/工具不能因“独立”自行获额外提交权限。PASS：无阻断、证据真实、覆盖率门槛满足、resource归属和释放次序已复核；STOP：需扩大范围/真实额度/缺实际证据。报告§13并保留deferred列表。

### W10：MODULE-CLOSEOUT 最终关闭 Gate

- 目标：冻结最终实现/审查后，新构建镜像和新唯一隔离资源进行完整离线回归与模块验收，符合S §7。
- 前置：W08+W09 PASS；W10授权；无未知dirty/staged；closeout后若新改生产代码必须重新评估受影响Gate。
- 白名单：scripts/verify_module14.ps1及coverage/support worker仅范围内验证修正；README/I/F作真实收尾记录；W14与本文规范正文只读。
- 操作：执行Closeout，检查实际Dockerfile默认CMD为python -m app.web_application、本机m14-local运行profile调用同一入口/生产装配，README的显式Key/三模块迁移/启动/停止/浏览器步骤与实际文件和命令相符；核验本机回环绑定/独立卷及测试清理边界。无配置隔离启动必须安全明确配置失败，不以旧import-and-print假装Web启动。真实ready的离线应用通过测试工厂运行真正主图；实际生产依赖ready和真实业务成功留M15首先在本机获得API/验收授权后验证，通过再另获部署授权上云。默认CMD不能改成测试worker或指定旧缓存镜像。
- PASS：全量Docker unit/contract、新真实PG integration、browser、capacity、三coverage及逐条审查有效；仅当前前缀资源安全清理；README明确M14实际资格与M15未验收；未发布体验URL。STOP：任何门禁未执行/失败、用缓存/skip/xfail代替、无资源关闭证据。
- 输出：MODULE14_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE，交付包括本机可启动的完整应用入口和本机离线资格，而不是上线、已commit或真实供应商验收通过。后续按W14§12.2先本机真实网页验收，后云部署/实机资格；用户最终验收和独立提交/部署仍需各自授权。

## 5. Store 与调度实现顺序约束

先落实W14 §6的数据库声明和幂等，再接HTTP。SQL写入只允许目标六表；运行时不自动create schema。FK、复合归属键与部分唯一索引严格按W14§6.1，不对M10/M11建立外键或级联。迁移固定status/availability CHECK、指纹长度、nonce=12字节、ciphertext至少tag长度、日期/size/body联合非空约束；代码同时校验业务联合条件。不能把已有同PK冲突一律当replay。包根按阶段只导出已经实现的符号；W02不提前import W05/W06才创建的service/api，也不写空实现假装接口已可用。

quota算法按W14 §6.2原顺序；try_acquire本地操作不得await排队。事务先幂等后计数再三条新记录原子提交；不把仅校验失败或Busy落成耗额度任务。start的slot和内存command由唯一后台task接管；HTTP disconnect不能取消。未知start commit只能读取原ID，不另跑start；依赖仍不可读就保持reserved/uncertain。控制命令先声明代数、再取已认证凭证交给M11，旧选择绝不“自动换成新代数”。

只有server-created有强引用的工作task可以执行start/resume/recover。取消维护不携带用户业务命令，不抢占其他run，不在睡眠中持slot；request_cancel不是立即图终态。读API无外部调用计数必须为0；最终摘要发生在后台收束，GET summary不主动finish也不保留事件缓存。

M14 summary表中available和unavailable都是不可覆盖终态诊断收据；未知commit通过同ID/hash确认，不能先假定失败再另写不同summary。读回复验body/hash/size及run/trace/terminal；损坏返回安全typed错误，不输出半个JSON。W14§9的8秒/3次/8份只针对最终诊断保存，不能当模型API重试政策。

## 6. 迁移和资源安全门槛

所有数据库验证必须是本次新前缀资源，先记录容器/卷/网络的绝对/完整标识再操作；不根据模糊名称删除资源。现有开发库/真实云库只读且本轮不接触。Windows不得跨shell拼接删除/移动；宿主目录若需递归清理，先验证绝对路径位于本次明示测试目录，再用同一PowerShell的LiteralPath操作。未知或非本前缀目标STOP。

verify_module14.ps1必须要求-ProjectPrefix，不给固定默认值；前缀cta-m14-后接8..40位小写字母/数字/短横线。已存在同前缀资源拒绝复用；只有脚本本次创建的测试容器/网络/卷可清理，报告保留的证据路径。不得docker system prune、删除通用named volumes、向共享默认项目down -v或杀死未识别容器。新进程重启测试在本次同一专属PG卷上，结束才安全清理。

脚本不得解析真实.env：临时生成只含假Secret/当前随机测试资源的显式env文件，通过compose --env-file传入，构建上下文排除.env/Key/私有证据。test profile PG网络允许内部互通；外部模型/地图网络默认关闭；使用同接口typed替身。不得以整个network_mode=none阻断PG后宣称只fakeDB通过。

应用迁移是显式一次性命令python -m app.web_application.migrate；它只升级自己schema，验证前置head，不自动运行M10/M11。依次应用三套显式迁移须清楚记录；Web启动只校验。新cloud库first-init参数与旧默认库不同应拒绝，不自动UPDATE/迁移已有capacity行；没有删除、TTL、清空账本或扩大业务预算的实施权限。

## 7. 关闭、内存和密码材料必查项

逐条核对W14 §9–10：terminal与执行停止证明、M11仍open时预取state/最终预算并await一次finish、严格小摘要冻结、数据库回执/未知commit核对、正常事件释放、失败小摘要有限重试、definition业务清理。关闭前5秒执行等待/8秒预收束、M11.close自身5秒；关后不能get_state/get_terminal_budget_snapshots/finish，只保存已冻结小摘要且不重置其8秒寿命。未获得合法终态的关闭中断不补造summary。M11停止后才关闭借用provider/地图资源，私有_histories不取、不直接clear；release前活动operation必须退出。

可用weakref/可观察引用释放和重复真实执行的稳定内存测量检验事件不随已完成run增长；不为测试暴露生产内部字典，不只测emptyfake。因PG失败造成事件未释放、summary task无限累积、SSE无界缓冲均为高概率Demo P2/P1阻断。等待用户的合法上下文不按终态删除。

Key/nonce/CSRF/cursor来源必须密码学随机或域隔离HMAC/HKDF；不从用户输入、UUID时间、固定fixture或Math.random取Key。普通HTTP不能用Secure Cookie或依赖crypto.randomUUID。生产Key现存拒绝覆盖；fakeKey只在隔离tests；Key生成/迁移/导出不输出payload。Key丢失绝不自动生成新Key后重置旧pause。

## 8. 测试分层和真实资格

unit：严格DTO/settings/security/单槽/有界重试与任务生命周期，可用typed替身。contract：公共导出/无资源副作用、HTTP安全、合法上游命令、组合根依赖归属。integration：必须真PG、显式迁移、并发quota/密文/summary、真正M11和M13、M12完整结果读取，模型/地图用受控typed替身。browser：实际Chromium普通HTTP、页面按钮/断线/多标签/身份/统计显示；不能只用HTTP client替代。capacity：2GB profile的资源/连接/磁盘/重复终态释放证据，测试配置模拟低磁盘必须用显式disk_probe注入，不修改真实宿主磁盘。

新解释器是新Docker进程读取同测试PG，不能只重新创建Python对象或复用模块内cache。restart_worker只使用包根API读取此前真实保存的summary/结果/暂停，模型/地图调用为0；只有另行明确测试的合法恢复动作才可执行typedfake，记录次数。

M14离线通过不等于M15真实DeepSeek/百度地图、杭州网页主链、2GB腾讯云、HTTP公网访问或公开发布通过。本轮及默认离线Gate无真实API授权；M15须独立冻结额度/场景并重新取得实际资格，不能复用旧M13数字填PASS。

## 9. Coverage 门槛

M14全部生产Python（包括migrate/keygen/assembly/SQL/关闭边界）纳入app.web_application，branch=true。line、branch、combined分别至少70%；combined=(covered statements+covered branches)/(total statements+total branches)。不以平均值掩盖一项不足；不omit低覆盖模块、不追加pragma no cover、不删分支、不靠collection计数。迁移/新进程的覆盖通过锁定coverage官方subprocess机制合并实际执行，不把不存在的coverage片段捏造为已覆盖。

覆盖脚本沿S明确列unit/contract/integration选集，并生成JSON实际分子分母；浏览器JS的行为资格单独记录，不拿Python70%证明JS覆盖。受影响M10/M13/M11/M12原合同与coverage门槛不降低，本文只定义M14新模块门槛；不无差别重跑无关live。不能用skip、xfail、rerun、复制实现测试或仅私有helper断言凑覆盖。

## 10. 必测矩阵

编号供阶段和实施提示词引用，具体行为以W14相应AC/章节为准，不在提示词重新粘贴合同。

| Case | 输入/竞争/故障 | 必须观察的结果 | AC |
| --- | --- | --- | --- |
| C01 | 无参import、非法DTO、extra、bool-as-int、model_construct/嵌套mutation | 无资源/Secret副作用；严格typed拒绝 | 01/02 |
| C02 | M10快照正常/坏row/limits漂移/closed/cross-loop；M13active/unknown/repeated release | 固定只读SELECT；拒绝非法；停止后真实释放，不影响summary值 | 01/12/16 |
| C03 | Cookie首发/30天/他人run/cursor；显式127.0.0.1/localhost/公网IP origin及跨Host；XFF/CSRF/body/XSS/canary；session/admission/samples DTO | 每实例只接受配置的精确Origin/Host，无本机通配；归属/统一404/strict响应、可信socket IP，无外部调用/泄漏 | 03/04/14/15 |
| C04 | 首次/重复/并发迁移，新解释器；错旧head/冲突/DDL失败 | 新六表正确，旧两schema/fixture未变，回滚和拒ready | 17 |
| C05 | quota=200/20/5边界，上海跨日、恰好一小时前；双连接并发新run | 预留原子、滚动正确、失败取消不退款、429/Retry-After | 05 |
| C06 | 同ID同body/改body、多标签；start先插M11后断HTTP；metadata未知commit；claimed记录GET | 一个run/固定ID，无重复调用/扣额/假退款；confirming安全只读投影 | 06/07 |
| C07 | startup仅metadata核对、M11有/无/不可读、server进程重启 | 仅确定才confirm/release；无自动recover/start | 06/13 |
| C08 | executing/waiting/terminal/blocked竞争槽，另实例/失锁 | waiting释槽、Busy不排队；停止旧执行后安全关闭 | 07 |
| C09 | nonce/AAD/Key/代数/hash/tamper、sink写失败、PG重启读取 | 正确凭证重放；错误失败关闭、无token暴露/重置 | 09 |
| C10 | 真M01/M03 initial/clarify/confirm revise的extract/repair事件、内生/已知turn、recover/no/过期binding；酒店/旧代数/伪Ref | 真实公开命令和事件、scope不重复resumed，finally失效，人工门槛不变 | 02/08/10 |
| C11 | cancel并发/Busy/断线、唯一维护和退避、未知外部、显式recover | requested与cancelled区分，重启不重建维护，不重放未知 | 10 |
| C12 | 真正完整主图、完整/degraded/failed/decline/cancel；M12索引/版本损坏 | 完整load才交付、无半攻略；GET外部调用0 | 11/13 |
| C13 | summary成功/重放/冲突/损坏/未知commit/3次失败/8份上限、缺行时活动与终态、M11关闭窗口 | get_run判真实状态，缺行分别not_finalized/pending_save/not_saved；关前取证/finish，关后只保存冻结摘要；原≤8秒寿命/无泄漏 | 12/13 |
| C14 | 21条本人记录/全站他人混入/cursor篡改/SSE超限/断线/到期 | 每页20，无他人泄漏，有界快照/no-store，不取消业务 | 14 |
| C15 | Chromium本机普通HTTP首页→澄清→确认→酒店→完整攻略→统计；reload/429/Busy | 本机页面实际操作、真实主图+明确替身、getRandomValues请求ID、重复点击不重复执行；不冒充生产真实API验收 | 15 |
| C16 | 新云200000/4GiB初始化、旧默认capacity、free<5GiB/等于5GiB/探测失败 | 合法新库，漂移拒ready；只低于拒新；无删库/降预算 | 16 |
| C17 | 本机runtime入口/回环端口/独立卷/启动步骤；启动依赖顺序/head错误/中途失败；顺序多run/暂停/SSE、摘要DB失败/独占连接/关闭时未得终态 | 本机实际生产装配配置与测试工厂区分、测试清理不碰Demo卷；先diagnostics/sink再M03，各公开runtime核验自身head，失败逆序清理；资源有界、关后禁查询，未得终态不伪造 | 02/18 |

顺序多run至少5个完整终态，不能全用空事件fake；暂停至少2个后继续或取消，并说明保留内存。C13通过测试注入短时钟/失败，但必须核对生产实际8秒/3次/8份参数；不得只把参数改小掩盖算法。C17记录峰值/增长趋势和容器OOM，达不到profile就STOP，不自增云机配置或把未测写足够。

## 11. 统一 Docker 验证入口

未来scripts/verify_module14.ps1参数固定-Set UnitContract|Integration|Browser|Capacity|Closeout、-ProjectPrefix（必填）、-EvidenceDir（显式、不能已存在）、可选-ImageTag（仅本次构建且包含前缀）。脚本宿主不跑Python，不调用默认compose项目，不用真实.env，不打印Secret。完成须核验退出码和实际报告，不把collection/import/容器启动当测试通过。

```powershell
./scripts/verify_module14.ps1 -Set UnitContract -ProjectPrefix cta-m14-u-20261005a -EvidenceDir <EVIDENCE_DIR>
./scripts/verify_module14.ps1 -Set Integration -ProjectPrefix cta-m14-i-20261005a -EvidenceDir <EVIDENCE_DIR>
./scripts/verify_module14.ps1 -Set Browser -ProjectPrefix cta-m14-b-20261005a -EvidenceDir <EVIDENCE_DIR>
./scripts/verify_module14.ps1 -Set Capacity -ProjectPrefix cta-m14-c-20261005a -EvidenceDir <EVIDENCE_DIR>
./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-z-20261005a -EvidenceDir <EVIDENCE_DIR>
```

示例路径/前缀只示范语法；实际必须本次新值，不执行本轮文档任务。UnitContract只含当期已实现M14 unit/contract及M10/M13新增unit/受影响contract，不含任何integration；阶段Gate按§4具体子项，W08/Closeout必须全部测试文件已实现，不以缺文件跳过。W02首次实现该入口；W03补Integration的显式三迁移+C04/M10容量PG；W04+W05扩到全部当期M14 store/service integration和新进程；W06+W07补HTTP/Browser/Capacity；未实现的Set明确不支持而非空跑PASS。Browser为实际C15/C14普通HTTP；Capacity为C16/C17资源；Closeout新构建全量既有+新增unit/contract、全部integration/browser/capacity、三coverage和独立审查。旧模块定向入口沿现有脚本/规范，由W00核验后提示词精确引用，不能擅改旧脚本。

本轮这些命令全部NOT_RUN。后续命令失败保存安全报告，停止相关Gate；不能升级依赖、增加live、删除数据或降设计来“试到通过”。小修仅阶段白名单内，修后仅重跑相应受影响测试和必要关闭Gate。

## 12. 停止条件与未来实施提示词

停止条件：未获设计/阶段授权；分支/HEAD/dirty不符合阶段前置；required文件/符号缺失；需要改W14冻结决定或未获准上游；白名单不足/跨仓大改；迁移目标不明确；旧capacity/head漂移；未知commit被迫猜退款/重放；凭证/归属/摘要完整性无法确认；资源有泄漏/容量不足；测试必须靠live/skip/xfail/降门槛；要求stage/commit/push/PR/deploy但无授权。

STOP报告说明事实与最小证据、已完成的只读/安全排查、受影响文件/资源、为何不能白名单内解决及唯一需要用户决定的问题。停止相关动作但可以完成不依赖该决定的既有授权只读工作；不得在报告后偷偷扩范围。

文档批准后才按用户请求生成实施提示词。整份提示词必须只放一个代码框；精确引用W14条款/AC、本文阶段/矩阵和S，不重复粘贴规范已写的大段内容。必须保留当前阶段目标、前置、branch/开工HEAD、预期dirty/staged、精确必读、文件白名单、允许/禁止操作、步骤、测试矩阵/命令、PASS/STOP和§13报告。普通批次按§4相邻两阶段，迁移安全门和最终关闭Gate单独；不可过度精简为“按文档完成”。

## 13. 最终报告格式

```text
任务/阶段/状态：
文档依据：W14具体节/AC；本文阶段/Case；S；批准正文hash：
授权与范围：
分支；前HEAD；后HEAD；是否切分支/worktree：
修改/新增文件（逐项）；只读/异常/保留文件：
迁移：NOT_RUN或显式入口/revision/前后head/隔离DB/旧表对照/回滚证据：
测试：命令、Docker项目/本次image、退出码、实际passed/failed/warnings：
Coverage：line、branch、combined分子分母与百分比；NOT_RUN说明：
业务/安全/资源：逐条AC结果、slot/取消/凭证/summary释放、memory/PG连接/磁盘/关闭：
新解释器重启：读取hash/结果/摘要/凭证、外部调用次数：
独立审查：角色/轮次/阻断/关闭项/延期链接：
Secret/env/live/云操作：是否执行、次数、授权；未运行明确NOT_RUN：
异常文件/staged；资源创建/清理/保留与目标核验：
stage；commit；merge；push；PR；deploy（逐项NOT_RUN/实际事实与授权）：
最终状态与待用户批准项：
```

文档任务完成=两份M14文件和获准同步文档完整、两新agent初审/主修/两位第二轮复核/主总审真实完成、Markdown链接/白名单/diff/staged检查通过，无未处理阻断，最终DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL。不是M14代码完成。代码未来全部Gate完成才MODULE14_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE，仍不是commit/发布/M15通过。

## 14. 本轮文档审查与现场记录（非规范性）

现场基线：2026-10-05，feature/module-14-web-application，HEAD=3a7564fa1ea451c356b2d998094e9aa0b4de57fc。开工staged为空、跟踪文件无已有变更；唯一既有未跟踪 ../m13-live-evidence/ 保留且未读正文。此节是现场记录，不作为未来开工HEAD约束。

两份M14文档初稿及10份既有文档同步完成后安排两个新的GPT-6.1-sol/max仅只读审查，A架构/上游一致性、B弱模型可执行性；首轮→主修→同两位第二轮→主总审。用户演示优先策略沿W14§13，不要求为边缘P2/P3无限扩大修复。记录合并进本节/W14§14，延期入F，不创建第三份文件。

### 14.1 实际审查结果

首轮两位实际结论均BLOCK：A两项P1及两项P3；B四项P1及一项P3，其中关闭顺序P1重复。去重5项P1/3项P3，主模型全部最小修订，详见W14§14.2的DR14-01..08。同两位第二轮确认首轮项全部关闭，但A发现摘要读取依据冲突A2-01（P2），B发现启动依赖顺序冲突B2-01（P1），两位当轮仍BLOCK；主模型一次修订W14§8.3/§9.3/§10及本文C13/C17后，原两位定向补充复核均PASS。

| 角色 | Agent/模型/思考档位 | 初审 | 第二轮 | 定向补充 |
| --- | --- | --- | --- | --- |
| A 架构/上游一致性 | m14_architecture_review，GPT-6.1-sol/max | BLOCK | 首轮项关闭；A2-01 P2，BLOCK | A2-01/B2-01关闭，无回归或新阻断，PASS |
| B 弱模型可执行性 | m14_execution_review，GPT-6.1-sol/max | BLOCK | 首轮项关闭；B2-01 P1，BLOCK | B2-01/A2-01关闭，无回归或新阻断，PASS |

主总审逐条核对D14-01..16、AC14-01..19、W00..W10、C01..C17、公共合同和10份同步增量，结果PASS。累计6项P1/1项P2/3项P3全部文档层关闭，无本轮延期、未改变确认决定或修改代码。未来既有M10导出测试的窄白名单不代表本轮已改测试；早期验证PARTIAL_BY_STAGE不能冒充整Case资格，文档PASS也不能当作实现或M15资格。

### 14.2 送审版本与检查

正文SHA256范围为文件开头到“## 14. ”之前，CRLF归一为LF后按UTF-8计算。本节回填属于非规范性记录，不改变通过复核的正文。

| 版本 | W14正文SHA256 | 本文正文SHA256 |
| --- | --- | --- |
| 第二轮送审（两项剩余阻断） | 49C61ABD276393D9B074A3260E65EA3CE45A8A24A8142827ABD191521C4C4E4A | E5A23AA2A7E3B7EE15D3DE4E9316E66C7FA497C14CDCC5ADEFAAD770C4EACB22 |
| 此前定向补充复核通过版本（历史） | E3BC156C1EB54A19A232FE2719CFF6D26896D0DBE6369381F722210A597F285B | 058ADE66C515D7DA78D13873B5035511C29040DAA17E4CFF15336F7A6F01B170 |

本轮文档检查通过：两新文档21条本地文件链接存在、代码框配对、16项决定/19条AC/17组Case完整；12文件白名单、diff --check、HEAD及staged核对通过。修改文件逐项见§2：两份新建、10份既有文档同步，无第三份M14文件或异常代码文件；既有私有未跟踪目录保留、未读取正文。开工/最终分支均feature/module-14-web-application，前后HEAD均3a7564fa1ea451c356b2d998094e9aa0b4de57fc，未切分支/新建worktree，staged为空。

此前定稿最终状态DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL。Python/代码测试/coverage/Docker/数据库/迁移/真实API/云操作全部NOT_RUN；stage/commit/merge/push/PR/deploy全部NOT_RUN。未取得设计最终批准或实施阶段授权，2GB实测和真实网页资格仍留后续Gate；后续澄清及当前资格见§14.3。

### 14.3 用户追加本机优先修订

用户随后明确先实现本机可使用的前端入口、通过测试再部署云服务器；本次在W14§1/D14-17/§4.2/§12.2及本文§1/W07/W10/C03/C15/C17冻结该顺序，保持同一代码/runtime镜像、四页面和既有上游合同。本机运行使用已有compose.yaml白名单，不新增报告或代码。此前§14.1–14.2是追加澄清前的实际审查和hash记录，不能当作本次修订资格。

原A架构审查者和B可执行性审查者分别对本次送审文件/正文hash独立核验，两位定向结论均PASS，无新增阻断或延期，旧关闭项未回归。主总审核对17项决定、19条AC、W07/W10与C03/C15/C17通过；本机运行配置、离线测试工厂、本机真实生产验收和云部署的边界一致。

| 本次通过复核的正文 | SHA256（沿§14.2算法） |
| --- | --- |
| W14 | 58407E283906EB90F86317F4644686F84D6A904D756B452EF6DE1DDB81213333 |
| 本文 | 7B5536BA51920C106346546D118BAB4418A34CFD18825C5BAF06A8A805A19794 |

本次仅修改两M14文件及README/IMPLEMENTATION_STATUS的顺序与真实状态说明；工作区合计仍为§2授权的两份新文档和10份同步文档。当前DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL；分支与前后HEAD保持§14所列值、staged为空、无新增异常文件，私有目录未触碰。文档结构/链接/白名单/diff及通过复核后的正文hash检查通过；服务/Python/代码测试/coverage/Docker/数据库/迁移/API/云操作及stage/commit/merge/push/PR/deploy仍全部NOT_RUN，本机入口与云端均未实施。

### 14.4 批准后的实施与逐阶段核查（2026-10-05）

用户已明确批准两份文档并授权按W00–W10实施整个M14、每个通过阶段由原两位子Agent核查、完成后等待提交。顶部为批准后的开工授权状态，§14.1–14.3保留当时历史事实；当前实施资格以本节和IMPLEMENTATION_STATUS为准。批准规范正文SHA256（首“## 14. ”前，CRLF转LF/UTF-8）为W14 C1044580CEBDB290FC1A18F581C1F27F426563C69AC42DC4A9C6FA0886C6E2E6、E14 8BE34F71BB0DF858BEF7275BB04CC6A7CF3D68CCD8C57393BA6CF7FC7A8FABDD，实施期间保持。

W00、W01、W02、W03、W04、W05、W06、W07、W08、W09现均获原架构A和执行B各自正式PASS。相邻批按合同统一验证，迁移W03独立；各阶段发现的小范围合同一致问题已在精确白名单修复并复核。W08最终证据来自<EVIDENCE_DIR>的M14通过分项、w08up-20261005e完整上游补验、w08browser-20261005e和w08capacity-20261005e独立资格；d整批FAIL保留，上游补验仅TARGETED_UPSTREAM_GATE_ONLY，均不冒充W10 Closeout。两位逐AC14-01..18及C01..17核对源码、事务、凭证、终态释放和资源证据，无未结P0/P1/高概率Demo P2、无新增延期；AC14-19/M15仍NOT_RUN。

M14 279定向/2594全量unit-contract、53真PG及三个新Docker读取通过，读取外部调用0。覆盖全部16份生产Python/166实际官方subprocess片段，line2002/2232=89.6953%、branch490/652=75.1534%、combined2492/2884=86.4078%；无omit/缺源/skip/xfail/rerun。受影响M10原coverage及真实PG restart对、M11全253功能、M13原122 coverage、M12原94诊断/4PG/三日真restart一致通过，旧M11严格coverage债和M13专用记录窗口边界保持。Browser五项及非终态SSE SIGTERM退出0、EOF0.651479s、资源均关闭；Capacity五完整/两暂停、weakref释放、peak307007488 bytes/connections10/OOM0、root close0.002132s通过。详细计数/命令/早期失败和证据见IMPLEMENTATION_STATUS，F仅记录真实延期边界。

当前进入W10最终同步和新构建完整关闭Gate，README使用实际生产CLI、本机独立profile、显式Key/三迁移/启动/停止。分支feature/module-14-web-application，开工/当前HEAD均3a7564fa1ea451c356b2d998094e9aa0b4de57fc，staged空，78获准文件（57新建/21跟踪修改），私有旧目录保留且未读正文。真实API/云/公开链接尚未执行；stage/commit/merge/push/PR/deploy均未授权、未执行。W10完成并双审通过后才报告MODULE14_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE。

### 14.5 整模块实施完成与最终关闭资格（2026-10-05）

当前状态 `MODULE14_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE`，W00–W10 全部由原 A 架构与 B 执行审查者分别逐阶段正式 PASS，等待用户验收与提交授权。顶部和 §14.1–14.4 为批准开工及各时点快照，不改写当时进行中或失败；本节与 [实施状态最终报告](VALIDATION.md) 是最新事实。规范正文 hash 保持 §14.4 批准值，没有为实现更改设计或扩大范围。

同步后唯一完整关闭命令 `./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-w10-20261005b -EvidenceDir <EVIDENCE_DIR>` 退出 0；新 runtime/test/browser 镜像、65 步骤全部 exit0，qualification status/cleanup PASS。279 定向、2594 全仓 unit/contract、53 真 PG、三个新 Docker 重读、M10 原 coverage/真 PG restart、M11 全253功能、M13 原122 coverage、M12 原94诊断/4 PG/三完整日块 restart manifests、独立 Capacity 与5 Chromium 均通过。M14 覆盖全16生产 Python/166官方 subprocess片段/七 required入口，line2002/2232=89.6953405018%、branch489/652=75%、combined2491/2884=86.3730929265%，无omit/缺源/skip/xfail/rerun；原 M10/M13 三项70% Gate通过，M12仍仅诊断，M11原coverage债保持。

独立资源测量为5完整终态、2暂停均真实取消、终态事件weakref释放，peak307208192 bytes、最多10应用连接、OOM0、root close0.001917s；模型42/地图125计数仅typed替身，真实外调0。非终态SSE SIGTERM后0.574163s EOF，server exit0/OOMfalse、service/provider/maps已关闭、SSE0。五精确归属资源cleanup各exit0，主独立inventory剩余0；亲看本批历史/公开样例PNG完整。新schema实际revision为 `m14_0001_web_application`，只在本次独立测试PG显式迁移，旧业务结构/数据与日常库边界保持。

两位 W10 正式核查逐AC14-01..18/C01..17对源码和原始收据，确认实际生产CLI、README本机Key/三迁移/启动停止、回环端口、Demo与测试独立卷、云文件仅准备；文案及收据引用P3均在白名单内修正，无未结阻断或新延期，不改变最终构建输入。a批为最后README同步而有意中断，integration137/原Gate1/无qualification仍保留，不能套为PASS。所有计数、镜像ID、逐AC、阶段表、78文件逐项和早期失败见上述实施状态；F原风险与UF13-RECORD-001专用记录窗口保持。

最终分支 `feature/module-14-web-application`，前/后HEAD均 `3a7564fa1ea451c356b2d998094e9aa0b4de57fc`，78文件（57新建/21修改）、异常源文件0、staged空、diff/hash检查PASS，私有旧目录保留且未读正文。stage/commit/merge/push/PR/deploy均NOT_RUN；M15真实API、本机实际供应商业务、腾讯云2GB实机、公网资格及README真实体验链接均NOT_RUN。当前本机应用与离线资格完成，尚未提交、合并或发布。

## 15. 澄清选择表单维护执行规范（已获用户批准）

### 15.1 目标、依据与前置条件

本次维护执行 W14 D14-18/§8.1/§11.1/AC14-20，仍遵守W14 §3/5/7–10/12–13、本文§6–13及S §1/3–9；上游必读M01 §6.5/6.6/§7/8、M13 §4/13，直接依赖仍为R §2的[13]。只将澄清文字改成准确的选择表单，不重写领域规则、图、预算、数据库或写请求合同。

用户已批准该增量及实施、原两位子agent核查；开工分支 `feature/module-14-web-application`，HEAD `3a7564fa1ea451c356b2d998094e9aa0b4de57fc`，不切分支/worktree。W10已完成的源码、当前Docker入口及原资格可作前置资料，不能替代本次新源码回归。56个用户已暂存新文件、21个既有跟踪修改、未跟踪compose.cloud.yaml及旧私有证据保留；主代理不得暂存、撤销或覆盖index。开工与收尾记录index清单指纹和本次实际变更清单；未知并行编辑冲突STOP。

### 15.2 相邻阶段U01+U02及精确白名单

| 阶段 | 目标及允许修改的文件（相对项目根） |
| --- | --- |
| U01 | 先按批准增量更新docs/14_web_application.md、docs/14_web_application_vibe_coding.md；实现app/web_application/models.py、presentation.py、__init__.py中的严格问题DTO、有限字段投影和导出；只补tests/unit/web_application/test_models_settings.py、tests/contract/web_application/test_public_contract.py、test_http_boundaries.py |
| U02 | 实现app/web_application/static/app.js、static/site.css的选择表单、条件年龄、其他说明、草稿与原提交；只补tests/browser/web_application/test_demo_pages.py和tests/unit/web_application/fakes.py中的typed供应商边界 |
| 关闭Gate | README.md仅同步新交互事实；docs/IMPLEMENTATION_STATUS.md记录本次授权/源码/精确新Gate及正式双审；两份M14文档只追加本次非规范事实/修正本次增量的明确小问题，不改原合同。Dockerfile只读核验现有COPY app/tests、runtime CMD及browser-test覆盖本次文件 |

白名单共14份既有文件，禁止创建第三份M14报告/新增目录/改service、store、api、上游或迁移；测试入口scripts/verify_module14.ps1、tests/support/run_module14_coverage.py及worker只读使用。允许宿主PowerShell文件操作/只读Git、Docker内离线Python/实际Chromium、新隔离测试PG与其显式既有迁移、按§6精确清理本次资源；禁止真实供应商规划、云操作、stage/commit/merge/push/PR/deploy、接触日常库及停止其他项目。正在运行的cta-m14-local真实Demo不作为测试对象；如交付需本机更新镜像，只在隔离资格通过并确认没有正在执行任务后进行，不删除Demo卷/Key，不自动恢复、取消或提交访客任务。

实施顺序：U01先严格DTO/安全投影及对应边界测试，U02再网页和typed测试替身；两个阶段完成后统一运行相应测试，再原两人各自逐条对照W14新增AC与原受影响AC正式核查。不得在生成questions时套完整model_dump，metadata仅选择实际问题、原message仍保留；冲突由原领域处理，不以控件强行篡改已保存值。未发布的响应增加默认空questions，原写请求保持；不开放传入完整state或结构化可信patch的捷径。

### 15.3 必测矩阵与验证命令

| Case | 要观察的行为 | 原条款与增量 |
| --- | --- | --- |
| U-C01 | 选项/问题/card字段严格；extra、bool-as-int、重复value/field、kind联合约束、非澄清questions、预算字段和嵌套伪造均拒绝；新公共导入无副作用 | AC14-01/04/20，C01/C03 |
| U-C02 | 真NeedClarification的必要字段/偏好/问题路径投影；完整字段不追问、0儿童不追问年龄、正人数年龄数、冲突信息未丢、纯文本/无证据token泄漏 | W14§11.1，AC14-20 |
| U-C03 | Chromium普通HTTP实际选项→原clarify文本POST→真M01/M03证据/领域→确认→酒店→M12完整攻略；不能填旧full_text冒充选择验证 | AC14-08/11/15/20，C10/C12/C15 |
| U-C04 | 无默认选择/空提交不调用；Agent决定互斥；其他必填；儿童正数/0条件及年龄范围；日期时间/数量格式；10.5秒轮询和SSE保留所有草稿；新代数清旧；总文本8000字限制 | AC14-04/15/20，C03/C15 |
| U-C05 | 原手动文本入口、确认修改、酒店人工、取消、断线、唯一request_id/截断响应、归属、XSS以及历史/样例不退化 | AC14-03/04/06/08/10/14/15，C03/C06/C10/C11/C14/C15 |
| U-C06 | 同步后新runtime/test/browser镜像、runtime默认CMD、完整unit/contract、真PG、新进程、全包coverage各>=70%、原受影响上游Gate、容量和安全清理 | AC14-18，C01–C17，S§7 |

U01+U02统一定向入口先执行 `./scripts/verify_module14.ps1 -Set Browser -ProjectPrefix cta-m14-clarify-20261005a -EvidenceDir <EVIDENCE_DIR>`（同时含模块/受影响unit/contract与隔离PG、Chromium、非终态SSE关闭）。在进入最终Gate前同步README与I可验证的当前状态，再执行 `./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-clarify-20261005b -EvidenceDir <EVIDENCE_DIR>`，使用实际新前缀，不能已存在；保持原完整离线门槛。初次失败保留报告，白名单内明确修复后只重跑受影响项和必要新Closeout，不rerun/skip/xfail，不修改旧测试门槛。最终计数/coverage/镜像与资源只写真实收据，M15/live/cloud仍NOT_RUN。

typed供应商替身只解读本例用户实际提交的有限中文字段行，evidence取当前消息实际子串，不因长度或缺项自动补齐其他字段。旧full_text可继续服务既有容量/配额样例，但新澄清资格必须点选控件，不绕过真实M02/M03/M01/M13/PG链；最终确认及结果核对实际选择。UI隔离响应fixture只证明DOM，不冒充业务主图资格。

### 15.4 PASS、STOP与报告

PASS必须同时具备U-C01–06、W14 AC14-20及原受影响条款的源码/实际命令证据，原两人正式逐条PASS和S §7同步后资格；不能只凭测试绿灯或旧W10 hash。STOP：需改变未批准上游/意图/预算/恢复/数据库、白名单不足、无法安全保留用户index或现有真实任务、泄漏、不能满足Gate、必须降门槛；停止相关动作并报告最小事实及需要的决定。commit/stage/push/PR/deploy均NOT_AUTHORIZED。

最终报告使用本文§13，至少分支、前后HEAD、维护14文件实际子集、批准增量及本次正文hash、用户index前后、迁移（只既有隔离测试迁移/无schema改动）、定向/模块/全回归/coverage/Docker/Chromium及异常文件、两人正式结论、commit/push/PR/部署/真实API状态及最终状态；不宣称本机真实规划或云资格。本节规范冻结后的小修须与D14-18一致，改变已确认设计或扩大范围仍先取得用户同意。
### 15.5 本轮执行、双审与交付事实（非规范性，2026-10-05）

U01+U02及同步后关闭Gate均完成；原`m14_architecture_review`和`m14_execution_review`分别逐条对照W14新增/原受影响AC、U-C01–06、源码与b批实际安全收据，正式U01 PASS / U02 PASS / Closeout PASS。无未结P0/P1/高概率Demo P2，无新增延期；早期失败/定向a2/源审修复和原W10历史保留，不借用旧资格。完整文件清单、发现关闭及正式矩阵见[本轮实施报告](VALIDATION.md)。

实际新命令`./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-clarify-20261005b -EvidenceDir <EVIDENCE_DIR>`退出0，65步骤全exit0，qualification/cleanup PASS。305模块/受影响与2620全仓unit/contract、53真PG、三新Docker重读、7实际Chromium、原上游Gate、独立容量及非终态SSE关闭全部通过；覆盖全部16生产Python/166官方subprocess片段，line2078/2308=90.0347%、branch532/696=76.4368%、combined2610/3004=86.8842%。五精确测试资源清理和交付前独立inventory剩余0；本机随后合法复用验收runtime，不算测试泄漏。M11原coverage债与UF13专用记录边界保留。

本次冻结规范SHA256（CRLF转LF/UTF8）：W14首§14前`CB53CECAE96FD369E0688F567CE9A3E9FF5244471011874DF005BF640B00227F`；E14首§14前`DC0EF99EF9EB51C6BEF06661B238F29B69EC543C11A4CC658A7DDD93045C60CA`；E14规范§15从标题起至本§15.5标题前`40AD24236CCD9704F66EA78A3DB8BE144378452C95BCD6E3A3784D0AFF9B33DB`。本节仅回填事实，不属于规范，也不改变最终镜像构建输入。

按§15.2确认真实Demo没有执行中的任务后，仅更新本机Web。首次进程环境空值覆盖导致exit2/就绪失败，安全收据保留；移除具体进程覆盖变量并通过非Secret形状检查后重新更新成功。最终本机就绪/首页/JS/CSS均200，静态字节哈希与源码一致，原PG容器/同卷/Key及一个暂停任务保留；没有自动业务POST、恢复或取消任务。用户可在`http://127.0.0.1:8080`原页面Ctrl+F5体验。本机/云库迁移、M15真实规划/云资格均NOT_RUN。

分支`feature/module-14-web-application`，前后HEAD同`3a7564fa1ea451c356b2d998094e9aa0b4de57fc`；实际14份既有文件/新增0/异常0，用户56项index及raw指纹`E55F57634C38AB43FCD6028492B0AFE875A702A83517819ABFF538F9B05AE5AD`保持。schema变更0，仅隔离测试显式验证既有迁移。stage/commit/merge/push/PR/云部署逐项NOT_RUN，本机Web更新已执行；当前`MODULE14_CLARIFICATION_MAINTENANCE_READY_FOR_USER_ACCEPTANCE`，等待用户验收与提交授权。

## 16. 真实API三场景测试提示词（非规范性，2026-10-06）

用户本轮明确授权“用真实api测试三次直到成功为止”，随后要求“发现问题就修复”。以下为当前阶段执行记录与提示词，引用既有批准设计，不改变冻结业务合同；最新结果仍以I的真实收据为准。

```text
任务/目标：M14-LIVE03。使用当前真实生产网页入口、DeepSeek、百度地图及PostgreSQL，顺序完成3个独立场景的完整攻略。失败保存记录、先定位明确问题，修复后同场景重测；不将失败覆盖、不用离线样例充当成功。达到最终版本3个完整成功场景即停止业务调用，不推断长期成功率或全M15资格。
依据/必读：W14 §3/5/8/9/11.1/12.2及AC14-08/11/12/13/15/19/20；E14 §6/8/10的C10/C12/C13/C15/C17、§12/13/15；S §3–9；上游出现明确问题时只读其拥有该合同的设计与执行规范，不能靠改门槛获成功。
前置：最近当前源码Closeout b及原A/B正式双审PASS；核验锁定runtime与现有源码、生产默认CMD；API已配置，由Docker Compose正常加载，不输出.env/Key/DSN。当前本机Demo与1个用户暂停任务保留。分支feature/module-14-web-application，前HEAD3a7564fa1ea451c356b2d998094e9aa0b4de57fc；已有56项用户index指纹E55F57634C38AB43FCD6028492B0AFE875A702A83517819ABFF538F9B05AE5AD保持，不切分支/worktree。
文件白名单：本轮测试记录仅docs/14_web_application.md、docs/14_web_application_vibe_coding.md、docs/IMPLEMENTATION_STATUS.md的非规范现场记录；仓库外安全证据/配置/控制脚本仅<EVIDENCE_DIR>。仓库生产与测试暂不编辑；明确实现问题先记录所属条款、精确修复文件/测试白名单和局部修复步骤，按用户追加授权修复不改变合同的必要实现问题；改变已确认设计、大改或其他仓库仍先征求用户同意。不能借测试整理已有78份修改或私有旧证据。
允许操作：PowerShell安全文件操作/只读Git；Docker内部Python，基于现有compose m14-local profile在独立cta-m14-live-20261006a项目、新PG卷/新Key/18080回环端口启动真实生产CLI，保留单worker、1GiB/512MiB、200/20/5及原模型/研究/规划预算；只对自己新建测试任务执行网页澄清/确认/酒店/必要安全恢复或取消，透明读取结果/摘要与资源计数。测试配置和Cookie独立，不读现有真实访客正文/UUID/Token，不操作cta-m14-local或其他平台资源。
阶段：L01为独立初始化安全门，明确资源归属后单独顺序执行既有M10/M11/M14迁移与ready；L02+L03为杭州与上海真实浏览器主链，统一核对已选需求、终态、M12完整结果和持久摘要；L04为苏州主链及所有3场景统一关闭/读取验收。修复阶段先生成精确相邻阶段提示词、运行受影响定向/模块与必要完整回归、S §7新镜像资格后再真实复测；迁移安全门不能跳过。每阶段通过后沿用原A/B逐条核查，不以测试绿灯替代文档对照。
测试矩阵：杭州2天/1成人/无儿童，实际选择澄清控件→确认→酒店→完整攻略；上海2天/2成人+1儿童(6岁)/无老人，核对人数年龄、明确偏好与两日完整结果；苏州3天/2成人，核对Agent决定/无特殊要求及三日完整日块。日期使用2026年11月的未来日期，固定合成场景，无真实个人信息，不订房。三个场景都验证真实调用计数、终态demo_ready/degraded分别记录、完整结果/Markdown/日块、数据库summary已保存、刷新/统计读取及无额外供应商调用。失败的HTTP/安全错误码/类型/阶段/调用次数与修复证据保留。
PASS：最终已验证源码上3个独立完整成功、各场景输入与持久输出一致；M12完整load且summary保存；真实供应商调用确有发生；原A/B对实际条款和收据核查通过。degraded按原合同记录，不冒充demo_ready。M15恢复/失败质量集、云2GB和公网资格未实测仍NOT_RUN。
STOP：密钥泄漏/原始响应输出、未知并行源修改、无法保护index/现有任务、需要增加预算或降低安全门槛、需要改变冻结设计/跨仓大改；供应商余额鉴权或长期不可用须报告真实原因，不伪造成功。429遵守滚动窗口等待，不改配额/时钟/伪造IP或重置数据规避限制。commit/stage/merge/push/PR/云部署均未授权。
报告：分支/前后HEAD、修改/新增文件与异常、规范对照/精确镜像、独立迁移及原Demo保留、每次失败/修复/正式和诊断调用次数、三场景结果/质量状态/summary/重读hash、资源/清理、双审、index指纹、stage/commit/merge/push/PR/部署和最终状态；秘密值与模型/供应商原文不进报告。
```

### 16.1 R-F01+R-F02：真实否定约束派生修复提示词

```text
当前目标：修复杭州首趟公开确认摘要中“行动限制：无”却派生mobility_assistance_needed的问题；本趟不计成功，取消并保存安全失败/摘要收据，再用同一合成场景重新验证。用户已追加授权发现问题就修复，当前为必要普通maintenance，不是M01 RSC或重建。
设计/执行依据：W01 docs/01_requirement_and_dialogue.md §6.4/6.5/7/9/11/12；E01 docs/01_requirement_and_dialogue_vibe_coding.md §0 maintenance/§2/3/10.4/19–22及其现行95%三coverage门槛；W14 §11.1/12.2/AC08/11/12/19/20；E14 §16与C10/12/13/15/17；S §3–9。冻结权威设计和上游Prompt只读。
前置/分支：L01原A/B正式PASS，杭州初始/选项POST为真实生产链，003/005安全公开页面收据重现问题；没有确认错误需求或选择酒店。branch feature/module-14-web-application，前HEAD3a7564fa1ea451c356b2d998094e9aa0b4de57fc、56用户index指纹E55F57634C38AB43FCD6028492B0AFE875A702A83517819ABFF538F9B05AE5AD保持，不切分支/worktree。
精确白名单：生产仅app/requirements/merge.py；测试仅tests/unit/requirements/test_merge.py、test_natural_language_normalization.py；收尾仅README.md、docs/IMPLEMENTATION_STATUS.md及两M14文件非规范现场记录。Dockerfile、verify_module14.ps1及原覆盖入口只读；工作区外证据/配置仅本次live-web及新<EVIDENCE_DIR>。无M01/03权威设计、Prompt、领域DTO/预算、迁移、依赖或其他生产文件修改。
R-F01：先补回归用例，在原验收test镜像仅挂新用例证明旧派生规则失败；再最小修复显式否定判定。原约束正文/metadata/evidence/presence/merge/纠正语义完全保留；只有整串是原明确否定值，或精确“行动限制”字段名+全角/半角冒号+完整原否定值时不派生辅助。额外正限制、空答案、未知字段名不得当否定；家庭/老人场景保留，原输入不原地改变。
R-F02：普通maintenance测试与同步后的新交付资格。两阶段合并验证：新增阴性/阳性及真实领域evidence/service确认链；M01全部unit/contract及原95% line/branch/combined；全项目unit/contract；M14真实PG/浏览器/容量/重启/上游/三coverage沿原Closeout完整入口。同步README/状态后新构建runtime/test/browser，实际命令 ./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-negation-20261006a -EvidenceDir <EVIDENCE_DIR>；原A/B逐文档与真实收据正式核查。新测试不skip/xfail/rerun、不改阈值。
测试矩阵：标签全角/半角/空白/联合否定正确清除仅mobility场景；“无，但不能久走/需要轮椅”等阳性不清除；标签只有空值/未知标签保守保留；原children/seniors与输入不可变；原M03证据不被改写且领域NeedConfirmation无错误辅助场景。M14其他合同和失败/取消summary保存保持。
允许：宿主文件/只读Git、隔离Docker离线测试/新测试PG及既有显式迁移；只取消自己这趟新测试任务。通过新资格及双审且无执行任务后，仅更换独立真实测试Web镜像、保留新PG/Key，实际浏览器从相同输入重新开始。原8080 Demo及用户任务不操作，真实供应商恢复调用必须等离线修复合格。
STOP：根因要求改冻结语义、广泛清洗文本、猜测否定、改变Prompt/其他上游、增加预算/降低阈值/迁移或超此精确白名单；未知用户并行修改、不能保留index/任务/Secret；供应商鉴权或余额问题单独报告。commit/stage/merge/push/PR/云部署未授权。
报告：前后branch/HEAD/index、三精确代码/测试文件与非规范收尾实际清单、原失败/取消及summary、旧源码红测试、新源码定向/完整/coverage/镜像/原上游Gate/清理、逐条双审和重测调用计数/质量、异常/迁移/原Demo保留及commit/push/PR/云部署和最终三场景状态。
```

### 16.2 C-R01+C-R02：测试控制器中断重启修复提示词

```text
目标：仅修复工作区外真实浏览器控制器的中断重启风险；先验证旧命令库存，再阻止“已开始但尚无完成回执”的副作用命令自动重放。原杭州a2因Docker停止而中断，保持失败/未完成事实，不计入三个完整成功。
依据：W14 §5/7/9/12.2、AC14-08/11/12/15/19/20；E14 §6/10 C06/C07/C11/C12/C13/C15、§12/13/16；S §3–9；M11 §22.3公共取消维护。此为测试装配修复，不改变生产合同或M15边界。
前置/分支：qualified runtime与453份构建输入不变；feature/module-14-web-application，前HEAD3a7564fa1ea451c356b2d998094e9aa0b4de57fc、56项用户index指纹保持。恢复后的独立PG/Web精确ID保持且ready200；旧001–016全部有完成回执，唯一无回执017是只读snapshot。原8080和用户任务不操作，不切分支/worktree。
白名单：仓库仅两M14文档及I的非规范现场记录；工作区外仅<EVIDENCE_DIR>/browser/driver.py、控制器安全检查脚本和本批安全收据。自有浏览器会话由Playwright保存到本批private-session.json，工具/报告/审查者均不读取其正文；真实.env、Key及其他凭证仍不读取。
C-R01：副作用start/click/choose_hotel执行前原子写started标记；恢复时存在started而无完成reply则STOP，先只读核对，不再次点击。已有完成reply只跳过原命令，不据此宣称普遍exactly-once。只读命令可恢复；准备异常与实际POST区分记录。保留每次初始化收据，保存自己新会话以供后续正常浏览器恢复。
C-R02：独立Docker网络none检查空库存、三种副作用未完成时均被识别、完成回执后均不重放、只读snapshot可继续，共8断言；宿主不跑Python，真实供应商调用0。旧控制器显式close成功后，只启动精确归属的原测试browser容器，原A/B只读核查后继续真实场景。
允许：安全文件操作、只读Git及Docker内部离线控制器检查；只关闭/启动自己测试browser，不更新生产源码、镜像、配额或PG/Key。旧a2如需维护只可使用M11公开request_cancel/settle_cancel/get_run且不执行Graph/供应商，真实blocked不得写为cancelled或补造summary；不能伪造Cookie或重放016。
STOP：存在副作用开始标记但无完成回执；无法确认精确资源/任务归属；source/index/冻结规范漂移；需要重放未知外部工作、改冻结合同、改依赖或操作原Demo；供应商鉴权/余额/配额问题仍沿§16实际报告与等待。
报告：8断言/exit/真实调用0、控制器审查关闭项、旧中断任务状态与summary缺失、已开始/完成命令安全核对、实际浏览器新初始化、453源文件/index/HEAD、异常准备命令及业务POST区分；迁移/stage/commit/merge/push/PR/云部署均NOT_RUN。三场景资格仍按§16实际完整结果统一审查。
```

### 16.3 R-F03+R-F04：正常终态断流提示修复提示词

```text
目标：关闭杭州a3真实终态页面“攻略已完成”仍显示“任务会继续”的A-LIVE-SSE-01/B-LIVE-UI-01高概率Demo P2。保留该趟demo_ready、2日/summary saved/复制下载一致的真实业务成功；修复后的最终源码三场景资格重新核验，不套旧资格。
依据/必读：W14 §5/8.3/9/11/12.2、AC14-08/11/12/14/15/19/20；E14 §6/10 C06/C07/C10/C12/C13/C14/C15/C17、§12/13/16；S §3–9，特别§7.3同步后新镜像和完整离线回归。API、领域、预算及权威设计保持。
前置/分支：原R-F01/R-F02与C-R01/C-R02均原A/B正式PASS；031真实公开页面复现、032/a3收据保留且不覆盖。下一真实场景POST暂停。feature/module-14-web-application，前HEAD3a7564fa1ea451c356b2d998094e9aa0b4de57fc、56项用户index指纹保持；不切分支/worktree。
精确白名单：生产仅app/web_application/static/app.js；测试仅tests/browser/web_application/test_demo_pages.py；收尾仅README.md（确有事实需同步时）、docs/IMPLEMENTATION_STATUS.md及两M14文件非规范记录。其他生产/测试、Prompt、Dockerfile、验证脚本、依赖、迁移及冻结文档只读。外部安全证据/控制脚本仅本轮live-web、terminal-red-20261006a和terminal-20261006a。
R-F03：先在既有真主图browser用例复制前断言正常终态无错误断流提示；补真实EventSource的可控迟到error/终态GET顺序与消息归属边界测试，明确它只验证UI时序、不冒充供应商真实验收。在未修app.js的新test/browser镜像执行Browser红回归，保留真实FAIL/清理；随后最小修复回调连接归属与终态及时关闭、仅清SSE专属提示。POST未知/复制反馈/真实读取失败不得被无条件清空；不重开流、不发业务重放。
R-F04：两阶段统一验证全部M14 unit/contract与全仓离线、真PG、浏览器、SSE关闭、容量、重启、上游及原三70%coverage，按原完整Closeout。同步准确状态后从当前源码新构建runtime/test/browser，命令./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-terminal-20261006a -EvidenceDir <EVIDENCE_DIR>。红回归独立命令同脚本-Set Browser -ProjectPrefix cta-m14-terminal-red-20261006a -EvidenceDir <EVIDENCE_DIR>；不使用真实.env/供应商，不把红批资格写PASS。原A/B对源码及原始安全收据逐文档核查通过后才恢复真实三场景。
测试矩阵：真主图完成结果/摘要保持、复制前正常EOF无误报；非终态error有合理提示且只读；EOF早于终态GET时清专属提示；关闭/替换后的迟到回调无影响；POST未知/复制/结果读取失败提示保留；普通HTTP、草稿/幂等/下载/取消/恢复边界及原C15/C17不回归。所有新测试无skip/xfail/rerun/降低阈值。
允许：白名单内文件小修、只读Git、隔离Docker内部Python及现有显式测试迁移；保留旧a1/a2/a3记录。新资格双审后，仅安全更换独立真实测试Web，保留PG/Key；旧a2中断任务不重放，必要维护限M11公开取消并如实接受blocked，不能伪造Cookie或补造summary。原8080及其他平台不操作。
STOP：需要修改已确认设计、其他上游或白名单外源文件；无法保护index/既有任务/Secret；需要清空全局消息抹去真实失败、重放未知外部工作、增加预算、跳过完整Gate/coverage或改变依赖；新源码资格未通过不得继续供应商调用。
报告：分支/前后HEAD/index与冻结hash、两精确代码文件及实际收尾清单、旧a3成功及页面FAIL分别记录、红/修后完整Gate命令exit/计数/coverage/新镜像/清理、逐条双审、三最终场景调用与质量、资源/异常/迁移、原Demo保留；stage/commit/merge/push/PR/云部署均NOT_RUN。
```

R-F03旧源码红Browser明确FAIL，复制前真主图准确复现；修后定向Browser命令exit0，305模块与10 Chromium（含3个明确UI时序fixture）、原非终态SSE关闭通过，真实外调0。修后诊断证据在本轮live-web/offline-terminal-green，不能替代本节指定同步后完整Closeout或真实三场景。原A源审SOURCE_READY，正式两阶段双审PENDING。453构建输入、56项index/HEAD及三冻结规范hash保持；README当前事实准确未改，Dockerfile的COPY app/tests覆盖本次变化。最新完整结果以I的实际收据更新，不预授PASS。

### 16.4 L-R01+L-R02：独立测试任务维护与版本更换提示词

```text
目标/依据：安全收束自己因Docker中断的杭州a2，再仅更换18080独立测试Web为R-F04正式合格版本。引用W14 §5/7/8.3/9/12.2及AC08/11/12/15/19/20、E14 §16.2/16.3、W11 §22.3、S §3–9；不改变合同、不处理原8080暂停任务。
前置/分支：新terminal-20261006a完整Closeout及原A/B正式PASS；453构建输入/三冻结规范hash、feature/module-14-web-application、HEAD3a7564fa1ea451c356b2d998094e9aa0b4de57fc及56项index指纹均保持。核验自有Web/PG精确ID、卷及project labels，当前无有效执行/未决接纳；浏览器所有副作用已完成，显式关闭控制器后停止精确独立Web，防止并行接纳。
白名单：仓库仅两M14文档和I非规范事实；外部仅本批live-web安全helper/receipt/非Secret镜像override。生产/测试/迁移/依赖/规范正文不改。维护helper cancel_interrupted_a2.py与update_terminal_runtime.ps1，后者沿原受审更新步骤，仅替换合格镜像/收据路径并保留原helper。
L-R01：新合格runtime、原独立Compose配置的一次性容器装配真实M10全注册表/原容量和公开M13 definition；拒绝执行extractor/酒店/研究/规划端口，不返回fake业务DTO、不创建供应商客户端。公开M11 get_run→request_cancel→settle_cancel→get_run只针对a2 UUID684e898f-ec2c-4577-964e-554bb68de029并核对trace；不调用start/resume/recover/直接Graph，不访问私有service._deps或SQL写入。实际blocked保留，不写cancelled或补造summary；finally依次关闭M11/M13/M10。
L-R02：只读重新核验有效lease、inflight、未决request、reserved admission及open run均0，旧summary/PG卷/Key保持；更新独立Web，核验qualified image/默认CMD/ready200/静态源码hash。浏览器正常恢复自己的新会话，不读取秘密正文，不冒充旧a2 owner。统一两阶段核查后恢复§16三个真实场景。
测试矩阵/允许：helper网络none语法/import/公开调用表先验证；维护只访问独立PG，收据包含前后状态与拒绝入口计数0；重读计数/精确镜像与源hash/HTTP就绪/旧摘要数保持，不触发供应商。只读Git、宿主安全文件、Docker内Python、公开取消与精确自有Web停止更换允许；既有迁移仍0。
STOP：存在有效业务执行、未完成副作用、身份或源码/index漂移、维护需Graph/供应商或私人Cookie、requested未安全收束、拒绝业务入口计数非0、资源/清理/资格失败。不能直接SQL改状态、覆盖失败或重放未知动作；原Demo/其他平台不操作。
报告：分支/前后HEAD/index/hash、helper实际命令exit、公开维护状态/summary真实缺失、精确资源/镜像/PG/Key保留、更新ready与源码匹配、双审、异常及真实调用0；迁移/stage/commit/merge/push/PR/云部署逐项NOT_RUN，三场景实际结果另报。
```

原A/B现均正式R-F03 PASS/R-F04 PASS，逐W14受影响AC、E14 §16.3矩阵与S §7.3亲读实际原始收据；A-LIVE-SSE-01/B-LIVE-UI-01的源码及正式离线维护阻断关闭，无未结阻断。独立原始9条Docker查询保留exit0/stdout空数组、剩余0于terminal-cleanup-inventory.json；薄汇总序列化警告不替代完整leaf证据。453输入、56项用户index、HEAD与三规范hash保持。接续E14 §16.4仅执行自己的独立任务维护/18080 Web更换；当前最终源码真实成功仍0/3，L-R01/L-R02实际验收及真实现场三场景不预写PASS，迁移/stage/commit/merge/push/PR/云部署均NOT_RUN。

L-R01/L-R02实际已完成：先显式关闭033控制器、停止精确独立Web，再使用qualified runtime的一次性容器执行公开M11维护；a2从open/blocked、cancel_requested=false收束为lifecycle=blocked、cancel_requested=true，settle_status=blocked。五拒绝业务入口计数全部0、供应商客户端0、无Graph或恢复、未直接执行SQL改状态；取消意图和blocked由公开M11正常持久化，finally关闭真实M11/M13/M10、exit0；实际没有a2 summary，旧2份available摘要保留，不伪造cancelled或新摘要。后核有效lease/inflight/未决request/reserved/open均0，一次性容器已自动清理。

独立18080 Web已更换最终qualified runtime 12912f5d…e54486，新Web ID5139002d54d73e3c0bc1225c6aae4f76acfb787b9b0dd2fcdd0b1e8656621989；原PG精确ID、同卷/只读Key绑定、Key长度/UTC修改ticks保持，Key正文未读。默认生产CMD、ready/home/JS/site.css均200，HTTP静态hash同已合格源码，OOMfalse/restart0。新controller正常载入自己的会话，ready-recovery-03 PASS；副作用库存0、不重放。维护准备的unsupported --no-build及外部脚本import path错误均在业务入口前失败，原失败日志保留，最终以未请求build的run --pull never加runpy执行；HTTP app.css误探及DateTime字符串对比误报只修正观察器，实际源码/Key均未变、业务POST0。安全收据为cancel-a2-public-maintenance-receipt.json、terminal-live-update-ready.json、terminal-live-replacement-audit.json及各准备失败收据。原8080/用户任务未操作，迁移与Git写/云部署均NOT_RUN；原A/B实际两阶段核查PENDING，新最终真实场景仍0/3。

补充L-R实际只读验收：034-read-previous-a3-read-case.json通过新的qualified Web读取旧杭州a3，两日完整Markdown/摘要saved、SHA256仍00724cba00c4b9e58a7d1178b3ce92930aba524fee4ce35e47f06a34b0f37f23；真实终态页message空、page_errors0。该读取不计为新场景成功，不触发供应商，旧成功原收据未覆盖。

原A/B已正式L-R01/L-R02 PASS，独立18080的最终qualified runtime已安全更换；当前杭州a4/上海a1两完整场景demo_ready、summary saved、确认与最终输出一致、复制下载PASS，成功2/3。按§16统一进行原A/B L02/L03核查；苏州及重启读取仍PENDING，详细事实与限制见I最新收据。

现场L04第三场景苏州a1为真实workflow_provider_failed，tool_arguments_invalid严格参数拒绝、运输失败0，available失败摘要1743 bytes；不计成功、不recover/replay。按§16允许保持合格源码、schema、同输入/模型/预算和原配额，从UI创建独立a2重测，原失败保留；不能改日志读取供应商正文或猜测不合法字段。成功仍2/3，原A合同核查PASS、B及新任务/重启验收PENDING，详见I。本次仅非规范事实和观察helper，Git写/迁移/云部署均NOT_RUN。

### 16.5 L04：第三完整场景及统一重启读取实施提示词

```text
目标/依据：完成§16苏州第三个完整真实场景后停止新业务调用，统一验证三个已成功场景经独立Web进程重启仍可完整读取。引用W14 §5/8.3/9/11/12.2及AC08/11/12/13/15/19/20、E14 §6/10/12/13/16和S §3–9，不扩大为全M15或云资格。
前置/分支：R-F04正式Closeout及原A/B PASS、L02/L03双审PASS；苏州新任务实收完整结果PASS，原失败留存。feature/module-14-web-application，前HEAD3a7564fa1ea451c356b2d998094e9aa0b4de57fc；453构建输入/三冻结hash/56项用户index保持。第三成功前不能执行统一Web重启。
白名单：仓库仅两M14文件与I非规范事实；外部仅本批live-web安全helper/receipt/公开合成输出。生产/测试/README/镜像/迁移/依赖/冻结合同不改；原8080/其他平台/PG卷/Key不操作。
允许与步骤：核对三个成功run与summary，分别保存独立PG种类/数量、journal、预算、摘要hash/大小的只读聚合；核验有效lease/inflight/未决request/reserved/open均0及控制器副作用待定0。正常close自有浏览器并取得完成回执/停止状态后，按精确ID、project/service、qualified image核验，仅restart独立18080 Web；保留PG容器/卷/只读Key，记录Web StartedAt前后不同、默认CMD/ready/静态hash相同。正常restart自己的精确browser，沿自身Playwright会话读取三个已知成功case，不读会话秘密。read_case按真实HTTP重读M12完整输出与saved摘要；逐份比较日块/hash/统计及前后只读聚合完全一致，额外业务POST和模型/地图预算增长均0。
测试矩阵：第三需求确认与最终结果一致、3日完整/summary saved、复制下载一致且终态无误断流；重启后三case均完整load/summary saved/原hash与日数保持/page_errors0，无额外supplier work。资源采样完整收尾、OOM/连接/内存如实统计；不重复已通过离线Gate，只有新源码/失败/未决风险才追加必要测试。
STOP：第三完整结果未成功、任一执行计数或未知副作用未收束、source/index/identity漂移、需恢复/replay未知任务或读秘密/原供应商正文、需动PG/Key/原Demo或改合同、重启后数据/计数/静态不一致。失败保留；不得用旧版本a3或观察器修正充当第三成功。
报告：branch/前后HEAD/index/hash、九份本轮实际修改文件及异常、真实失败/三成功与调用数/输出/摘要、重启精确资源与读取hash/零增长、原完整Gate计数/coverage/双审/清理、资源限制、独立初始化已执行迁移与本阶段迁移0分别记录。stage/commit/merge/push/PR/云部署均NOT_RUN；原A/B逐条最终核查PASS后方可报告完成待提交。
```

L04实际三份完整结果及统一Web重启读取已完成：2份demo_ready、1份主营业信息未知degraded；三份M12/summary保存、导出/重读hash与质量保持，前后只读记录/journal/预算/摘要均不增，实际重启后POST0。安全收据live-l04-receipt.json及I最新记录；原A/B正式最终实际审查PENDING，不将源级SOURCE_READY代替实收验收。原a1失败和旧版本成功均保留且未混入第三资格；本阶段迁移/Git写/云部署仍NOT_RUN。

原A/B最终已逐W14/E14/M13及原始安全证据正式L04 PASS；本轮§16完成，2demo_ready+1degraded三份完整结果/持久摘要/导出一致/真重启重读与零额外业务已验证。live-l04-receipt.json正式PASS，达到完整结果目标后停止新业务调用；保留旧失败、不授全M15/云资格。九份实际文件、HEAD/index/冻结hash、迁移/异常/资格清理见I最终记录；stage/commit/merge/push/PR/云部署均NOT_RUN，待提交。


## M09参数修复展示执行入口（2026-10-06）

设计为M14本次展示维护及M09 §26，按[E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准) R03+R04实施。生产只改static/app.js失败文案；沿原browser/PG/owner Gate和两个子agent逐条核查；冻结正文不改，新真实API资格NOT_RUN，提交/发布等待用户授权。

## 枢纽澄清与安全输入提示执行入口（2026-10-07）

采用 D14“枢纽澄清与安全输入提示增量”、D01/D03新端点规则及 D13输入映射；统一执行 E01“枢纽澄清维护执行入口”HF01+HF02、HF03/MODULE-CLOSEOUT 的目标、前置、精确文件白名单、操作、测试矩阵、STOP和报告。single 类型选项无代选，含目的城市的标签以 textContent 安全呈现，实际答案仍经原 WebClarifyRequest/M03/M01。input_issues 是只读有限投影，不提供已结束任务的恢复/澄清动作，不改变历史摘要。证明所属详情终态读取与列表/SSE/非输入失败的禁止读取边界，隔离 PG/Chromium 和双审通过后保持未提交，真实 API/生产部署 NOT_RUN。
### 前端首版最小扩展实施规范（2026-10-10，已获批准）

引用 W14“前端首版批准增量：流程画布与活动阶段投影”、M13 活动阶段批准增量与执行规范、W14 §8.3/§11/AC14-04/11/14/15/20、本文 §8—10、M12 §8、共享规则 §3/§5/§7—9。用户已明确批准扩大到真实安全阶段投影；不再以人为执行 phase 的 fixture 宣称实时业务资格。

UI03+UI04 相邻实施后统一测试。前置分支 feature/web-ui-progress-results，main 起点 aa83d5ecbc925411b1d12b1da77db92906010348；保留本轮已识别六份修改及 ../m13-live-evidence/。M14 精确白名单：app/web_application/models.py、service.py、static/task.html、static/app.js、static/site.css；tests/unit/web_application/test_service_control.py、tests/integration/web_application/test_workflow_api.py、tests/support/module14_browser_worker.py、tests/browser/web_application/test_demo_pages.py；两份 M14 文档及 closeout 的 README.md、docs/IMPLEMENTATION_STATUS.md。M13 的五份既有文件白名单沿其本次规范。不新增文件、路由、迁移或依赖。

UI03 落实 M13 有界真实节点观测、活动身份校验与释放；UI04 增加严格 progress_phase 字段及服务合法投影，前端消费并保留未知/中断语义。不得改 M11、完整 state、持久合同 digest、业务预算、领域验证和成功资格。小范围合同一致修复可在上述名单内执行；进一步扩大范围/改变设计需用户同意。

统一测试矩阵：M13 作用域与类型/嵌套/取消/异常释放及不改变摘要；M14 DTO 非法组合、归属和非本进程/非活动标记拒绝；真实 M11/M13+隔离 PG 在需求、酒店、研究、规划、整理执行中公开 run.phase=None而progress_phase为实际阶段，GET/SSE只读不增调用；暂停/终态标记为空、重开进程不补造；实际主链浏览器轮转、补研回退、未知、中断、手机居中/无溢出、减少动画、合法人工选择、安全攻略和原字节下载。模型/地图只用 typed 替身，不能称真实供应商通过。补研真实链与界面投影各自验证，不改供应商输出门槛。

统一使用 scripts/verify_module14.ps1 的定向 Browser 及同步后全新前缀 Closeout；M13增量同时由完整 unit/contract和真实业务主图覆盖；不得降低既有覆盖率门槛。失败原样保留，修复后换独立前缀。安全资源预检与精确清理沿既有脚本；不碰原8080、18080或云端。测试/审查失败、非法范围或身份不符则停止对应后续资格，不报告 COMPLETE。两个代码子 agent 逐文档核查；最终报告必须包含分支/前后 HEAD/文件/迁移/测试/异常文件/commit/push/PR/部署/状态。stage、commit、merge、push、PR、真实 API 和部署均未授权。


## 2026-10-11 批准执行入口：研究合同修复

执行本设计同日R条款及 [M08 R1～R6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约拥有阶段目标、前置/分支、联合文件白名单、允许操作、测试矩阵、停止条件和最终报告。R1/R2、R3/R4、R5/R6每组结束两个子agent逐条对照文档审查。未经新的提交授权保持未暂存；禁止push/PR/部署。

## 2026-10-12 必要资格判断执行入口

执行本设计“必要资格判断接线”和 [M08 Q08合同](08_candidate_research_loop.md)、[Q1～Q6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约明确分支/HEAD、既有变更、每组目标/前置、生产及测试白名单、允许操作、统一测试、停止条件和报告字段。每两阶段统一测试后两个子agent逐条对照文档；全部离线通过后隔离三城各一次真实API，记时间及token。不得提交、推送、PR、部署或更新既有服务。

## 2026-10-13 UI05、UI06 实施契约

依据：[M14 页面风格与攻略阅读优化](14_web_application.md#2026-10-13-页面风格与攻略阅读优化)、M14 §8.3/§11/AC14-04/11/14/15/20、[M12 §8～10](12_result_formatting.md)、[共享规则](SHARED_IMPLEMENTATION_RULES.md)与用户本轮要求。用户指定继续使用已有 UI 分支，优先于共享规则中新建功能分支的默认安排。

前置条件：确认 main 与 UI 分支的 HEAD、无未知已跟踪修改；保留 `../m13-live-evidence/`，不读取正文；UI 分支从 237b243 快进到 ff57088 后开始编辑。分支为 `feature/web-ui-progress-results`。使用当前已确认的公开 DTO，不运行真实供应商，不读取真实 .env、Key 或历史访客数据。

文件白名单：`app/web_application/static/` 下的 index.html、task.html、history.html、samples.html、site.css、app.js；`tests/browser/web_application/test_demo_pages.py`；两份 M14 文档；`docs/IMPLEMENTATION_STATUS.md`。README 与 Dockerfile 只读核查；本批静态资源由现有 `COPY app ./app` 打包，浏览器测试由 `COPY tests ./tests` 打包。

UI05：统一公共样式与四页布局，按现有字段调整补充需求、确认、酒店候选、历史列表和样例卡片。保留画布的状态算法和布局，不引入依赖、后端模板、接口或虚构候选信息。

UI06：在安全 DOM 排版后识别唯一来源章节与合法编号，默认折叠来源目录并收起匹配的正文标签；展开后能够恢复查看。来源解析失败或日块边界不匹配时不做精简。原始 Markdown、复制和下载使用同一服务器快照，不写数据库，不调用模型或地图。

允许操作：本地快进同步、编辑白名单、语法检查、定向浏览器与现有 Browser Gate、构建本批 runtime/test/browser 镜像、检查截图及精确清理本批隔离资源。阶段相邻实施后统一测试；不暂存、不提交、不合并 main、不 push、不创建 PR、不更新云端或既有本机服务。

测试矩阵：

| 项目 | 验证方式 |
| --- | --- |
| 四页公共样式、导航、空状态、手机布局 | Chromium 的实际 DOM、计算样式、键盘操作与截图 |
| 补充、确认、酒店、取消、历史、样例 | 原 Browser Gate 的离线真实业务主链与界面用例 |
| 来源折叠与展开、未知编号、代码与链接保留、异常回退 | 定向浏览器断言 |
| 具体未核验提醒、降级、日程与乘车顺序 | 原攻略用例与新增呈现断言 |
| 安全 DOM、复制下载原字节、流程状态不变 | 原浏览器用例及对应源码审查 |
| 模块与打包 | 现有 Browser Gate；同步后重建 runtime、默认 CMD 安全启动失败、静态资源一致性 |

本批只修改静态呈现，不执行全项目数据库/上游覆盖率或真实 API 资格；按用户要求使测试与改动范围相称。Browser Gate 在独立测试 PostgreSQL 中初始化原有结构，属于测试准备，不是产品迁移。生产迁移为零。

停止条件：未知工作区冲突；需要修改白名单外生产代码、持久化格式或导出语义；解析不确定却丢内容；依赖、预算或安全门槛变化；离线测试失败而准备发布；发现凭据或真实数据进入页面、日志或截图。失败应保留收据，定位后修复重测受影响部分。

报告格式：分支、切换及同步前后 HEAD、文件、规范逐条对照、迁移、定向/Browser/runtime 的实际结果与截图、未运行项及理由、异常文件、暂存/commit/merge/push/PR/部署状态、最终状态与限制。

### LS01、LS02：已验证 UI 的本机网页同步

依据：上述设计“本机网页同步”、UI05/UI06 实收及用户本次同步要求；沿用 M14 §7.4 的显式恢复规则与共享执行规范。目标是让用户在原本机网页查看效果。分支仍为 `feature/web-ui-progress-results`，HEAD 为 ff570887e090550a26099b808d8518ad0d60b419。前置条件为 Browser Gate 与 runtime 验证通过，新旧镜像后端内容一致。

LS01 核对现有 18080 Web、数据库和原 8080 项目身份，复用原 Compose 配置及 env-file 链。秘密值只由 Compose 在内存中解析和比对，不写入收据；暂停密钥只核对绑定位置及文件元数据。无有效租约、accepted 命令、claimed 请求、reserved 接纳或近期执行后，才允许正常关闭 Web。旧 clarify 不确定记录和人工暂停记录保留，不为换版进行业务维护。

LS02 仅重建该项目的 m14-web 容器，使用已验证的 cta-m14-ui-unified-20261013c-runtime，不重新构建、不拉取镜像、不启动迁移。确认正常退出后检查数据库计数未改变，再启动新 Web；确认原数据库、数据卷、密钥及原 8080 项目身份保持。随后检查健康状态、四页与静态资源，打开本机网页。

文件白名单为本节对应三份 M14 文档，以及忽略目录 `.publication/ui-unified-20261013c/` 内的本机同步脚本、镜像覆盖文件和安全收据。允许原配置只读核查、精确 Web 停止与换版、HTTP GET、浏览器查看；不发业务 POST，不恢复或取消任务，不提交、合并、push、PR 或部署云端。两阶段统一执行资源身份、持久化计数、ready 与 HTTP 字节核对，沿用已通过的 381 项单元/合同和 32 项浏览器验证，不重复完整测试。

停止条件为活动执行、配置或后端不一致、正常关闭失败、数据库或密钥变化、资源身份不符、健康或资源字节核对失败。必要回退仅使用原镜像与配置，不改变业务记录。报告保留分支、前后 HEAD、文件、迁移、验证、准备失败、异常文件、Git 操作、本机及云端换版状态和最终访问地址。

### SP01、SP02：用户指定攻略的静态样例替换

依据：M14“用户指定攻略作为完整样例”、§8/§11、M12 结果加载合同、上述本机同步契约及共享执行规范。目标是将用户明确选定的攻略作为默认完整样例，并同步现有 18080 网页。分支为 feature/web-ui-progress-results，前后 HEAD 保持 ff570887e090550a26099b808d8518ad0d60b419。前置条件为确认目标任务 closed、原输出可完整加载，既有工作区修改与异常目录保留。

SP01 仅导出指定终态结果：所有数据库连接强制只读，使用原有有界检查点读取与完整指针核验，再调用 M12.load；禁止执行图、模型、地图、恢复或迁移。私有导出暂存到忽略目录，检查密钥、任务/trace 标识、内部引用、联系方式和本机路径。只读审查通过后替换 samples/demo_trip.md 和 manifest.json。保留三天正文与降级提醒，仅补充快照说明和公开样例元数据。

SP02 将首页说明和真实结果标签与新样例一致，保留合成来源标签及来源折叠规则。同步 README 的固定样例说明，但云端仍为原版本，应准确说明本机更新与待部署状态。调整原浏览器用例的样例断言和显式合成夹具。两阶段完成后统一核对清单 DTO、原正文保持、隐私与三天完整性、真实/合成标记、来源展开收起、手机阅读及静态读取不调用供应商。使用原 Browser 流程的相关四项定向用例，加上生成暂停夹具的既有用例，共五项；沿用原 SSE 关闭、资源关闭和精确清理检查。不重复全项目、覆盖率或真实 API 验收。

文件白名单为 samples 下两份文件、static/index.html 和 app.js、tests/browser/web_application/test_demo_pages.py、README.md、两份 M14 文档、IMPLEMENTATION_STATUS.md，以及 `.publication/ui-sample-20261013a/` 中的导出、定向验证、本机同步脚本与安全收据。允许构建 runtime/test/browser 镜像、隔离浏览器验证、正常关闭与精确更换现有本机 Web、HTTP GET 和浏览器查看；复用原数据库和密钥，沿用 LS01/LS02 的活动执行检查及资源保持规则。禁止业务 POST、伪造访客身份、读取其他任务正文、数据库修改、提交、合并、push、PR 或云端部署。

停止条件为结果加载或脱敏失败、出现活动执行、新旧后端差异超出样例读取、原文非说明性改动、旧数据或密钥变化、测试失败、公开样例含私有标识或把降级称为完全成功。报告记录分支、前后 HEAD、文件、原文及隐私检查、实际测试与准备失败、迁移、异常文件、Git 操作、本机换版与云端状态。

### JR01、JR02 与 D01、D02：本地提交、合并及云端更新

用户本次明确授权“提交至本地分支并合并至 main，然后把最新的 main 部署至远程云服务器”。依据 UI05/UI06、SP01/SP02 的设计和实收、M15 §5/§6/§8.2/§10、M15 执行规范 C01/C02/C07 的发行、凭据保护与回退规则，以及共享执行规范。授权包含本批精确 stage、commit、合并和云端 Web 换版，优先于默认禁止 Git 写入与部署的规定；不包含 GitHub push、PR、新库迁移或追加真实 API 场景。

JR01、JR02 相邻执行：在 feature/web-ui-progress-results 对照六条展示规则及样例合同复核十三份已跟踪修改，保留 ../m13-live-evidence/；仅暂存这十三份文件并提交，随后切换 main，若仍从 ff570887e090550a26099b808d8518ad0d60b419 开始则 fast-forward 合并。遇到未知修改、分支漂移或冲突时停止，不覆盖或清理文件。白名单为 README.md、static 六份文件、samples 两份文件、test_demo_pages.py、两份 M14 文档及 IMPLEMENTATION_STATUS.md。

D01、D02 相邻执行：从合并后的干净 main 构建私有发行物，记录实际 HEAD、文件清单、镜像及归档指纹。私有发行与安全收据位于 <PROJECT_DIR>/.publication/cloud-ui-release-20261013a/；构建输入仅为 Git 树中的 app、scripts、README、Dockerfile、依赖与 Compose 文件，不包含 .git、真实 env、密钥、测试证据或业务数据库。构建当前 runtime，检查默认入口、UID、依赖和八份页面/样例资源；沿用已通过的 381 项单元/合同、32 项 UI Chromium 及五项样例/关闭定向验证，不重复付费规划或全项目验收。

目标为腾讯云上海实例 INSTANCE_ID、203.0.113.10:8080，既有 cta-cloud-demo 项目。云端发行目录限定 /srv/china-travel-agent/releases/<release>/，仅包含本批镜像归档、当前 Compose 副本、发行清单、必要维护脚本和安全收据；原发行目录只读保留。复用 /srv/china-travel-agent/private/m15/config.env，只允许更新本批镜像引用，其他配置只在内存中比对；保留原暂停密钥、独立备份、PostgreSQL、数据卷、网络与公网规则。

云端换版前核对实例、容器标签、挂载、配置权限及活动执行。存在有效租约、accepted 命令、待接纳请求或正在执行任务时暂缓；旧暂停和未知记录不自动恢复或维护。新镜像加载、校验通过后正常停止精确 Web，确认正常退出、持久化计数保持，再以同项目及原配置仅重建 Web，不重建 PG，不运行任何迁移。健康请求使用原公网 Host，检查四页、样式、脚本和公开样例的内容一致性；检查服务资源及公开网页，不发规划 POST。失败按已保留的原镜像和原镜像配置回退 Web，不改数据库或 Key。

测试矩阵为已有 UI/样例实收、当前 main runtime 默认 CMD 和打包字节、云端身份/配置/活动执行预检、正常退出与数据保持、ready/live/静态/样例及实际公网浏览器读回。停止条件为未授权文件、来源漂移、构建或加载失败、活动执行未结束、schema 不兼容、正常关闭失败、数据/密钥变化、不可确认的发布状态或网页读取失败。报告包含分支、前后 HEAD、精确文件、迁移、验证、准备异常、commit、merge、push、PR、云端镜像、访问地址及最终状态；实际 Git/部署结果以本批安全收据和会话报告记录。

## 有效输入调整执行契约（2026-10-13）

设计依据：M14“有效输入与历史兼容”、主设计 §3.1、M01 §6/§8/需求确认、M05 公共状态与检查点、M12 §5–8/§11、M14 §5–11；执行依据：本文及 SHARED_IMPLEMENTATION_RULES.md。用户最新批准范围优先适用。本任务继续使用 feature/web-ui-progress-results；开始 HEAD 535a564dc0570fdc8399ce67acda8d966a715902，暂存为空，已有 ../m13-live-evidence/ 不读正文、不改动。

阶段 IN01/IN02：完成新任务范围、提取版本、确认及手工模板兼容，再调整网页引导、问题投影及地图选项。阶段 OUT01/OUT02：新结果模板及旧读兼容、定向测试，再由一个独立子 agent 逐条审查。两个相邻阶段完成后统一运行其测试。本机 LS03：审查问题修复后更新 127.0.0.1:18080 的原 Web 服务，只读核验旧库、暂停 Key 绑定和数据计数，保持 PostgreSQL 和原卷。

代码白名单：app/requirements/{collection_policy.py,models.py,extraction_contract.py,service.py,merge.py,dialogue.py,manual_template.py,__init__.py}；app/requirement_extraction/{prompt_loader.py,adapter.py,session.py,events.py,prompts/extract_v5_active_inputs.txt,prompts/repair_v5_active_inputs.txt}；app/workflow_contracts/state.py；app/requirement_subgraph/coordinator.py（同版本初始空状态检查）；app/demo_workflow/models.py；app/web_application/{service.py,presentation.py,static/index.html,static/app.js}；app/result_formatting/{artifacts.py,projection.py,renderer.py,service.py}。测试白名单：相关 unit/contract 中的需求范围、提取版本、状态和命令、结果读写及版本、网页问题投影用例；tests/browser/web_application/test_demo_pages.py；tests/support/module14_browser_worker.py。文档白名单：README.md、docs/00_agent_master_design.md、docs/01_requirement_and_dialogue.md、docs/05_requirement_subgraph.md、docs/12_result_formatting.md、docs/14_web_application.md、docs/14_web_application_vibe_coding.md、docs/IMPLEMENTATION_STATUS.md。私有验证、同步脚本与收据写入忽略目录 .publication/ui-active-inputs-20261013a/。

允许：上述范围内修改、离线模型与地图替身测试、隔离测试数据库初始化、构建当前 runtime/test/browser 镜像、只读生产计数、本机 Web 容器换版和失败回退。不得修改 M09 规划规则、真实供应商预算、存量任务或公开样例；不得提交、合并 main、推送、创建 PR 或更新云端。

测试矩阵：需求与提取版本；缺字段/冲突/确认/修订/新旧手工模板；状态/命令/检查点往返；新旧结果 prepare/save/load 与确定性；网页只收集有效字段、文字入口和地图选项；浏览器正常链、刷新、移动页面；Docker 默认入口和本机 ready/live、生产文件一致。保留全部旧断言，通过版本化场景增加新断言。测试规模按本次变更，不重复原 M15 云端或真实供应商资格。

停止条件：需要改变四项保留能力、删字段或迁移旧库、出现目标文件冲突、改白名单外核心模块、旧结果或检查点字节不兼容、独立审查存在未处理问题、本机目标/Key/卷不一致或正在执行不可安全停止的任务。报告包含分支、前后 HEAD、文件、迁移、各层测试、异常文件、审查结果、commit/push/PR、同步和最终状态；未运行的资格明确说明原因。

### 合肥首页预设执行补充

沿 M14“合肥三日游首页预设”设计、有效输入执行契约及共享规则，在 feature/web-ui-progress-results 完成相邻 PRE01/PRE02：改写 static/index.html 的默认内容及引导，再验证有效字段完整性并同步现有本机 Web。前置为上一批输入收集兼容和本机预览通过；其他工作区改动保留。仅替换目的地和对应车站，日期、时间、天数及偏好保持。

白名单为 app/web_application/static/index.html、docs/14_web_application.md、本文、docs/IMPLEMENTATION_STATUS.md；验证和同步文件位于忽略目录 .publication/ui-hefei-preset-20261013a/。允许静态文案修改、断网替身提取后的真实领域校验、runtime 构建和原 18080 Web 换版；测试只覆盖预设字段、确认准备状态、页面源一致性和资源保持。无需重复上一批完整浏览器、全项目或真实供应商资格。

日期及枢纽字段缺失/冲突、需要跳过正式确认或改后端逻辑、后端字节变化、同步目标或数据/Key 不一致时停止。报告包含分支、前后 HEAD、修改文件、迁移、定向验证、异常文件、commit/push/PR 和本机同步结果。不提交、合并、远程推送或更新云端。

### 有效输入与合肥预设的本地集成

用户明确授权“提交至当前本地分支并合并到 main”。依据 W14“有效输入与历史兼容”“合肥三日游首页预设”、本文有效输入执行契约及共享实施规则，完成相邻 GI01/GI02：先在 feature/web-ui-progress-results 精确暂存并提交已完成改动，再将该提交快进合并到本地 main。此次授权覆盖本地 Git 写入，取代本批此前等待提交的限制。

前置为已有独立审查、定向兼容、浏览器、预设及本机同步通过；开工 UI 分支和 main 均为 535a564dc0570fdc8399ce67acda8d966a715902，暂存为空。文件白名单固定为 IMPLEMENTATION_STATUS“2026-10-13 有效输入调整与本机网页同步”列出的 41 份文件，README 及状态记录仅同步真实交付说明。使用明确文件清单暂存，不使用 git add .，保留其他工作区文件和 ../m13-live-evidence/；验证及 Git 收据位于忽略目录 .publication/ui-local-integration-20261013a/。

测试矩阵为白名单和暂存差异、既有资格的源码一致性、git diff --check、runtime 构建及默认入口、提交树与 main 合并结果。生产代码未新增功能，不重复已完成的完整离线矩阵或真实供应商测试。允许本地 commit、switch main、merge --ff-only；不执行 push、PR、迁移或云端部署。

发现未知改动、已有暂存、已验证源码变化、资格缺失、HEAD 漂移或合并冲突时停止，保留现场，不覆盖文件。最终报告包含分支、前后 HEAD、文件、迁移、测试、异常文件、commit、合并、push、PR 和最终状态。
