import click

from . import __version__
from .config import AppConfig, ConfigManager


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
        click.echo(f"  RSS URL: {existing.rss_url}")
        click.echo(f"  拉取间隔: {existing.fetch_interval}秒")
        if not click.confirm("\n是否覆盖现有配置？", default=False):
            click.echo("已取消")
            return

    # Get bot token
    click.echo("\n1. Telegram Bot Token")
    click.echo("   从 @BotFather 获取你的 Bot Token")
    bot_token = click.prompt("   请输入 Bot Token", type=str)

    # Get RSS URL
    click.echo("\n2. RSS 订阅地址")
    rss_url = click.prompt(
        "   请输入 RSS URL",
        type=str,
        default="https://linux.do/latest.rss"
    )

    # Get fetch interval
    click.echo("\n3. 拉取间隔")
    fetch_interval = click.prompt(
        "   请输入拉取间隔（秒）",
        type=int,
        default=60
    )

    # Save config
    config = AppConfig(
        bot_token=bot_token,
        rss_url=rss_url,
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
    click.echo(f"  RSS URL: {cfg.rss_url}")
    click.echo(f"  拉取间隔: {cfg.fetch_interval}秒")
    click.echo(f"\n  配置文件: {config_manager.config_path}")
    click.echo(f"  数据库: {config_manager.db_path}")


@cli.command(help="启动监控服务")
@click.option(
    "--config-dir",
    type=click.Path(),
    default=None,
    help="配置文件目录"
)
def run(config_dir):
    config_manager = ConfigManager(config_dir)

    if not config_manager.exists():
        click.echo("❌ 配置文件不存在，请先运行 'linux-do-monitor init'")
        return

    cfg = config_manager.load()
    click.echo("🚀 启动 Linux.do 关键词监控服务...")
    click.echo(f"   RSS: {cfg.rss_url}")
    click.echo(f"   拉取间隔: {cfg.fetch_interval}秒\n")

    from .app import Application
    app = Application(cfg, config_manager.get_db_path())
    app.run()
