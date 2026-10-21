# 轻量错误定位（2026-09-29）

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本文件继续拥有普通脱敏错误定位日志；新增完整正文记录另由 [阶段预算与完整交互记录设计](09_itinerary_planning_loop.md) §7～8 拥有。这里“不增表/不存原文”属于当期轻量 logger 范围，不限制后来批准的专用诊断表；普通日志不得改成全文日志。 新增目标尚未实施，文档审查/批准状态见 [本次报告](VALIDATION.md)。

用户批准范围：快速定位错误，不建设诊断平台。依据[M02设计](02_llm_runtime_and_model_profiles.md)、[M02执行规范](02_llm_runtime_and_model_profiles_vibe_coding.md)、[M09设计](09_itinerary_planning_loop.md)、[M09执行规范](09_itinerary_planning_loop_vibe_coding.md)；本文件为用户“实现这个功能”授权的增量。复用logging、运行编号和真实测试输出目录，不增表、不迁移、不保存回放材料、不新增API调用。

## 行为

- M02在工具DTO失败转换为tool_arguments_invalid之前，记录已注册工具名、调用计数、白名单字段路径、错误类型、数量/数值限制及源码位置。未知字段名统一unknown_field；不记录参数值、异常消息、ctx原文。
- 供应商AIMessage非法调用及原始mapping解析失败记录调用序号、参数UTF-8字节数、JSON字符位置/行列、固定原因码及结束原因；不保存参数正文、尾部内容或推理。
- M09普通提交、最后一次提交、补研业务拒绝记录reason、field、调用计数、terminal_only和异常链源码位置。泛化结构/引用错误在转换前另记位置；已有通用原因不能提供精确业务字段时，以源码文件/函数/行号定位，不假称所有字段已穷尽标注。每日路线的已知错误路径包含日/路线索引。未完成终态同时记录stop reason。
- M09执行循环通过ContextVar关联run_id，结束后还原，避免并发运行串号。该字段只用于日志，不改请求、结果、预算或持久化合同。
- 统一使用app.error_location logger。真实测试ObservedProvider自动启用该通道，克服此前logging.disable(CRITICAL)吞掉所有诊断的问题；输出LIVE_EVENT error_location，并追加到测试目录error-locations.jsonl。默认目录/evidence，可用LIVE_DIAGNOSTIC_DIR指定已存在的输出目录。未挂载目录时仍有控制台事件，由现有runner保存。测试阶段与现有RUN_ID作为无M09上下文时的关联信息。
- 单条参数诊断最多20个字段错误；只记录数字计数/边界、字段白名单和代码位置，不记录密钥、认证头、原始输入/供应商响应、思维过程或局部变量。日志设施异常不改变原业务失败结论。

排查只需打开error-locations.jsonl：先按run_id找最后的错误，再看tool、errors[].path或field、reason，以及location中的file/line/function。计数错误示例：candidate_handles，too_long，actual_length=20，max_length=16。日期或跨字段自定义校验可按validator源码位置定位，不输出异常原文。

## 白名单与验收

源码：app/llm_runtime/diagnostics.py、runtime.py、provider.py；app/itinerary_planning/service.py。测试：tests/unit/llm_runtime/test_error_locations.py、tests/unit/itinerary_planning/test_service_initial.py、tests/live/travel_pipeline/test_live_requirements_to_result.py。文档：本文件、M02/M09设计/执行规范、实施状态和延期台账。保留当前分支此前公交展示及修复改动，不把它们重复归入本轮。

验证矩阵：真实运行时错误映射与数量边界、未知字段脱敏、实际SDK非法JSON位置、普通/最终提交拒绝、run_id关联、全局日志关闭后测试通道仍可写入、重复启用不重复记录、文件追加可重新读取、日志写入异常不覆盖原错误，以及M02/M09/M12/M13相关回归。不调用真实模型或地图；不修改旧steps3～6证据，也无法恢复它们已丢失的错误原因。

分支feature/transit-step-display；前后HEAD均d5e45a43729d2c49fc32e7ad1d0e549d13039daf。无新增迁移、数据库改动或回放平台。未stage、commit、merge、push、PR或部署。仓库外旧m13-live-evidence/保留；本轮无新增真实调用证据。

最终验收：Docker断网只读运行M02/M09/M12/M13全部unit/contract，648 passed、1条既有畸形DTO序列化warning，26.02秒。镜像sha256:20c9908a2c6285f4867caa43e93afb112f695cf7b040502d57aea0b4bfc29bd9。逐项核对上述采集点与字段白名单；普通和terminal_only提交均有运行编号、原因与源码位置；文件追加、启用恢复及日志故障均有覆盖。git diff --check通过，staged为空。最终状态IMPLEMENTED_OFFLINE_VALIDATED；真实API调用0，未重跑三天规划，既有M13-LIVE-002业务故障不因此关闭。

2026-09-29五天测试补充：发现_validate_batch抛出的ToolBatchError此前未覆盖。现已在通用control failure转换前记录planning_batch_rejected、白名单工具名/调用数量/调用序号/终结标志及源码位置。无规则或合同改变，新增重复证据批次回归；M09 unit/contract303 passed、1条既有warning。本次历史批次具体细节无法恢复，补丁未追加真实调用。此前“错误出口已覆盖”不应解释为所有可能异常分支已经穷尽验证。
