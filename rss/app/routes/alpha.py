import os
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, StrictBool, StrictInt, field_validator
from .auth import get_current_user
from .console import require_auth
from models.models import get_session
from alpha.storage import job, snapshot, export_markdown, sync_sources
from alpha.scheduler import latest_slot, TZ

router = APIRouter(prefix='/api/alpha')


class Config(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool | None = None
    publish_enabled: StrictBool | None = None
    schedule_hours: list[StrictInt] | None = None

    @field_validator('schedule_hours')
    @classmethod
    def validate_hours(cls, value):
        if value is not None and (not value or any(h < 0 or h > 23 for h in value) or len(set(value)) != len(value)):
            raise ValueError('时刻必须为不重复的0–23整数小时')
        return sorted(value) if value is not None else None
    model: str | None = None


@router.get('/status')
def status(user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        sync_sources(db)
        db.commit()
    finally:
        db.close()
    return snapshot()


@router.put('/config')
def configure(data: Config, user=Depends(get_current_user)):
    require_auth(user)
    from utils.settings import load_ai_models
    db = get_session()
    try:
        config = job(db)
        changes = data.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(400, '配置字段不能为null')
        enabled = changes.get('enabled', config.enabled)
        model = changes.get('model', config.model)
        if enabled and not model:
            raise HTTPException(400, '启用任务前请选择模型')
        if model and model not in load_ai_models() and (model != config.model or enabled and not config.enabled):
            raise HTTPException(400, '请选择已配置模型')
        hours = changes.get('schedule_hours', config.schedule_hours)
        if (enabled and not config.enabled) or hours != config.schedule_hours:
            # Enabling is not an immediate AI call; first scheduled time is the next slot.
            config.last_slot = latest_slot(datetime.now(TZ), hours)
        for field, value in changes.items():
            setattr(config, field, value)
        db.commit()
        return {'status': 'success', 'publish': 'enabled' if config.publish_enabled else 'none'}
    finally:
        db.close()


@router.post('/run')
async def run(user=Depends(get_current_user)):
    require_auth(user)
    port = int(os.getenv('INTERNAL_BRIDGE_PORT', '8001'))
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            result = await client.post(f'http://127.0.0.1:{port}/alpha-run', timeout=10)
        if result.status_code != 200:
            raise HTTPException(result.status_code, result.json().get('message', '任务未启动'))
        return result.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, '主进程不可用，机会任务未启动') from exc


@router.get('/export')
def export(internal: bool = False, user=Depends(get_current_user)):
    require_auth(user)
    name = 'alpha-internal.md' if internal else 'alpha-drafts.md'
    return Response(export_markdown(internal=internal), media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})
