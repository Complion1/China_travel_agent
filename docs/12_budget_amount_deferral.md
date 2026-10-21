# 预算金额延期与三天测试修订

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

用户于 2026-09-26 明确要求“预算金额这个功能暂时延期……测试设计也做相应修改”。此授权覆盖本页所列 M01/M03/M12 合同的必要同步；这是范围延期，不代表金额解析已修复或预算评估通过。

后续 Prompt 维护：用户又批准 LIVE-013 新版本 `requirement-extraction-v3-explicit-negatives`，继承本页全部预算延期语义，v2 资源保持只读。当前 v3 hash、真实资格和轮次以 [统一问题台账](VALIDATION.md) 及 M03 两份文档的顶部修订为准；下文 v2 标识是延期首次实施的历史快照。

## 当前阶段与合同

- 设计依据：M01 `01_requirement_and_dialogue.md` §6.5、§7.6、§8.3、§10–11；M03 `03_requirement_extraction_adapter.md` 的 Prompt/版本合同；M12 `12_result_formatting.md` §6–8。执行依据为各自同名 `_vibe_coding.md` 及 `12_live_pipeline_fixes.md`。本修订优先于这些文档中仍供历史参考的预算金额要求。
- 分支 `feature/module-01-12-live-test-fixes`；开始 HEAD `d0b92de86e82190e14ca081bbe90ea7a514c6547`。当前真实调用 4/20；构建、迁移、自检和断网回归不计轮次。
- 阶段 A：文档/测试设计与 M01/M03 入口调整一起完成；阶段 B：M12 空金额兼容及结果回归一起完成；统一 Docker 离线验证后，专属空 PG 迁移单独作为门槛，再开始第 5 轮。
- 前置条件：保留第 1–4 轮历史事实及旧 Prompt 资源；无正在运行的真实 worker；只在本仓库和专属 Docker 资源内操作。
- 允许操作：下列白名单内局部修改、新 Prompt 资源及 hash、离线测试；继续已授权的最多 20 轮真实链。无 commit、merge、push、PR、部署、订房和支付授权。

## 延期语义

1. 本期自然语言和手填业务入口不采集预算金额/约数，不规范化或校验它们，不追问缺失金额，不把它们作为需求确认门槛。金额不会填入 0、5000 或其他默认值。
2. 保留既有 DTO 路径、类型和低层兼容 helper，避免无关序列化迁移。M01 在本期入口忽略延期金额字段及对应 evidence；其余字段的严格 schema、原文证据、一次 repair 和完整确认要求不变。旧状态不自动擦除，新三天测试以空状态开始。
3. M03 使用 `requirement-extraction-v2-budget-deferred`，新增 `extract_v2_budget_deferred.txt`、`repair_v2_budget_deferred.txt`，要求两个预算属性始终 null、无金额 evidence，也不转移到 free_text_notes。v1 原文/hash 不修改。事件可读取旧版本并记录当前版本。
4. 手填模板不再展示/要求总预算键；解析兼容旧模板的该键但忽略其内容，不产生金额 Patch。其他键的顺序、重复、类型和缺失门槛保留。
5. M12 `ResultRequirement.budget_total_cny` 扩为有限正数或 null；保留字段名及既有 artifact 版本。新工件空金额照常生成、存储和读取，新攻略不显示预算金额/约数，不宣称费用已核验或满足预算。既有费用“未核验”提示保留。为兼容旧状态确认摘要 digest 和旧 M12 工件模板，已经存储的非空金额仍按原文案只读展示；这不恢复当前业务入口的金额采集，也不改写历史工件。
6. 这是当前开发基线的兼容性放宽，不修改既有 DB 表、迁移和日常库；不承诺旧程序能读取新 null 工件。真实测试仅使用新建专属空库。M04/M08/M09 的 API 调用额度、迭代额度、路线/备选/完整三日门槛全部保留。

## 精确文件白名单

- M01：`app/requirements/service.py`、`merge.py`、`manual_template.py`、`dialogue.py`；`normalization.py` 只撤销本批尚未交付的预算解析扩展，保留日期时间修复。
- M03：`app/requirement_extraction/prompt_loader.py`、`events.py`、`prompts/extract_v2_budget_deferred.txt`、`prompts/repair_v2_budget_deferred.txt`。
- M12：`app/result_formatting/artifacts.py`、`renderer.py`。
- 测试：`tests/unit/requirements/test_requirement_service.py`、`test_manual_template.py`、`test_dialogue_renderer.py`、`test_natural_language_normalization.py`、`test_live_diagnostics.py`；`tests/unit/requirement_extraction/test_prompt_loader.py`；`tests/unit/result_formatting/test_renderer.py`、`test_service.py`、`fakes.py`；`tests/live/requirement_extraction/test_deepseek_requirement_extraction_live.py`；`tests/live/travel_pipeline/test_live_requirements_to_result.py`。
- 文档：M01/M03/M12 的六份设计/执行规范、本页及 `12_live_pipeline_fixes.md`。脚本：`scripts/verify_live_requirements_to_result.ps1` 同步用户批准后的 M12 文档哈希，并复用既有隔离 PG 生命周期增加 `Regression` 模式：内部网络、M10/M11 既有迁移、全部 unit/contract，无供应商密钥和外网连接，不计真实轮次。

## 修订后的真实需求与验收

杭州，2 位成人；2026-10-20 09:00 到杭州东站，10-22 19:00 离开；三天两晚、一间双床房、西湖附近，首个合格酒店按既有规则确认；自然/人文、标准节奏，无儿童老人、行动/饮食/交通/作息限制和指定必去/避开地点。移除原文整段 5000 元预算，不添加预算替代文本。其余原文不变。

断网测试必须证明：无金额能通过真实 M01 完整确认；明确金额输入也不会启用延期能力；活跃字段 evidence 仍严格；手填新旧格式都不产生金额；M03 新 Prompt/hash/事件有效且 v1 保持原样；M12 null 金额可 prepare/build/load，旧正数工件仍按原模板读取；费用未核验和其他完整性门槛保留。之后继续真实酒店/研究/三天 Completed/三日块索引/PG 重建读取，不用离线通过替代真实通过。

停止条件：无效资源身份、真实额度用尽、真实链通过、必须扩大到无关模块或改动额外已确认设计；每次真实失败先定位，不跳过非预算门槛。报告须列分支、前后 HEAD、文件、迁移、测试、历史失败、异常文件、清理、commit/push/PR/deploy 和最终状态。

## 当前基线标识

新需求 SHA256：88590f2b01da1b116abddcf735b4549a3ede582875d519d6e192b5dbed006079。M03 新 extract hash：82c50823cca0cf9dacc3d954527bc4fa11d02e8a69bc3ebe0d21d707b66175ad；repair hash：6911a585ea0c24f71d0c6b04d3a564e1b73230016a165742a1c415962b10bb0f。

M12 设计原始文件 SHA256：6713D6EB83213381DCB38D10C9F1E5B31B9B1F7FE06A8BA7B4F04F68330C6C1B；执行规范：778D607E21B0200199CFDDC8FFABBE7B3BCC182AAFD173A0B282B23A16F6B5B7。脚本同步检查此用户批准后的基线，旧哈希见历史修复记录。

## 验证记录

- 延期入口、M03、M05、M12 定向断网回归：688 passed、7 warnings，退出 0；随后补旧摘要/旧结果渲染兼容与新 Prompt 中时间约数规则。
- 初次全部 unit/contract 在无数据库配置的容器中得到 2261 passed、7 warnings、1 setup error；唯一错误是 M11 合同测试缺少 PostgreSQL 配置，不能记为全部通过。独立 worker 自检首次直接路径调用漏 PYTHONPATH，补与正式脚本一致的 PYTHONPATH 后通过，API 0 次。
- 正式 `-Mode Regression -RunTag repair-0926-budget-reg`：新建专属 PG、M10/M11 既有迁移通过，全部 unit/contract 为 **2262 passed, 7 warnings in 56.00s**；退出 0，source_changes=0、cleanup_failures=0、供应商 API 0 次。镜像 ID `sha256:8129f07835e45611f8245e83599504a95b5b3d59f7f67cb1c6b242c7543384ae`。
- 逐条审查：M01 新输入不产生预算 Patch，未放宽活跃 evidence；M03 v1 未改、v2 双 Prompt/hash/事件一致；M12 nullable 字段无默认补值，三天 null 工件公共 prepare/build/load 回归通过，旧正数文案保持兼容，费用仍未核验；API 配额控制未改。真实业务与 PG 重启仍待第 5 轮实际到达后报告。
