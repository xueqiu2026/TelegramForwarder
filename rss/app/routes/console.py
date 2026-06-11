from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Request, File, UploadFile
from fastapi.responses import JSONResponse
from models.models import (
    get_session, ForwardRule, Chat, Keyword, ReplaceRule,
    PushConfig, SummaryHistory, User, MediaTypes, MediaExtensions,
    RSSConfig, RSSPattern
)
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from enums.enums import ForwardMode, PreviewMode, MessageMode, AddMode, HandleMode
from .auth import get_current_user
from typing import Optional, List, Dict
import logging
import asyncio
import json
import os
import re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ── 枚举映射表 ──────────────────────────────────────────────────────
ENUM_MAP = {
    "forward_mode": ForwardMode,
    "message_mode": MessageMode,
    "is_preview": PreviewMode,
    "add_mode": AddMode,
    "handle_mode": HandleMode,
}

# ── 鉴权守卫 (BUG-02) ──────────────────────────────────────────────
def require_auth(user):
    """所有受保护端点统一调用，未登录直接抛 401"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")


# ══════════════════════════════════════════════════════════════════════
# 0. JSON 登录/登出/会话检查 (React 前端专用)
# ══════════════════════════════════════════════════════════════════════
from .auth import create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, init_db_ops as init_auth_db
from datetime import timedelta

@router.post("/auth/login")
async def json_login(data: Dict):
    """JSON 格式登录，返回 JWT Cookie"""
    username = data.get("username", "")
    password = data.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    db = get_session()
    try:
        init_auth_db()
        from .auth import db_ops as auth_db_ops
        user = await auth_db_ops.verify_user(db, username, password)
        if not user:
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        token = create_access_token(
            data={"sub": user.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        response = JSONResponse({"status": "success", "username": user.username})
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            samesite="lax",
        )
        return response
    finally:
        db.close()


@router.post("/auth/logout")
async def json_logout():
    """清除 JWT Cookie"""
    response = JSONResponse({"status": "success"})
    response.delete_cookie("access_token")
    return response


@router.get("/auth/me")
async def get_me(user=Depends(get_current_user)):
    """检查当前会话状态"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"username": user.username}


@router.post("/trigger-summary-now")
async def trigger_summary_now(user=Depends(get_current_user)):
    """手动触发一次聚合总结（含写作推送），用于测试"""
    require_auth(user)
    try:
        import sys
        main_module = sys.modules.get('__main__') or sys.modules.get('main')
        scheduler = getattr(main_module, 'scheduler', None) if main_module else None
        if not scheduler:
            return {"status": "error", "message": "调度器未初始化，请确认主服务已完全启动"}
        # 使用 rule_id=1 触发
        asyncio.create_task(scheduler._execute_summary(1, is_now=True))
        return {"status": "success", "message": "已触发手动总结，预计 1-2 分钟后推送到目标群组"}
    except Exception as e:
        logger.error(f"手动触发总结失败: {e}")
        return {"status": "error", "message": str(e)}


# ══════════════════════════════════════════════════════════════════════
# WebSocket 连接管理器
# ══════════════════════════════════════════════════════════════════════
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()


# BUG-14: 使用 logging handler 配置的日志路径，而非硬编码
async def log_generator():
    """从本地日志文件中读取并推送到 WebSocket"""
    # 尝试常见的日志文件路径
    log_candidates = [
        "temp/forwarder.log",
        "logs/forwarder.log",
    ]
    log_file = None
    for candidate in log_candidates:
        if os.path.exists(candidate):
            log_file = candidate
            break

    if not log_file:
        # 创建默认日志文件
        os.makedirs("temp", exist_ok=True)
        log_file = "temp/forwarder.log"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("System Log Stream initialized.\n")

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            # 先输出最后 50 行历史日志
            lines = f.readlines()
            for line in lines[-50:]:
                yield line
            # 然后 tail -f 模式
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(1)
                    continue
                yield line
    except Exception as e:
        logger.error(f"WebSocket 日志流生成出错: {e}")


# ══════════════════════════════════════════════════════════════════════
# 1. Chat 列表
# ══════════════════════════════════════════════════════════════════════
@router.get("/chats")
async def get_chats(user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        chats = db.query(Chat).all()
        return [{"id": c.id, "telegram_chat_id": c.telegram_chat_id, "name": c.name} for c in chats]
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 2. 规则管理 (CRUD)
# ══════════════════════════════════════════════════════════════════════
@router.get("/rules")
async def get_rules(user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        rules = db.query(ForwardRule).all()
        result = []
        for r in rules:
            result.append({
                "id": r.id,
                "source_chat_id": r.source_chat_id,
                "source_chat_name": r.source_chat.name if r.source_chat else "Unknown",
                "target_chat_id": r.target_chat_id,
                "target_chat_name": r.target_chat.name if r.target_chat else "Unknown",
                "enable_rule": r.enable_rule,
                "enable_forward": getattr(r, 'enable_forward', True),
                "is_ai": r.is_ai,
                "is_summary": r.is_summary,
                "summary_time": r.summary_time
            })
        return result
    finally:
        db.close()


@router.post("/rules")
async def create_rule(data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        rule = ForwardRule(
            source_chat_id=data.get("source_chat_id"),
            target_chat_id=data.get("target_chat_id"),
            forward_mode=data.get("forward_mode", "BLACKLIST"),
            use_bot=data.get("use_bot", True),
            enable_rule=data.get("enable_rule", True)
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return {"status": "success", "id": rule.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.get("/rules/{id}")
async def get_rule_detail(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        r = db.query(ForwardRule).filter(ForwardRule.id == id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Rule not found")

        return {
            "id": r.id,
            "source_chat_id": r.source_chat_id,
            "target_chat_id": r.target_chat_id,
            "forward_mode": r.forward_mode.name if r.forward_mode else "BLACKLIST",
            "use_bot": r.use_bot,
            "message_mode": r.message_mode.name if r.message_mode else "MARKDOWN",
            "is_replace": r.is_replace,
            "is_preview": r.is_preview.name if r.is_preview else "FOLLOW",
            "is_original_link": r.is_original_link,
            "is_delete_original": r.is_delete_original,
            "is_original_sender": r.is_original_sender,
            "userinfo_template": r.userinfo_template,
            "time_template": r.time_template,
            "original_link_template": r.original_link_template,
            "is_original_time": r.is_original_time,
            "add_mode": r.add_mode.name if r.add_mode else "BLACKLIST",
            "enable_rule": r.enable_rule,
            "enable_forward": getattr(r, 'enable_forward', True),
            "handle_mode": r.handle_mode.name if r.handle_mode else "FORWARD",
            "enable_comment_button": r.enable_comment_button,
            # AI
            "is_ai": r.is_ai,
            "ai_model": r.ai_model,
            "ai_prompt": r.ai_prompt,
            "enable_ai_upload_image": r.enable_ai_upload_image,
            "is_summary": r.is_summary,
            "summary_time": r.summary_time,
            "summary_prompt": r.summary_prompt,
            "is_keyword_after_ai": r.is_keyword_after_ai,
            "is_top_summary": r.is_top_summary,
            # 其他
            "enable_push": r.enable_push,
            "enable_only_push": r.enable_only_push,
            "only_rss": r.only_rss,
            "enable_sync": r.enable_sync
        }
    finally:
        db.close()


@router.put("/rules/{id}")
async def update_rule(id: int, data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        r = db.query(ForwardRule).filter(ForwardRule.id == id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Rule not found")

        # BUG-07: 枚举字段需要类型转换
        for field in [
            "forward_mode", "use_bot", "message_mode", "is_replace", "is_preview",
            "is_original_link", "is_delete_original", "is_original_sender",
            "userinfo_template", "time_template", "original_link_template",
            "is_original_time", "add_mode", "enable_rule", "handle_mode",
            "enable_comment_button", "is_ai", "ai_model", "ai_prompt",
            "enable_ai_upload_image", "is_summary", "summary_time", "summary_prompt",
            "is_keyword_after_ai", "is_top_summary", "enable_push", "enable_only_push",
            "only_rss", "enable_sync", "enable_forward"
        ]:
            if field in data:
                val = data[field]
                # 枚举字段需要从字符串转为枚举实例
                if field in ENUM_MAP and isinstance(val, str):
                    try:
                        val = ENUM_MAP[field](val)
                    except (ValueError, KeyError):
                        # 尝试按 name 匹配
                        try:
                            val = ENUM_MAP[field][val]
                        except KeyError:
                            raise HTTPException(
                                status_code=400,
                                detail=f"无效的 {field} 值: {val}"
                            )
                setattr(r, field, val)

        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rules/{id}")
async def delete_rule(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        r = db.query(ForwardRule).filter(ForwardRule.id == id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Rule not found")
        db.delete(r)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 3. 关键字管理 (含 DELETE)
# ══════════════════════════════════════════════════════════════════════
@router.get("/rules/{id}/keywords")
async def get_keywords(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        kw = db.query(Keyword).filter(Keyword.rule_id == id).all()
        return [{"id": k.id, "keyword": k.keyword, "is_regex": k.is_regex, "is_blacklist": k.is_blacklist} for k in kw]
    finally:
        db.close()


@router.post("/rules/{id}/keywords")
async def add_keyword(id: int, data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        k = Keyword(
            rule_id=id,
            keyword=data.get("keyword"),
            is_regex=data.get("is_regex", False),
            is_blacklist=data.get("is_blacklist", True)
        )
        db.add(k)
        db.commit()
        return {"status": "success", "id": k.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rules/{rule_id}/keywords/{kw_id}")
async def delete_keyword(rule_id: int, kw_id: int, user=Depends(get_current_user)):
    """删除单个关键字"""
    require_auth(user)
    db = get_session()
    try:
        k = db.query(Keyword).filter(Keyword.id == kw_id, Keyword.rule_id == rule_id).first()
        if not k:
            raise HTTPException(status_code=404, detail="Keyword not found")
        db.delete(k)
        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 4. 替换规则管理 (含 DELETE)
# ══════════════════════════════════════════════════════════════════════
@router.get("/rules/{id}/replace")
async def get_replace_rules(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        rr = db.query(ReplaceRule).filter(ReplaceRule.rule_id == id).all()
        return [{"id": r.id, "pattern": r.pattern, "content": r.content} for r in rr]
    finally:
        db.close()


@router.post("/rules/{id}/replace")
async def add_replace_rule(id: int, data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        r = ReplaceRule(
            rule_id=id,
            pattern=data.get("pattern"),
            content=data.get("content")
        )
        db.add(r)
        db.commit()
        return {"status": "success", "id": r.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rules/{rule_id}/replace/{rr_id}")
async def delete_replace_rule(rule_id: int, rr_id: int, user=Depends(get_current_user)):
    """删除单个替换规则"""
    require_auth(user)
    db = get_session()
    try:
        rr = db.query(ReplaceRule).filter(ReplaceRule.id == rr_id, ReplaceRule.rule_id == rule_id).first()
        if not rr:
            raise HTTPException(status_code=404, detail="Replace rule not found")
        db.delete(rr)
        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 5. 媒体类型过滤 (BUG-12: 补 rollback)
# ══════════════════════════════════════════════════════════════════════
@router.get("/rules/{id}/media-types")
async def get_media_types(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        mt = db.query(MediaTypes).filter(MediaTypes.rule_id == id).first()
        if not mt:
            return {"photo": False, "document": False, "video": False, "audio": False, "voice": False}
        return {
            "photo": mt.photo, "document": mt.document,
            "video": mt.video, "audio": mt.audio, "voice": mt.voice
        }
    finally:
        db.close()


@router.put("/rules/{id}/media-types")
async def update_media_types(id: int, data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        mt = db.query(MediaTypes).filter(MediaTypes.rule_id == id).first()
        if not mt:
            mt = MediaTypes(rule_id=id)
            db.add(mt)
        mt.photo = data.get("photo", False)
        mt.document = data.get("document", False)
        mt.video = data.get("video", False)
        mt.audio = data.get("audio", False)
        mt.voice = data.get("voice", False)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 6. 推送通道管理 (含 DELETE)
# ══════════════════════════════════════════════════════════════════════
@router.get("/rules/{id}/push-config")
async def get_push_config(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        pc = db.query(PushConfig).filter(PushConfig.rule_id == id).all()
        return [{
            "id": c.id,
            "enable_push_channel": c.enable_push_channel,
            "push_channel": c.push_channel,
            "media_send_mode": c.media_send_mode
        } for c in pc]
    finally:
        db.close()


@router.post("/rules/{id}/push-config")
async def create_push_config(id: int, data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        pc = PushConfig(
            rule_id=id,
            enable_push_channel=data.get("enable_push_channel", True),
            push_channel=data.get("push_channel"),
            media_send_mode=data.get("media_send_mode", "Single")
        )
        db.add(pc)
        db.commit()
        return {"status": "success", "id": pc.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rules/{rule_id}/push-config/{pc_id}")
async def delete_push_config(rule_id: int, pc_id: int, user=Depends(get_current_user)):
    """删除推送通道"""
    require_auth(user)
    db = get_session()
    try:
        pc = db.query(PushConfig).filter(PushConfig.id == pc_id, PushConfig.rule_id == rule_id).first()
        if not pc:
            raise HTTPException(status_code=404, detail="Push config not found")
        db.delete(pc)
        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 7. AI 调试沙盒接口
# ══════════════════════════════════════════════════════════════════════
@router.post("/sandbox/run-test")
async def run_sandbox_test(data: Dict, user=Depends(get_current_user)):
    """离线测试沙盒运行"""
    require_auth(user)
    prompt = data.get("prompt")
    model = data.get("model", "gemini-2.5-flash")
    test_message = data.get("test_message", "这是一条测试消息。")

    try:
        from ai import get_ai_provider
        provider = await get_ai_provider(model)
        result = await provider.process_message(test_message, prompt=prompt, model=model)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"AI 沙盒测试出错: {e}")
        return {"status": "error", "detail": str(e)}


# ══════════════════════════════════════════════════════════════════════
# 8. 定时总结历史归档 (BUG-06: 修复 or_() )
# ══════════════════════════════════════════════════════════════════════
@router.get("/summaries")
async def get_summaries(page: int = 1, limit: int = 10, search: Optional[str] = None,
                        user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        query = db.query(SummaryHistory)
        if search:
            # BUG-06: 使用 or_() 而非 Python | 运算符
            query = query.filter(or_(
                SummaryHistory.summary_text.like(f"%{search}%"),
                SummaryHistory.source_channel_name.like(f"%{search}%")
            ))

        total = query.count()
        offset = (page - 1) * limit
        summaries = query.order_by(SummaryHistory.created_at.desc()).offset(offset).limit(limit).all()

        result = []
        for s in summaries:
            result.append({
                "id": s.id,
                "rule_id": s.rule_id,
                "source_channel_name": s.source_channel_name,
                "summary_text": s.summary_text,
                "message_count": s.message_count,
                "time_range_start": s.time_range_start.strftime("%Y-%m-%d %H:%M:%S") if s.time_range_start else None,
                "time_range_end": s.time_range_end.strftime("%Y-%m-%d %H:%M:%S") if s.time_range_end else None,
                "ai_model": s.ai_model,
                "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else None
            })
        return {"total": total, "data": result}
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 9. RSS 配置 CRUD (从 rss.py 迁移为 JSON API)
# ══════════════════════════════════════════════════════════════════════
@router.get("/rss-configs")
async def get_rss_configs(user=Depends(get_current_user)):
    """获取所有 RSS 配置"""
    require_auth(user)
    db = get_session()
    try:
        configs = db.query(RSSConfig).options(joinedload(RSSConfig.rule)).all()
        result = []
        for c in configs:
            result.append({
                "id": c.id,
                "rule_id": c.rule_id,
                "enable_rss": c.enable_rss,
                "rule_title": c.rule_title,
                "rule_description": c.rule_description,
                "language": c.language,
                "max_items": c.max_items,
                "is_auto_title": c.is_auto_title,
                "is_auto_content": c.is_auto_content,
                "is_ai_extract": c.is_ai_extract,
                "ai_extract_prompt": c.ai_extract_prompt,
                "is_auto_markdown_to_html": c.is_auto_markdown_to_html,
                "enable_custom_title_pattern": c.enable_custom_title_pattern,
                "enable_custom_content_pattern": c.enable_custom_content_pattern,
                "source_chat_name": c.rule.source_chat.name if c.rule and c.rule.source_chat else None,
                "target_chat_name": c.rule.target_chat.name if c.rule and c.rule.target_chat else None,
            })
        return result
    finally:
        db.close()


@router.post("/rss-configs")
async def create_rss_config(data: Dict, user=Depends(get_current_user)):
    """创建 RSS 配置"""
    require_auth(user)
    db = get_session()
    try:
        rule_id = data.get("rule_id")
        if not rule_id:
            raise HTTPException(status_code=400, detail="rule_id 不能为空")

        # 检查是否已存在
        existing = db.query(RSSConfig).filter(RSSConfig.rule_id == rule_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="该规则已存在 RSS 配置")

        config = RSSConfig(
            rule_id=rule_id,
            enable_rss=data.get("enable_rss", True),
            rule_title=data.get("rule_title", ""),
            rule_description=data.get("rule_description", ""),
            language=data.get("language", "zh-CN"),
            max_items=data.get("max_items", 50),
            is_auto_title=data.get("is_auto_title", False),
            is_auto_content=data.get("is_auto_content", False),
            is_ai_extract=data.get("is_ai_extract", False),
            ai_extract_prompt=data.get("ai_extract_prompt", ""),
            is_auto_markdown_to_html=data.get("is_auto_markdown_to_html", False),
            enable_custom_title_pattern=data.get("enable_custom_title_pattern", False),
            enable_custom_content_pattern=data.get("enable_custom_content_pattern", False),
        )
        db.add(config)
        db.commit()
        db.refresh(config)
        return {"status": "success", "id": config.id, "rule_id": config.rule_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.put("/rss-configs/{config_id}")
async def update_rss_config(config_id: int, data: Dict, user=Depends(get_current_user)):
    """更新 RSS 配置"""
    require_auth(user)
    db = get_session()
    try:
        config = db.query(RSSConfig).filter(RSSConfig.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="RSS 配置不存在")

        for field in [
            "enable_rss", "rule_title", "rule_description", "language", "max_items",
            "is_auto_title", "is_auto_content", "is_ai_extract", "ai_extract_prompt",
            "is_auto_markdown_to_html", "enable_custom_title_pattern", "enable_custom_content_pattern"
        ]:
            if field in data:
                setattr(config, field, data[field])

        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rss-configs/{config_id}")
async def delete_rss_config(config_id: int, user=Depends(get_current_user)):
    """删除 RSS 配置（级联删除 patterns）"""
    require_auth(user)
    db = get_session()
    try:
        config = db.query(RSSConfig).filter(RSSConfig.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="RSS 配置不存在")
        db.delete(config)
        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.post("/rss-configs/{config_id}/toggle")
async def toggle_rss_config(config_id: int, user=Depends(get_current_user)):
    """切换 RSS 启用/禁用"""
    require_auth(user)
    db = get_session()
    try:
        config = db.query(RSSConfig).filter(RSSConfig.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="RSS 配置不存在")
        config.enable_rss = not config.enable_rss
        db.commit()
        return {"status": "success", "enable_rss": config.enable_rss}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 10. 正则模式 CRUD
# ══════════════════════════════════════════════════════════════════════
@router.get("/rss-configs/{config_id}/patterns")
async def get_patterns(config_id: int, user=Depends(get_current_user)):
    """获取指定 RSS 配置的所有正则模式"""
    require_auth(user)
    db = get_session()
    try:
        patterns = db.query(RSSPattern).filter(RSSPattern.rss_config_id == config_id).all()
        return [{
            "id": p.id,
            "pattern": p.pattern,
            "pattern_type": p.pattern_type,
            "priority": p.priority
        } for p in patterns]
    finally:
        db.close()


@router.post("/rss-configs/{config_id}/patterns")
async def create_pattern(config_id: int, data: Dict, user=Depends(get_current_user)):
    """创建正则模式"""
    require_auth(user)
    db = get_session()
    try:
        config = db.query(RSSConfig).filter(RSSConfig.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="RSS 配置不存在")

        pattern = RSSPattern(
            rss_config_id=config_id,
            pattern=data.get("pattern"),
            pattern_type=data.get("pattern_type", "title"),
            priority=data.get("priority", 0)
        )
        db.add(pattern)
        db.commit()
        db.refresh(pattern)
        return {"status": "success", "id": pattern.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rss-configs/{config_id}/patterns")
async def delete_all_patterns(config_id: int, user=Depends(get_current_user)):
    """删除配置的所有正则模式"""
    require_auth(user)
    db = get_session()
    try:
        count = db.query(RSSPattern).filter(RSSPattern.rss_config_id == config_id).delete()
        db.commit()
        return {"status": "success", "deleted_count": count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/rss-configs/patterns/{pattern_id}")
async def delete_pattern(pattern_id: int, user=Depends(get_current_user)):
    """删除单个正则模式"""
    require_auth(user)
    db = get_session()
    try:
        p = db.query(RSSPattern).filter(RSSPattern.id == pattern_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Pattern not found")
        db.delete(p)
        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 11. 正则表达式在线测试
# ══════════════════════════════════════════════════════════════════════
@router.post("/test-regex")
async def test_regex(data: Dict, user=Depends(get_current_user)):
    """测试正则表达式匹配"""
    require_auth(user)
    pattern_str = data.get("pattern", "")
    test_text = data.get("test_text", "")
    pattern_type = data.get("pattern_type", "title")

    if not pattern_str:
        return {"success": False, "message": "请输入正则表达式"}
    if not test_text:
        return {"success": False, "message": "请输入测试文本"}

    try:
        match = re.search(pattern_str, test_text)
        if not match:
            return {"success": True, "matched": False, "message": "未找到匹配"}
        if not match.groups():
            return {
                "success": True, "matched": True, "has_groups": False,
                "message": "匹配成功，但没有捕获组。请使用括号 () 来创建捕获组。"
            }
        return {
            "success": True, "matched": True, "has_groups": True,
            "extracted": match.group(1), "message": "匹配成功！"
        }
    except re.error as e:
        return {"success": False, "message": f"正则表达式语法错误: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"测试失败: {str(e)}"}


# ══════════════════════════════════════════════════════════════════════
# 11.5 AI 模型获取端点
# ══════════════════════════════════════════════════════════════════════
@router.get("/ai-models")
async def get_ai_models(user=Depends(get_current_user)):
    require_auth(user)
    from utils.settings import load_ai_models
    return load_ai_models(type="dict")


@router.get("/ai-default-model")
async def get_ai_default_model(user=Depends(get_current_user)):
    require_auth(user)
    from utils.constants import DEFAULT_AI_MODEL
    return {"default_model": DEFAULT_AI_MODEL}


# ══════════════════════════════════════════════════════════════════════
# 11.6 P3 规则同步与 AI 沙盒训练辅助端点
# ══════════════════════════════════════════════════════════════════════
@router.post("/rules/{id}/sync-to-all")
async def sync_rule_to_all(id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        source_rule = db.query(ForwardRule).filter(ForwardRule.id == id).first()
        if not source_rule:
            raise HTTPException(status_code=404, detail="Source rule not found")
        
        exclude_fields = {"id", "source_chat_id", "target_chat_id", "created_at"}
        update_data = {}
        for column in ForwardRule.__table__.columns:
            if column.name not in exclude_fields:
                update_data[column.name] = getattr(source_rule, column.name)
                
        db.query(ForwardRule).filter(ForwardRule.id != id).update(update_data)
        db.commit()
        return {"status": "success", "message": "已成功将此规则的配置复制到所有其他频道规则"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.post("/sandbox/upload-json")
async def upload_sandbox_json(file: UploadFile = File(...), user=Depends(get_current_user)):
    require_auth(user)
    import os
    os.makedirs("scratch", exist_ok=True)
    file_path = "scratch/messages_dump.json"
    
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="只允许上传 .json 格式文件。")
        
    try:
        contents = await file.read()
        import json
        try:
            json.loads(contents.decode("utf-8"))
        except Exception as json_err:
            raise HTTPException(status_code=400, detail=f"上传的文件不是合法的 JSON 格式: {str(json_err)}")
            
        with open(file_path, "wb") as f:
            f.write(contents)
        return {"status": "success", "message": "本地 JSON 数据导入成功。"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传沙盒数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")


@router.get("/sandbox/history-samples")
async def get_history_samples(user=Depends(get_current_user)):
    """从数据库获取频道消息统计，替代旧版 JSON 文件读取"""
    require_auth(user)
    from sqlalchemy import func
    from models.models import ForwardedMessage
    db = get_session()
    try:
        # 按 source_chat_id 分组统计
        stats = db.query(
            ForwardedMessage.source_chat_id,
            ForwardedMessage.source_chat_name,
            func.count(ForwardedMessage.id).label('msg_count')
        ).filter(
            ForwardedMessage.message_text.isnot(None)
        ).group_by(
            ForwardedMessage.source_chat_id,
            ForwardedMessage.source_chat_name
        ).all()

        if not stats:
            return {"status": "error", "message": "数据库中暂无消息数据。请先运行 backfill_messages.py 回填历史数据。"}

        result = []
        for source_id, source_name, count in stats:
            # 取最近 10 条作为样本
            samples_q = db.query(ForwardedMessage).filter(
                ForwardedMessage.source_chat_id == source_id,
                ForwardedMessage.message_text.isnot(None)
            ).order_by(ForwardedMessage.message_date.desc()).limit(10).all()

            samples = [
                {"id": m.telegram_message_id, "date": m.message_date.isoformat() if m.message_date else "", "summary": (m.message_text or "")[:40]}
                for m in samples_q
            ]
            result.append({
                "channel_link": source_id,
                "channel_name": source_name or source_id,
                "message_count": count,
                "samples": samples
            })

        # 按消息数量降序排列
        result.sort(key=lambda x: x["message_count"], reverse=True)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取频道消息统计失败: {e}")
        return {"status": "error", "message": f"查询数据库失败: {str(e)}"}
    finally:
        db.close()


@router.post("/sandbox/aggregate-messages")
async def aggregate_messages(data: Dict, user=Depends(get_current_user)):
    """从数据库聚合消息，替代旧版 JSON 文件读取"""
    require_auth(user)
    from datetime import datetime, timezone, timedelta
    from models.models import ForwardedMessage

    channels = data.get("channels", [])
    days = int(data.get("days", 1))

    db = get_session()
    try:
        time_limit = datetime.now(timezone.utc) - timedelta(days=days)

        # 查询指定频道、指定时间范围内的消息
        messages = db.query(ForwardedMessage).filter(
            ForwardedMessage.source_chat_id.in_(channels),
            ForwardedMessage.message_date >= time_limit,
            ForwardedMessage.message_text.isnot(None)
        ).order_by(
            ForwardedMessage.source_chat_id,
            ForwardedMessage.message_date.desc()
        ).all()

        if not messages:
            return {"status": "success", "text": f"在过去 {days} 天内未找到所选频道的消息。"}

        # 按频道分组
        from collections import defaultdict
        grouped = defaultdict(list)
        for m in messages:
            grouped[m.source_chat_id].append(m)

        aggregated_texts = []
        for ch_id, msgs in grouped.items():
            ch_name = msgs[0].source_chat_name or ch_id
            aggregated_texts.append(f"=== 频道: {ch_name} (共 {len(msgs)} 条消息) ===")
            for m in msgs:
                date_str = m.message_date.isoformat() if m.message_date else ""
                aggregated_texts.append(f"[{date_str}] ID {m.telegram_message_id}: {m.message_text}")
            aggregated_texts.append("\n")

        combined_text = "\n".join(aggregated_texts)
        return {"status": "success", "text": combined_text}
    except Exception as e:
        logger.error(f"聚合消息失败: {e}")
        return {"status": "error", "message": f"聚合消息失败: {str(e)}"}
    finally:
        db.close()


@router.post("/sandbox/analyze-channels")
async def analyze_channels(data: Dict, user=Depends(get_current_user)):
    require_auth(user)
    model = data.get("model", "gpt-5.5")
    
    import json
    import os
    file_path = "scratch/messages_dump.json"
    if not os.path.exists(file_path):
        return {"status": "error", "message": "未找到抓取的数据文件。"}
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            dump_data = json.load(f)
            
        channel_summaries = []
        for link, info in list(dump_data.items())[:50]:
            ch_name = info.get("channel_name", link)
            msgs = [m.get("text", "") for m in info.get("messages", []) if m.get("text")]
            sample_txt = " | ".join(msgs[:3])[:150]
            channel_summaries.append(f"- 频道: {ch_name}\n  近期消息抽样: {sample_txt}")
            
        prompt = (
            "你是一个极其专业的 Telegram 频道聚合分析专家与 AI 提示词工程专家。\n"
            "下面是用户订阅的约 50 个频道的列表及近期内容抽样。请为用户生成一份「50 频道内容调性与定时总结 Prompt 优化诊断报告」。\n\n"
            "要求：\n"
            "1. 归纳这些频道的内容分布类型（例如 DeFi 呼叫、Airdrop 空投、宏观资讯等）及估算占比分布。\n"
            "2. 指出这些频道的噪声点和信息冗余特征。\n"
            "3. 针对以上特征，为用户自动训练并生成一套最专业的『定时消息总结提示词 System Prompt』。这套提示词应该具有超强的去噪、去广告能力，并能清晰地按照版块、项目重要程度对 24 小时内的聚合消息流进行条理总结。\n"
            "4. 以精美的 Markdown 格式输出整篇报告，不要使用 JSON 包装结果，直接输出 Markdown 报告文本。"
        )
        
        test_message = "\n\n".join(channel_summaries)
        
        from ai import get_ai_provider
        provider = await get_ai_provider(model)
        result = await provider.process_message(test_message, prompt=prompt, model=model)
        
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"分析 50 频道数据出错: {e}")
        return {"status": "error", "message": f"分析失败: {str(e)}"}


# ══════════════════════════════════════════════════════════════════════
# 12. WebSocket 实时日志流推送
# ══════════════════════════════════════════════════════════════════════
@router.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        async for log_line in log_generator():
            await websocket.send_text(log_line)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket 连接异常: {e}")
        manager.disconnect(websocket)
