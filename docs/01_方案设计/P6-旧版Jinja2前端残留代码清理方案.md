# P6 - 旧版 Jinja2 前端残留代码清理方案

> 创建时间：2026-06-11
> 状态：待执行

## 一、背景

项目在 P2/P3 迭代中构建了全新的 React + Vite 前端 Console（端口 5173），但旧版 Jinja2 服务端渲染前端（端口 8000）的代码从未清理，导致：

1. 访问 `localhost:8000` 仍显示旧版 RSS Dashboard 页面，容易混淆
2. 旧版 HTML 登录/注册路由与新版 JSON API 登录并行，存在两套认证入口
3. 约 700 行废弃代码增加维护负担

## 二、项目服务架构（清理后）

| 服务 | 端口 | 说明 |
|---|---|---|
| 后端 API (FastAPI) | 8000 | `/console/*` JSON API + `/api/*` RSS Feed XML |
| 前端 Console (Vite) | 5173 | React 管理面板（开发模式）|
| PostgreSQL | 5432 | 数据库 |
| Telegram Client | — | 后台常驻，无 HTTP 端口 |

> **注意**：日常使用应访问 `http://localhost:5173`，不是 8000。

## 三、依赖分析

### 3.1 auth.py 函数引用关系

| 函数/路由 | 被谁引用 | 结论 |
|---|---|---|
| `get_current_user()` | `console.py` 全部 40+ 个 API 端点 | **必须保留** |
| `create_access_token()` | `console.py` L61 | **必须保留** |
| `SECRET_KEY` / `ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | `create_access_token()` + `get_current_user()` | **必须保留** |
| `init_db_ops()` | `get_current_user()` 内部使用 | **必须保留** |
| `GET /login` (L62-75) | 仅旧版 HTML 页面 | ❌ 删除 |
| `POST /login` (L77-108) | 仅旧版 HTML Form 提交 | ❌ 删除 |
| `GET /register` (L110-120) | 仅旧版 HTML 页面 | ❌ 删除 |
| `POST /register` (L122-161) | 仅旧版 HTML Form 提交 | ❌ 删除 |
| `GET /logout` (L163-167) | 旧版跳转，新版用 `/console/auth/logout` | ❌ 删除 |
| `GET /` (L169-175) | 重定向到旧 Dashboard | ❌ 删除 |
| `POST /rss/change_password` (L177-225) | 旧版修改密码 | ❌ 删除（新版在 console.py 中） |

### 3.2 新前端实际调用的认证接口

```typescript
// frontend/src/api.ts
authApi.login  → POST /console/auth/login    (JSON body)
authApi.logout → POST /console/auth/logout   (JSON)
authApi.me     → GET  /console/auth/me       (Cookie)
```

**结论**：新前端不调用 `auth.py` 中的任何 HTML 路由，全部走 `console.py` 的 JSON API。

### 3.3 rss.py 功能覆盖验证

| rss.py 旧路由 | console.py 替代路由 | 状态 |
|---|---|---|
| `GET /rss/dashboard` (HTML) | React 前端 `/rss` 页面 | ✅ 已覆盖 |
| `POST /rss/config` (Form) | `POST /console/rss-configs` (JSON) | ✅ 已覆盖 |
| `GET /rss/toggle/{id}` | `POST /console/rss-configs/{id}/toggle` | ✅ 已覆盖 |
| `GET /rss/delete/{id}` | `DELETE /console/rss-configs/{id}` | ✅ 已覆盖 |
| `GET /rss/patterns/{id}` | `GET /console/rss-configs/{id}/patterns` | ✅ 已覆盖 |
| `POST /rss/pattern` (Form) | `POST /console/rss-configs/{id}/patterns` (JSON) | ✅ 已覆盖 |
| `DELETE /rss/pattern/{id}` | `DELETE /console/rss-configs/patterns/{id}` | ✅ 已覆盖 |
| `DELETE /rss/patterns/{id}` | `DELETE /console/rss-configs/{id}/patterns` | ✅ 已覆盖 |
| `POST /rss/test-regex` (Form) | `POST /console/test-regex` (JSON) | ✅ 已覆盖 |

**结论**：`rss.py` 全部 464 行可安全删除。

### 3.4 不可删除的文件

| 文件 | 理由 |
|---|---|
| `rss/app/api/endpoints/feed.py` | RSS Feed XML 输出给阅读器，是核心功能 |
| `rss/app/services/feed_generator.py` | feed.py 的依赖 |
| `rss/app/crud/entry.py` | 数据操作层 |
| `rss/app/models/entry.py` | 数据模型 |
| `rss/app/core/config.py` | 配置 |

## 四、清理操作清单

### 4.1 删除文件（4 个）

```
DELETE  rss/app/routes/rss.py              # 464 行，旧版 Dashboard 路由
DELETE  rss/app/templates/login.html        # 旧版登录页 HTML
DELETE  rss/app/templates/register.html     # 旧版注册页 HTML
DELETE  rss/app/templates/rss_dashboard.html # 旧版 Dashboard HTML
```

### 4.2 修改 auth.py — 精简为纯认证模块

**保留内容（约 40 行）：**
```python
# 保留的 import
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from models.models import get_session, User
from models.db_operations import DBOperations
import jwt
from datetime import datetime, timedelta
import pytz
from utils.constants import DEFAULT_TIMEZONE
from typing import Optional
import secrets

router = APIRouter()

# JWT 配置 — 保留
SECRET_KEY = secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440

# 数据库操作 — 保留
db_ops = None
def init_db_ops():
    global db_ops
    if db_ops is None:
        db_ops = DBOperations()

# 核心认证函数 — 保留
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    ...  # 保持原样

async def get_current_user(request: Request):
    ...  # 保持原样
```

**删除内容（约 185 行）：**
```python
# 删除的 import
- from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
- from fastapi.responses import HTMLResponse, RedirectResponse
- from fastapi.templating import Jinja2Templates
- from sqlalchemy.orm import joinedload
- import models.models as models
- import os

# 删除的变量
- templates = Jinja2Templates(directory="rss/app/templates")

# 删除的路由
- GET  /login          (HTML 页面渲染)
- POST /login          (Form 登录)
- GET  /register       (HTML 页面渲染)
- POST /register       (Form 注册)
- GET  /logout         (重定向)
- GET  /               (重定向到旧 Dashboard)
- POST /rss/change_password (旧版改密码)
```

### 4.3 修改 rss/main.py

```diff
- from rss.app.routes.rss import router as rss_router
- from fastapi.templating import Jinja2Templates

- app.include_router(rss_router)

- templates = Jinja2Templates(directory="rss/app/templates")

+ # 根路由重定向到新前端
+ from fastapi.responses import RedirectResponse
+ @app.get("/")
+ async def root_redirect():
+     return RedirectResponse(url="http://localhost:5173")
```

### 4.4 修改 rss/app/__init__.py

```diff
- from fastapi.templating import Jinja2Templates
- templates = Jinja2Templates(directory="rss/app/templates")
```

## 五、回滚方案

如果清理后发现问题，可通过 Git 回滚：

```bash
git checkout HEAD -- rss/app/routes/rss.py
git checkout HEAD -- rss/app/routes/auth.py
git checkout HEAD -- rss/app/templates/
git checkout HEAD -- rss/main.py
git checkout HEAD -- rss/app/__init__.py
```

> **建议**：执行清理前先 `git add -A && git commit -m "checkpoint: before P6 cleanup"` 保存当前状态。

## 六、验证步骤

1. 重启后端 → 访问 `localhost:8000/` → 应跳转到 `localhost:5173`
2. 访问 `localhost:8000/rss/dashboard` → 应返回 404
3. 访问 `localhost:5173` → React Console 正常登录
4. Console 中操作 RSS 配置（增删改查）→ 功能正常
5. Console 中运行 AI 沙盒 A/B 测试 → 功能正常
6. 检查 RSS Feed 输出：`localhost:8000/api/feed/{rule_id}` → 正常输出 XML
