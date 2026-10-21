# 06 百度地图 typed adapter、MCP Gateway、规范化和技术限流规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 状态：已批准并冻结（2026-08-19）。本文件是模块 06 的冻结权威设计。
> 本文件只定义合同，不证明代码、Docker、coverage 或 live 已完成。

## 1. 文档职责与权威层级

本文件冻结模块 06 的公共类型、外部系统边界、失败语义、生命周期、资源保护、测试门槛和下游衔接。实现不得通过修改本文件来迁就代码或供应商漂移。

冲突按以下顺序处理：

1. 用户最新明确决定；
2. <code>docs/00_agent_master_design.md</code>；
3. <code>docs/IMPLEMENTATION_ROADMAP.md</code>；
4. 本文件；
5. <code>docs/SHARED_IMPLEMENTATION_RULES.md</code>；
6. 模块 04 已批准公共合同；
7. 模块 05 与 M2 有关的已批准合同；
8. 锁定依赖及官方一手资料；
9. 配套 Vibe Coding、状态文档、当前实现、测试和历史报告。

本文件只在模块 06 范围内提供唯一细化事实；它不得覆盖主设计、路线图或用户决定。下位资料冲突时按上述顺序停止并上报，不得选择对实现更方便的一方。

本轮已将两项经用户批准的上位设计变化最小同步到主设计和路线图：

- 模块 06/发布资格门由四项核心能力扩大为七项；
- 首版内部坐标由 GCJ-02 改为 BD-09LL。

如果未来实现、供应商工具或审查意见要求改变本文件，普通实施必须停止，精确报告冲突章节、符号和影响，经过单独设计变更、用户批准和重新审查后才能恢复。

## 2. 官方事实基线

模块 06 只以以下一手来源为外部事实依据：

- [百度地图 MCP Server 快速开始](https://lbsyun.baidu.com/docs/ai?title=mcpserver%2Fquickstart)：官方托管端点和 Streamable HTTP 接入；
- [百度地图 MCP Server 功能介绍](https://lbsyun.baidu.com/docs/ai?title=mcpserver%2Fintroduction)：能力、输入语义、BD-09LL 和输出概述；
- [百度地图 MCP Server 更新日志](https://lbsyun.baidu.com/docs/ai?title=mcpserver%2Fupdate)：Streamable HTTP、地点详情、地图调起等能力演进；
- [百度地图官方 MCP GitHub 仓库](https://github.com/baidu-maps/mcp)：七项核心工具的公开名称；
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：锁定 <code>mcp==1.28.1</code> 的 ClientSession、初始化、工具发现、工具调用和内容类型事实。

官方托管服务与开源仓库可能不同步。开源仓库可证明公开工具名称，但不能替代托管服务的 <code>tools/list</code> 资格结果。实施不得把博客、第三方 registry、历史响应或猜测写进 binding manifest。

## 3. 目标与非目标

### 3.1 目标

模块 06 必须：

- 为业务层提供供应商无关、严格类型化的地图端口；
- 连接且只连接百度官方托管 MCP Server；
- 使用官方 MCP SDK 的 Streamable HTTP client；
- 发现并校验允许的工具；
- 把供应商响应规范化为项目 DTO；
- 清除原始 payload、二维码、图片、音频、资源和供应商对象；
- 提供明确的无结果、歧义和技术错误语义；
- 管理 MCP session、超时、取消、并发和资源释放；
- 为七项核心能力提供 offline 合同与真实 live 资格门；
- 为地图调起保留独立、可缺失、不可阻塞普通 Demo 的扩展边界。

### 3.2 非目标

模块 06 不得拥有：

- 酒店候选选择、UID 去重策略和三次搜索业务预算；
- 候选研究循环、Place 业务预算和停止条件；
- 行程规划、路线优化策略和 Direction 业务预算；
- Graph 路由、interrupt/resume、用户交互或终态映射；
- Artifact 查找/写入、OperationId 分配或业务幂等；
- PostgreSQL、长期记忆、Production 调度、审计平台或 Secret 生命周期平台；
- 任意 MCP 工具代理、通用供应商插件平台或 Agent；
- 坐标近似转换；
- 模块 07～15 的业务实现。

首版“技术限流”只指每个 Gateway 最多三个在途工具调用。未取得官方、稳定且经批准的配额事实前，不增加时间窗口、QPS 或令牌桶策略。

## 4. 模块所有权、依赖与下游空间

### 4.1 直接依赖

模块 04 是唯一直接上游。模块 06 只允许从 <code>app.workflow_contracts</code> 包根导入已公开的 <code>UtcClock</code>；不得导入模块 04 私有 helper、仓储实现、预算实现或测试对象。

模块 06 直接使用锁定的 <code>mcp==1.28.1</code>。不得经由 <code>langchain-mcp-adapters</code> 包装工具，也不得向下游暴露 MCP Tool、ClientSession、CallToolResult、content block 或供应商 schema。

### 4.2 模块 05 与 M2

模块 05 不是模块 06 的依赖。两者完成后允许用非正式 harness 验证：

需求输入 → 同进程 interrupt/resume → 一次 typed 地图调用 → 安全结构化结果。

该 harness 不进入 <code>app.baidu_map</code>，不得成为主编排器，不得实现模块 07～15，不得写入 <code>demo_ready</code>。

### 4.3 下游衔接

| 模块 | 可依赖的模块 06 能力 | 仍由下游拥有 |
|---|---|---|
| 07 | 地点搜索、地点详情、地理编码 | 三次搜索预算、酒店筛选、UID 去重、人工选择 |
| 08 | 地点搜索、地点详情、地理编码 | 研究循环、48 次 Place 预算、证据合并 |
| 09 | 地理编码、路线矩阵、路线规划 | 日程生成、优化策略、确定性提交校验和 <code>8 × days + 2</code> Direction 执行预算 |
| 10 | 无直接依赖；只经 Module 04 Repository 端口持久化既有 typed Artifact／evidence | PostgreSQL Artifact 持久化与迁移，不重新判定地图事实 |
| 11 | 无直接依赖；恢复时只使用稳定 Ref、checkpoint 和执行账本 | PostgreSQL checkpoint、run 协调与跨进程恢复，不重发已有确定结果的地图调用 |
| 12 | 无直接依赖；只消费已验证的上游结果 | 结构化输出和费用未核验标签，不解析供应商 payload |
| 13 | 显式装配 Gateway 生命周期 | 唯一主图、Application Service、取消后的终态映射 |
| 15 | 七项 live 资格结果 | 真实主链、发布资格和 Production 运营门 |

“七项核心能力”表示七项均为模块 06 发布资格门，不表示每个下游或每次行程必须调用全部七项。

## 5. 固定生产包结构

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

文件职责：

- <code>adapter.py</code>：七项核心 typed Protocol；
- <code>capabilities.py</code>：独立可选能力 Protocol 和可用性 DTO；
- <code>errors.py</code>：稳定错误码和零敏感异常；
- <code>limits.py</code>：冻结上限、超时和并发配置；
- <code>settings.py</code>：只读取、校验和脱敏 <code>BAIDU_MAPS_API_KEY</code>；
- <code>runtime.py</code>：显式生命周期工厂和 async context manager；
- <code>models/</code>：公共请求、结果和值对象；
- <code>_mcp_gateway.py</code>：官方 SDK、Streamable HTTP、session 和调用控制；
- <code>_tool_registry.py</code>：私有 binding manifest 及 schema 兼容检查；
- <code>_response_parsing.py</code>：内容选择、JSON 边界和本地严格规范化。

下游只能从 <code>app.baidu_map</code> 包根导入。禁止导入带下划线模块，禁止直接导入 <code>mcp</code>，禁止依赖 <code>models</code> 子模块路径。

## 6. 包根公共 API

<code>app.baidu_map.__all__</code> 必须是以下精确有序 tuple，不得增删、改名或从包根泄漏其他名字：

~~~python
__all__ = (
    "MAP_SCHEMA_VERSION", "MAP_BINDING_MANIFEST_VERSION",
    "DEFAULT_BAIDU_MAP_LIMITS",
    "BaiduMapAdapter", "MapLaunchCapability", "BaiduMapRuntime",
    "create_baidu_map_runtime", "BaiduMapSettings",
    "load_baidu_map_settings", "BaiduMapLimits",
    "MapCapability", "CoordinateSystem", "TravelMode",
    "MatrixTravelMode", "MapOutcomeStatus", "PlaceSearchAreaType",
    "MapLaunchService", "MapCapabilityUnavailableReason",
    "BaiduCoordinate", "MapProvenance",
    "GeocodeRequest", "GeocodeCandidate", "GeocodeSuccess",
    "GeocodeNoResult", "GeocodeAmbiguous", "GeocodeOutcome",
    "ReverseGeocodeRequest", "AddressComponent",
    "ReverseGeocodeSuccess", "ReverseGeocodeNoResult",
    "ReverseGeocodeOutcome", "PlaceSummary", "PlaceDetails",
    "CitySearchArea", "NearbySearchArea", "PlaceSearchArea",
    "PlaceSearchRequest", "PlaceSearchSuccess", "PlaceSearchNoResult",
    "PlaceSearchOutcome", "PlaceDetailsRequest", "PlaceDetailsSuccess",
    "PlaceDetailsNoResult", "PlaceDetailsOutcome", "RouteMatrixRequest",
    "RouteMatrixCellSuccess", "RouteMatrixCellNoResult", "RouteMatrixCell",
    "RouteMatrixSuccess", "RouteMatrixNoResult", "RouteMatrixOutcome",
    "RouteEndpoint", "DirectionsRequest", "RouteStep",
    "RouteAlternative", "DirectionsSuccess", "DirectionsNoResult",
    "DirectionsOutcome", "WeatherRequest", "CurrentWeather",
    "DailyWeatherForecast", "WeatherSuccess", "WeatherNoResult",
    "WeatherOutcome", "MapLaunchRouteRequest", "MapLaunchSearchRequest",
    "MapLaunchRequest", "MapLaunchSuccess", "MapLaunchNoResult",
    "MapLaunchOutcome", "MapCapabilityAvailability",
    "BaiduMapErrorCode", "BaiduMapError", "BaiduMapRequestError",
    "BaiduMapConfigurationError", "BaiduMapAuthenticationError",
    "BaiduMapRateLimitError", "BaiduMapTimeoutError",
    "BaiduMapTransportError", "BaiduMapSessionError",
    "BaiduMapToolUnavailableError", "BaiduMapToolSchemaError",
    "BaiduMapProtocolError", "BaiduMapResponseError",
    "BaiduMapConcurrencyError",
)
~~~

不得导出 Gateway 具体类、tool registry、parser、MCP SDK 类型、endpoint 常量、session 或原始 response 类型。具体 <code>BaiduMapMcpGateway</code> 保持包内私有；runtime 只把它作为 <code>BaiduMapAdapter</code> 和可选 capability 暴露。

## 7. 公共模型共同合同

除本节明确列出的两个配置模型外，所有公共 DTO 必须满足：

- Pydantic v2 <code>BaseModel</code>；
- <code>ConfigDict(extra="forbid", frozen=True, strict=True)</code>；
- 字段声明顺序约束 Pydantic 模型、JSON Schema 和普通 <code>model_dump</code> 的规范展示顺序；它不是 Artifact canonical bytes 的键顺序；
- 每个模型首字段固定为 <code>schema_version: Literal["1.0"] = "1.0"</code>；
- 集合使用 tuple，不使用可变 list/set；
- 数字必须有限，拒绝 NaN 和 infinity；
- 字符串在校验前后均不得静默截断；
- 未声明字段、错误类型、空白必填字符串和越界值均失败关闭；
- JSON round-trip 必须得到值相等但对象身份独立的 fresh copy；
- 公共签名不得出现无边界 <code>Any</code> 或 <code>dict[str, Any]</code>。

<code>BaiduMapSettings</code> 与 <code>BaiduMapLimits</code> 是 strict/frozen/extra-forbid 的配置模型，不参与业务 DTO schema，因此是“首字段必须为 schema_version”和 JSON Artifact round-trip 规则的明确例外；其字段由第 17、20 节冻结。

所有公共枚举继承 <code>str, Enum</code>，名称和值精确为：

- <code>CoordinateSystem</code>：只有 <code>BD09LL = "bd09ll"</code>；
- <code>MapCapability</code>：<code>GEOCODING="geocoding"</code>、<code>REVERSE_GEOCODING="reverse_geocoding"</code>、<code>PLACE_SEARCH="place_search"</code>、<code>PLACE_DETAILS="place_details"</code>、<code>ROUTE_MATRIX="route_matrix"</code>、<code>DIRECTIONS="directions"</code>、<code>WEATHER="weather"</code>、<code>MAP_LAUNCH="map_launch"</code>；
- <code>TravelMode</code>：<code>DRIVING="driving"</code>、<code>RIDING="riding"</code>、<code>WALKING="walking"</code>、<code>TRANSIT="transit"</code>；
- <code>MatrixTravelMode</code>：<code>DRIVING="driving"</code>、<code>RIDING="riding"</code>、<code>WALKING="walking"</code>；
- <code>MapOutcomeStatus</code>：<code>SUCCESS="success"</code>、<code>NO_RESULT="no_result"</code>、<code>AMBIGUOUS="ambiguous"</code>。
- <code>PlaceSearchAreaType</code>：<code>CITY = "city"</code>、<code>NEARBY = "nearby"</code>；
- <code>MapLaunchService</code>：<code>ROUTE = "route"</code>、<code>SEARCH = "search"</code>；
- <code>MapCapabilityUnavailableReason</code>：<code>NOT_IN_MANIFEST = "not_in_manifest"</code>、<code>TOOL_NOT_LISTED = "tool_not_listed"</code>、<code>SCHEMA_INCOMPATIBLE = "schema_incompatible"</code>。

### 7.1 坐标

<code>BaiduCoordinate</code> 字段依次为：

1. <code>schema_version</code>；
2. <code>coordinate_system</code>，固定 <code>bd09ll</code>；
3. <code>latitude</code>，有限数值且在 [-90, 90]；
4. <code>longitude</code>，有限数值且在 [-180, 180]。

供应商输入字段顺序可能在不同工具中使用纬经度或经纬度字符串；该差异只存在于私有 binding。公共 DTO 始终使用命名字段，不允许位置 tuple 或含糊字符串。

禁止本地近似坐标转换。未来需要 GCJ-02、WGS84 或 BD-09MC 时，必须新增显式坐标类型、官方转换边界和设计版本，不得复用 <code>BaiduCoordinate</code> 冒充。

### 7.2 来源与验证状态

<code>MapProvenance</code> 字段依次为：

1. <code>schema_version</code>；
2. <code>source: Literal["baidu_maps_mcp"]</code>；
3. <code>capability: MapCapability</code>；
4. <code>retrieved_at: datetime</code>，必须为 UTC aware datetime；
5. <code>verified: bool</code>。

只有通过允许的官方工具、结构校验和本地 DTO 校验得到的字段，或第 16.4 节唯一明确允许的已校验请求地址回显，可进入 <code>verified=True</code> 的结果。测试 fake 可以产生 <code>verified=False</code>；业务层不得把未验证 fake 结果当作真实事实证据。

验证只表示“供应商字段来自本次百度工具响应且已通过合同校验；明确标注的请求回显来自同次已校验请求”，不表示酒店有房、价格可成交、天气永远准确、路线永不变化或供应商对现实作永久保证。

### 7.3 固定容量

| 项目 | 上限 |
|---|---:|
| 一般字符串 | 512 字符 |
| 路线 instruction | 1024 字符 |
| URL | 2048 字符 |
| 地理编码歧义候选 | 10 |
| 地点搜索结果 | 50 |
| 逆地理附近地点 | 20 |
| 矩阵单元 | 100 |
| 路线 alternatives | 3 |
| 每条路线 steps | 128 |
| business_hours | 16 |
| 天气 | current + 未来 5 天 |
| 可持久化规范化结果 | 1 MiB 模块 04 canonical JSON |

<code>PlaceSearchRequest.result_limit</code> 精确为 <code>int | None = None</code>。显式提供时范围为 [1, 50]。解析顺序固定为：先验证完整选中 JSON 的 1 MiB 边界和供应商原始 <code>results</code> array 数量 ≤50；任一超限立即响应错误；之后才按供应商原顺序本地取前 N 个。只有当本次 <code>tools/list</code> 已验证 supplier schema 存在一个 binding manifest 明确命名且整数类型兼容的 limit 字段时，才可把 N 下推；不得发送未知参数。不论是否下推，供应商若仍返回 >50 均失败关闭，显式 limit 不能绕过绝对上限。下游持久化 DTO 时，模块 04 还必须按其唯一 canonical JSON 算法校验 1 MiB；模块 06 不自行计算 Artifact bytes/hash。所有其他容量也是绝对上限。

### 7.4 精确 DTO 字段、默认值与封闭 union

下表是第 8～14 节的规范化字段合同。表内字段按书写顺序声明；除明确写出 <code>| None</code> 的字段外均不可空，除明确写出默认值的字段外均必填。一般字符串为去除首尾空白后的非空字符串、最长 512 字符；空值不得用空字符串代替。所有 <code>status</code> 和 discriminator 均带表中 literal 默认值并作为 Pydantic discriminated union 的 discriminator。为压缩表格，<code>Literal["success"]</code>/<code>"no_result"</code>/<code>"ambiguous"</code> 精确表示对应 <code>MapOutcomeStatus</code> enum member，<code>area_type</code> 和 <code>service</code> 同理使用 <code>PlaceSearchAreaType</code>、<code>MapLaunchService</code> 对应 member；Python 值是 enum，JSON 值是表内字符串。

| 模型/别名 | 精确字段或封闭分支 | 附加不变量 |
|---|---|---|
| <code>BaiduCoordinate</code> | <code>schema_version="1.0"; coordinate_system: Literal[CoordinateSystem.BD09LL]=CoordinateSystem.BD09LL; latitude: float; longitude: float</code> | 有限数及第 7.1 节范围 |
| <code>MapProvenance</code> | <code>schema_version="1.0"; source: Literal["baidu_maps_mcp"]="baidu_maps_mcp"; capability: MapCapability; retrieved_at: datetime; verified: bool</code> | UTC aware；真实 Gateway 固定 verified=True，fake 固定 False |
| <code>GeocodeRequest</code> | <code>schema_version="1.0"; address: str; city: str &#124; None = None</code> | address 非空 |
| <code>GeocodeCandidate</code> | <code>schema_version="1.0"; formatted_address: str; coordinate: BaiduCoordinate; confidence: float &#124; None = None</code> | confidence 若存在为有限 [0,1] |
| <code>GeocodeSuccess</code> | <code>schema_version="1.0"; status: Literal["success"]="success"; result: GeocodeCandidate; provenance: MapProvenance</code> | capability=GEOCODING |
| <code>GeocodeNoResult</code> | <code>schema_version="1.0"; status: Literal["no_result"]="no_result"; provenance: MapProvenance</code> | 版本化公共端口；当前真实 binding 1.7 不产生 |
| <code>GeocodeAmbiguous</code> | <code>schema_version="1.0"; status: Literal["ambiguous"]="ambiguous"; candidates: tuple[GeocodeCandidate, ...]; provenance: MapProvenance</code> | candidates 2..10 |
| <code>GeocodeOutcome</code> | <code>GeocodeSuccess &#124; GeocodeNoResult &#124; GeocodeAmbiguous</code> | discriminator=status |
| <code>ReverseGeocodeRequest</code> | <code>schema_version="1.0"; location: BaiduCoordinate</code> | 无其他字段 |
| <code>AddressComponent</code> | <code>schema_version="1.0"; country: str &#124; None=None; province: str &#124; None=None; city: str &#124; None=None; district: str &#124; None=None; town: str &#124; None=None; adcode: int &#124; None=None</code> | 不补造缺失层级；adcode 为非 bool 的 0..999999 整数 |
| <code>ReverseGeocodeSuccess</code> | <code>schema_version="1.0"; status="success"; formatted_address: str; address_component: AddressComponent; nearby_places: tuple[PlaceSummary, ...]=(); provenance: MapProvenance</code> | nearby 0..20；capability=REVERSE_GEOCODING |
| <code>ReverseGeocodeNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 版本化公共端口；当前真实 binding 1.7 不产生 |
| <code>ReverseGeocodeOutcome</code> | <code>ReverseGeocodeSuccess &#124; ReverseGeocodeNoResult</code> | discriminator=status |
| <code>PlaceSummary</code> | <code>schema_version="1.0"; uid: str; name: str; address: str; coordinate: BaiduCoordinate; category: str &#124; None=None</code> | 前四个证据字段非空 |
| <code>PlaceDetails</code> | <code>schema_version="1.0"; uid: str; name: str; address: str; coordinate: BaiduCoordinate; category: str &#124; None=None; brand: str &#124; None=None; telephone: str &#124; None=None; rating: float &#124; None=None; business_hours: tuple[str, ...]=()</code> | rating [0,5]；business_hours 0..16 |
| <code>CitySearchArea</code> | <code>schema_version="1.0"; area_type: Literal["city"]="city"; region: str</code> | region 非空 |
| <code>NearbySearchArea</code> | <code>schema_version="1.0"; area_type: Literal["nearby"]="nearby"; center: BaiduCoordinate; radius_m: int</code> | radius_m 1..50000 |
| <code>PlaceSearchArea</code> | <code>CitySearchArea &#124; NearbySearchArea</code> | discriminator=area_type |
| <code>PlaceSearchRequest</code> | <code>schema_version="1.0"; query: str; tag: str &#124; None=None; area: PlaceSearchArea; result_limit: int &#124; None=None</code> | result_limit 1..50（若存在） |
| <code>PlaceSearchSuccess</code> | <code>schema_version="1.0"; status="success"; places: tuple[PlaceSummary, ...]; provenance: MapProvenance</code> | places 1..50 |
| <code>PlaceSearchNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 封闭无结果 |
| <code>PlaceSearchOutcome</code> | <code>PlaceSearchSuccess &#124; PlaceSearchNoResult</code> | discriminator=status |
| <code>PlaceDetailsRequest</code> | <code>schema_version="1.0"; uid: str</code> | uid 非空 |
| <code>PlaceDetailsSuccess</code> | <code>schema_version="1.0"; status="success"; details: PlaceDetails; provenance: MapProvenance</code> | UID 与请求一致 |
| <code>PlaceDetailsNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 版本化公共端口；当前真实 binding 1.7 不产生 |
| <code>PlaceDetailsOutcome</code> | <code>PlaceDetailsSuccess &#124; PlaceDetailsNoResult</code> | discriminator=status |
| <code>RouteMatrixRequest</code> | <code>schema_version="1.0"; mode: MatrixTravelMode; origins: tuple[BaiduCoordinate, ...]; destinations: tuple[BaiduCoordinate, ...]</code> | 两 tuple 均非空，乘积 ≤100 |
| <code>RouteMatrixCellSuccess</code> | <code>schema_version="1.0"; status="success"; origin_index: int; destination_index: int; distance_m: int; duration_s: int</code> | 索引有效；距离/时长 ≥0 |
| <code>RouteMatrixCellNoResult</code> | <code>schema_version="1.0"; status="no_result"; origin_index: int; destination_index: int</code> | 索引有效 |
| <code>RouteMatrixCell</code> | <code>RouteMatrixCellSuccess &#124; RouteMatrixCellNoResult</code> | discriminator=status |
| <code>RouteMatrixSuccess</code> | <code>schema_version="1.0"; status="success"; origin_count: int; destination_count: int; cells: tuple[RouteMatrixCell, ...]; provenance: MapProvenance</code> | 完整笛卡尔积且至少一个 cell success |
| <code>RouteMatrixNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 全部不可达或合法空结果 |
| <code>RouteMatrixOutcome</code> | <code>RouteMatrixSuccess &#124; RouteMatrixNoResult</code> | discriminator=status |
| <code>RouteEndpoint</code> | <code>schema_version="1.0"; coordinate: BaiduCoordinate; label: str &#124; None=None</code> | label 仅展示提示 |
| <code>DirectionsRequest</code> | <code>schema_version="1.0"; mode: TravelMode; origin: RouteEndpoint; destination: RouteEndpoint; alternatives_limit: int=1</code> | alternatives_limit 1..3 |
| <code>RouteStep</code> | <code>schema_version="1.0"; instruction: str; distance_m: int; duration_s: int; start: BaiduCoordinate; end: BaiduCoordinate</code> | instruction ≤1024；数值 ≥0 |
| <code>RouteAlternative</code> | <code>schema_version="1.0"; distance_m: int; duration_s: int; steps: tuple[RouteStep, ...]</code> | steps 1..128；数值 ≥0 |
| <code>DirectionsSuccess</code> | <code>schema_version="1.0"; status="success"; alternatives: tuple[RouteAlternative, ...]; provenance: MapProvenance</code> | alternatives 1..3 |
| <code>DirectionsNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 封闭无结果 |
| <code>DirectionsOutcome</code> | <code>DirectionsSuccess &#124; DirectionsNoResult</code> | discriminator=status |
| <code>WeatherRequest</code> | <code>schema_version="1.0"; location: BaiduCoordinate</code> | 无其他字段 |
| <code>CurrentWeather</code> | <code>schema_version="1.0"; condition: str; temperature_c: float; feels_like_c: float &#124; None=None; humidity_percent: float &#124; None=None; wind_description: str &#124; None=None</code> | 数值有限；湿度 [0,100] |
| <code>DailyWeatherForecast</code> | <code>schema_version="1.0"; date: date; day_condition: str; night_condition: str; high_c: float; low_c: float</code> | 温度有限且 high_c ≥ low_c |
| <code>WeatherSuccess</code> | <code>schema_version="1.0"; status="success"; current: CurrentWeather; forecast: tuple[DailyWeatherForecast, ...]=(); provenance: MapProvenance</code> | forecast 0..5、日期递增 |
| <code>WeatherNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 版本化公共端口；当前真实 binding 1.7 不产生 |
| <code>WeatherOutcome</code> | <code>WeatherSuccess &#124; WeatherNoResult</code> | discriminator=status |
| <code>MapLaunchRouteRequest</code> | <code>schema_version="1.0"; service: Literal["route"]="route"; origin: RouteEndpoint; destination: RouteEndpoint; mode: TravelMode; region: str &#124; None=None</code> | 可选能力 |
| <code>MapLaunchSearchRequest</code> | <code>schema_version="1.0"; service: Literal["search"]="search"; query: str; region: str &#124; None=None; location: BaiduCoordinate &#124; None=None; radius_m: int &#124; None=None</code> | location/radius 同时有或同时无；radius 1..50000 |
| <code>MapLaunchRequest</code> | <code>MapLaunchRouteRequest &#124; MapLaunchSearchRequest</code> | discriminator=service |
| <code>MapLaunchSuccess</code> | <code>schema_version="1.0"; status="success"; url: str; provenance: MapProvenance</code> | 第 14 节安全 URL |
| <code>MapLaunchNoResult</code> | <code>schema_version="1.0"; status="no_result"; provenance: MapProvenance</code> | 仅已绑定工具合法空结果 |
| <code>MapLaunchOutcome</code> | <code>MapLaunchSuccess &#124; MapLaunchNoResult</code> | discriminator=status |
| <code>MapCapabilityAvailability</code> | <code>schema_version="1.0"; capability: MapCapability; available: bool; reason_code: MapCapabilityUnavailableReason &#124; None=None</code> | available=True 当且仅当 reason_code=None；七项核心只能 available=True |

供应商 manifest 1.7 的 geocode 正向响应只含单一 <code>result.location</code> 合同，因此当前真实 parser 只产生 success；<code>GeocodeNoResult</code> 和 <code>GeocodeAmbiguous</code> 作为版本化公共端口保留，但当前 binding 不产生。只有未来取得官方结构依据或经过单独授权的负向合同发现，并升级 binding manifest，才允许启用对应分支。不得从地址文本、confidence、<code>result=null</code> 或多次调用自行构造无结果/歧义分支。

## 8. 地理编码

公共模型：

- <code>GeocodeRequest(schema_version, address, city)</code>：address 必填，city 可空；单项字符串上限 512；
- <code>GeocodeCandidate(schema_version, formatted_address, coordinate, confidence)</code>：confidence 可空，存在时为 [0, 1] 有限数；
- <code>GeocodeSuccess(schema_version, status, result, provenance)</code>；
- <code>GeocodeNoResult(schema_version, status, provenance)</code>；
- <code>GeocodeAmbiguous(schema_version, status, candidates, provenance)</code>；
- <code>GeocodeOutcome</code>：上述三个结果的封闭 discriminated union。

当前 binding 1.7 中唯一且可校验的正向结果返回 success；缺失、null、空 object 或候选集合形状均为 <code>BaiduMapResponseError</code>。公共 no_result/ambiguous 只有未来满足第 16.4、26 节的官方结构依据或单独授权负向发现，并升级 binding 后才能由真实 Gateway 产生；ambiguous 启用后最多 10 个候选。Gateway 不得基于字符串相似度自行挑选。

## 9. 逆地理编码

公共模型：

- <code>ReverseGeocodeRequest(schema_version, location)</code>；
- <code>AddressComponent(schema_version, country, province, city, district, town, adcode)</code>，各文本字段可空但不得补造，adcode 可空且存在时为 0..999999 的严格整数；
- <code>ReverseGeocodeSuccess(schema_version, status, formatted_address, address_component, nearby_places, provenance)</code>；
- <code>ReverseGeocodeNoResult(schema_version, status, provenance)</code>；
- <code>ReverseGeocodeOutcome</code>：上述两个结果的封闭 union。

nearby_places 使用 <code>PlaceSummary</code>，最多 20 个。binding 1.7 必须先按供应商原顺序验证全部原始 <code>result.pois[]</code>；缺 uid/name/addr/point.x/point.y 核心证据的项目确定性丢弃，字段存在但类型、文本、有限值或坐标范围畸形则整体响应错误。全部原始项验证完成后，才按原顺序把合格项显式投影前 20 个；公共 20 项是规范化 DTO 上限，不是供应商原始数组上限，且不得用先截断掩盖第 21 项之后的畸形数据。附近 POI 全部被确定性丢弃时，只要地址主体仍完整，仍返回 <code>ReverseGeocodeSuccess(nearby_places=())</code>。<code>ReverseGeocodeNoResult</code> 是保留端口，当前真实 binding 1.7 遇到 null/缺失/空 object 必须响应错误。逆地理编码只阻断依赖当前位置的流程，不改变普通 Demo 的发布语义。

## 10. 地点搜索与地点详情

### 10.1 地点值对象

<code>PlaceSummary</code> 字段依次为：

1. <code>schema_version</code>；
2. <code>uid</code>；
3. <code>name</code>；
4. <code>address</code>；
5. <code>coordinate</code>；
6. <code>category</code>。

uid、name、address 和 coordinate 是地点身份证据；缺少任一项的结果不得成为下游酒店候选。uid 只在百度供应商命名空间内唯一，不是项目生成 ID。category 可空，不得用于把普通地点改写成酒店。

<code>PlaceDetails</code> 字段依次为：

1. <code>schema_version</code>；
2. <code>uid</code>；
3. <code>name</code>；
4. <code>address</code>；
5. <code>coordinate</code>；
6. <code>category</code>；
7. <code>brand</code>；
8. <code>telephone</code>；
9. <code>rating</code>；
10. <code>business_hours</code>。

rating 存在时为 [0, 5] 有限数；business_hours 为最多 16 项的字符串 tuple。供应商价格、图片、评论正文、任意 detail object 和未白名单字段一律丢弃。当前 Module 12 只能把相关费用标记为未核验；未来价格能力也不得把地点详情中的供应商价格直接当作可信成交价格。

### 10.2 搜索区域

- <code>CitySearchArea(schema_version, area_type, region)</code>；
- <code>NearbySearchArea(schema_version, area_type, center, radius_m)</code>，radius_m 为 (0, 50000]；
- <code>PlaceSearchArea</code>：二者封闭 union。

<code>PlaceSearchRequest</code> 字段依次为 <code>schema_version</code>、<code>query</code>、<code>tag</code>、<code>area</code>、<code>result_limit</code>。result_limit 默认 None，存在时为 [1, 50]。

结果：

- <code>PlaceSearchSuccess(schema_version, status, places, provenance)</code>，places 非空；
- <code>PlaceSearchNoResult(schema_version, status, provenance)</code>；
- <code>PlaceSearchOutcome</code>：两者封闭 union。

搜索返回多个候选是正常 success，不等同于 ambiguous；候选选择属于模块 07/08。

### 10.3 详情

- <code>PlaceDetailsRequest(schema_version, uid)</code>；
- <code>PlaceDetailsSuccess(schema_version, status, details, provenance)</code>；
- <code>PlaceDetailsNoResult(schema_version, status, provenance)</code>；
- <code>PlaceDetailsOutcome</code>：两者封闭 union。

响应 UID 必须与请求 UID 完全一致；不一致是 <code>BaiduMapResponseError</code>，不是 no_result。

<code>PlaceDetailsNoResult</code> 是保留端口；当前真实 binding 1.7 遇到 <code>result=null</code>、缺失或空 object 必须响应错误，不能自行产生 no_result。

## 11. 路线矩阵

<code>RouteMatrixRequest</code> 字段依次为：

1. <code>schema_version</code>；
2. <code>mode: MatrixTravelMode</code>；
3. <code>origins: tuple[BaiduCoordinate, ...]</code>；
4. <code>destinations: tuple[BaiduCoordinate, ...]</code>。

origins 和 destinations 均非空，笛卡尔积不得超过 100。公共请求只接受 BD-09LL 坐标，不接受含糊地点名。

单元：

- <code>RouteMatrixCellSuccess(schema_version, status, origin_index, destination_index, distance_m, duration_s)</code>；
- <code>RouteMatrixCellNoResult(schema_version, status, origin_index, destination_index)</code>；
- <code>RouteMatrixCell</code>：两者封闭 union。

整体：

- <code>RouteMatrixSuccess(schema_version, status, origin_count, destination_count, cells, provenance)</code>；
- <code>RouteMatrixNoResult(schema_version, status, provenance)</code>；
- <code>RouteMatrixOutcome</code>：两者封闭 union。

cells 必须恰好覆盖每个 origin/destination 索引组合一次，并按 origin_index、destination_index 升序排列。部分不可达使用 cell 级 no_result；全部不可达时使用整体 no_result。distance_m 和 duration_s 为非负整数，不从文本猜测单位。

## 12. 路线规划

<code>RouteEndpoint(schema_version, coordinate, label)</code> 中 coordinate 必填；label 只供本地/下游展示，不可替代坐标，当前 binding 1.7 绝不把 label 发送给供应商。

<code>DirectionsRequest</code> 字段依次为：

1. <code>schema_version</code>；
2. <code>mode: TravelMode</code>；
3. <code>origin: RouteEndpoint</code>；
4. <code>destination: RouteEndpoint</code>；
5. <code>alternatives_limit</code>，范围 [1, 3]。

结果值：

- <code>RouteStep(schema_version, instruction, distance_m, duration_s, start, end)</code>；
- <code>RouteAlternative(schema_version, distance_m, duration_s, steps)</code>；
- <code>DirectionsSuccess(schema_version, status, alternatives, provenance)</code>；
- <code>DirectionsNoResult(schema_version, status, provenance)</code>；
- <code>DirectionsOutcome</code>：两者封闭 union。

alternatives 最多 3 条，每条最多 128 steps。instruction 最长 1024 字符。Gateway 不做路线排序、优化或“最佳”选择，只保持供应商顺序；路线优化属于模块 09。

binding 1.7 的 Directions 响应是 mode-specific：walking、driving、riding 的 <code>result.routes[].steps</code> 必须是单层对象 array；transit 必须是严格两层对象 array <code>result.routes[].steps[][]</code>。四种 mode 的供应商指令字段都精确为单数 <code>instruction</code>，不得接受复数 <code>instructions</code> 或其他 alias。transit 先按外层 array 原顺序、再按每个内层 array 原顺序形成原始 step 序列；必须先验证全部原始 step 的 object、distance/duration、坐标、instruction 类型和原始展平数量 1..128，instruction 必须是 string，但仅 transit 允许 strip 后为空。全部验证通过后，transit 才按原序显式丢弃空白 instruction 的 step，并把剩余非空文本映射为公共 <code>RouteStep.instruction</code>；丢弃后必须仍有 1..128 项，否则整体为 <code>BaiduMapResponseError</code>，不得映射 no_result。非 transit 的空白 instruction 直接响应错误。禁止排序、合并猜测、伪造文案、保留嵌套占位或读取第三层；单层/双层混合、空洞、对象与 array 混杂、第三层及任一其他字段畸形均为响应错误。四种 mode 的 route 仍必须直接提供 distance/duration；walking、driving、riding 彼此同型，transit 按其独立嵌套和显式空白过滤合同验证，不再要求四种 mode 的 steps 嵌套层级一致。

## 13. 天气

<code>WeatherRequest(schema_version, location)</code> 只接受 BD-09LL 坐标。

天气值对象：

- <code>CurrentWeather(schema_version, condition, temperature_c, feels_like_c, humidity_percent, wind_description)</code>；
- <code>DailyWeatherForecast(schema_version, date, day_condition, night_condition, high_c, low_c)</code>；
- <code>WeatherSuccess(schema_version, status, current, forecast, provenance)</code>；
- <code>WeatherNoResult(schema_version, status, provenance)</code>；
- <code>WeatherOutcome</code>：两者封闭 union。

公共 forecast 最多未来 5 天，日期必须严格递增且不得与 current 冒充同一结构。binding 1.7 允许供应商原始 <code>result.forecasts</code> 为 0..7 项：必须先对全部原始项验证字段、类型、日期严格递增、日期唯一和 <code>high >= low</code>，任一项失败或超过 7 项均整体响应错误；全部通过后才按供应商原顺序显式投影前 5 项到公共 tuple。该 7→5 投影是冻结合同，不是静默截断，不得跳过被投影掉两项的校验。温度为有限摄氏度数值，humidity_percent 存在时在 [0, 100]。天气失败由下游映射为 degraded，不得伪造晴天或沿用历史响应。

<code>WeatherNoResult</code> 是保留端口；当前真实 binding 1.7 遇到 <code>result=null</code>、缺失或空 object 必须响应错误，不能自行产生 no_result。

## 14. 可选地图调起

地图调起与“马克地图”不是同一能力。本模块只保留百度官方“地图调起”链接，不支持旅游文本生成地图、二维码、Base64 或图片。

请求：

- <code>MapLaunchRouteRequest(schema_version, service, origin, destination, mode, region)</code>，service 固定 route；
- <code>MapLaunchSearchRequest(schema_version, service, query, region, location, radius_m)</code>，service 固定 search；
- <code>MapLaunchRequest</code>：二者封闭 union。

结果：

- <code>MapLaunchSuccess(schema_version, status, url, provenance)</code>；
- <code>MapLaunchNoResult(schema_version, status, provenance)</code>；
- <code>MapLaunchOutcome</code>：两者封闭 union；
- <code>MapCapabilityAvailability(schema_version, capability, available, reason_code)</code>，reason_code 只能为稳定、无敏感枚举。

URL 必须：

- scheme 为 HTTPS；
- 不含 userinfo；
- 端口为空或 443；
- hostname 为 <code>map.baidu.com</code> 或以 <code>.map.baidu.com</code> 结尾；
- 总长度不超过 2048；
- 不含 data/javascript/file scheme；
- 不主动跟随或探测重定向。

URL 的任意组成部分不得包含当前 AK 的原文或任意百分号编码形式；query 参数名经大小写折叠和 percent-decoding 后不得为 <code>ak</code>、<code>key</code>、<code>api_key</code>、<code>token</code> 或 <code>access_token</code>。用户输入也不得等于或包含当前 Secret。校验失败只抛零敏感响应错误，禁止把 URL 或匹配片段放入异常。

二维码、Base64 和图片 block 必须确定性丢弃且不影响一个同时存在的合格 JSON URL；任何其他已知或未知的非文本/非结构化 block 均失败关闭。用户未请求时不得调用。能力缺失或调用失败只降级地图展示，不阻断普通 Demo。

七项核心工具名有官方公开依据；官方公开资料尚未给出地图调起的稳定 MCP tool name。因此 binding manifest 1.7 不得猜测名称，也不得按描述动态绑定未知工具。首版可公开独立 capability 并报告 unavailable；只有取得官方稳定名称/schema 证据后，才可在不改变核心 Protocol 的情况下升级私有 binding manifest、增加 parser 和执行独立 live probe。

## 15. Typed adapter 与可选能力

<code>BaiduMapAdapter</code> 是 runtime-checkable async Protocol，固定方法为：

~~~python
class BaiduMapAdapter(Protocol):
    async def geocode(self, request: GeocodeRequest) -> GeocodeOutcome: ...
    async def reverse_geocode(
        self, request: ReverseGeocodeRequest
    ) -> ReverseGeocodeOutcome: ...
    async def search_places(
        self, request: PlaceSearchRequest
    ) -> PlaceSearchOutcome: ...
    async def get_place_details(
        self, request: PlaceDetailsRequest
    ) -> PlaceDetailsOutcome: ...
    async def calculate_route_matrix(
        self, request: RouteMatrixRequest
    ) -> RouteMatrixOutcome: ...
    async def plan_directions(
        self, request: DirectionsRequest
    ) -> DirectionsOutcome: ...
    async def get_weather(
        self, request: WeatherRequest
    ) -> WeatherOutcome: ...
~~~

地图调起不加入核心 Protocol：

~~~python
class MapLaunchCapability(Protocol):
    def get_availability(self) -> MapCapabilityAvailability: ...
    async def create_map_launch(
        self, request: MapLaunchRequest
    ) -> MapLaunchOutcome: ...
~~~

公共 API 不提供工具名、任意参数字典、通用 <code>call_tool</code>、session handle 或 supplier response。新增可选能力必须使用独立 Protocol；只有需要所有业务调用者共同依赖的稳定能力，才允许经设计变更进入核心 Protocol。

## 16. MCP Gateway、工具发现与绑定

### 16.1 生产端点和 transport

生产只允许官方托管端点：

<code>https://mcp.map.baidu.com/mcp</code>

endpoint 是 <code>_mcp_gateway.py</code> 的私有常量。生产与 discovery 共用同一 URL 构造合同：只在内存对 Secret 值执行 <code>urlencode({"ak": api_key})</code>，把结果以唯一 query 追加到上述无 query/fragment 的常量。最终 URL 必须恰好有一个名为 <code>ak</code> 的参数，不得有 <code>key</code>/<code>api_key</code> 别名、重复参数、其他 query 或 fragment。URL 和 AK 不进入 settings repr、日志、异常、指标标签或文档。

只使用 Streamable HTTP。禁止：

- 读取可配置 URL；
- 回退 SSE 或 stdio；
- 重定向到自建/第三方 endpoint；
- 从 MCP 元数据接受替代 endpoint；
- 自动跟随跨主机重定向。

### 16.2 Binding manifest 1.7

七项核心稳定映射为：

| Capability | 官方公开 tool name | 必须可见的输入合同 |
|---|---|---|
| GEOCODING | <code>map_geocode</code> | object；必需 address；项目发送的 city、is_chinese_mainland properties 必须存在且类型兼容；inarea 可选但首版不使用 |
| REVERSE_GEOCODING | <code>map_reverse_geocode</code> | object；latitude、longitude properties 必须存在且为 JSON number-compatible；两者不要求出现在顶层 required；允许第 16.2 节所述唯一受限根 anyOf；lat/lng 即使作为 provider optional property 或另一个 anyOf 分支的 required 出现，也不得作为输入别名或发送；binding manifest 1.7 reverse outbound 只发送 latitude/longitude |
| PLACE_SEARCH | <code>map_search_places</code> | object；必需 query；支持 region 或 location/radius 受控形式；is_chinese_mainland property 必须兼容 |
| PLACE_DETAILS | <code>map_place_details</code> | object；必需 uid；is_chinese_mainland property 必须兼容 |
| ROUTE_MATRIX | <code>map_directions_matrix</code> | object；required 精确含 origins、destinations，不含 model；model property 必须存在且为 string-compatible；若有 enum 必须包含 driving/riding/walking |
| DIRECTIONS | <code>map_directions</code> | object；required 精确含 origin、destination，不含 model；model property 必须存在且为 string-compatible；若有 enum 必须包含 walking/driving/riding/transit；is_chinese_mainland property 必须兼容 |
| WEATHER | <code>map_weather</code> | object；必须支持 location 和 is_china；district_id 可选但首版不使用 |

此表定义项目期望，不声称公开文档给出了完整 JSON Schema。实施 T00 必须从锁定 SDK 和官方一手资料核验上述名称；首次获准连接托管服务时，<code>tools/list</code> 是实际 schema 的资格证据。

兼容规则：

- 每个核心名称必须恰好出现一次；
- inputSchema 根必须为 object；除 reverse 的下一条特例外，根或 property 出现 oneOf/anyOf/allOf/not/const 均失败关闭；
- 项目需要的 property 必须存在且类型兼容；reverse geocoding 的 latitude/longitude property 必须存在且为 JSON number-compatible，但不要求供应商在 required 中声明；
- reverse 根 anyOf 只有同时满足以下条件才兼容：根键只允许 type、properties、可选顶层 required 和 anyOf，direct type 仍精确为 object；anyOf 精确为两个互不重复的非空分支；每个分支精确只含 required，required 必须是无重复 string array；分支 required 只能引用 lat、lng、latitude、longitude，且每个被引用 property 都存在并为 JSON number-compatible；两个分支中至少一个 required 集合精确为 latitude、longitude；根不得再含任何未批准约束键。该判断只证明项目固定 outbound 的 latitude/longitude 能满足一个分支，不把 lat/lng 暴露为 DTO alias 或 outbound；
- 供应商不得增加项目未知的 required 字段；
- 新增 optional 字段允许存在但项目不得自动使用；
- 除 matrix/directions 的 model 特例外，enum 若存在必须包含项目实际发送的值；matrix/directions 的 model 若 enum 完全缺失，只在独立 PROVIDER-CONTRACT-DISCOVERY Gate 的 schema 阶段不失败并进入 Gate-only fixed-mode reinforcement；Gate 通过后，生产 registry 对该已资格化 binding 直接判定兼容，不保留 pending 或 reinforcement 状态；
- matrix/directions 的 model 若存在 enum，则 matrix 必须包含 driving/riding/walking，directions 必须包含 walking/driving/riding/transit；model 缺失或非 string-compatible、enum 存在但缺任一固定值均失败；
- reverse geocoding 的 latitude/longitude property 缺失、类型冲突或出现未知 required 均失败；provider 未将二者列入 required 不削弱 DTO 对有限值和范围的 required 校验；
- 除上述 reverse 根 anyOf 特例外，oneOf/anyOf/allOf/not/const 一律不兼容；
- 名称重复、缺失、类型冲突、未知必填字段、含糊组合或 malformed schema 均失败关闭；
- 未列入 manifest 的工具一律忽略，不按相似名称、描述或 schema 猜测绑定；
- 可选地图调起缺失时 runtime 正常启动并报告 unavailable。
- 每个启动 attempt、每个 <code>ClientSession</code> 若到达发现阶段，都最多且恰好发出一个无分页 <code>tools/list</code> MCP request；返回的 <code>nextCursor</code> 必须为 None/null，首版不请求第二页，非空 cursor 直接抛工具 schema 错误。正常启动总计一次；只有第 20.3 节获准的首次启动 attempt 在 list 阶段发生瞬时连接错误/timeout 时，全新第二 attempt 才可再发一次，因此该重试路径总计两次、每个 session 仍各一次。

manifest 保存 expected schema 约束和 parser ID，不保存供应商完整原始 schema。tools/list 原始响应只在内存中完成比较，随后丢弃。

### 16.3 发现顺序

每个 startup attempt 的启动顺序固定为：

1. 校验 settings 和 limits；
2. 创建 Streamable HTTP transport；
3. 创建 ClientSession；
4. 执行 initialize；
5. 若到达该阶段，在本 attempt/ClientSession 恰好执行一次无分页 tools/list；
6. 校验 binding manifest；
7. 只在七项核心均唯一兼容时发布 ready runtime。

正常 startup 在发布 ready 前后 tools/call 均为 0；enum 缺失不得触发 matrix/directions probe。tools/call 只能由已 ready runtime 收到真实 adapter/capability 请求后发生。生产 registry/runtime 不保留 pending、reinforcement 或动态资格化状态。T03/T04 只能在独立 PROVIDER-CONTRACT-DISCOVERY Gate 已返回 READY_FOR_T03_T04 后开始。核心资格失败不得返回“部分核心 adapter”。地图调起不影响核心 ready。运行期间不再次发现工具，不热更新 schema；供应商变化通过新 runtime 和重新资格处理。

### 16.4 精确 outbound 与响应字段路径

坐标字符串禁止科学计数法并使用 <code>lat,lng</code>，天气例外按官方合同使用 <code>lng,lat</code>。路线矩阵的 <code>origins</code>/<code>destinations</code> 分别按 DTO tuple 原顺序把每个坐标编码为 <code>lat,lng</code>，再用单个 ASCII U+007C 竖线连接；不得排序、加空白或尾分隔符。Directions 的 origin/destination 只编码 coordinate，<code>RouteEndpoint.label</code> 永不外发。GEOCODING、PLACE_SEARCH、PLACE_DETAILS、DIRECTIONS 只发送 <code>is_chinese_mainland="true"</code>；WEATHER 只发送 <code>is_china="true"</code>；REVERSE_GEOCODING 和 ROUTE_MATRIX 两者都不发送。不得发送两个字段、别名或表外字段，也不得运行时猜测或动态切换。manifest 资格已强制要求 geocode 的 city property 兼容；<code>request.city</code> 非 None 时必须发送，None 时省略，不存在“单次调用再容忍 schema 缺 city”的分支。<code>result_limit</code> 永远先作为本地确定性上限；supplier schema 存在 manifest 明确命名的兼容 limit property 时才额外下推，不存在时不发送该参数且不报错。

| Capability | outbound arguments | status=0 后的唯一白名单响应路径与规范化 |
|---|---|---|
| GEOCODING | <code>address</code>；<code>request.city</code> 非 None 时必发 <code>city</code>；<code>is_chinese_mainland</code> | <code>result.location.lat/lng</code> 必需；<code>result.confidence</code> 可选整数 0..100，除以 100；DTO 的 formatted_address 精确回显已校验的 request.address，不冒充供应商规范化地址 |
| REVERSE_GEOCODING | <code>latitude</code>、<code>longitude</code> | <code>result.formatted_address</code>、<code>result.addressComponent.country/province/city/district/town/adcode</code>；adcode 接受非 bool JSON integer，或严格 ASCII 十进制 string 后规范化为 int；<code>result.pois[]</code> 只读 uid/name/addr/point.x/point.y/tag，映射为 address/longitude/latitude/category；全部原始项验证后按原序投影前 20 个合格项 |
| PLACE_SEARCH | <code>query</code>、可选 <code>tag</code>、<code>is_chinese_mainland</code>；city 使用 <code>region</code>；nearby 使用 <code>location="lat,lng"</code> 和 <code>radius</code>；可资格时下推 limit | 官方开源实现调用 Place v3，<code>results[]</code> 只读 uid/name/address/location.lat/lng/tag；空数组为 no_result；顺序保持 |
| PLACE_DETAILS | <code>uid</code>、<code>is_chinese_mainland</code> | 官方开源实现调用 Place v2 detail；只读 <code>result.uid/name/address/location.lat/lng/telephone</code> 及可选 <code>result.detail_info.tag/brand/overall_rating/shop_hours</code>；shop_hours 若为非空字符串只作为单个 tuple 项，不按标点猜测拆分 |
| ROUTE_MATRIX | <code>model</code>；按上述原序 <code>lat,lng</code> + ASCII U+007C 规则编码的 <code>origins</code>、<code>destinations</code> | <code>result[]</code> 按 origin-major 顺序映射；每项同时存在 <code>distance.value</code>、<code>duration.value</code> 才为 cell success，二者同时缺失才为 cell no_result，单缺为响应错误 |
| DIRECTIONS | <code>model</code>、<code>origin</code>、<code>destination</code>、<code>is_chinese_mainland</code> | <code>result.routes[]</code> 的 distance/duration；walking/driving/riding 读取单层 <code>steps[]</code>，transit 读取严格两层 <code>steps[][]</code> 并按外层后内层原序展平；四种 mode 的 step 指令字段都精确为 <code>instruction</code>；transit 必须先验证全部原始 step 和原始展平容量，再按原序显式丢弃空白 instruction，丢弃后为空则响应错误；step 其余只读 distance/duration/start_location.lat/lng/end_location.lat/lng，step 坐标只在本 capability 接受 JSON number 或严格 ASCII 十进制 string 后规范化为 float；空 routes 为 no_result；按原顺序取显式 alternatives_limit |
| WEATHER | <code>location="lng,lat"</code>、<code>is_china</code> | <code>result.now.text/temp/feels_like/rh/wind_class</code> 与 <code>result.forecasts[]</code> 的 date/text_day/text_night/high/low；原始 forecast 0..7 项须全部验证，再按原顺序显式投影前 5 项 |

所有七项响应根必须是 object 且 <code>status</code> 必须是非 bool 整数。<code>status=0</code> 才进入上表；HTTP 401/403/429 先按第 18 节映射。<code>CallToolResult.isError=True</code> 且没有另经批准的结构化安全错误码时固定为 <code>BaiduMapProtocolError</code>；<code>status!=0</code> 且没有批准的结构化安全错误码时固定为 <code>BaiduMapResponseError</code>。二者的自由文本绝不分类。字段路径、类型、单位或空结果形状与上表不一致属于供应商合同漂移，不能用 alias、双 parser 或自由文本补偿。

当前 manifest 1.7 只启用三个 capability-specific no_result allowlist：PLACE_SEARCH 为 <code>status=0</code> 且 <code>results</code> 是空 array，或仅按下一段“缺核心证据则整项丢弃”规则过滤后为空；ROUTE_MATRIX 为 <code>status=0</code> 且 <code>result</code> 是空 array，或其长度精确等于请求笛卡尔积且每个 cell 都同时缺失 distance.value/duration.value；DIRECTIONS 精确为 <code>{"status": 0, "result": {"routes": []}}</code>。部分 matrix cell 不可达仍返回 <code>RouteMatrixSuccess</code> 并含 cell-level no_result，不属于整体空结果。GEOCODING、REVERSE_GEOCODING、PLACE_DETAILS、WEATHER 的公共 no_result DTO 仅为版本化端口；在取得官方结构依据或单独授权的负向合同发现并升级 binding 前，<code>{"status": 0, "result": null}</code> 以及缺失、空 object、错误 container、部分 success 字段缺失或其他“看起来为空”的形状一律为 <code>BaiduMapResponseError</code>。offline 合同必须证明三个启用 allowlist 可映射、四个 null 形状和其他空形状失败关闭；transit 原始 steps 非空但经空白 instruction 过滤后为空是响应错误，不新增 no_result；13 次正向 discovery/live 不得被表述为证明任何 no_result 形状。

供应商标量规范化是封闭的：除 Directions step 坐标外，坐标只接受 JSON number（排除 bool）并校验有限/范围；reverse POI 的 <code>point.y</code> 映射 latitude、<code>point.x</code> 映射 longitude，不接受 location/lat/lng alias 或十进制 string。Directions 的 <code>start_location.lat/lng</code> 与 <code>end_location.lat/lng</code> 接受 JSON number，或完全匹配 <code>-?(0|[1-9][0-9]*)(\.[0-9]+)?</code> 的 ASCII 十进制 string，后者使用 Decimal 解析后转换为有限 float 并校验纬经度范围；禁止空白、加号、指数、NaN/Infinity，其他坐标路径不得复用该转换。距离、时长和索引只接受 JSON integer（排除 bool）；reverse <code>addressComponent.adcode</code> 接受非 bool JSON integer，或完全匹配 <code>0|[1-9][0-9]*</code> 的 ASCII 十进制 string，后者按十进制转换为 int；两种输入均须满足 0..999999。adcode string 禁止空白、正负号、小数、指数和前导零，其他整数路径不得复用该转换。confidence 只接受 0..100 integer 并除以 100。天气温度/湿度和地点 rating 接受 JSON number，或完全匹配 <code>-?(0|[1-9][0-9]*)(\.[0-9]+)?</code> 的 ASCII 十进制字符串；禁止空白、加号、指数、NaN/Infinity，使用 Decimal 解析后再校验范围并转换有限 float。天气日期只接受 <code>YYYY-MM-DD</code>。可空文本缺失/null/空白时规范化为 None，非空文本去首尾空白后再验长度；必填文本为空即响应错误。搜索结果缺 uid/name/address/location 任一证据时整项确定性丢弃，若所有搜索项均被丢弃则 <code>PlaceSearchNoResult</code>；reverse nearby POI 则以 uid/name/addr/point.x/point.y 为核心证据并采用同样逐项丢弃，全部原始项验证后才投影前 20 个合格项；即使全部被丢弃，只要地址主体有效仍返回 <code>ReverseGeocodeSuccess(nearby_places=())</code>。字段存在但类型/范围畸形仍是响应错误。除这些明列转换外不得 coercion、alias 或猜测。

### 16.5 官方 SDK 的受控调用路径

生产直接使用 <code>mcp==1.28.1</code>。initialize 和每个启动 attempt/ClientSession 唯一一次 <code>ClientSession.list_tools()</code> 可使用官方高层方法；工具调用不得使用会自行缓存 schema、再次 list 或先执行 outputSchema 校验的高层 <code>ClientSession.call_tool()</code>。Gateway 必须通过同一官方 session 调用 <code>send_request(ClientRequest(CallToolRequest(params=CallToolRequestParams(name=tool_name, arguments=arguments))), CallToolResult, request_read_timeout_seconds=timedelta(seconds=remaining_s))</code>，发出恰好一个 <code>tools/call</code>；不得省略 <code>ClientRequest</code> 根包装或传任意 meta/progress callback。remaining_s 来自已含准入/semaphore/解析的 capability 总期限，外层总 timeout 仍为最终上限。随后由项目第 19 节边界选择 content 并严格解析。不得复制或改写 SDK transport。

Streamable HTTP client factory 必须显式注入 <code>httpx.AsyncClient(follow_redirects=False)</code>；connect/write/pool 各 10 秒，capability 外层期限承担 read/总期限。任意 3xx 都映射为 transport error，不读取 Location 后继续访问。

日志保护使用包内进程级、线程安全、引用计数 guard。首个 runtime 进入时保存当前 LogRecordFactory，以及根/现有 handler 和 <code>mcp</code>、<code>mcp.client.streamable_http</code>、<code>mcp.shared.session</code>、<code>httpx</code>、<code>httpcore</code> 及所有现有子 logger 的 level/disabled/propagate/handlers/filters；同时从锁定安装包的 import spec 冻结并规范化 <code>mcp</code>、<code>httpx</code>、<code>httpcore</code> 三个包根。record 的敏感来源 predicate 精确为：<code>record.name</code> 等于上述 namespace 或以 <code>mcp.</code>/<code>httpx.</code>/<code>httpcore.</code> 开头，或者规范化绝对 <code>record.pathname</code> 位于三个锁定包根之一；后一个条件不受 logger name 限制，专门覆盖 SDK 使用的 <code>root</code> 和 <code>client</code>。命中时必须在任何 formatter 前固定替换 msg/args/exc_info/stack_info 并删除非标准 extra，或直接拒绝输出；未命中 predicate 的其他 root/application record 不得过滤。重叠 runtime 只增加引用计数；只有最后一个退出才原样恢复本 guard 保存且仍由本 guard 持有的对象，若应用期间主动改变同一配置则不覆盖该新值。测试必须包含来自锁定包路径且 logger name 分别为 <code>root</code>/<code>client</code> 的 DEBUG/INFO/异常 canary、带 AK endpoint/raw message/session ID、同名但来源于应用路径的反例，以及两个 runtime 重叠并以相反顺序关闭；敏感 canary 均不得出现，应用反例和原配置必须保持。

### 16.6 PROVIDER-CONTRACT-DISCOVERY 安全门

在实现 capability parser 前，必须取得独立网络授权并执行一次供应商合同发现。它不是最终 live qualification：只允许 initialize、一次无分页 tools/list，以及第 23.2 节七个能力步骤。geocode 对 A/B 各一次；reverse/search/details/weather 各一次；matrix 对 walking/driving/riding 各一次；directions 对 walking/driving/riding/transit 各一次，总计恰好十三个 tools/call。不得写业务代码来迎合结果。schema 阶段必须验证 binding manifest 1.7 的七项唯一工具、object 根、required 精确性、property/type、未知 required、reverse latitude/longitude number-compatible property，以及 reverse 根 anyOf 是否精确满足第 16.2 节的封闭规则。matrix/directions 的 model 必须为 string-compatible；若 enum 存在，分别验证 matrix 包含 driving/riding/walking、directions 包含 walking/driving/riding/transit；若 enum 完全缺失，不在 schema 阶段失败，而必须继续既有十三次正向调用，以三个 matrix mode 同型成功、directions 的 walking/driving/riding 单层 steps 和非空单数 instruction 同型成功、transit 严格双层 steps、单数 instruction string、全部原始 step 验证和显式空白过滤成功，作为该 model 的唯一资格依据。任一 mode 失败、空结果或偏离其 mode-specific 结构/指令规则均绑定失败，不能推断未探测的其他 mode。原始 MCP/JSON 仅驻留内存并立即丢弃，证据只记录工具名、required/property/type/enum、content block 类型组合、结构化字段路径和 scalar/array/object/null 类型，不记录标量值、地址、坐标、UID、天气、URL、错误正文或 Secret。

binding 1.7 的脱敏依据由既有 schema/mode 资格和一次单独授权的 reverse 响应诊断共同组成：tools/list 返回 nextCursor=None；七项 core tool 各恰好一次且根均为 object；map_geocode、map_search_places、map_place_details、map_directions 使用 is_chinese_mainland，map_weather 使用 is_china；map_reverse_geocode 暴露 lat、lng、latitude、longitude 四个 property，latitude/longitude 为 number，顶层 required 缺失或等价为空，并有精确两个只含 required 的根 anyOf 分支；本项目只资格化 latitude/longitude outbound，lat/lng 不成为输入或发送 alias。reverse 诊断只执行 initialize=1、tools/list=1、geocode=1、reverse=1，确认 result/addressComponent/adcode 结构兼容、原始 POI array 可超过公共 20 项、POI 使用 uid/name/addr/point.x/point.y 而不是 address/location alias；tag→category 另由百度官方返回参数合同支持，且未保存或输出标量、精确数量、Secret、URL 或原始响应。map_directions_matrix 与 map_directions 的 model 为 string 且未声明 enum；matrix required 精确为 origins、destinations，directions required 精确为 origin、destination；walking/driving/riding 的 directions steps 为单层 array、指令字段为单数 instruction 且 step 坐标为严格 ASCII 十进制 string；transit 的 steps 为严格双层 array、指令字段仅为单数 instruction、该字段是 string 且观察到允许空白，step 坐标为 number；weather 的原始 forecasts 最多七项并按第 13 节完整验证后投影前五项。百度官方公交路线规划 REST 文档的复数 instructions 仅为解释差异的补充资料，不覆盖 hosted MCP 的实际投影，也不进入 binding。上述依据不能扩大到其他自由文本或未观察事实。

发现结果必须与第 16.2 节以及第 16.4、19 节中本次实际观察到的正向路径逐项相符，才能进入 parser 实现。该 Gate 只资格化 tools/list、正向响应路径、mode-specific 结构、content boundary 和交叉一致性；它不资格化、也不得报告已证明任何 no_result 形状。若 model enum 缺失，3 个 matrix mode 与 4 个 directions mode 的 reinforcement 只在本 Gate 内执行；3 个 matrix mode 必须彼此同型，directions 的 walking/driving/riding 必须彼此保持单层 steps 同型，transit 必须单独满足严格双层 steps 合同，不得要求 transit 与前三种 mode 具有相同嵌套层级。全部七次正向调用满足各自结构后，Gate 才能返回 READY_FOR_T03_T04，生产 registry 对已资格化的无 enum schema 直接判定兼容，不保留 pending/reinforcement 或动态资格化状态。no_result 只按第 16.4 节的当前 allowlist 和 offline fail-closed contract 实现；若正向 Gate 意外返回空结果或未知形状，立即停止并返回设计阶段，不能把该一次结果升级为合同。任何路径、类型、单位、block 组合或 schema 差异均返回设计阶段并请求用户批准；不得把临时原始响应保存为 fixture、snapshot、golden file 或报告。不得把上述 Gate 调用写成生产 startup 行为或持久化 discovery marker。最终七项资格仍必须在完整 offline/coverage 之后重新执行，发现通过不得冒充资格通过。

## 17. Settings 与 Secret

公共模型 <code>BaiduMapSettings</code> 只有：

- <code>api_key: SecretStr</code>。

<code>repr(settings)</code> 与 <code>str(settings)</code> 均固定为 <code>BaiduMapSettings(api_key=**********)</code>，不使用 Pydantic 默认格式暴露 SecretStr 对象结构。<code>load_baidu_map_settings(environ: Mapping[str, str] | None = None)</code> 复用模块 02 的成熟模式：

- 显式 Mapping 时只读该 Mapping，不访问 <code>.env</code> 或 <code>os.environ</code>；
- 未提供 Mapping 时，从项目根固定 <code>.env</code> 执行 <code>override=False</code>、<code>interpolate=False</code> 加载，再读取进程环境；
- 进程变量优先于 <code>.env</code>；
- <code>BAIDU_MAPS_API_KEY</code> 缺失、空白，或经 strip/casefold 后等于 <code>changeme</code>、<code>your-baidu-maps-api-key</code>、<code>&lt;your-baidu-maps-api-key&gt;</code>、<code>__baidu_maps_api_key_required__</code> 任一占位符时抛 <code>BaiduMapConfigurationError</code>；
- 显式 Mapping 或默认加载后的环境中只要存在遗留键 <code>BAIDU_MAPS_MCP_URL</code>，无论值是否为空，均抛配置错误；
- 不得读取、打印或返回真实 Secret。

未来配置迁移必须：

1. 从 <code>compose.yaml</code> 删除 <code>BAIDU_MAPS_MCP_URL</code>；
2. 从 <code>.env.example</code> 删除该变量和“URL 可配置”说明；
3. 保留 <code>BAIDU_MAPS_API_KEY</code> 占位符；
4. 不读取或修改用户真实 <code>.env</code>，只在报告中要求用户手工清理遗留变量；
5. 证明 offline 测试不读取真实 <code>.env</code>。

生产部署使用环境变量/Secret 注入与本地开发使用 <code>.env</code> 是同一 settings 合同，不是两套代码路径。

## 18. 技术异常和业务 outcome

### 18.1 公共异常

稳定错误码枚举 <code>BaiduMapErrorCode</code> 与异常一一对应：

| 异常 | 稳定 code | 适用场景 |
|---|---|---|
| <code>BaiduMapRequestError</code> | REQUEST_ERROR | Gateway 边界重新校验传入 DTO 失败 |
| <code>BaiduMapConfigurationError</code> | CONFIGURATION_ERROR | Secret、遗留 URL 变量、limits 或本地装配错误 |
| <code>BaiduMapAuthenticationError</code> | AUTHENTICATION_ERROR | 明确的 401/403 或经资格确认的鉴权错误 |
| <code>BaiduMapRateLimitError</code> | RATE_LIMIT_ERROR | 明确的 429 或经资格确认的限流错误 |
| <code>BaiduMapTimeoutError</code> | TIMEOUT_ERROR | 总调用期限或启动/关闭期限到期 |
| <code>BaiduMapTransportError</code> | TRANSPORT_ERROR | DNS、TLS、连接、HTTP 或流传输失败 |
| <code>BaiduMapSessionError</code> | SESSION_ERROR | 生命周期误用、初始化或资源关闭失败 |
| <code>BaiduMapToolUnavailableError</code> | TOOL_UNAVAILABLE | 核心工具缺失或调用前已知不可用 |
| <code>BaiduMapToolSchemaError</code> | TOOL_SCHEMA_ERROR | tools/list schema 不兼容或歧义 |
| <code>BaiduMapProtocolError</code> | PROTOCOL_ERROR | MCP 协议、request ID、未知 isError 或内容协议错误 |
| <code>BaiduMapResponseError</code> | RESPONSE_ERROR | 响应 JSON 或规范化 DTO 不合法 |
| <code>BaiduMapConcurrencyError</code> | CONCURRENCY_ERROR | Gateway 内部并发不变量或 semaphore 生命周期损坏 |

<code>BaiduMapErrorCode</code> 还包含 <code>REQUEST_ERROR</code>，枚举值均为表中 code 的小写字符串。<code>BaiduMapError</code> 构造签名精确为 <code>__init__(*, capability: MapCapability | None = None)</code>；具体子类以 class-level 常量绑定唯一 <code>code</code>，调用方不得传 code。实例只保存只读 <code>code</code> 和 <code>capability</code>；<code>args == (code.value, capability.value if capability else "none")</code>；<code>str(error)</code> 与 <code>repr(error)</code> 均固定为 <code>ClassName(code=&lt;value&gt;, capability=&lt;value-or-none&gt;)</code>。继承自 <code>BaseException</code> 的 <code>__traceback__</code> 是 Python/contextlib 必需的运行时元数据，允许按 <code>BaseException</code> 原生类型约束回填或清空，不属于业务状态，也不得改变 code/capability/args/str/repr；其他额外属性仍禁止。不定义值相等、JSON/pickle 序列化或额外业务属性。

每个 Gateway 公共方法先要求 <code>type(request) is 对应请求 DTO</code>，再用对应 DTO 的 <code>model_validate(request.model_dump(mode="python"))</code> 构造 fresh copy；dict、subclass、错误 DTO 或无法 dump 的对象均不触发网络并映射 <code>BaiduMapRequestError(capability=...)</code>。捕获 Pydantic <code>ValidationError</code> 后作相同包装；直接构造 DTO 的 ValidationError 属于调用方本地错误，但同样不得在 provider 边界记录。所有技术异常只保存上述稳定字段；<code>str</code>、<code>repr</code>、<code>args</code> 不得包含 URL、AK、请求文本、坐标、UID、供应商错误正文、原始 payload 或底层异常正文。

公共异常不得在活动的底层 <code>except</code> 中抛出：except 内只计算稳定 error class/capability，清除临时请求/响应/异常引用后退出 except；确认没有 active exception 后才构造并 <code>raise</code> 公共异常。仅写 <code>raise ... from None</code> 不满足此合同，因为它仍保留 <code>__context__</code>。除原样传播的 <code>asyncio.CancelledError</code> 外，每个被包装异常必须满足 <code>__cause__ is None</code>、<code>__context__ is None</code>，且公共 traceback/local 不可达底层异常、请求、含 AK URL 或 payload。

### 18.2 业务 outcome

以下不是异常：

- 合法零结果 → capability-specific no_result；
- 地理编码存在多个无法唯一判定的候选 → GeocodeAmbiguous；
- 矩阵部分不可达 → cell-level no_result；
- 搜索返回多个地点 → PlaceSearchSuccess。

配置、鉴权、限流、超时、传输、session、工具、schema、协议和畸形响应永远不是 no_result 或 ambiguous。

### 18.3 错误优先级

单次调用按以下优先级处理：

1. <code>asyncio.CancelledError</code> 原样传播；
2. 精确请求类型与 fresh DTO 校验；
3. runtime 生命周期准入；
4. semaphore 等待与总 timeout；
5. transport/HTTP/MCP request 异常；
6. <code>CallToolResult.isError</code>；
7. content 类型和 JSON 边界；
8. capability parser 和 DTO 不变量；
9. success/no_result/ambiguous 业务映射。

只有明确 HTTP 状态或经 live 资格冻结的结构化安全错误码可映射鉴权/限流。未知 <code>isError=True</code> 一律为 <code>BaiduMapProtocolError</code>；未知 <code>status!=0</code> 一律为 <code>BaiduMapResponseError</code>；不得解析自由文本猜测错误类别。

## 19. 响应选择、规范化与原始 payload

响应处理固定为：

1. 若 <code>isError=True</code>，不解析成功 payload，按第 18 节处理；
2. 若存在 <code>structuredContent</code>，它必须是单一 JSON object，优先使用；
3. 否则只允许 content 恰好包含一个 TextContent，且完整文本能解析为一个 JSON object；
4. 禁止从 Markdown fence、前后说明、多个文本块、数组顶层或子字符串中提取 JSON；
5. 七项核心能力遇到 ImageContent、AudioContent、ResourceLink、EmbeddedResource 或未知 block 时失败关闭；
6. 地图调起只从合格 JSON object 读取 URL；明确的 ImageContent 或带 QR/Base64 语义的块确定性丢弃，其他已知/未知附加块失败关闭，绝不保留其内容；
7. capability parser 只读取白名单字段，校验单位、索引、坐标、UID 和容量；
8. 构造项目 DTO 和 provenance；
9. 立即释放对原始 CallToolResult、content、JSON object 和供应商异常的引用。

原始 payload、tools/list 原始 schema、MCP 对象和底层异常不得进入：

- 公共 DTO；
- Graph state/checkpoint；
- Artifact；
- 日志、trace、metric label；
- 异常正文或 repr；
- 测试 snapshot、golden file 或最终报告。

允许日志只包含稳定事件名、capability、manifest version、成功/失败分类、脱敏耗时和计数。请求内容、位置、UID、地址、URL query 和 Secret 均禁止记录。

## 20. 生命周期、超时、重试与取消

### 20.1 Runtime 所有权

<code>create_baidu_map_runtime</code> 返回 async context manager。进入时完成连接、initialize、tools/list 和 manifest 校验；退出时关闭 session 和 transport。SDK transport/session context 具有任务亲和性：每次 startup attempt 必须创建一个专用 SDK owner task，且只有该 task 可以创建并进入 transport/session context、执行 initialize/tools/list、等待关闭信号以及退出相同 context。公共签名精确为：

~~~python
class BaiduMapRuntime:
    @property
    def adapter(self) -> BaiduMapAdapter: ...

    @property
    def map_launch(self) -> MapLaunchCapability: ...

    def get_capability_availability(
        self, *, capability: MapCapability
    ) -> MapCapabilityAvailability: ...

    async def aclose(self) -> None: ...

@asynccontextmanager
async def create_baidu_map_runtime(
    *,
    settings: BaiduMapSettings,
    clock: UtcClock,
    limits: BaiduMapLimits = DEFAULT_BAIDU_MAP_LIMITS,
) -> AsyncIterator[BaiduMapRuntime]: ...
~~~

七项核心在 runtime 发布后 availability 恒为 available=True；MAP_LAUNCH 必须满足第 7.4 节 available/reason 不变量。对 unavailable 的 map_launch 调用必须在发送前抛 <code>BaiduMapToolUnavailableError(capability=MAP_LAUNCH)</code>，不能返回 no_result；no_result 只表示已绑定工具返回合格空结果。

一个 Application/CLI runtime 拥有一个 session，并可供多个 run 并发使用。禁止 import-time/global singleton，禁止把 runtime/session 放入 state、checkpoint 或 Artifact。关闭后任何新调用都抛 session error。

owner task 通过私有 one-shot ready future 向创建者发布 session/registry 或稳定启动失败，并在发布成功后只等待私有 close signal；它不是调用 worker，不执行任何 tools/call。瞬时 startup retry 必须先让失败 attempt 在其 owner task 内完成同任务清理并 join，再创建全新的 owner task/session；调用方取消 startup 时也必须取消并 join 当前 owner，CancelledError 原样传播。

session 或 transport 发生终端故障后，Gateway 进入 broken 状态，不原地重连。Application 可以显式关闭并创建新 runtime，但下游只有使用新的 OperationId 并重新消费业务预算时才可重新发起业务调用。

生命周期状态机只有 <code>starting → ready → broken/closing → closed</code>；starting 失败先清理再直接 closed，broken 只能进入 closing。状态检查、调用准入、in-flight 计数和 closing 切换由同一 async lock 原子保护：

- 请求按第 18.1 节完成 fresh 校验后，只有 ready 可接纳；获取准入后才登记为 in-flight，再等待共享 semaphore；校验期间若发生 close，随后准入必须拒绝且零网络；
- closing 切换先关闭准入，尚未登记的调用和等待 lock 的调用抛 session error；已经登记（含 semaphore waiter）的调用在正常 closing 下允许继续；
- 首个 <code>aclose()</code> 原子切换 closing、建立唯一 absolute monotonic public deadline，并创建且缓存唯一私有 close task；Gateway 强引用该 task并安装异常回收机制，它是明确持有、可观察、可回收的生命周期任务，不是未跟踪 orphan。所有并发或后续 close waiter 只用 cancellation shield 观察同一 task和同一 deadline；取消某一个 waiter只向该 waiter传播 CancelledError，不取消 close task或其他 waiter；
- <code>close_timeout_s</code> 是 <code>aclose()</code> 调用方的公开等待上限，不是底层 task/context 的强制终止期限。每个 waiter只等待共享 deadline 的剩余时间，晚到 waiter不得重新获得完整期限；close task在期限内正常完成则成功，在期限内完成普通 owner/context 关闭失败则稳定映射 session error；deadline 到达而 task仍 pending 时，当前及清理期内的后续 <code>aclose()</code> 稳定抛 <code>BaiduMapTimeoutError</code>，但不得取消唯一 close task；
- 唯一 close task在 public deadline 前等待已登记调用自然归零并请求 owner正常退出；deadline 到达时取消仍登记调用和 owner，然后可以在调用方已经收到 timeout 后继续被跟踪地等待 in-flight finally、semaphore恢复、owner同 task context exit和 owner join。transport/session context仍只能由原 owner task退出；禁止第二个 context-exit/cleanup task，禁止跨 task调用 <code>__aenter__</code>/<code>__aexit__</code>；
- 只要上述清理尚未真正完成，state必须保持 closing，所有新能力调用均以 session error、零网络退出，原 session不得复用，进程级日志 guard继续持有；不得提前报告 closed或释放 MCP/httpx/httpcore 日志保护。只有 in-flight为空、semaphore全恢复、owner task done且同 task context exit完成后，唯一 close task才切换 closed并精确释放一次 guard；真实 closed后重复 <code>aclose()</code> 立即成功；
- close task在共享期限后仍 pending时，重复 <code>aclose()</code> 立即得到同一 timeout；不得新建第二个 close task或重复退出同一 context，也不得永久复用一个已经结束却未完成状态收尾的错误 task。若 owner/context的延迟清理最终失败，close task必须回收脱敏后的底层异常，不产生“Task exception was never retrieved”；已经向调用方报告的 timeout不得被该延迟错误改写；
- 正常显式 close或正常 context exit的关闭失败按上述时间边界映射 session error；若 context body已有异常或调用方取消，原异常/CancelledError优先，close timeout或延迟清理异常不得覆盖它；
- Python不能安全强杀永久拒绝协作的 task。本模块只保证锁定 asyncio/AnyIO/MCP SDK的协作式取消和受控、被跟踪的延迟清理；永久拒绝协作的第三方 task会使唯一 <code>_close_task</code> 保持 pending、Gateway保持 closing且 guard继续持有，不能通过提前 closed、释放 guard或宣称强制终止来伪造零 orphan；
- 准入前的 request error、semaphore 等待 timeout/取消、明确 429 和 optional unavailable 不破坏 session；发送后的取消/timeout、鉴权、transport、session、protocol、schema 或 response 错误使 runtime 原子进入 broken。进入 broken 时，已经发送 request 的调用只允许完成接收/清理；已经登记但仍在等待 semaphore、尚未发送的调用在获得唤醒后必须原子重查状态，以 session error、零网络退出并归零 in-flight；
- broken/closing/closed 后尚未准入的新调用均零网络、零 retry；正常 closing 前已经登记的调用遵循上一条 closing 规则。任何清理路径都必须使 in-flight 归零且 semaphore 许可恢复。

### 20.2 超时

<code>BaiduMapLimits</code> 是 strict/frozen/extra-forbid 配置模型，字段顺序、类型和默认值精确为：

~~~python
connect_timeout_s: float = 10.0
write_timeout_s: float = 10.0
pool_timeout_s: float = 10.0
initialize_timeout_s: float = 15.0
list_tools_timeout_s: float = 15.0
geocode_timeout_s: float = 15.0
reverse_geocode_timeout_s: float = 15.0
place_search_timeout_s: float = 20.0
place_details_timeout_s: float = 15.0
route_matrix_timeout_s: float = 30.0
directions_timeout_s: float = 30.0
weather_timeout_s: float = 15.0
map_launch_timeout_s: float = 20.0
close_timeout_s: float = 5.0
max_in_flight: int = 3
~~~

所有 timeout 必须为有限正数且不得大于各自默认值；<code>max_in_flight</code> 为 [1,3]。较小值只用于测试或更严格装配，不能放宽合同。<code>DEFAULT_BAIDU_MAP_LIMITS = BaiduMapLimits()</code>。默认值汇总：

| 阶段/能力 | 秒 |
|---|---:|
| connect、write、pool | 10 |
| initialize | 15 |
| tools/list | 15 |
| geocode | 15 |
| reverse geocode | 15 |
| place search | 20 |
| place details | 15 |
| route matrix | 30 |
| directions | 30 |
| weather | 15 |
| map launch | 20 |
| <code>aclose()</code> public wait | 5 |

每次工具调用的总 timeout 从进入 Gateway 方法开始计算，包含 semaphore 等待、SDK 调用、内容解析和 DTO 规范化。不得在不同层叠加形成比合同更长的隐形期限。

关闭只在首次 close时建立一个 <code>time.monotonic() + close_timeout_s</code> public deadline；所有 waiter共享该值。deadline只限制公开 <code>aclose()</code>等待，唯一 tracked close task可在 deadline后继续完成第 20.1 节规定的同 task context清理；该延迟清理不得为任何 waiter续发新的公开 timeout预算。

### 20.3 重试

启动连接、initialize、tools/list 作为一个整体最多尝试两次。只有瞬时连接错误或启动 timeout 可触发第二次；第一次的 session/transport 必须先尽力关闭，第二次必须使用全新资源。正常路径跨整个创建过程 tools/list 总计 1；若第一次已经发出 list 后发生上述可重试失败，第二个全新 session 到达发现阶段可再发 1 次，因此该路径总计 2，但任一 session/attempt 仍最多 1 次且绝不分页。

以下不得重试：

- 配置或鉴权；
- schema、工具缺失或协议错误；
- 任何 tools/call；
- 供应商限流；
- response/DTO 错误；
- 取消。

业务若决定再次查询，必须由拥有业务预算和 OperationId 的下游显式发起，不得由模块 06 隐式执行。

### 20.4 取消

取消必须在 semaphore 等待、SDK I/O、解析和关闭边界传播，不包装为技术异常，不吞掉。已经发送后被取消的调用视为结果不确定；Gateway 不自动重试，也不声称“未发生”。

模块 13 负责把取消映射为产品终态；CLI 总取消门保持 130 秒。若业务需要重试，必须遵循模块 04 的 artifact → budget → external call → put 顺序。

## 21. 并发与技术资源保护

每个 Gateway 实例共享一个容量为 3 的 async semaphore，覆盖所有 run、七项核心能力和地图调起。一个实际 tools/call 占一个许可，initialize 和 tools/list 不计入该值但受各自启动 timeout 保护。

要求：

- 许可只在调用实际结束、失败或取消清理完成后释放；
- 等待许可的时间计入 capability 总 timeout；
- 不得为不同 capability 创建独立 semaphore 绕过总上限；
- 不得用 sleep 证明并发；
- 公平性只依赖 async runtime 的等待顺序，不承诺业务优先级；
- 首版不实现 QPS/token bucket；
- 供应商 429 映射 typed rate-limit error，零自动 retry。

并发 3 是技术资源保护，不是模块 07～10 的业务调用预算。

## 22. 幂等、Artifact 与业务安全顺序

模块 06 的一次 typed 方法对应至多一次 tools/call。它不接收 OperationId，不查找 Artifact，不消费 ExecutionBudget，也不写 Artifact。

下游调用者必须遵守模块 04：

1. 按 OperationId 查找 Artifact；
2. 已存在则复用；
3. 不存在才消费业务预算；
4. 执行一次 adapter 调用；
5. 在其他 await 前写入规范化结果 Artifact。

被取消、断连或 timeout 的外部调用可能已被供应商处理。模块 06 只报告 typed 技术状态；下游不得在同一 OperationId 下盲重试。

只有规范化 DTO 可以交给下游进入 Artifact。Artifact canonical bytes、hash 和 1 MiB 持久化边界唯一使用模块 04 第 9 节算法：先按其类型感知规则规范化，再以 <code>sort_keys=True</code>、紧凑分隔符、UTF-8 编码；该键顺序与本模块模型字段声明展示顺序无关。模块 06 不得另造 canonical serializer，不计算 Artifact bytes/hash，也不直接写 Artifact。session、Gateway、MCP Tool、CallToolResult、原始 payload、Secret、URL 中的 AK 和底层异常永远不可持久化。

## 23. Offline、live 与 M2 资格

### 23.1 Offline

默认 unit/contract/adapter-contract 完全离线：

- 不读取真实 <code>.env</code>；
- 不访问 DNS 或网络；
- 使用可编程 fake MCP client/session/transport；
- 真实执行 initialize、tools/list、call result 解析、规范化、超时、取消、并发和关闭逻辑；
- 不得用 mock 直接返回最终 DTO 来掩盖 Gateway 解析；
- 显式 Mapping 测 settings；
- offline 容器使用 <code>--network none</code>。

fake 在 <code>tests/support/baidu_map_fake_mcp.py</code>，仅测试导入。生产包不得提供 fake。Gateway 私有构造器允许注入私有 client factory；公共 runtime 工厂不暴露任意 endpoint 或 transport。

### 23.2 七项真实 live 资格

live 是模块 06 首次验收和发布门，不能用 offline 替代。凭据缺失必须失败，不能 skip。

七项核心 probe 按顺序执行：

1. geocode A：address=<code>浙江省杭州市西湖区龙井路1号</code>、city=<code>杭州市</code>；geocode B：address=<code>浙江省杭州市西湖区北山街89号</code>、city=<code>杭州市</code>，得到两个 BD-09LL；
2. 用其中一个坐标 reverse geocode；
3. place search：query=<code>博物馆</code>、tag=<code>旅游景点</code>、CitySearchArea(region=<code>杭州市</code>)、result_limit=5，先验证供应商原始 results 数量不超过绝对上限 50，再按原顺序本地取前 5，得到非空 UID；
4. 用该 UID place details，并校验 UID 一致；
5. 用 A→B 依次以 walking、driving、riding 执行 route matrix；
6. 用 A→B 依次以 walking、driving、riding、transit，alternatives_limit=1 执行 directions；
7. 用 A 坐标执行 weather。

上述七个能力步骤合计 tools/call=13：geocode=2、reverse/search/details/weather 各 1、matrix=3、directions=4。请求常量冻结，动态响应不冻结。probe 校验 matrix 三 mode 同型、directions 三个非 transit mode 单层同型与 transit 独立双层合同，以及其他 capability 的结构和交叉一致性；不保存或报告具体坐标、返回地址、距离、耗时、天气、forecast 数量、UID 或 POI 数量。地图调起是独立可选 probe：无稳定 binding 时报告 <code>NOT_AVAILABLE</code>，不影响七项核心通过；有 binding 时使用 A→B、walking 的 route 请求且只验证脱敏 HTTPS 百度 URL。

live 必须使用独立授权、独立命令、独立容器和精确 Secret 注入；不得打印 compose 展开配置、AK、原始响应或完整 URL。动态结果不写入冻结设计；执行命令、依赖版本、manifest version、脱敏结果和资格状态进入实施/closeout 报告及状态文档。一次 live 通过不代表永久可靠。

以下变化使既有 live 资格失效并要求重跑：

- mcp SDK、endpoint 或认证合同；
- core tool name/schema/parser；
- 规范化 DTO 或错误映射；
- binding manifest；
- 七项核心集合。

### 23.3 M2

必须保留三个相互独立的证据：

1. offline 纵向 M2：真实模块 05/06，fake MCP，完成 interrupt/resume 后一次 typed 调用；
2. 七项核心真实 live qualification；
3. live 纵向 M2：模块 05 interrupt/resume 确认目的地后，恰好一次真实 geocode，并返回安全结构化结果。

M2 harness 的装配固定为：从 <code>app.requirement_subgraph</code> 包根使用真实 <code>create_demo_requirement_subgraph_runtime</code>；注入一个确定性 fake <code>RequirementDomainService</code>，不得构造 DeepSeek extractor、读取 <code>DEEPSEEK_API_KEY</code> 或导入模块 05 私有实现。使用真实公共 <code>start(state=..., turn=...)</code> 得到 confirmation pause，再以该结果的公开 pause_token/interrupt 构造公共 <code>RequirementResumeEnvelope</code> 和纯 accept confirmation，调用 <code>resume(run_id=..., envelope=...)</code> 得到 <code>RequirementSubgraphConfirmed</code>。目的地只能从 confirmed 结果中 <code>state.requirement_state.requirement.destination</code> 的公开路径读取，不得从输入常量旁路取得。

offline M2 注入真实模块 06 Gateway 的 fake MCP client boundary，然后以该目的地构造一次 <code>GeocodeRequest</code>；fake 的 <code>tools/call</code> 计数必须总计恰好 1 且唯一 capability 为 GEOCODING。live M2 复用同一模块 05 harness，但使用新建的真实模块 06 runtime，并在公开 adapter 外包装只计数不窥视内容的测试 decorator；从 confirmed 后到 runtime 关闭，geocode 恰好 1 次且其他六项和地图调起均为 0。两者均不调用下游规划、不写 Artifact、不标记 <code>demo_ready</code>，也不把 fake service 误报为模块 01/DeepSeek 资格证据。

## 24. 测试矩阵

| 类别 | 必测合同 |
|---|---|
| unit/models | strict、frozen、extra forbid、边界、有限数、tuple、字段顺序、JSON round-trip、fresh copy |
| unit/settings | 进程优先、固定 .env、显式 Mapping 隔离、占位符、遗留 URL fail-closed、repr redaction |
| unit/registry | 七项唯一绑定、可选缺失、未知忽略、unknown required、enum/type/oneOf 冲突、manifest version=1.7；reverse latitude/longitude property 存在且为 number-compatible、可不在顶层 required；reverse 根 anyOf 精确覆盖两个仅 required 分支、已批准坐标 property、互异非空集合和 latitude/longitude outbound 分支，其他组合/畸形全部失败 |
| unit/registry | binding 1.7 的 model property 存在且为 string-compatible；无 enum 的已资格化 schema 直接兼容且不存在 pending/reinforcement；显式 enum 缺值/类型冲突、model 缺失或未知 required 失败；正常 startup 总 list=1、tools/call=0 |
| unit/parsing | 第 16.4 节每条正向路径；reverse adcode 的 integer 与严格 ASCII 十进制 string 均规范化为 int，并拒绝 bool、空白、正负号、小数、指数、前导零、越界及其他整数路径字符串；Directions step 坐标只在该路径接受 number 或严格 ASCII 十进制 string，walking/driving/riding 只接受单层 steps、单数且非空 instruction；transit 只接受严格双层 steps 和单数 instruction string，先验证全部原始 step/原始 1..128 容量，再按外层后内层原序丢弃空白 instruction，覆盖首/中/尾多个空白、丢弃后顺序、全部空白失败、原始 >128 即使过滤后合规仍失败，以及复数 instructions/混合/更深/其他畸形失败；weather 原始 forecasts 只允许 0..7，全部验证通过后显式投影前五项，并覆盖第六/七项畸形也整体失败；search/matrix/directions 三个启用 no_result；geocode/reverse/details/weather 的 result=null 必须响应错误；structuredContent 优先、唯一全文 JSON 回退、isError→Protocol、status!=0→Response、block 拒绝、容量和单位 |
| unit/parsing | reverse binding 1.7 的 uid/name/addr/point.x/point.y/tag 封闭映射；point.y→latitude、point.x→longitude 且只接受有限 JSON number；旧 address/location alias 不启用；缺核心证据逐项丢弃，字段畸形整体失败；原始 0/20/21/大于 20 项全部验证后按原序投影前 20 个合格项，并以第 21 项之后畸形反例证明没有先切片 |
| unit/outbound | ak 唯一 query；GEOCODING/PLACE_SEARCH/PLACE_DETAILS/DIRECTIONS 精确发送 is_chinese_mainland，WEATHER 精确发送 is_china，REVERSE_GEOCODING/ROUTE_MATRIX 两者均不发送；matrix 1×N、N×1、N×M 的 tuple 原序、lat,lng 与 ASCII U+007C 精确编码；无表外字段 |
| adapter-contract | 七方法签名、全部公共 outcome 的模型/端口合同、当前 binding 可达分支、matrix 三 mode、directions 四 mode 的正常能力调用、错误优先级、零 supplier 泄漏；不把这些 mode 调用当作 startup qualification |
| session | 正常启动 initialize=1、tools/list=1、tools/call=0；获准重试只增加新的 initialize/tools/list，不执行资格化 tools/call；ClientRequest 包装的 send_request 路径、SDK cache/outputSchema/TextContent、完整状态机、close race/shield/timeout/异常优先级，以及 broken 时已登记未发送 waiter 零网络退出 |
| timeout/cancel | semaphore 等待计时、每能力期限、取消原样传播、call 零 retry |
| concurrency | 全能力共享最大 3、许可释放、跨 run 合并计数；一调用触发 broken、另一调用等待 semaphore 的确定性屏障；不用 sleep |
| security | Secret、URL query、请求、坐标、UID、payload、异常链不进入日志/repr/artifact；包装异常 cause/context 为 None 且 traceback/local 不可达底层对象；3xx 不跟随；name 或锁定包 pathname predicate 覆盖 root/client/raw message，应用路径反例不误杀；重叠 runtime guard 的 DEBUG/INFO/异常 canary 及条件恢复 |
| public API | 精确 __all__、包根导入、私有 MCP/Gateway 不导出、下游禁止子模块和 mcp import |
| upstream | 模块 04 公共 API 与回归；模块 05 无直接生产依赖 |
| live | 七项顺序 probe、可选地图调起、凭据缺失 fail、无 skip/xfail/rerun |
| M2 | offline 纵向、live 单次 geocode、非 demo_ready |
| static | compileall、类型边界、git diff、whitespace、pycache、文件白名单 |
| coverage | 模块 06 line、branch、combined 各自至少 95.00% |

coverage 公式使用 JSON totals：line=<code>100*covered_lines/num_statements</code>（num_statements=0 失败）；branch=<code>100*covered_branches/num_branches</code>（仅零分支时定义为 100）；combined=<code>100*(covered_lines+covered_branches)/(num_statements+num_branches)</code>（零分母失败）。阈值用未舍入整数比例判断，只有报告显示用 ROUND_HALF_UP 两位小数；配套 Vibe Coding 第 11.4 节冻结单进程 helper。

不得通过删除/弱化断言、skip、xfail、自动 rerun、宽泛 Any、访问私有状态替代公共行为、旧镜像、历史结果或修改冻结设计获得绿灯。

## 25. Docker 与网络边界

未来代码实施和正式验收全部在从当前源码新建的 Docker 镜像/容器中执行。offline 默认：

- <code>--network none</code>；
- read-only filesystem；
- <code>/tmp</code> tmpfs；
- <code>no-new-privileges</code>；
- <code>cap-drop ALL</code>；
- 不注入任何 Secret；
- 不复用旧镜像或容器。

live 只允许百度地图边界，使用独立授权与精确 AK 注入。不得启动本地 MCP Server、PostgreSQL 或其他项目资源。

配置迁移必须从共享 <code>x-agent-environment</code>、offline <code>test</code> 和非百度 <code>live-test</code> 删除 <code>BAIDU_MAPS_API_KEY</code> 与遗留 URL；不得让不需要地图的容器继承百度 Secret。只有实际装配地图能力的 <code>runtime</code> service 可在其自有 environment 显式加入 <code>BAIDU_MAPS_API_KEY=${BAIDU_MAPS_API_KEY:-}</code>，专用 <code>baidu-map-live-test</code> 则使用下述失败关闭 sentinel。迁移不得顺带改变现有 DeepSeek/PostgreSQL 的键名、默认值、service 或安全合同。

<code>compose.yaml</code> 必须有 profile=<code>live</code> 的专用 <code>baidu-map-live-test</code> service：从当前源码 build test target；image tag 使用不会阻断普通 Compose 解析的 <code>${MODULE06_LIVE_IMAGE_TAG:-not-authorized}</code>；BAIDU_MAPS_API_KEY 使用不会阻断解析、但会被 settings 拒绝的占位默认值 <code>${BAIDU_MAPS_API_KEY:-__BAIDU_MAPS_API_KEY_REQUIRED__}</code>。environment 只含 TZ、临时 HOME/XDG_CACHE_HOME 和该 key；read-only、<code>/tmp</code> tmpfs、no-new-privileges、cap-drop ALL。它不得继承通用 env/env_file，不得注入 DeepSeek/PostgreSQL Secret、遗留 URL 或任意 endpoint。网络 Gate 在运行前单独验证唯一 tag 已显式设置且非 sentinel；真实 key 缺失由容器内 settings 固定失败，普通 offline/下游 Compose 解析不需要这两个变量。完整 YAML 与命令由配套 Vibe Coding 第 9、10、12 节冻结。

MODULE-CLOSEOUT 必须检查：

- <code>pyproject.toml</code> 将锁定 <code>mcp==1.28.1</code> 声明为直接依赖是否必要；
- <code>requirements.lock</code> 不升级既有版本；
- <code>compose.yaml</code> 和 <code>.env.example</code> 删除遗留 URL；
- Dockerfile runtime import self-check 包含模块 01～06；
- README 和 IMPLEMENTATION_STATUS 只按真实实现、测试和 live 结果同步；
- 同步后重新构建并重跑模块 01～06 离线回归。

## 26. 版本与迁移

- <code>MAP_SCHEMA_VERSION = "1.0"</code>；
- <code>MAP_BINDING_MANIFEST_VERSION = "1.7"</code>；
- 未知 DTO 或 manifest version 失败关闭；
- 不提供隐式迁移、deprecated alias、双 parser 或供应商兼容 fallback；
- 新增可选能力或新 DTO 不改变既有 DTO 的 schema version；
- 删除/重命名字段、改变字段含义、坐标或错误语义时升级公共 major version；
- 供应商工具名、required schema 或 parser 变化时升级 binding manifest 并重跑 live；
- 只放宽已验证的供应商 optional 字段可作为私有 parser 兼容变更，但必须有合同测试；
- mcp SDK、endpoint、认证或七项核心集合改变时必须重新批准设计并 live 资格。

本模块没有历史业务数据迁移。配置迁移只包括删除 <code>BAIDU_MAPS_MCP_URL</code>、把百度 key 注入范围从共享环境收窄到实际地图 runtime/专用 live service，并在 <code>.env.example</code> 保留 key 占位符；项目不得自动编辑用户真实 <code>.env</code>。

## 27. 验收标准

模块 06 只有按顺序满足以下条件才可达到最终 COMPLETE；进入独立代码审查本身只要求配套 Vibe Coding 第 12 节 LIVE-QUALIFICATION 已通过：

- 公共 API、DTO、错误、Gateway、settings 和 runtime 按本文实现；
- 七项 core 在 offline adapter-contract 中全部覆盖；
- session、timeout、retry、cancel、concurrency 和 close 合同通过；
- 无供应商/MCP/Secret/raw payload 泄漏；
- 模块定向 line、branch、combined coverage 分别至少 95.00%；
- 模块 04 回归与模块 01～06 完整 offline 回归通过；
- 七项 live qualification 在独立授权下通过；
- M2 offline 与 live 单次 geocode 证据满足本文；
- 独立审查完成后，MODULE-CLOSEOUT 完成；
- 所有 P0/P1/P2 已修复；
- 用户另行授权后才可提交或合并。

地图调起 unavailable 不阻断核心验收；若实现并声称 available，则其合同与独立 live probe 必须通过。

## 28. 停止条件

实施遇到以下任一情况必须停止：

- 官方托管 endpoint、Streamable HTTP 或 AK 鉴权无法确认；
- 七项核心任一工具缺失、重名或 schema 不兼容；
- 需要猜测工具名、字段、单位、错误码或坐标；
- 需要自动回退 SSE/stdio/其他 endpoint；
- 需要新增或升级未批准依赖；
- 需要修改模块 04 公共合同或模块 01～05；
- 需要把 MCP/session/raw payload 暴露给业务层；
- 需要扩大模块 06 到业务预算、Artifact 或编排；
- 需要读取/打印真实 Secret，运行未经授权 live；
- 需要改变已确认 DTO、错误、坐标、并发、重试或能力范围；
- 出现未知 dirty/staged、白名单外文件或其他项目资源；
- 测试只能通过 skip、xfail、rerun、弱化断言或修改冻结设计。

环境、Docker daemon、镜像源、网络或供应商失败必须报告 BLOCKED，不能冒充代码 PASS。

## 29. 决策记录

| ID | 已确认主题 | 决定 | 被否决/延期 |
|---:|---|---|---|
| 01 | 模块所有权 | 模块 06 是 Component，唯一直接上游为 04；05 只参与 M2；不拥有业务/Graph/Artifact/预算 | 把 05 设为生产依赖或把业务编排放入 06 |
| 02 | 供应商与 transport | 只连接百度官方托管 MCP，只用 Streamable HTTP，无 fallback | 自建、第三方、SSE、stdio |
| 03 | Secret | BAIDU_MAPS_API_KEY；进程环境优先、根 .env fallback、SecretStr；与 DeepSeek 相同模式 | .env.example 保存真 key、offline 读真实环境 |
| 04 | endpoint 配置 | 完全删除 BAIDU_MAPS_MCP_URL；endpoint 为私有常量；遗留键 fail-closed | 可配置生产 URL、自动修改真实 .env |
| 05 | SDK 与角色 | 直接使用官方 mcp==1.28.1；私有 Gateway 实现 BaiduMapAdapter；MCP 是被调用工具 | langchain wrapper、让 MCP 充当 Agent |
| 06 | 能力范围 | 七项核心均为 release/live gate；地图调起可选；其余列明延期 | 原四项 gate、把延期能力塞入首版 |
| 07 | 失败语义 | 阻断能力只在被阻断流程调用时失败；reverse 只阻断当前位置；weather/map launch 降级 | 所有失败一律阻断或伪造结果 |
| 08 | 坐标 | 首版内部统一 BD-09LL，DTO 显式标注，禁止近似本地转换 | GCJ-02 内部基线、静默转换 |
| 09 | 公共能力面 | 七个稳定命名方法；可选能力独立 Protocol；私有 registry；无任意 call_tool | 公共工具名/字典/任意调用 |
| 10 | 工具发现 | 每启动 attempt/session 到达发现阶段后恰好一次无分页 tools/list；正常总计1，获准全新启动重试路径可总计2且每 session=1；核心唯一绑定；可选缺失不阻止 runtime ready 但能力标为 unavailable；未知忽略；严格 schema | 动态猜测、模糊绑定、部分核心 ready |
| 11 | session | 每 Application/CLI runtime 一个显式可复用 session，共享并发调用，显式关闭 | 每调用 session、global/import-time session、持久化 session |
| 12 | timeout | connect/write/pool=10；initialize/list=15；能力 15/20/30；aclose public wait=5且共享首次 absolute deadline；semaphore等待计入能力总期限 | 给晚到 close waiter重置期限、把 public wait误作强杀 context期限 |
| 13 | retry/cancel | 启动整体仅瞬时错误最多两次且新 session；所有 tools/call 零 retry；取消原样传播且结果不确定 | 自动 call retry、吞取消 |
| 14 | 技术并发 | 每 Gateway 所有能力合计最多 3 in-flight；不拥有业务预算 | 每能力各 3、在 06 内实现业务调用预算 |
| 15 | outcome/异常 | success/no_result/ambiguous 为业务结果；技术失败使用零敏感 typed exceptions | 把技术失败伪装 no_result/ambiguous |
| 16 | 响应边界 | isError 优先；structuredContent 优先；唯一完整 JSON 文本 fallback；核心拒绝非文本；地图 URL 严格清洗；raw 立即丢弃 | fence/自由文本提取、保存 raw/QR/Base64 |
| 17 | DTO/容量 | Pydantic v2 strict/frozen/extra-forbid、schema 1.0、tuple/finite/BD09LL/provenance；冻结全部容量 | 宽泛 dict/Any、静默截断、无版本 DTO |
| 18 | 资格证据 | offline fake 无真实 env/network；七项顺序 live；M2 offline 与 live 单次 geocode；缺 key 失败 | skip live、offline 冒充 live、demo_ready |
| 19 | 包与版本 | 包根唯一 facade、私有文件；schema 1.0、binding 1.7；未知版本失败；breaking 升 major；供应商/SDK变化重资格/重批准 | 隐式迁移、alias、双 parser、越层导入 |

## 2026-09-29 公交无路径限定映射（用户批准）

本节精确修订 §16.4/§18 及执行规范中 isError 一律 Protocol 的规则。仅 DirectionsRequest.mode=transit、CallToolResult.isError=true、structuredContent=None、唯一精确 TextContent 且正文完全等于 `API response error: no bus path` 时，直接映射 DirectionsNoResult；不伪造 status=0 响应，不把原始API的1001假装为MCP字段。其余错误仍按原合同处理，不做包含匹配、strip、正则猜测或模糊分类。匹配后的业务无结果不使会话 broken，不自动重试；同批其他请求正常执行。该例外由真实同路段公交/步行及原始 API 对照证实，见 [根因报告](VALIDATION.md)。

M09 已有 no_result 反馈和公共交通不可行时查询步行/网约车的 Prompt，不放宽路线证据或预算。地图恢复默认 max_in_flight=3（产品默认原本就是3，移除真实测试worker的1覆盖）。验收覆盖精确匹配、未知错误拒绝、非公交/混合信封拒绝、同会话并行及后续成功；从M09真实测试一次，通过即止，不自动追加。
