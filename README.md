# Linux.do 关键词监控机器人

监控 [Linux.do](https://linux.do) 论坛，当新帖子标题匹配订阅关键词时，通过 Telegram Bot 推送通知。

---

# 使用指南

> 如果你只是想使用这个机器人，不需要自己部署，直接看这部分即可。

## 快速开始

1. 在 Telegram 搜索 [@Linuxdo_keyword_bot](https://t.me/Linuxdo_keyword_bot)
2. 点击 **Start** 或发送 `/start`
3. 订阅你感兴趣的关键词，例如：`/subscribe docker`

就这么简单！当 Linux.do 有新帖子标题包含你订阅的关键词时，机器人会自动推送通知给你。

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

- 支持 RSS 和 Discourse API 两种数据源
- 多用户订阅不同关键词
- 支持订阅特定用户的所有帖子
- 支持订阅所有新帖子
- 关键词匹配不区分大小写
- 防止重复推送
- Web 配置管理页面（支持刷新缓存）
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

### 2. 初始化配置

```bash
# 创建工作目录
mkdir -p ~/linux-do-monitor && cd ~/linux-do-monitor

# 交互式配置
linux-do-monitor init
```

按提示输入：
- **Bot Token**: 从 BotFather 获取的 Token
- **数据源类型**: RSS（公开）或 Discourse API（需要 Cookie）
- **拉取间隔**: 默认 60 秒

### 3. 启动服务

```bash
linux-do-monitor run
```

启动后会显示 Web 配置页面地址，可以在浏览器中管理配置。

### 4. 后台运行

使用 systemd（推荐）：

```bash
sudo tee /etc/systemd/system/linux-do-monitor.service << EOF
[Unit]
Description=Linux.do Keyword Monitor
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/linux-do-monitor
ExecStart=$(which linux-do-monitor) run
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

或使用 Docker：

```bash
# TODO: Docker 部署方式
```

## CLI 命令

```bash
linux-do-monitor --help      # 查看帮助
linux-do-monitor version     # 查看版本
linux-do-monitor init        # 初始化配置
linux-do-monitor config      # 查看当前配置
linux-do-monitor run         # 启动服务
```

## 配置文件

运行后会在当前目录生成：

- `config.json` - 配置文件
- `data.db` - SQLite 数据库

配置示例：

```json
{
  "bot_token": "your_bot_token",
  "admin_chat_id": 123456789,
  "source_type": "discourse",
  "rss_url": "https://linux.do/latest.rss",
  "discourse_url": "https://linux.do",
  "discourse_cookie": "your_cookie_here",
  "fetch_interval": 60
}
```

## Web 管理页面

启动服务后，访问 `http://localhost:8080?pwd=yourpassword` 可以：

- 切换数据源（RSS / Discourse API）
- 更新 Cookie
- 设置管理员 Chat ID（接收系统告警）
- 查看用户统计
- 刷新缓存（清除所有缓存数据）

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

## TODO

### 短期优化（提升体验）

- [x] 订阅用户 - 订阅某个用户的所有帖子
- [x] 关键词热度统计 - `/stats` 命令查看统计信息
- [x] 刷新缓存 - Web 页面支持手动刷新缓存
- [x] 正则匹配 - 支持正则表达式订阅，如 `\bopenai\b`、`(免费|白嫖)`
- [x] 封禁检测 - 检测用户封禁 Bot 并统计
- [ ] 静默时段 - 用户可设置夜间不推送（如 23:00-8:00）
- [ ] 消息合并 - 短时间内多条通知合并成一条，减少打扰
- [ ] 分类订阅 - 按 Linux.do 的分类（如"开发调优"、"资源荟萃"）订阅

### 中期优化（提升稳定性）

- [ ] 消息队列 - 引入 Redis 队列，解耦拉取和发送
- [ ] 失败重试 - 发送失败的消息进入重试队列
- [ ] 健康检查 - `/health` 端点，方便监控
- [ ] 数据清理 - 定期清理过期的 posts 和 notifications 表
- [ ] 多实例部署 - 支持多实例负载均衡
- [ ] Docker 部署 - 提供 Dockerfile 和 docker-compose

### 长期扩展（新功能）

- [ ] 内容摘要 - 推送时附带帖子摘要
- [ ] AI 智能推荐 - 根据用户历史点击推荐关键词
- [ ] 多平台支持 - 支持其他 Discourse 论坛
- [ ] Web 订阅管理 - 用户通过网页管理订阅，不只是 Bot

## License

MIT
