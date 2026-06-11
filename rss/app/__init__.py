"""
TG Forwarder Console Application
"""

from fastapi import FastAPI
from .routes.auth import router as auth_router

app = FastAPI(title="TG Forwarder Console")

# 注册路由
app.include_router(auth_router)