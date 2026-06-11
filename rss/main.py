import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from rss.app.routes.auth import router as auth_router
from rss.app.routes.rss import router as rss_router
from rss.app.routes.console import router as console_router
from rss.app.api.endpoints import feed
import uvicorn
import logging
import os
from utils.log_config import setup_logging


# 获取日志记录器
logger = logging.getLogger(__name__)

app = FastAPI(title="TG Forwarder RSS")

# CORS 中间件 — 允许 React 前端跨域访问
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth_router)
app.include_router(rss_router)
app.include_router(feed.router)
app.include_router(console_router)

# 模板配置
templates = Jinja2Templates(directory="rss/app/templates")

def run_server(host: str = "0.0.0.0", port: int = 8000):
    """运行 RSS 服务器"""
    uvicorn.run(app, host=host, port=port)

# 添加直接运行支持
if __name__ == "__main__":
    # 只有在直接运行时才设置日志（而不是被导入时）
    setup_logging()
    run_server() 