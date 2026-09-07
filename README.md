# InvenTree + 钉钉库存预警

基于 [InvenTree](https://github.com/inventree/InvenTree) 的开箱即用库存管理系统，**集成钉钉群机器人**实现：
<img width="1914" height="832" alt="image" src="https://github.com/user-attachments/assets/e3b0cb01-cf64-428f-81b7-e86a47b6cb9b" />
<div align="center">
<img width="315" height="425" alt="7c692b4501e95a6ec9ad1010a8848e81" src="https://github.com/user-attachments/assets/c59a4136-5a14-438b-ae59-0495c67c9a5e" />
</div>


- **实时事件推送** —— 库存变动 / 采购订单 / 销售订单 / 生产工单状态变更时自动告警
- **定时库存巡检** —— 扫描所有低于最低阈值的部件，批量推送告警（同一部件 24 小时内只告警一次）
- **通用部署** —— 换任何电脑、放任何目录（含中文/空格路径）都能直接跑，无需手动 `docker cp`

---

## 目录

- [架构概览](#架构概览)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [详细部署步骤](#详细部署步骤)
- [项目结构](#项目结构)
- [钉钉预警使用](#钉钉预警使用)
- [配置文件 .env 说明](#配置文件-env-说明)
- [常用运维命令](#常用运维命令)
- [常见问题](#常见问题)
- [换电脑迁移](#换电脑迁移)
- [License](#license)

---

## 架构概览

```
                 +-----------------------+
   浏览器 --->  |  Caddy (inventree-proxy)  |  :80 / :443
                 +-----------+-----------+
                             |
                             v
                 +-----------------------+
                 |  inventree-server     |  gunicorn :8000
                 |  (InvenTree Web)      |  + 钉钉插件 (bind mount)
                 +-----------+-----------+
                             |
            +----------------+----------------+
            |                                 |
            v                                 v
+-----------------------+          +-----------------------+
|  inventree-db         |          |  inventree-cache     |
|  PostgreSQL 17        |          |  Redis 7             |
+-----------------------+          +-----------------------+

   独立服务:
     - inventree-worker   (django-q 后台任务)
     - init-csrf          (一次性自动注入 CSRF 配置)
```

**通用化设计**：

| 项 | 方式 | 说明 |
|----|------|------|
| 数据库 / InvenTree 数据 | named volume | 不污染项目目录，换电脑数据不跟走 |
| `./scripts` / `./plugins` | **bind mount (相对路径)** | 代码自动注入容器，**无需 `docker cp`** |
| CSRF / ALLOWED_HOSTS | `init-csrf` 服务自动注入 | 任何 IP / 任何电脑都不会触发 403 |
| 反向代理 | Caddy 纯 `reverse_proxy` | Django whitenoise 直接 serve 静态资源 |

---

## 环境要求

| 项 | 要求 |
|----|------|
| 操作系统 | Windows 10/11 64位 / Linux / macOS（需 Docker） |
| Docker | Docker Desktop 4.x（Windows）或 Docker Engine 20+（Linux） |
| **Docker 内存** | **≥ 4GB**（低于此值数据库会频繁超时，强烈建议 6GB+） |
| 磁盘 | 至少 5GB（镜像 + 数据卷） |
| 端口 | 80 / 443 未被占用（被占用改 `.env` 的 `INVENTREE_HTTP_PORT` / `INVENTREE_HTTPS_PORT`） |

---
## 快速开始

下载所有文件到文件夹，或下载inventree.zip并解压，在文件夹中打开终端
直接按照详细部署步骤命令安装，或按照安装.txt命令进行安装

----------------------------------------------------------------------

## 详细部署步骤

完整命令清单见 安装.txt，要点如下：

| Step | 操作 | 命令 |
|:-----|:-----|:-----|
| 0 | 改 .env 4 项 | 见配置文件说明 |
| 1 | 清理旧环境 | `docker compose down -v` （会删除存在的所有容器和卷）|
| 2 | 启动 db + cache | `docker compose up -d inventree-db inventree-cache` |
| 3 | 数据库迁移 | `docker compose run --rm inventree-server bash -lc "cd /home/inventree/src/backend/InvenTree && python3 manage.py migrate --run-syncdb --traceback"` |
| 4 | 收集静态 | `docker compose run --rm inventree-server bash -lc "cd /home/inventree/src/backend/InvenTree && python3 manage.py remove_stale_contenttypes --include-stale-apps --no-input 2>/dev/null; python3 manage.py collectstatic --noinput"` |
| 5 | 创建管理员 | `docker compose run --rm inventree-server bash -lc "cd /home/inventree/src/backend/InvenTree && DJANGO_SUPERUSER_PASSWORD=$INVENTREE_ADMIN_PASSWORD python3 manage.py createsuperuser --noinput --username=$INVENTREE_ADMIN_USER --email=$INVENTREE_ADMIN_EMAIL"` |
| 6 | 启动 server<br>清一下 __pycache__ 避免旧代码缓存 |命令1 `docker compose up -d inventree-server`<br>命令2`docker exec inventree-server rm -rf /home/inventree/data/plugins/inventree_dingtalk/__pycache__` |
| 7 | 启动全部 | `docker compose restart inventree-server inventree-worker` |
| 8 | 确保后台5个容器都启动后验证是否成功 | 浏览器访问： http://localhost |
| 9 | 不能访问则重启后台容器 |命令1 `docker compose stop`<br>命令2`docker compose up -d` |

重要：不要跑 docker compose run --rm inventree-server invoke update！

InvenTree stable 镜像内置 Python 3.14，invoke 库在 3.14 下有 fcntl.ioctl buffer overflow bug，会报 SystemError: buffer overflow。本项目用 manage.py 直接迁移已绕过此问题。

----------------------------------------------------------------------

## 项目结构

```
inventree/
├── .env                      # ★ 部署前改 4 项（必改）
├── .env.example              # .env 模板
├── .gitignore
├── docker-compose.yml        # 编排 5 个容器
├── Caddyfile                 # 反向代理配置
├── README.md                 # 本文件
├── 安装.txt                  # 详细命令清单
├── 配置说明.txt              # 详细排障/维护说明
│
├── plugins/                  # 钉钉插件（bind mount 到容器）
│   └── inventree_dingtalk/
│       ├── __init__.py       # 插件入口
│       └── InvenTreeDingTalk.py  # 事件监听 → 推钉钉
│
└── scripts/                  # 定时扫描脚本（bind mount 到容器）
    └── check_and_notify.py   # 批量检查库存 → 推钉钉
```

## 钉钉预警使用

---

### 方式 A：插件实时推送（推荐，开箱即用）

插件监听 InvenTree 事件并实时推送钉钉：

- **库存变动**（StockItem 创建 / 更新 / 删除）→ 自动检查是否低于最低阈值
- **采购订单 / 销售订单 / 生产工单** 状态变更
- **新部件创建**（可选，默认关闭）

> 后台开关入口：InvenTree → 系统 → 插件 → DingTalk Notification

| 设置项 | 说明 | 默认 |
|:-------|:-----|:-----|
| `DINGTALK_NOTIFY_LOW_STOCK` | 库存不足告警 | ✅ 开 |
| `DINGTALK_NOTIFY_PO_STATUS` | 采购订单状态变更 | ✅ 开 |
| `DINGTALK_NOTIFY_SO_STATUS` | 销售订单状态变更 | ✅ 开 |
| `DINGTALK_NOTIFY_BUILD` | 生产工单状态变更 | ✅ 开 |
| `DINGTALK_NOTIFY_PART_NEW` | 新部件创建通知 | ⬜ 关 |

> **Webhook / Secret 优先级**：先读取插件后台设置；后台留空时自动回退到 `.env` 中的 `DINGTALK_WEBHOOK` / `DINGTALK_SECRET`。

---

### 方式 B：定时脚本批量巡检（补充）

扫描所有设置了 `minimum_stock` 的部件，低于阈值即推送告警；同一部件 **24 小时内只告警一次**（冷却机制，避免刷屏）。

**立即扫描告警**（忽略冷却，用于手动测试）：

```bash
docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py --scan
```

**定时检查**（受 24h 冷却限制，同一部件不重复告警）：

```bash
docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py
```

**仅扫描不发送**（dry-run，查看库存状态）：

```bash
docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py --dry-run
```

> **冷却记录**：容器内 `/home/inventree/data/plugins/_alert_cooldown.json`
> **强制所有部件重新告警**：删除上述冷却文件后，重新执行 `--scan`

---

### 设置 Windows 定时任务（自动巡检）

让电脑按固定周期自动执行库存检查，无需手动运行命令：

| 步骤 | 操作 |
|:-----|:-----|
| 1. 打开任务计划程序 | `Win + R` → 输入 `taskschd.msc` → 回车 |
| 2. 创建任务 | 右侧点击「创建任务」（非「创建基本任务」） |
| 3. 触发器 | 新建 → 按预定时间：每天 / 每小时 / 自定义周期 |
| 4. 操作 | 新建 → 启动程序 → 程序或脚本填 `cmd.exe` |
| 4.1 添加参数 | `/c "docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py"` |
| 4.2 起始于 | 填本项目文件夹路径（可选） |
| 5. 条件 | 取消勾选「只有在计算机使用交流电源时才启动」（笔记本电池模式下也能运行） |
| 6. 验证 | 右键任务 → 运行，检查钉钉群是否收到消息 |

## 配置文件 .env 说明

部署前**必须改 4 项**，其余保持默认。

### 1. INVENTREE_SITE_URL —— 访问地址

```env
# 本机使用
INVENTREE_SITE_URL=http://localhost

# 局域网其他电脑访问（推荐）
# PowerShell 跑 ipconfig 查 IPv4，例如 192.168.1.100
INVENTREE_SITE_URL=http://192.168.1.100
```

### 2. INVENTREE_ADMIN_PASSWORD —— 管理员密码

```env
INVENTREE_ADMIN_USER=admin
INVENTREE_ADMIN_PASSWORD=your_secure_password
INVENTREE_ADMIN_EMAIL=admin@localhost
```

### 3 / 4. DINGTALK_WEBHOOK / DINGTALK_SECRET —— 钉钉机器人

获取步骤：钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义 → 安全设置选「加签」

```env
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxxxx
DINGTALK_SECRET=SECxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 常用运维命令

所有命令在本项目根目录下运行：

```bash
# 查看所有容器状态
docker ps

# 启动 / 停止 / 重启全部服务
docker compose up -d
docker compose down
docker compose restart inventree-server

# 看实时日志（Ctrl+C 退出）
docker compose logs -f
docker compose logs inventree-server --tail 50

# 进入容器 shell（调试用）
docker exec -it inventree-server bash

# 彻底重置（清空数据库和卷，重新部署）
docker compose down -v
```

### 改代码后怎么生效

| 改了什么 | 操作 |
|----------|------|
| `.env`（webhook / 端口 / SITE_URL） | `docker compose up -d --force-recreate` |
| 插件 Python 代码（`plugins/inventree_dingtalk/`） | 清 `__pycache__` + `docker compose restart inventree-server inventree-worker` |
| 脚本（`scripts/check_and_notify.py`） | **无需任何操作**，下次执行自动用新代码 |

> 插件和脚本通过 bind mount 挂载到容器，改完本地代码立即生效，无需 `docker cp`。

---

## 常见问题

### Q1：登录返回 403 "CSRF verification failed"

本项目内置 `init-csrf` 服务，启动时自动往 `config.yaml` 追加 `csrf_trusted_origins`（localhost + 127.0.0.1 + SITE_URL），同时 `INVENTREE_ALLOWED_HOSTS="*"`。任何电脑/任何 IP 都不会触发 403。

仍报错（多为旧 volume 残留）：

```bash
docker compose down -v
docker compose up -d
```

### Q2：网页白屏 / "no response from server"

99% 是 Docker 内存不足 4GB 导致 db/cache 变 unhealthy。

```bash
# 检查健康状态
docker inspect --format "{{.State.Health.Status}}" inventree-db inventree-cache
# 两个都应是 healthy

# 处理：Docker Desktop → Settings → Resources → Memory 调到 4GB+
# 重启 Docker Desktop 后:
docker compose up -d
```

### Q3：`invoke update` 报 `SystemError: buffer overflow`

InvenTree stable 镜像内置 Python 3.14，`invoke` 库用 `fcntl.ioctl(FIONREAD)` 检测终端输入，Python 3.14 严格校验 buffer 大小，2 字节 buffer 被拒绝。

**处理**：不要跑 `invoke update`！直接用本项目的 `manage.py` 命令（见部署 Step 3）。

### Q4：钉钉没收到消息

1. 确认 `.env` 里 `DINGTALK_WEBHOOK` / `DINGTALK_SECRET` 不为空
2. 改完 `.env` 跑过 `docker compose up -d --force-recreate`
3. 手动测试：

```bash
docker exec inventree-server python /home/inventree/data/scripts/check_and_notify.py --scan
# 看输出是"发送成功"还是报错
```

4. 查钉钉机器人日志：群设置 → 智能群助手 → 机器人 → 日志
5. 容器能上网吗？

```bash
docker exec inventree-server curl -s https://oapi.dingtalk.com/
```

### Q5：插件在「系统 → 插件」里看不到

`plugins/` 已通过 bind mount 自动挂载，无需 `docker cp`。

```bash
# 1. 确认挂载成功
docker exec inventree-server ls /home/inventree/data/plugins/
# 应看到 inventree_dingtalk 目录

# 2. 看不到说明 docker-compose.yml 没生效：
docker compose up -d --force-recreate

# 3. 清 __pycache__ 并重启
docker exec inventree-server rm -rf /home/inventree/data/plugins/inventree_dingtalk/__pycache__
docker compose restart inventree-server inventree-worker
```

### Q6：`/web` 显示 "INVE-E1 - No frontend included"

根因：Caddyfile 里手动分了 `/static` 和 `/media` 的 `file_server`，路径和 Django `collectstatic` 的目标不匹配。

**修复**：本项目 Caddyfile 已是最简版（纯 `reverse_proxy`），不应出现此问题。如自行改过 Caddyfile，恢复为：

```caddy
:80 {
    encode gzip
    request_body { max_size 100MB }
    reverse_proxy http://inventree-server:8000
}
```

然后 `docker compose restart inventree-proxy`。

---

## 换电脑迁移

### 部署文件迁移（项目代码）

整个文件夹复制到新电脑 → 装 Docker → 改 `.env` 4 项 → 按 [快速开始](#快速开始) 跑。

### 业务数据迁移（数据库）

数据库存在 Docker named volume 里，**不会跟着文件夹走**。要用 InvenTree 的备份导出功能：

```
旧电脑后台 → 系统 → 数据导入导出 → 备份（导出 JSON/CSV）
新电脑后台 → 系统 → 数据导入导出 → 导入
```

---

## License

- InvenTree：[MIT License](https://github.com/inventree/InvenTree/blob/master/LICENSE)
- 本项目钉钉插件 / 脚本：MIT
