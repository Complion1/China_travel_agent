# 设计文档目录

这里提供当前源码对应的总体设计、各模块设计与配套执行规范。设计说明业务目的、公共合同和边界；执行规范说明实施顺序、文件范围和验证要求。文档同时保留历史版本兼容和已知限制，不能将历史阶段的验证记录视为所有能力均已完成。

设计文件中的批准、分支和阶段状态属于写作时的开发快照，当前公开版本的交付与验证状态以 README 和验证说明为准。内部阶段验收脚本未随设计资料发布，公开测试入口见测试说明。

首次阅读可先看[架构说明](ARCHITECTURE.md)、[总体设计](00_agent_master_design.md)和[实施路线图](IMPLEMENTATION_ROADMAP.md)。部署和测试分别见[部署说明](DEPLOYMENT.md)、[测试说明](TESTING.md)，当前验证范围见[公开验证说明](VALIDATION.md)。

## 模块设计与执行规范

| 模块 | 设计 | 执行规范 |
| --- | --- | --- |
| M01 需求与对话 | [设计](01_requirement_and_dialogue.md) | [执行规范](01_requirement_and_dialogue_vibe_coding.md) |
| M02 模型运行时与配置 | [设计](02_llm_runtime_and_model_profiles.md) | [执行规范](02_llm_runtime_and_model_profiles_vibe_coding.md) |
| M03 需求结构化提取 | [设计](03_requirement_extraction_adapter.md) | [执行规范](03_requirement_extraction_adapter_vibe_coding.md) |
| M04 工作流状态与产物 | [设计](04_workflow_state_and_artifacts.md) | [执行规范](04_workflow_state_and_artifacts_vibe_coding.md) |
| M05 需求确认子图 | [设计](05_requirement_subgraph.md) | [执行规范](05_requirement_subgraph_vibe_coding.md) |
| M06 百度地图与 MCP 适配 | [设计](06_baidu_map_adapter_and_mcp_gateway.md) | [执行规范](06_baidu_map_adapter_and_mcp_gateway_vibe_coding.md) |
| M07 酒店候选与人工选择 | [设计](07_hotel_candidate_selection.md) | [执行规范](07_hotel_candidate_selection_vibe_coding.md) |
| M08 候选研究循环 | [设计](08_candidate_research_loop.md) | [执行规范](08_candidate_research_loop_vibe_coding.md) |
| M09 行程规划循环 | [设计](09_itinerary_planning_loop.md) | [执行规范](09_itinerary_planning_loop_vibe_coding.md) |
| M10 业务产物持久化 | [设计](10_artifact_persistence.md) | [执行规范](10_artifact_persistence_vibe_coding.md) |
| M11 执行持久化与恢复 | [设计](11_durable_execution_and_recovery.md) | [执行规范](11_durable_execution_and_recovery_vibe_coding.md) |
| M12 攻略结果整理 | [设计](12_result_formatting.md) | [执行规范](12_result_formatting_vibe_coding.md) |
| M13 业务工作流编排 | [设计](13_demo_workflow.md) | [执行规范](13_demo_workflow_vibe_coding.md) |
| M14 网页应用 | [设计](14_web_application.md) | [执行规范](14_web_application_vibe_coding.md) |
| M15 云端部署与演示交付 | [设计](15_demo_release_gate.md) | [执行规范](15_demo_release_gate_vibe_coding.md) |
| M24 前端体验与业务过程展示 | [设计](24_frontend_experience.md) | [执行规范](24_frontend_experience_vibe_coding.md) |

## 配套资料

- [共享实施规则](SHARED_IMPLEMENTATION_RULES.md)
- [预算金额功能边界](12_budget_amount_deferral.md)
- [错误定位日志设计](error_location_logging.md)
- [第三方代码来源与许可](../THIRD_PARTY_NOTICES.md)

个人路径、实例标识和具体运维环境已改为通用示例。历史验收与私有部署记录由维护者保管，相关链接在公开资料中汇总至验证说明。示例目录、实例编号和运行标识需替换成自己的环境值。
