"""独立的 RSS 服务启动模块 - 避免 spawn 时 import main.py 创建 TelegramClient"""
import uvicorn


def run_rss_server(host: str, port: int):
    """在新进程中运行 RSS 服务器"""
    from rss.main import app as rss_app
    uvicorn.run(
        rss_app,
        host=host,
        port=port
    )
