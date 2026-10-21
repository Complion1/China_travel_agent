# 开发与测试

## 选择验证范围

生产入口为 `python -m app.web_application`。开发采用 Python 3.12，Docker 从 `requirements.lock` 安装固定依赖。默认 pytest 收集单元与契约测试，部分持久化契约需要 PostgreSQL，不能把无数据库环境的全量运行当作有效验收。

按修改范围选择测试。页面样式和说明修改可以检查资源、页面行为和相关 HTTP 协议；业务协议或迁移修改需要增加对应单元、契约和隔离数据库集成测试。真实供应商调用与云端发布单独安排。

当前模块验证摘要见[验证说明](VALIDATION.md)，详细公共合同和阶段测试矩阵见[设计文档目录](README.md)。程序回归与攻略质量评价分别记录，测试通过不能代替用户体验判断。

## 网页应用离线检查

以下示例不读取私有配置，关闭容器网络，临时写入只放在 `/tmp`：

```bash
docker build --target test --tag china-travel-agent:public-test .
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  china-travel-agent:public-test python -m pytest -q -p no:cacheprovider \
  tests/unit/web_application tests/contract/web_application \
  tests/unit/demo_workflow/test_interaction_export.py
```

这组测试检查网页配置、身份与暂停凭证保护、任务操作、供应商装配协议和诊断导出。使用模拟供应商和业务服务，不会验证真实 API 可用性。

前端公开过程记录还包括 `tests/unit/frontend_experience`、`tests/contract/frontend_experience` 和 `tests/integration/frontend_experience`。集成测试应使用独立的真实 PostgreSQL，先应用已有迁移，再应用前端过程记录迁移；不得连接在线演示数据库。

## 测试目录

| 目录 | 内容与前置条件 |
| --- | --- |
| `tests/unit` | 领域逻辑和局部模块测试，按修改模块选择 |
| `tests/contract` | 模块间协议和公开接口检查，部分目录需要隔离 PostgreSQL |
| `tests/integration` | 持久化、迁移和跨连接读取检查，需要专门的测试数据库 |
| `tests/browser/web_application` | Chromium 页面测试，需要显式启动网页测试服务 |
| `tests/live` | 真实供应商调用，需要明确的凭据、额度和执行范围 |
| `tests/support` | 合成数据、模拟服务、覆盖率和隔离数据库辅助工具 |

浏览器测试采用 Dockerfile 的 `browser-test` 目标和 Playwright。它读取 `MODULE14_EVIDENCE_DIR/browser-origin.json` 中明确标记为离线的测试服务地址；只构建浏览器镜像不能自动创建该服务。测试服务通过 `tests/support` 中的辅助入口启动，不能接入线上访客数据库。

## 敏感信息与提交检查

提交前核对文件清单与差异，确保只包括本次修改需要的代码、资源、测试和说明。`.gitignore` 与 `.dockerignore` 排除私有配置、暂停密钥、日志、导出文件、缓存和开发工具目录。

密钥扫描应覆盖待发布文件及将推送的全部 Git 历史。告警需要检查来源，不能直接忽略测试目录或整个业务模块。数据库约束校验包含固定的 SHA-256 摘要，可能触发通用密钥规则；需要通过代码用途证明后单独记录。

诊断导出可能含用户需求、任务标识与供应商正文。请保存在 `diagnostics/` 或 `exports/` 等忽略目录，不提交实际运行记录。公开样例位于 `app/web_application/samples`，与访客任务数据分开保存。当前完整样例来自已脱敏的真实任务结果，保留生成日期和未核验信息的说明，不代表实时出行安排。
