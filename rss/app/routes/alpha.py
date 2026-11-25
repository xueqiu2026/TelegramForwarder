import os
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, StrictBool
from .auth import get_current_user
from .console import require_auth
from models.models import get_session
from alpha.storage import job, snapshot, export_markdown, sync_sources
from alpha.scheduler import latest_slot, TZ

router = APIRouter(prefix='/api/alpha')


class Config(BaseModel):
    enabled: StrictBool
    model: str


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
    if data.enabled and not data.model:
        raise HTTPException(400, '启用任务前请选择模型')
    db = get_session()
    try:
        config = job(db)
        if data.model and data.model not in load_ai_models() and (data.enabled or data.model != config.model):
            raise HTTPException(400, '请选择已配置模型')
        if data.enabled and not config.enabled:
            # Enabling is not an immediate AI call; first scheduled time is the next slot.
            config.last_slot = latest_slot(datetime.now(TZ))
        config.enabled, config.model = data.enabled, data.model
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
