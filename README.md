# Linux.do 关键词监控机器人

监控 [Linux.do](https://linux.do) 论坛，当新帖子标题匹配订阅关键词时，通过 Telegram Bot 推送通知。支持多论坛（如 NodeSeek）。

---

# 使用指南

> 如果你只是想使用这个机器人，不需要自己部署，直接看这部分即可。

## 快速开始

**Linux.do 论坛：**
1. 在 Telegram 搜索 [@Linuxdo_keyword_bot](https://t.me/Linuxdo_keyword_bot)
2. 点击 **Start** 或发送 `/start`
3. 订阅你感兴趣的关键词，例如：`/subscribe docker`

**NodeSeek 论坛：**
1. 在 Telegram 搜索 [@ns_keyword_bot](https://t.me/ns_keyword_bot)
2. 点击 **Start** 或发送 `/start`
3. 订阅你感兴趣的关键词，例如：`/subscribe vps`

就这么简单！当论坛有新帖子标题包含你订阅的关键词时，机器人会自动推送通知给你。

## 命令列表

| 命令 | 说明 | 示例 |
|------|------|------|
| `/start` | 开始使用 | `/start` |
| `/subscribe <关键词>` | 订阅关键词 | `/subscribe docker` |
| `/unsubscribe <关键词>` | 取消订阅 | `/unsubscribe docker` |
| `/list` | 查看我的关键词订阅 | `/list` |
| `/subscribe_user <用户名>` | 订阅某用户的所有帖子 | `/subscribe_user neo` |
| `/unsubscribe_user <用户名>` | 取消订阅用户 | `/unsubscribe_user neo` |
| `/list_users` | 查看已订阅的用户 | `/list_users` |
| `/subscribe_all` | 订阅所有新帖（慎用，消息量大） | `/subscribe_all` |
| `/unsubscribe_all` | 取消订阅所有 | `/unsubscribe_all` |
| `/stats` | 查看关键词热度统计 | `/stats` |
| `/help` | 帮助信息 | `/help` |

## 使用示例

```
# 订阅多个关键词
/subscribe docker
/subscribe NAS
/subscribe 求助
/subscribe homelab

# 使用正则表达式（高级用法）
/subscribe \bopenai\b          # 精确匹配 openai 单词
/subscribe gpt-?4              # 匹配 gpt4 或 gpt-4
/subscribe (免费|白嫖)          # 匹配 免费 或 白嫖

# 订阅某个用户的所有帖子
/subscribe_user neo

# 查看当前订阅
/list
/list_users

# 取消某个订阅
/unsubscribe docker
/unsubscribe_user neo

# 查看统计信息
/stats
```

## 推送效果

当有匹配的新帖子时，你会收到这样的消息：

```
🔔 Linux.do 新帖提醒
━━━━━━━━━━━━━━━

📌 匹配关键词：docker

📝 标题
Docker 容器部署最佳实践分享

🔗 点击查看原帖 →
```

## 常见问题

**Q: 关键词区分大小写吗？**
A: 不区分。订阅 `Docker` 和 `docker` 效果相同。

**Q: 可以订阅多少个关键词？**
A: 每位用户最多可订阅 5 个关键词和 5 个用户。如需更多，可使用 `/subscribe_all` 订阅所有帖子。

**Q: 支持正则表达式吗？**
A: 支持！系统会自动检测。例如 `\bopenai\b` 精确匹配单词，`(免费|白嫖)` 匹配多个词。可以用 AI 工具帮你生成正则。

**Q: 可以订阅某个用户的帖子吗？**
A: 可以！使用 `/subscribe_user <用户名>` 订阅某个用户的所有帖子。

**Q: 为什么没收到通知？**
A: 可能是帖子标题没有包含你的关键词，或者该帖子已经推送过了（不会重复推送）。

**Q: `/subscribe_all` 是什么？**
A: 订阅所有新帖子，不管标题是什么都会推送。消息量较大，请谨慎使用。

---

# 开发部署指南

> 以下内容面向想要自己部署机器人的开发者。

## 功能特性

- 支持多论坛（Linux.do、NodeSeek 等）
- 支持 RSS 和 Discourse API 两种数据源
- 多用户订阅不同关键词
- 支持订阅特定用户的所有帖子
- 支持订阅所有新帖子
- 关键词匹配不区分大小写
- 防止重复推送
- Web 配置管理页面
- Web SQL 查询页面（支持只读/管理员模式）
- Cookie 失效自动降级 + 管理员告警
- 关键词热度统计

## 安装

从源码安装：

```bash
git clone https://github.com/zhuxian89/linux-do-keyword-monitor.git
cd linux-do-keyword-monitor
pip install -e .
```

## 部署步骤

### 1. 创建 Telegram Bot

1. 在 Telegram 搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称和用户名
4. 保存获得的 Bot Token（格式如：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

### 2. 初始化数据库

```bash
# 创建工作目录
mkdir -p ~/linux-do-monitor && cd ~/linux-do-monitor

# 初始化数据库表结构
linux-do-monitor db-init
```

### 3. 启动服务

```bash
# 启动服务（带 Web 管理页面）
linux-do-monitor run --web-port 8080

# 可选参数：
# --web-port 8080      Web 管理页面端口
# --web-password xxx   Web 访问密码（默认: admin）
# --config-dir ./      配置文件目录
```

### 4. 配置论坛

启动后访问 Web 管理页面配置论坛：

```
http://localhost:8080/linuxdo/config?pwd=admin
```

在 Web 页面中配置：
- **Bot Token**: 从 BotFather 获取的 Token
- **数据源类型**: RSS（公开）或 Discourse API（需要 Cookie）
- **RSS URL**: 论坛的 RSS 地址
- **拉取间隔**: 默认 60 秒

### 5. 后台运行（systemd）

```bash
sudo tee /etc/systemd/system/linux-do-monitor.service << EOF
[Unit]
Description=Linux.do Keyword Monitor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/linux-do-monitor
ExecStart=/usr/local/bin/linux-do-monitor run --web-port 8080 --web-password yourpassword
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable linux-do-monitor
sudo systemctl start linux-do-monitor

# 查看日志
sudo journalctl -u linux-do-monitor -f
```

## CLI 命令

```bash
linux-do-monitor --help        # 查看帮助
linux-do-monitor version       # 查看版本
linux-do-monitor init          # 交互式初始化配置（可选）
linux-do-monitor config        # 查看当前配置
linux-do-monitor db-init       # 初始化数据库表结构
linux-do-monitor db-version    # 查看数据库版本
linux-do-monitor db-migrate    # 执行数据库迁移
linux-do-monitor run           # 启动服务
```

## 配置文件

运行后会在当前目录生成：

- `config.json` - 配置文件
- `data.db` - SQLite 数据库

配置示例（多论坛）：

```json
{
  "forums": [
    {
      "forum_id": "linux-do",
      "name": "Linux.do",
      "bot_token": "your_bot_token",
      "source_type": "rss",
      "rss_url": "https://linux.do/latest.rss",
      "discourse_url": "https://linux.do",
      "discourse_cookie": "",
      "fetch_interval": 60,
      "enabled": true
    },
    {
      "forum_id": "nodeseek",
      "name": "NodeSeek",
      "bot_token": "another_bot_token",
      "source_type": "rss",
      "rss_url": "https://www.nodeseek.com/rss.xml",
      "fetch_interval": 30,
      "enabled": true
    }
  ],
  "admin_chat_id": 123456789,
  "sql_admin_password": "your_sql_admin_password"
}
```

## Web 管理页面

启动服务后，访问以下页面：

| 页面 | 地址 | 说明 |
|------|------|------|
| 配置管理 | `/linuxdo/config?pwd=xxx` | 配置论坛、Bot Token、数据源等 |
| 用户统计 | `/linuxdo/users?pwd=xxx` | 查看用户和订阅统计 |
| SQL 查询 | `/linuxdo/sql?pwd=xxx` | 只读 SQL 查询 |
| SQL 管理 | `/linuxdo/sql?pwd=xxx&admin=yyy` | 可执行 INSERT/UPDATE/DELETE |

默认密码：
- Web 访问密码 (`pwd`): `admin`
- SQL 管理员密码 (`admin`): `admin`（可在 config.json 中设置 `sql_admin_password`）

## 数据库迁移

升级版本后如果数据库结构有变化，需要执行迁移：

```bash
# 查看当前版本
linux-do-monitor db-version

# 执行迁移
linux-do-monitor db-migrate -y
```

## 技术架构

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Linux.do RSS   │────▶│   Monitor    │────▶│  Telegram   │
│  / Discourse    │     │   Service    │     │    Bot      │
└─────────────────┘     └──────────────┘     └─────────────┘
                              │
                              ▼
                        ┌──────────────┐
                        │   SQLite     │
                        │   Database   │
                        └──────────────┘
```

- **数据源**: RSS（公开）或 Discourse JSON API（需要 Cookie，可访问更多内容）
- **调度器**: APScheduler 定时拉取
- **Bot 框架**: python-telegram-bot
- **数据库**: SQLite
- **HTTP 客户端**: curl_cffi（绕过 Cloudflare）
- **Web 框架**: Flask

## License

MIT
