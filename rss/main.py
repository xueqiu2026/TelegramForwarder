import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from rss.app.routes.auth import router as auth_router
from rss.app.routes.console import router as console_router
from rss.app.api.endpoints import feed
import uvicorn
import logging
from utils.log_config import setup_logging


# 获取日志记录器
logger = logging.getLogger(__name__)

app = FastAPI(title="TG Forwarder Console")

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
app.include_router(feed.router)
app.include_router(console_router)


# 根路由重定向到新前端
@app.get("/")
async def root_redirect():
    return RedirectResponse(url="http://localhost:5173")


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """运行后端 API 服务器"""
    uvicorn.run(app, host=host, port=port)

# 添加直接运行支持
if __name__ == "__main__":
    # 只有在直接运行时才设置日志（而不是被导入时）
    setup_logging()
    run_server()