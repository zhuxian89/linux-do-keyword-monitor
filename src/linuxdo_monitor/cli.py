import click

from . import __version__
from .config import AppConfig, ConfigManager, SourceType


@click.group(name="linux-do-monitor", help="Linux.do 关键词监控机器人")
def cli():
    pass


@cli.command(help="显示版本信息")
def version():
    click.echo(f"linux-do-monitor {__version__}")


@cli.command(help="交互式初始化配置")
@click.option(
    "--config-dir",
    type=click.Path(),
    default=None,
    help="配置文件目录"
)
def init(config_dir):
    config_manager = ConfigManager(config_dir)

    click.echo("🚀 Linux.do 关键词监控机器人 - 初始化配置\n")

    # Check existing config
    if config_manager.exists():
        existing = config_manager.load()
        click.echo("检测到已有配置：")
        click.echo(f"  Bot Token: {existing.bot_token[:10]}...{existing.bot_token[-5:]}")
        click.echo(f"  数据源: {existing.source_type.value}")
        if existing.source_type == SourceType.RSS:
            click.echo(f"  RSS URL: {existing.rss_url}")
        else:
            click.echo(f"  Discourse URL: {existing.discourse_url}")
            click.echo(f"  Cookie: {'已配置' if existing.discourse_cookie else '未配置'}")
        click.echo(f"  拉取间隔: {existing.fetch_interval}秒")
        if not click.confirm("\n是否覆盖现有配置？", default=False):
            click.echo("已取消")
            return

    # Get bot token
    click.echo("\n1. Telegram Bot Token")
    click.echo("   从 @BotFather 获取你的 Bot Token")
    bot_token = click.prompt("   请输入 Bot Token", type=str)

    # Choose data source
    click.echo("\n2. 选择数据源")
    click.echo("   [1] RSS (公开内容，无需登录)")
    click.echo("   [2] Discourse API (需要 Cookie，可获取登录后内容)")
    source_choice = click.prompt("   请选择", type=int, default=1)

    source_type = SourceType.RSS if source_choice == 1 else SourceType.DISCOURSE

    # Source specific config
    rss_url = "https://linux.do/latest.rss"
    discourse_url = "https://linux.do"
    discourse_cookie = None

    if source_type == SourceType.RSS:
        click.echo("\n3. RSS 订阅地址")
        rss_url = click.prompt(
            "   请输入 RSS URL",
            type=str,
            default="https://linux.do/latest.rss"
        )
    else:
        click.echo("\n3. Discourse 配置")
        discourse_url = click.prompt(
            "   请输入 Discourse URL",
            type=str,
            default="https://linux.do"
        )
        click.echo("\n   获取 Cookie 方法：")
        click.echo("   1. 浏览器登录 Linux.do")
        click.echo("   2. F12 打开开发者工具 -> Network")
        click.echo("   3. 刷新页面，找到任意请求")
        click.echo("   4. 复制 Request Headers 中的 Cookie 值")
        discourse_cookie = click.prompt("   请输入 Cookie", type=str)

    # Get fetch interval
    click.echo("\n4. 拉取间隔")
    fetch_interval = click.prompt(
        "   请输入拉取间隔（秒）",
        type=int,
        default=60
    )

    # Save config
    config = AppConfig(
        bot_token=bot_token,
        source_type=source_type,
        rss_url=rss_url,
        discourse_url=discourse_url,
        discourse_cookie=discourse_cookie,
        fetch_interval=fetch_interval
    )
    config_manager.save(config)

    click.echo(f"\n✅ 配置已保存到: {config_manager.config_path}")
    click.echo("\n使用 'linux-do-monitor run' 启动服务")


@cli.command(help="显示当前配置")
@click.option(
    "--config-dir",
    type=click.Path(),
    default=None,
    help="配置文件目录"
)
def config(config_dir):
    config_manager = ConfigManager(config_dir)

    if not config_manager.exists():
        click.echo("❌ 配置文件不存在，请先运行 'linux-do-monitor init'")
        return

    cfg = config_manager.load()
    click.echo("📋 当前配置：\n")
    click.echo(f"  Bot Token: {cfg.bot_token[:10]}...{cfg.bot_token[-5:]}")
    click.echo(f"  数据源: {cfg.source_type.value}")
    if cfg.source_type == SourceType.RSS:
        click.echo(f"  RSS URL: {cfg.rss_url}")
    else:
        click.echo(f"  Discourse URL: {cfg.discourse_url}")
        click.echo(f"  Cookie: {'已配置' if cfg.discourse_cookie else '未配置'}")
    click.echo(f"  拉取间隔: {cfg.fetch_interval}秒")
    click.echo(f"\n  配置文件: {config_manager.config_path}")
    click.echo(f"  数据库: {config_manager.db_path}")


@cli.command(help="更新 Discourse Cookie")
@click.option(
    "--config-dir",
    type=click.Path(),
    default=None,
    help="配置文件目录"
)
def set_cookie(config_dir):
    """Update Discourse cookie without reinitializing"""
    config_manager = ConfigManager(config_dir)

    if not config_manager.exists():
        click.echo("❌ 配置文件不存在，请先运行 'linux-do-monitor init'")
        return

    cfg = config_manager.load()

    click.echo("🔑 更新 Discourse Cookie\n")
    click.echo("获取 Cookie 方法：")
    click.echo("1. 浏览器登录 Linux.do")
    click.echo("2. F12 打开开发者工具 -> Network")
    click.echo("3. 刷新页面，找到任意请求")
    click.echo("4. 复制 Request Headers 中的 Cookie 值\n")

    new_cookie = click.prompt("请输入新的 Cookie", type=str)

    cfg.discourse_cookie = new_cookie
    if cfg.source_type == SourceType.RSS:
        if click.confirm("是否同时切换数据源为 Discourse API？", default=True):
            cfg.source_type = SourceType.DISCOURSE

    config_manager.save(cfg)
    click.echo("\n✅ Cookie 已更新")


@cli.command(help="启动监控服务")
@click.option(
    "--config-dir",
    type=click.Path(),
    default=None,
    help="配置文件目录"
)
@click.option(
    "--web-port",
    type=int,
    default=None,
    help="Web 管理页面端口 (如: 8080)"
)
@click.option(
    "--web-password",
    type=str,
    default="admin",
    help="Web 管理页面密码"
)
def run(config_dir, web_port, web_password):
    config_manager = ConfigManager(config_dir)

    if not config_manager.exists():
        click.echo("❌ 配置文件不存在，请先运行 'linux-do-monitor init'")
        return

    cfg = config_manager.load()
    click.echo("🚀 启动 Linux.do 关键词监控服务...")
    click.echo(f"   数据源: {cfg.source_type.value}")
    if cfg.source_type == SourceType.RSS:
        click.echo(f"   RSS URL: {cfg.rss_url}")
    else:
        click.echo(f"   Discourse URL: {cfg.discourse_url}")
    click.echo(f"   拉取间隔: {cfg.fetch_interval}秒\n")

    from .app import Application
    app = Application(cfg, config_manager.get_db_path(), config_manager)

    # Start web server if port specified
    if web_port:
        from .web import ConfigWebServer
        web_server = ConfigWebServer(
            config_path=config_manager.config_path,
            port=web_port,
            password=web_password,
            db_path=config_manager.get_db_path()
        )
        web_server.set_update_callback(app.reload_config)
        web_server.start()

    app.run()
