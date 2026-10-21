# M15 云端 Demo 部署与交付执行规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 配套权威设计：[15_demo_release_gate.md](15_demo_release_gate.md)，下称T15。状态：待用户批准，当前仅文档写作与只读审查授权。
> 本文定义未来阶段；不是现在执行部署的提示词。没有对应授权时，不运行Docker、不传输文件、不写服务器、不迁移、不调用真实API。

## 1. 目标、环境与授权凭据

执行T15 §1、§3所定义的云交付范围，复用现有生产代码，完成一个独立腾讯云部署及最小真实场景。M15是Gate，不拥有新的生产包、DTO、Service或迁移。

| 授权项 | 允许进入的动作 | 必须写入动态提示词的事实 |
| --- | --- | --- |
| 文档批准 | 冻结T15/E15及§3范围适用关系 | 批准消息定位、两文件hash；仅批准文档不开放外部步骤 |
| 构建/制包 | C01当前源码Docker构建与私有发行物 | 本机精确目录、源HEAD/dirty构建输入、镜像名、资源前缀 |
| 云部署准备 | C02传输、加载镜像、私有env与Key初始化 | 核验后的实例、账号、目录、资源、新增文件与凭据渠道；不得默认读本地.env |
| 新库迁移 | C03启动新PG、M10/M11/M14显式初始化 | 精确项目/卷/连接目标、新库前提和失败保留方案 |
| 服务/公网部署 | C04—05启动Web、开放8080、公网访问及保留服务 | 目标origin、防火墙规则/回退范围、公共匿名试用按M14 §6.2持续消费API的授权；仅一次验证额度不够 |
| 真实API与重启 | C05—06一个正式场景、同进程人工暂停续接、完整summary保存后的终态Web重启 | case ID、维护正式次数1、上游预算引用、时限、凭据范围；新增诊断调用0，区别公网其他访客 |
| 交付收尾 | C07 README/I、必要runtime重建和最终云镜像更换/读回 | 两个仓库文件白名单、本次私有构建输入和远端更换对象；不得扩大到app/tests |
| Git | 不属于以上授权 | stage、commit、merge、push、PR逐项依用户明确授权；实施者默认均禁止 |

用户可以一次明确授权一个相邻阶段批次及其所列远端动作，也可以分开授权。已有明确授权沿用，不重复征求；无法定位的权限不得推定。批准文档、只读服务器检查和购买服务器均不能替代部署/API/迁移授权。

所有Python、版本检查、compileall和将来必要的pytest/coverage在Docker内执行，宿主Python不提供验收证据。一次性离线检查使用新容器、--rm、默认--network none、只读root、/tmp tmpfs、无live Secret；PG检查仅使用精确项目网络；长期云Web/PG采用T15 §3.1、§6的持久服务环境。本方案随设计待批准。

## 2. 分支、冻结文件与路径合同

### 2.1 工作区与源版本

工作目录 `<PROJECT_DIR>`，Git根 `<WORKSPACE>`。设计分支及默认后续分支为 `feature/module-15-demo-release-gate`；每批核验实际分支、HEAD、subject、dirty/staged，具体HEAD只进入当次收据。

新实施采用获准源码基线；若T15/E15仍是预期未提交文件，明确列入只读预期dirty，不能因它们未提交自行stage/commit。发行物同时记录代码HEAD与批准文档hash。后续C07允许自己的预期README/I dirty；不把续执行当新clean任务。

若代码HEAD变化或出现不明dirty，停止并重新确认发行输入；不得reset、覆盖、stash或顺手合并。当前分支尚未并入main时沿用该分支；若已按用户授权合并且需新代码修复，依共享规则§3.1另建精确授权分支。

冻结只读：T15/E15批准后、T15 §2全部原设计/执行引用。实施前后记录hash。README/I仅C07开放；Dockerfile、两份Compose、.env.example、pyproject.toml、requirements.lock、app、tests、scripts在所有C阶段均禁止修改。

### 2.2 保护对象

既有 `<WORKSPACE>/m13-live-evidence/`仅识别目录，不读正文、不暂存、不清理。原本机8080和 `cta-m14-live-20261006a` 18080项目不操作，不根据旧收据声称当前健康。`process_simulation-platform-dev`不查询、不读、不操作。暂停中的网页流程进度首版及其分支继续暂停。

AGENT.md/AGENTS.md在任务交接已指明的位置不存在，不重复搜索、不因不存在停止。用户会话AGENTS指令是真实适用约束。

### 2.3 精确实例化路径与文件白名单

以下占位符只能由C00收据解析；具体提示词必须填写绝对值和归属，禁止将整盘、整个主目录或 `/srv` 授权为可修改目录。目录允许创建是为了所列文件，不能无限扩展文件范围。

| 代号 | 解析规则 / 有限文件集合 |
| --- | --- |
| LOCAL_RELEASE_DIR | 本机仓库外专属目录，例如 `<ABSOLUTE_DIR>/m15-release/<release-id>`；仅 source.tar、runtime.tar、发行收据和下述该批AC证据文件 |
| LOCAL_BUILD_DIR | LOCAL_RELEASE_DIR下独占build目录；仅source.tar中经过清单核验的当前Git树文件，以及C07批准的README构建副本；不是开发工作区 |
| CLOUD_RELEASE_DIR | 云上例如 `/srv/china-travel-agent/releases/<release-id>`；仅runtime.tar、compose.yaml、compose.cloud.yaml、.env.example及安全发行收据 |
| CLOUD_PRIVATE_DIR | 云上例如 `/srv/china-travel-agent/private/m15`；仅config.env、keys/pause-key.json；配置0700目录/0600文件，Key目录归UID/GID10001 |
| KEY_BACKUP_FILE | 独立于该云实例的维护者私有单文件绝对路径；仅云Key备份，渠道与写入权限须明确，Linux0600或Windows仅维护者/SYSTEM的NTFS ACL |
| EVIDENCE_DIR | 本机仓库外本批私有目录；文件名限定 AC15-01..13对应的授权/版本/配置/迁移/HTTP/主链/暂停/重启/资源/失败/收尾/审查安全收据，具体文件名逐批列明 |
| CLOUD_PROJECT | 独立项目名，例如cta-cloud-demo；精确网络、PG卷及容器由Compose解析和实际查询得出，不能仅猜前缀 |

Compose两文件须为批准源码的原字节副本，私有env不在发行归档、Git或build context中。两文件中的M14服务/变量名保留，不生成M15替代版本。云上PG卷的数据文件属于已明确授权PG服务的持久写入，维护者不能任意直接改写。

若路径/文件已存在，先识别本批归属并核对hash/权限；未知内容或Key冲突STOP。禁止使用无条件覆盖、`mkdir -p`后盲写、`chmod -R`或递归清理绕开这个检查。

服务器Bash变量映射固定：`M15_PROJECT=CLOUD_PROJECT`、`M15_RELEASE_DIR=CLOUD_RELEASE_DIR`、`M15_CONFIG_FILE=CLOUD_PRIVATE_DIR/config.env`；`M15_PUBLIC_ORIGIN`为批准的公网origin，`M15_PUBLIC_HOST`为去掉`http://`前缀后的精确IP:8080。具体提示词填实际值和安全shell引用，不从含Secret的env全文回显获取。后续所有Compose操作复用此映射。

## 3. 每阶段必读定位

每份提示词都必须精确引用T15与本执行规范对应阶段，再依下表展开上游定位，不能仅写“参考文档”。全部直接依赖首先在C00按T15 §2.2审计；后续仅补读该批实际消费合同，避免重复全文。

| 阶段 | 必读文件与章节 |
| --- | --- |
| 全阶段 | T15 §1、§3、§12；本文 §1—2、§11—13；共享规则 §1、§3、§5—6、§8—9；A §3、§6/6.1、§11—12、§18—21 |
| C00 | T15 §2.1—2.2全部映射；路线图 §1—6、§8—9；主设计 §10.1/10.3；共享规则 §7；T15 §4—5 |
| C01—02 | T15 §5—6；M14 §3—5、§10、§12.1—12.2；E14 §6—7、§11—13；M02 §19—20、M03枢纽增量、M09 §12—15/20/26—27 |
| C03 | T15 §7；M10 §6—15、§19—20、§23—24；M11 §7、§15、§22；M14 §6、§10、§12.1 |
| C04—05 | T15 §6、§8—10；M01 §6—11/§16及枢纽增量，M03 §8—10/15/18及枢纽增量，M05 §8—13/18—22；M06 §15—18/20/24/26—28，M07 §8—11/16—21/23—25，M08 §13—23/25—29及附录A，M09 §6—23/26—27；M11 §9—13/17.4/22、M12 §8—13、M13 §3—15及维护增量、M14 §5/7—13及枢纽增量 |
| C06—07 | T15 §8.2、§9—11；M10 §23—24、M11 §12—15/17.4/22、M12 §6/10/12—13、M13 §9/13、M14 §9—10/12—13；共享规则 §7.1—7.3与T15 §3.1适用关系 |

引用章节号以实际文件标题核对；维护增量不能被旧正文覆盖。读取范围以阶段实际所需补足，不得冒称读过未读全文。发现依赖合同冲突时停止，不通过改测试或偷偷改原设计解决。

## 4. 阶段组织与统一验证

| 批次 | 阶段 | 目标 | 批次结束统一验证 |
| --- | --- | --- | --- |
| 预检 | C00只读发行核验 | 锁定源版本、实例、目录、授权与历史风险 | AC15-01/02及04的身份部分；不产生发布资格 |
| 第一批 | C01构建制包 + C02远端准备 | 形成同一可追溯runtime并准备云私有配置 | AC15-03/04；不启动PG/Web、不迁移、不调用供应商 |
| 安全阶段 | C03新库迁移 | 空库核验、显式三链结构初始化 | AC15-05结构部分；独立验证后才进入下一批 |
| 第二批 | C04启动和资源观察 + C05公开入口/真实主链 | 云默认入口及容量行、唯一正式场景、同进程暂停续接 | AC15-05初始化及06/07/08/11，AC15-10持续观察 |
| 第三批 | C06终态重启读回 + C07交付收尾 | 同卷同Key读回、最终镜像/说明/交付审查 | AC15-09/10/12/13及全AC审计 |

同一批不在两个阶段间重复运行完整测试；迁移、来源/hash、权限和启动readiness属于安全前置，必须当场验证。没有必要前置或授权的阶段不能为了凑两个阶段提前执行。

## 5. C00：只读发行核验

**目标：** 执行T15 §4—5，产生可核验的动态收据和精确下一批提示词。**前置：** T15/E15批准可定位；C00仅只读，不借此执行后续动作。**分支/状态：** 本文§2.1的实际分支、HEAD、预期dirty/staged。

**文件白名单：** 仓库与服务器修改为空；如果授权保存预检报告，只开放该批AC15-01/02/04的安全证据文件。**允许操作：** Git/引用/hash、选定服务器控制台与只读命令；不读取配置Secret正文或其他项目。

1. 核验源码和已批准设计hash、22个交接入口存在性及本次使用入口；不重复寻找不存在的AGENTS文件。
2. 对照T15 §2、§3、§4形成原质量/coverage/live资格差异表，明确哪些只是历史、哪些本批不重新验证。
3. 从实际控制台取得选定实例ID/区域/IP；远端读取id、架构、Docker/Compose、目标容器/卷/网络库存、端口、可用内存/swap/磁盘。当前账号直接Docker权限不足时核验已有sudo，不改用户组。
4. 读取该实例云防火墙实际规则，确认PG无公网入口且8080尚未公开；若8080已公开，则C04私有启动前置不成立，先交用户确认新的公开启动范围，不能删除已有规则。
5. 解析§2.3全部路径，确定文件/Secret传输与独立Key备份安全渠道，核验目录存在性。按§9.3从当前源核对元数据查询/工具与完整性投影方法；容器工具实际检查留C01/C03。仅为获取事实，不创建目录、不测试写入。
6. 下一批提示词填写具体授权对象；来源、服务器或权限尚未核验时，相关字段写NOT_VERIFIED并阻断依赖动作。

**验证矩阵/PASS：** AC15-01/02及04身份部分有原始安全收据，差异和权限无隐瞒。**STOP：** T15 §12、本文§11，特别是没有文档批准或无法接受§3限定范围时。**报告：** 本文§13；未运行项写NOT_RUN，不能因C00通过写云部署通过。

## 6. C01 + C02：构建制包与云准备

### 6.1 C01：当前源码发行物

**目标：** T15 §5.1及AC15-03。**前置：** C00完成，本机Docker/构建/文件授权明确；构建网络仅用于基础镜像和锁定依赖获取，不提供live Secret。**分支/状态：** §2.1实际收据；批准设计若未提交，仅作为预期只读dirty。

**白名单：** 仓库修改为空；外部仅LOCAL_RELEASE_DIR的source.tar、runtime.tar、安全发行收据及AC15-03证据，LOCAL_BUILD_DIR经核验Git树副本。**允许操作：** Git导出、Docker runtime构建、一次性无Secret检查、镜像保存；不运行本机或云业务、不stage/commit。

1. 用 `git ls-tree -r --name-only <源码HEAD>:china_travel_agent`核验导出清单；用该Git树导出source.tar到授权外部目录，再解包到新独占build目录。项目为父Git仓库子目录，不能误打包兄弟证据目录。发现已跟踪Secret或不明文件立即停止。
2. 从该build目录使用现有Dockerfile `--target runtime`构建独立发行tag；验证Linux/amd64。基础镜像/依赖拉取失败记环境阻断，不写代码PASS，不自行切镜像源或升级依赖。
3. 安全inspect仅投影镜像ID/OS/Architecture/Config.Cmd/User；默认CMD必须 `python -m app.web_application`，用户必须为非root。
4. 新一次性容器使用--rm、--network none、--read-only、/tmp tmpfs，读取Python/Pydantic与关键锁定依赖实际版本；编译app时将pycache目标指向/tmp，不写宿主源码。可用 `python -X pycache_prefix=/tmp/pycache -m compileall -q /workspace/app`。
5. 使用镜像真实默认CMD运行一次无配置检查，预期安全配置失败exit2，明确记录为负向启动检查；不得包装成完整服务启动PASS。正向默认入口在C04真实配置中验证。
6. `docker image save`产生runtime.tar；计算source.tar/runtime.tar及两份Compose字节hash，检查归档不含私有env/Key/PG数据。记录容器已自动移除和本批精确残留；不删其他镜像/容器。

**验证：** AC15-03本机部分；构建/每条检查退出码与新镜像ID齐全。unit/contract/integration/全量离线/coverage按T15 §3.1均NOT_RUN，本阶段不制造M15测试包。**STOP/报告：** §11、§13。

### 6.2 C02：远端发行与私有配置

**目标：** T15 §6及AC15-04，完成云端准备。**前置：** C01发行物成功、传输/云准备/凭据与Key生成授权；重新核验实例与归属。**分支/状态：** 与C01同一代码HEAD，不引入云端业务源码差异。

**白名单：** 仓库修改为空；云上仅§2.3发行文件、config.env、keys/pause-key.json，独立位置仅KEY_BACKUP_FILE，及本批AC15-03/04安全证据。**允许操作：** 新目录/权限、指定文件传输、镜像load、首次私有配置/Key生成与独立备份、无输出配置校验；不启动PG/Web，不迁移，不开放端口，不调用供应商。

1. 通过已核验渠道传runtime.tar及两份Compose/.env.example；远端先查存在性和归属，禁止覆盖未知文件。校验归档hash后load，再安全inspect核对本机内容ID、默认CMD、用户与架构。
2. config.env由授权安全渠道填写现有变量：`WEB_PUBLIC_ORIGIN`、`M14_RUNTIME_IMAGE`、`M14_KEY_PATH`、`M14_LOCAL_PG_PASSWORD`、`DEEPSEEK_API_KEY`、`BAIDU_MAPS_API_KEY`；PG密码至少16字符。路径/origin为C00收据值，不用本机回环origin或本机Key。env目录/文件0700/0600，仅授权维护账号可读。
3. Key父目录先按UID/GID10001、0700创建并检查；一次性当前runtime容器--rm、无网络、只读root及/tmp tmpfs，仅该Key目录可写，执行 `python -m app.web_application.keygen --output /keys/pause-key.json`。检查Key0600、所属10001、存在性/大小；不cat、不打印正文、不覆盖既有Key。通过已授权渠道写独立KEY_BACKUP_FILE，核对维护者可读取、权限和字节一致，只输出布尔结果；若传输必须临时导出文件，先在本批提示词明确其绝对路径、权限及清理授权，不临时扩白名单。
4. 定义后续一致使用的服务器Bash函数。变量值先从收据安全赋值，不能复制占位符直接执行：

```bash
m15_compose() {
  sudo docker compose \
    --env-file "$M15_CONFIG_FILE" \
    --project-name "$M15_PROJECT" \
    -f "$M15_RELEASE_DIR/compose.yaml" \
    -f "$M15_RELEASE_DIR/compose.cloud.yaml" \
    --profile m14-local "$@"
}
m15_compose config --quiet
```

5. 解析配置时仅在受控进程中投影允许字段，验证image、origin、绑定绝对Key路径、卷/网络/端口、内存、command；禁止回显config全文、environment、inspect全文或Secret。确认PG无ports、云Web只映射8080，未意外保留另一个回环映射。
6. 记录新目录/文件/镜像归属、Key权限、项目预期资源名。不要启动隐含依赖、不 `up`全项目、不凭配置校验声明服务就绪。

**批次统一验证：** AC15-03/04通过，发行物相同、配置/key安全、无PG/Web容器产生，云写入仅授权有限对象。**STOP/报告：** §11、§13；凭据未提供可完成无Secret制包，配置/后续阶段保持NOT_RUN。

## 7. C03：新库迁移安全阶段

**目标：** T15 §7 / AC15-05。**前置：** C01—02统一验证通过，C03明确授权PG持久写入与三链迁移；重新确认精确实例、项目、卷与新库前提。**分支/状态：** 同一发行HEAD/镜像，staged为空。

**白名单：** 仓库修改为空、不得新增migration；云持久写入限该项目PG卷；只开放AC15-05安全收据。**允许操作：** 创建项目网络/新PG卷、启动PG、一次性迁移容器及只读schema/容量核对。不启动Web、不调供应商、不操作旧库。

1. 核验项目名、实际卷名/Compose归属、无旧业务、Key/config不变；若卷预先存在，需有本批创建收据和可验证初始化状态，否则STOP。
2. 用C02函数执行 `m15_compose up --detach --wait --no-build m14-postgres`，限制仅PG，确认健康、精确卷/网络及无宿主5432映射。PG镜像来源/tag/hash写收据；不把其自动pull误报为业务runtime替换。
3. 以同一runtime、仅项目网络依次执行，每条必须退出0才执行下一条：

```bash
m15_compose run --rm --no-deps --pull never m14-web python -m alembic -c app/artifact_persistence/alembic.ini upgrade head
m15_compose run --rm --no-deps --pull never m14-web python -m app.durable_execution.migrate
m15_compose run --rm --no-deps --pull never m14-web python -m app.web_application.migrate
m15_compose run --rm --no-deps --pull never m14-web python -m app.web_application.migrate --check
```

4. 依据当前迁移源核对三个schema的head、容量表结构和业务表空库前提，输出安全版本/行数与目标归属，禁止查业务正文或泄漏口令。首次新库 `artifact_store.capacity_state`、`durable_execution.capacity_state`预期零行，C04由真实runtime初始化，禁止手工补行。M11迁移已包含其schema/saver校验，不虚构不存在的 `--check`参数。
5. 核验一次性容器已移除；保留PG/卷/网络/Key。首次新库无历史业务备份对象，写明事实；不声称执行过备份或恢复演练。

**PASS：** 新库前提、三链显式结构初始化及核查有证据，AC15-05结构部分通过；容量行初始化尚待C04，不能误报已完成。**STOP：** 非零退出、迁移效果未知、head/结构冲突、已有业务或目标不明；保留失败/数据，不重跑到绿，不删卷。后续C04—07写NOT_RUN。**报告：** §13列迁移命令、目标、退出码及资源。

## 8. C04 + C05：云启动、公开与唯一真实场景

### 8.1 C04：默认入口与资源观察

**目标：** T15 §6、§9、AC15-06/10的启动部分。**前置：** C03通过；云Web启动授权、8080仍被云防火墙拒绝公网访问，私有origin配置可用。**分支/状态：** 同一发行镜像，不改源码/Compose/业务预算。

**白名单：** 仓库为空；云对象仅该项目Web容器、既有项目PG/网络/卷、AC15-05/06/10收据。**允许操作：** 指定Web启动、真实runtime容量行初始化及其只读核查、健康/静态/资源、日志安全投影、监测；不新增公开规则、不发规划POST。

1. 对照Compose最终解析配置及实际PG健康启动 `m15_compose up --detach --wait --no-build --pull never m14-web`。保存镜像ID、容器ID、生产command、单worker、readonly/tmpfs/limits/Key与PG卷身份。
2. Web没有Compose healthcheck，`up --wait`不能单独证明ready。服务器从回环访问时须带配置公网 `Host`；M14对所有HTTP校验Host，不能以默认127.0.0.1 Host被拒绝误判服务故障，也不能放宽源码安全校验。例如：

```bash
curl --fail --silent --show-error --max-time 10 \
  -H "Host: $M15_PUBLIC_HOST" http://127.0.0.1:8080/health/ready
```

3. 同样检查 `/health/live`、首页、实际引用JS/CSS；核对状态/类型/安全hash和源码静态资源，不能猜文件名。只读就绪轮询单次10秒、最多12次，总观察窗口120秒；不能循环重启服务取绿。
4. 启动持续资源观察，至少覆盖后续正式场景全部状态与重启。仅采集该项目容器/cgroup或docker stats安全数值、服务器内存/swap/磁盘、PG连接计数、响应字节与时间。采样间隔至多5秒，记录工具/间隔；无法取得绝对峰值时标“采样最大值”，不冒称精确峰值。
5. PG查询只取连接总数和配置数值，不读query/用户数据；采样连接自身计入观测。默认runtime就绪后核验M10/M11两张capacity_state各有合法singleton、配置与M14/M10/M11当前合同相同、计数符合尚未创建业务run的状态，AC15-05初始化部分此时完成。此阶段未运行真实主链，不能提前写实机容量通过。

**安全前置：** 默认入口与ready真实可用才进入C05；这是公开前必要核验，不是重复全套测试。**STOP/报告：** §11、§13；启动故障先保留安全code，不输出日志原文。

### 8.2 C05：公开访问、主链及同进程暂停续接

**目标：** T15 §8.1/8.2、AC15-06/07/08/11。**前置：** C04就绪，明确公开8080/失败回退、维护真实API及按M14公共配额保留运行的供应商调用授权；资源观察已运行。**分支/状态：** 与C04一致。

**白名单：** 仓库为空；仅指定实例新增TCP8080规则、经公共应用写入该项目PG、AC15-06/07/08/11安全收据；失败时本项目Web停止及本次规则撤回仅依§11明确回退授权。公开规则来源为IPv4 `0.0.0.0/0`，仅TCP8080；具体规则身份写收据。**允许操作：** 此有限公开发布、同一浏览器会话的一个正式规划；不在C05重启Web，不改22/80/443/3389等其他规则，不开PG、不改UFW/系统配置。

1. 重新读取规则库存，仅新增授权的8080条目；避免重复规则，记录本次新增规则ID及原规则集合。到达公网首页必须由用户实际浏览器证明，不能用服务器回环替代。确认会话、静态资源、origin/CSRF正常。
2. 具体case ID为当次收据定义的杭州普通游客短途场景；先按T15 §8.1和原需求合同核对完整输入，再从生产首页创建唯一run。保存安全run别名/状态/计数，私有run ID仅用于本次调用和证据关联，不放公开报告。
3. 浏览器完成必要澄清，经过至少一个M14/M11公共状态可证的持久人工暂停点，再在同一Web进程从公共入口续接该run；记录暂停时无执行名额与恢复状态，不重启进程。暂停跨进程新资格本批NOT_RUN，原因及原门槛差异按T15 §3.1记录。禁止修改私有状态、伪造凭证或操作DB模拟暂停。
4. 完成需求人工确认、真实酒店候选及人工选择、真实研究规划/路线、中文结果与保存summary；对照M01/M03/M06—09/M12/M13/M14原合同逐项判定。不能以页面存在结果替代demo_ready与内容质量。
5. 本批维护正式run上限1、新增诊断调用0，区别公网其他访客运行；提取/repair、研究/规划与地图内部预算分别引用M03 §9—10/15、M08 §14—23及维护版本、M09 §12—23/26—27、M13 §6—9/§11—13及维护增量，不自造更低业务额度或无限重试。只有维护一次验证授权时，公开所有IPv4写入口的前置不成立，STOP明确访问范围。
6. 正式场景观察上限90分钟，包括维护者操作；此值是本批操作时限，不修改生产deadline。若需更长，执行前在授权中替换并记录。超时后停止新增控制写入，保留当前状态；仅在已有取消授权内通过公共cancel安全收束，不能强杀在途外部节点、重置预算或新建run重试。
7. 真实degraded/failed/blocked或保存缺失时AC15-07失败，保留安全证据与具体上游限制，停止新增真实调用；按T15 §12提出最小修复或接受限制的决策。不能补跑另一场景使汇总全绿。

**批次统一验证：** AC15-06/07/08/11实际收据齐全，正式场景demo_ready；AC15-10资源观察继续至C06。没有运行的原质量集/本机fresh资格写NOT_RUN。**STOP/报告：** §11、§13，包含实际model/地图调用与预算状态及两种终态区分。

## 9. C06 + C07：终态读回与交付收尾

### 9.1 C06：同卷同Key的终态重启

**目标：** T15 §8.2、§9 / AC15-09/10。**前置：** C05正式场景终态、执行已停止、summary已保存，终态重启授权；PG/Key身份已留存。**分支/状态：** 当前锁定源码/镜像。

**白名单：** 仓库为空；该项目Web重启及AC15-09/10安全证据。**允许操作：** 同浏览器/公共API只读结果/summary/历史，§9.3限定正式run的PG安全元数据核查，受控Web重启、资源观测；禁止resume/recover/new-run或额外供应商调用。

1. 从结果与summary公共读取记录规范字节/安全投影hash、字节数和终态；确认summary为saved且完整，按§9.3建立独立元数据基线。summary比较排除HTTP传输头，不能随意删除业务字段使hash一致。
2. 重启仅本项目Web，确认新进程身份、卷名/挂载与Key权限/身份未变；不停止PG、不重生成Key。ready后同一身份重读历史、结果、summary，比对所有hash及计数。
3. 证明该正式run在重启只读窗口新增调用0：重复§9.3的同一组安全查询，逐项比较基线；公开summary是不可变收据，不是新请求计量器，生产NoOp事件sink不能作为调用日志。诊断不完整或无法核验时NOT_VERIFIED并阻断AC15-09；不把其他访客调用算作此run增量，不推导全站账单0。
4. 汇总整个单场景的实机观察，按M14 §10、§12.1核对内存、OOM、连接、磁盘、关闭及就绪。区分受控重启与非预期重启，不能简单要求累计RestartCount为0却忽略已授权重启。
5. 观察器只结束本批精确归属进程；正式Web/PG/卷/网络/Key作为交付资源保留。不要以“测试清理”为由关闭已发布应用。

**PASS：** AC15-09/10通过。**STOP：** hash/元数据不一致、summary不可读或资源合同失败；停止新增写入，按授权回退保留证据。**报告：** §13，说明这只证明同进程暂停和终态跨进程读取，不证明暂停跨进程或外部节点任意中途崩溃恢复。

### 9.2 C07：Gate交付同步与最终审查

**目标：** T15 §11及AC15-12/13。**前置：** C00—06适用项均通过；README/I修改、runtime重建及必要云镜像更换授权明确；无业务输入变化。**分支/状态：** 同一M15分支，允许本文指定README/I及批准设计的预期dirty，staged为空。

**仓库白名单：** 仅 `README.md`、`docs/IMPLEMENTATION_STATUS.md`；冻结T15/E15和原设计不能改。**外部白名单：** 本次收尾LOCAL_BUILD_DIR的README副本、runtime归档/发行收据、私有config.env的M14_RUNTIME_IMAGE字段、最终镜像加载与同项目Web更换、AC15-12/13安全证据。**允许操作：** 真实说明同步、Docker覆盖核查、当前runtime重建与最终镜像复核、§9.3正式run元数据及Key独立备份只读核查、独立只读交付审查。禁止源码/测试/Dockerfile/Compose/env.example变更与Git写入。

1. README新增已验证的真实入口、首次部署/启动/保留数据停止命令、私有配置位置和实际限制；不得嵌入Secret或宣称所有质量集通过。I追加当次分支/HEAD、镜像/资源、精确命令/结果、AC矩阵和待办，不覆盖历史。
2. 逐项检查Dockerfile runtime COPY/CMD、两份Compose、.env.example、依赖。给出具体不修改理由；任何真实不足均STOP申请精确扩展，不在closeout顺手修代码。
3. README影响镜像构建输入，使用已锁定源码副本叠加授权README字节重建runtime；manifest记录代码HEAD、READMEhash、批准文档hash及实际build输入。检查业务/静态/Prompt/依赖指纹与原正式场景相同，新镜像ID/版本/CMD明确。
4. 仅有说明差异时不重复供应商主链或全仓离线；按T15 §11明确标记正式场景来自旧镜像但同一业务源码，旧镜像测试不能冒充最终镜像测试。将新镜像归档load到云，核验ID、配置引用更新，授权更换本项目Web；保持同卷同Key，不迁移/轮换。私有config.env修改仅限本次锁定 `M14_RUNTIME_IMAGE` 字段，须在阶段外部文件白名单显式开放。
5. 在最终云镜像实际验证默认CMD/ready/静态资源，再只读同一终态结果/summary及§9.3元数据，证明hash和该run增量不变；再核对独立Key备份权限/一致性布尔结果。读取失败或业务/实际依赖版本指纹有差异时STOP，不用旧结果绕过。I记录最终镜像收据；I不进入Docker COPY输入，README事实不变时不为I追加无限重建。
6. 对照全部AC15和原质量差异表进行独立只读交付审查；不是仅看测试是否退出0。修复权限限README/I和已授权配置字段；涉及设计或业务变化先征求用户同意。全部未结P0/P1/P2必须关闭或阶段失败，低风险P3列回收条件。
7. 报告MODULE-CLOSEOUT按路线图§1/共享规则§7.1判定为Gate交付收尾，执行T15 §3.1/§11；不能填写生产Component全套回归已运行。给出保留资源、维护命令及原完整release gate尚未满足项目。

**批次统一验证：** AC15-09/10/12/13和全AC逐条审计通过；只有实际部署及最终镜像复核后才能写“本批云交付完成”。**STOP/报告：** §11、§13；原完整Gate和所有模块最终资格仍按真实矩阵单列。不得自行stage/commit/merge/push/PR。

### 9.3 正式run的零新增调用取证

此项是维护只读审计，不新增生产能力。C00核对当前源的列/公开记录结构，C03后核对实际工具与schema；C06/C07以明确授权的只读事务，仅查询该正式run，参数化run ID，设置有限statement_timeout，异常仅返回安全失败code，不输出连接信息或traceback。

| 取证面 | 允许的安全投影与前后比较 |
| --- | --- |
| `artifact_store.interaction_records` | WHERE run_id为正式run；按record_kind分组的COUNT、SUM(size_bytes)、MAX(captured_at)，全部稳定 |
| `durable_execution.budget_limits` | 同run的scope、name、phase_code、limit_value、consumed_value，稳定排序后逐值相同 |
| `durable_execution.budget_operations` / `operation_journal` | 同run行数和安全outcome/journal_state分类计数稳定；不输出operation/request/payload正文 |
| M10 `call_summary` / `run_summary` | 通过公开InteractionRecorder.read_run在容器内只投影，或授权的SQL仅提取其JSON payload中records_complete、record_failure_count、observed_provider_attempts，以及run_summary的observed_admitted_calls；不返回content_bytes全文/其他payload。比较记录数量、完整性布尔和数字；run_summary必须有完整收据 |

SQL投影属于特定审计权限，不授权应用绕过公共仓储。必须确认记录完整、record_failure_count为0，观察到的provider attempts已覆盖该run，预算/journal稳定，并核对本窗口只有公共GET读路径；任一证据不足即NOT_VERIFIED。未保存的新物理调用不能仅凭旧summary推断不存在。此结论限定该run及观察窗口，不代替供应商账单对账、其他访客运行统计或可靠性评估。

## 10. 测试矩阵与coverage适用规则

| 项目 | 本批无代码变更路径 | 必要证据 / 条件 |
| --- | --- | --- |
| 文档/引用/白名单/hash/静态 | 每批执行 | 引用实际存在、冻结hash不变、git diff --check及新增未跟踪文件额外空白检查 |
| runtime新构建/版本/compileall/默认CMD | C01、C07执行 | 新容器、当前发行输入、安全缺配置exit2与云正向启动分别报告 |
| 真实PG/三链迁移及容量初始化 | C03结构、C04容量行 | 空新库、显式顺序、head/结构及默认runtime初始化校验；不是重跑全套PG组件测试 |
| 真网页/真实DeepSeek/百度主链 | C05执行1个维护正式场景 | AC15-07；维护与公网持续调用授权分列，不能用browser worker替身替代 |
| 同进程人工暂停与终态重启 | C05、C06及C07最终镜像读回 | AC15-08/09/12；完整summary保存后重启，正式run安全元数据稳定 |
| 2GB资源观察 | C04—06及最终镜像安全复核 | AC15-10，限定单场景和服务器；不写并发压测通过 |
| unit/contract/模块全量/全仓离线 | NOT_RUN，按T15 §3.1批准范围 | 无业务代码变化，不将历史数量填本轮passed |
| line/branch/combined coverage | NOT_RUN，不新增M15分母 | 原资格、M11欠账和M12诊断政策列引用；未采集不填0%或PASS |
| 原完整3—5类质量集、M03新16例及暂停/崩溃跨进程恢复资格 | 本轮不新增执行，NOT_RUN | 原版本证据和当前版本缺口分列；终态读回不能代替暂停恢复，不能称全部release gate通过 |

若出现经另行批准的生产修改，重新引用受影响模块原测试和覆盖率：M01/03/04/05/06/07的原95%政策，M02原combined95%政策，M08/09/10/11/13/14原三项70%政策，M12 §13仅诊断。精确分母/执行入口以该模块当前权威文件为准；不以A通用70%压低原95%，不新设M12数字门槛，不清除M11历史欠账。此情形需要单独修复提示词和白名单，不能沿本批改app/tests。

测试失败、collection0、非零退出、coverage采集失败、证据不足和未授权外部步骤均不能写PASS；不删断言、skip/xfail、自动rerun或修改设计换绿灯。

## 11. 共用STOP与失败处置

执行T15 §12、共享规则§8及A §7.1/§18，具体包括：

- 文档批准、当前阶段权限、服务器身份或资源归属无法定位；源HEAD/设计hash漂移或白名单外文件。
- 需要改app/tests/依赖/Dockerfile/Compose/已批准设计，或恢复暂停中的网页工作。
- 已有未知库/Key/文件、迁移效果不明、容量冲突、配置Secret会进入回显/归档/Git。
- Docker/权限/网络环境阻断，origin/CSRF错误无法在已授权配置中解决，真实场景非预期终态或超时。
- OOM/非预期重启、连接/磁盘/关闭不符合同、保存失败、重读hash改变或无法证明新增调用0。

停止依赖动作，保留原失败证据，标明失败阶段及后续NOT_RUN。只有既有授权涵盖且原合同允许时，才在当前云配置白名单修正明确小问题；任何新的正式run/诊断调用、扩容、旧库迁移、业务修复或设计变更先给出具体范围供用户决定。

失败回退授权必须事先明确到：本项目Web停止或兼容镜像恢复、本次新增8080规则撤回；不改变其他规则/资源，始终保留PG卷/Key。未获回退权限则报告需要的具体动作，不擅自修改。禁止down -v、prune、清库、删卷/Key、服务器重置或未经授权续费。

## 12. 文档及后续提示词的独立审查

当前文档任务依A §17执行两位只读子Agent两轮审查，主模型唯一负责修订。架构者审T15 §2—4、§7—8、§11—12与原模块冲突和后续空间；执行者逐C00—07核验目标、前置、分支、白名单、步骤/命令、测试、PASS/STOP与报告可执行性。双方均须核对最新云部署优先要求、未授权项和T15 §3适用关系。

审查格式：文件；章节/行号；等级P0/P1/P2/P3；问题；后果；最小修复。首轮修订后由原两位第二轮复核，再主模型总审。审查者只读，不修改文件、不运行代码、Docker或云操作。

之后生成任何实施、修复、审查或收尾提示词，依A §6/6.1全部放一个代码框；保留各阶段目标、前置、分支、文件白名单、允许操作、测试矩阵、STOP、报告栏目并精确引用T15/本文章节或AC。动态HEAD/dirty、实际服务器/资源、文件存在性、本次新增权限可以具体展开，已成文内容不得重复复制。接收模型以后生成提示词同样执行此格式。

## 13. 完整报告字段

每批使用A §21与共享规则§9；以下为本Gate的填报定位，不用历史数值填本轮结果。执行提示词引用本节，不重复粘贴模板。

| 栏目 | 必填内容 |
| --- | --- |
| 任务与状态 | 任务/批次；最终状态；实现状态；模块收尾状态；设计、云启动、单场景及原完整Gate分开 |
| Git | 前后分支、完整HEAD、subject、开始/结束dirty、staged；预期/异常文件 |
| 引用审计 | 指定引用、实际完整读/章节读范围、缺失引用、允许创建目标、会话AGENTS及已确认文件不存在 |
| 冻结设计 | 全部冻结文件清单、是否修改、前后SHA-256、合同一致性、未批准设计差异；当前草案与批准版区分 |
| 变更 | 创建/修改文件、白名单外文件、源代码/依赖变化；迁移文件是否新增、实际执行迁移目标/命令/退出码 |
| 收尾同步 | MODULE-CLOSEOUT适用判定；README/I/Dockerfile/compose.yaml/compose.cloud.yaml/.env.example/其他说明逐项状态及具体不改理由，过期内容扫描 |
| Docker | runtime/test/browser构建各自状态、镜像ID、实际Python/Pydantic/关键依赖、默认CMD、一次性移除/残留、长期保留资源；云最终镜像与主链镜像分别定位 |
| 测试 | 定向unit、contract、integration、模块全量、全仓离线各自命令/结果或NOT_RUN原因；passed/failed/skipped/xfail实际数字，未收集项不可报0 |
| coverage | line/branch/combined各自结果或NOT_RUN；原阈值及M11欠账/M12诊断政策；不把历史采集当当前采集 |
| 静态 | trailing whitespace、git diff --check、compileall、pycache、禁止依赖、文件白名单、staged及冻结文件变更扫描 |
| 逐条文档对照 | 主设计、权威模块、E15、路线图、公共合同；AC15-01..13及原质量差异表，每项PASS/FAIL/NOT_RUN/NOT_VERIFIED与证据 |
| 云与真实API | 实例/区域/origin、项目/容器/卷/网络/Key归属安全投影、公开规则、实际调用/token/预算、正式/诊断性质、暂停/终态重启hash、资源观测及局限 |
| 独立审查 | 每轮P0/P1/P2/P3数量、逐项处理、复核结论和未结项；不得只写测试全绿 |
| 外部操作 | stage、commit/hash、merge main、push、PR、deploy分别状态与权限；设计任务仅只读预检不能填deploy完成 |
| 保护与剩余 | 其他项目/旧入口/既有证据/暂停工作保护；保留资源、剩余问题、回收条件与后续必要授权 |
| 提交及最终资格 | 提交NOT_AUTHORIZED/AUTHORIZED；云交付资格；原模块最终完成资格未达到/已达到及其精确范围 |

当前设计交付的Docker执行、迁移、pytest/coverage、compileall、真实API、部署均NOT_RUN；服务器只读预检另列实际命令。当前最终资格为“设计待用户批准”，不能填写“M15发布Gate通过”或“模块最终COMPLETE”。
