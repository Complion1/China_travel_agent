# 部署说明

## 环境与配置

使用 Docker Engine 或 Docker Desktop，以及 Docker Compose。公网覆盖文件使用 `!override`，需要支持该标记的 Compose 版本。应用采用 Python 3.12，数据库采用 PostgreSQL 16。

自行取得 DeepSeek 与百度地图 API 凭据，准备至少 16 个字符的数据库密码，为每个独立部署保留自己的暂停密钥。预留应用与数据库运行内存，以及超过 5 GiB 的数据卷可用空间。镜像与依赖下载需要联网，离线部署可以提前构建和传输镜像。

Dockerfile 和 Compose 保留现有镜像加速地址。需要采用自己的镜像源时，构建可传入 `PYTHON_IMAGE`，数据库可设置 `M14_POSTGRES_IMAGE`，不需要修改应用代码。

以下首次迁移只适用于新的空数据库。已有数据的部署需要先备份数据库与原密钥，并检查迁移状态。

## 本机首次启动：PowerShell 7

在项目根目录创建配置，已有文件时停止并复用原配置：

```powershell
New-Item -ItemType Directory -Path .m14-local/keys -Force | Out-Null
if (Test-Path -LiteralPath .m14-local/config.env) { throw '配置已存在，请复用原配置' }
Copy-Item -LiteralPath .env.example -Destination .m14-local/config.env
```

用编辑器填写配置中的 `DEEPSEEK_API_KEY`、`BAIDU_MAPS_API_KEY` 和 `M14_LOCAL_PG_PASSWORD`。数据库密码至少 16 个字符，不能使用示例值。`WEB_PUBLIC_ORIGIN` 保持为 `http://127.0.0.1:8080`。

构建镜像，再生成新的暂停密钥。密钥生成命令不会联网，且拒绝覆盖已有文件：

```powershell
docker build --target runtime --tag china-travel-agent:m14-local .
$demoKeysDirectory = (Resolve-Path -LiteralPath .m14-local/keys).Path
docker run --rm --network none --mount "type=bind,source=$demoKeysDirectory,target=/keys" china-travel-agent:m14-local python -m app.web_application.keygen --output /keys/pause-key.json
```

独立备份原密钥，限制配置、密钥及备份的读取权限。以后启动复用同一密钥。

先启动本机专用数据库，再依次执行 M10、M11、M14、M24 四个模块的迁移。当前版本新增前端过程记录结构，不能省略最后一个模块：

```powershell
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local up --detach --wait m14-postgres
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m alembic -c app/artifact_persistence/alembic.ini upgrade head
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.durable_execution.migrate
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.web_application.migrate
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.web_application.migrate --check
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.frontend_experience.migrate
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local run --rm --no-deps m14-web python -m app.frontend_experience.migrate --check
docker compose --env-file .m14-local/config.env --project-name cta-m14-local --profile m14-local up --detach --wait m14-web
```

浏览器访问 `http://127.0.0.1:8080`。默认只绑定本机地址。

## 公网首次启动：Linux

使用单独的部署目录和 Compose 项目，以下示例项目名为 `cta-cloud-demo`。当前应用接受规范的 HTTP IP 地址；请把配置中的地址改成自己的 IP 与端口。域名和 HTTPS 需要后续适配。

```bash
mkdir -p .m14-cloud/keys
chmod 700 .m14-cloud
test ! -e .m14-cloud/config.env && cp .env.example .m14-cloud/config.env
chmod 600 .m14-cloud/config.env
docker build --target runtime --tag china-travel-agent:m14-local .
```

用编辑器自行填写 `.m14-cloud/config.env` 的三个凭据字段，把 `WEB_PUBLIC_ORIGIN` 改为实际访问地址，把 `M14_KEY_PATH` 改为 `./.m14-cloud/keys/pause-key.json`。已有配置时复用原文件。

首次生成密钥前，允许应用容器的用户编号 `10001` 访问新的密钥目录：

```bash
sudo chown 10001:10001 .m14-cloud/keys
sudo chmod 700 .m14-cloud/keys
docker run --rm --network none --mount "type=bind,source=$(pwd)/.m14-cloud/keys,target=/keys" china-travel-agent:m14-local python -m app.web_application.keygen --output /keys/pause-key.json
```

把原密钥备份到独立位置后，再进入首次迁移。定义一个固定使用本部署配置的命令：

```bash
cloud_compose() {
  docker compose --file compose.yaml --file compose.cloud.yaml \
    --env-file .m14-cloud/config.env --project-name cta-cloud-demo \
    --profile m14-local "$@"
}
cloud_compose up --detach --wait m14-postgres
cloud_compose run --rm --no-deps m14-web python -m alembic -c app/artifact_persistence/alembic.ini upgrade head
cloud_compose run --rm --no-deps m14-web python -m app.durable_execution.migrate
cloud_compose run --rm --no-deps m14-web python -m app.web_application.migrate
cloud_compose run --rm --no-deps m14-web python -m app.web_application.migrate --check
cloud_compose run --rm --no-deps m14-web python -m app.frontend_experience.migrate
cloud_compose run --rm --no-deps m14-web python -m app.frontend_experience.migrate --check
cloud_compose up --detach --wait m14-web
```

确认页面正常后，按实际需要在服务器防火墙中放通 TCP 8080。公网覆盖文件绑定 `8080:8080`。访客任务会消耗部署者的模型与地图额度，默认配额见 README。

## 更新、停止与备份

前端文件包含在应用镜像中。修改后先完成本机检查，再构建新镜像，在任务停止且运行统计保存后更新应用容器。继续使用原 Compose 项目名、配置、数据库卷和暂停密钥；只有数据库版本发生变化时，才执行相应迁移。

从不含 `frontend_experience` 结构的旧版本升级时，应先备份数据库和原密钥，再对同一目标数据库显式执行 `python -m app.frontend_experience.migrate` 及其 `--check`。应用启动不会代替这一步。仓库更新与线上部署分别进行，GitHub 推送不会更新现有云服务器。

日常停止使用本机或云端对应的 Compose 命令。云端示例：

```bash
cloud_compose stop m14-web m14-postgres
```

数据库与原暂停密钥应配套备份到独立位置，配置单独按私密文件保管。复制源码或镜像不能恢复原任务数据。停止命令保留数据卷；需要保留数据时不要使用删除卷的命令。
