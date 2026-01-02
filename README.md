# Linux.do 关键词监控机器人

监控 [Linux.do](https://linux.do) 论坛 RSS，当新帖子标题匹配订阅关键词时，通过 Telegram Bot 推送通知。

## 功能特性

- 定时拉取 Linux.do RSS 订阅
- 支持多用户订阅不同关键词
- 支持订阅所有新帖子
- 关键词匹配不区分大小写
- 防止重复推送
- 消息格式美观，支持直达链接

## 安装

```bash
pip install linux-do-monitor
```

或从源码安装：

```bash
git clone https://github.com/zhuxian89/linux-do-keyword-monitor.git
cd linux-do-keyword-monitor
pip install -e .
```

## 部署步骤

### 1. 创建 Telegram Bot

1. 在 Telegram 中搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称和用户名
4. 保存获得的 Bot Token（格式如：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

### 2. 初始化配置

```bash
# 进入工作目录（配置文件和数据库将保存在此目录）
mkdir -p ~/linux-do-monitor && cd ~/linux-do-monitor

# 交互式配置
linux-do-monitor init
```

按提示输入：
- **Bot Token**: 从 BotFather 获取的 Token
- **RSS URL**: 默认 `https://linux.do/latest.rss`，可自定义
- **拉取间隔**: 默认 60 秒

### 3. 启动服务

```bash
linux-do-monitor run
```

### 4. 后台运行（推荐）

使用 `nohup`：

```bash
nohup linux-do-monitor run > monitor.log 2>&1 &
```

或使用 `screen`：

```bash
screen -S linux-do-monitor
linux-do-monitor run
# Ctrl+A+D 退出 screen
```

或使用 systemd（Linux）：

```bash
# 创建服务文件
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

# 启动服务
sudo systemctl daemon-reload
sudo systemctl enable linux-do-monitor
sudo systemctl start linux-do-monitor

# 查看日志
sudo journalctl -u linux-do-monitor -f
```

## 使用方法

### Bot 命令

在 Telegram 中与你的 Bot 对话：

| 命令 | 说明 |
|------|------|
| `/start` | 开始使用，注册用户 |
| `/subscribe <关键词>` | 订阅关键词 |
| `/unsubscribe <关键词>` | 取消订阅关键词 |
| `/subscribe_all` | 订阅所有新帖子 |
| `/unsubscribe_all` | 取消订阅所有 |
| `/list` | 查看我的订阅列表 |
| `/help` | 帮助信息 |

### 示例

```
/subscribe docker
/subscribe 求助
/subscribe NAS
/list
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

运行 `linux-do-monitor init` 后，会在当前目录生成：

- `config.json` - 配置文件
- `data.db` - SQLite 数据库

配置文件示例：

```json
{
  "bot_token": "your_bot_token",
  "rss_url": "https://linux.do/latest.rss",
  "fetch_interval": 60
}
```

## 日志示例

```
2024-01-02 22:52:20 - INFO - 🤖 Telegram Bot 启动中...
2024-01-02 22:52:21 - INFO - ⏰ 定时任务已启动, 每 60 秒拉取一次
2024-01-02 22:52:21 - INFO - 📡 开始拉取 RSS...
2024-01-02 22:52:22 - INFO -   📤 推送给 123456 (全部订阅): Docker 容器部署最佳实践...
2024-01-02 22:52:22 - INFO - ✅ 拉取完成: 共 30 条, 新增 2 条, 推送 1 条通知
```

## License

MIT
