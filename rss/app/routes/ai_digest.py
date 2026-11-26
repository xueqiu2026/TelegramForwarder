import json
import os
import re
from datetime import datetime
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, StrictBool, StrictInt, field_validator
from .auth import get_current_user
from .console import require_auth
from models.models import (get_session, BusinessSource, CollectedMessage, AIProcessing, AIItem, AIPublication)
from collection import EXCLUDED_SOURCES
from alpha.storage import source_id
from alpha.scheduler import latest_slot, TZ
from ai_digest.pipeline import job

router = APIRouter(prefix='/api/ai-digest')


class Config(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool | None = None
    schedule_hours: list[StrictInt] | None = None

    @field_validator('schedule_hours')
    @classmethod
    def validate_hours(cls, value):
        if value is not None and (not value or any(h < 0 or h > 23 for h in value) or len(set(value)) != len(value)):
            raise ValueError('时刻必须为不重复的0–23整数小时')
        return sorted(value) if value is not None else None
    model: str | None = None
    publish_enabled: StrictBool | None = None
    target_id: str | None = None


class Subscription(BaseModel):
    source_id: str
    name: str = ''
    active: StrictBool = True


@router.get('/status')
def status(user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        config = job(db)
        from source_catalog import source_names
        names = source_names(db)
        data = {'enabled': config.enabled, 'model': config.model or '', 'publish_enabled': config.publish_enabled,
                'target_id': config.target_id, 'status': config.status, 'error': config.error,
                'last_run': config.last_run, 'analysis_until': config.analysis_until, 'result': config.result,
                'schedule_hours': config.schedule_hours, 'times': [f'{h:02d}:00' for h in config.schedule_hours], 'sources': [], 'messages': [], 'items': [], 'publications': []}
        for sub in db.query(BusinessSource).filter_by(business='ai'):
            data['sources'].append({'id': sub.source_id, 'name': names.get(sub.source_id, sub.name), 'active': sub.active})
        for raw, processed in db.query(CollectedMessage, AIProcessing).join(AIProcessing).order_by(CollectedMessage.published_at.desc()):
            data['messages'].append({'id': raw.id, 'source': raw.source_name, 'date': raw.published_at,
                'body': raw.body, 'entities': raw.entities, 'url': raw.source_url, 'revision': raw.revision,
                'status': processed.status, 'recall': processed.recall, 'result': processed.result,
                'error': processed.error, 'version': processed.version})
        for item in db.query(AIItem).order_by(AIItem.created_at.desc()):
            data['items'].append({'id': item.id, 'subject': item.subject, 'goal': item.goal,
                'category': item.category, 'conditions': item.conditions, 'status': item.status,
                'sources': item.sources, 'output_zh': item.output_zh, 'error': item.error, 'dedup': item.dedup})
        for sent in db.query(AIPublication):
            data['publications'].append({'item_id': sent.item_id, 'target_id': sent.target_id,
                'status': sent.status, 'parts': sent.parts, 'sources': sent.sources, 'error': sent.error})
        db.commit()
        return data
    finally:
        db.close()


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
        publishing = changes.get('publish_enabled', config.publish_enabled)
        model = changes.get('model', config.model)
        activating = (enabled and not config.enabled) or (publishing and not config.publish_enabled)
        if (enabled or publishing) and model not in load_ai_models() and (activating or model != config.model):
            raise HTTPException(400, '请选择已配置模型')
        if changes.get('target_id', config.target_id) != '@wanshenmeAI':
            raise HTTPException(400, '本阶段AI目标为@wanshenmeAI')
        hours = changes.get('schedule_hours', config.schedule_hours)
        if (enabled and not config.enabled) or hours != config.schedule_hours:
            config.last_slot = latest_slot(datetime.now(TZ), hours)
        for field, value in changes.items():
            setattr(config, field, value)
        db.commit()
        return {'status': 'success'}
    finally:
        db.close()


@router.put('/sources')
def subscribe(data: Subscription, user=Depends(get_current_user)):
    require_auth(user)
    if not re.fullmatch(r'-?[0-9]+', data.source_id) or int(data.source_id) == 0:
        raise HTTPException(400, '填写TG数字频道ID')
    sid = source_id(data.source_id)
    if sid in EXCLUDED_SOURCES:
        raise HTTPException(400, '该来源已明确停用')
    db = get_session()
    try:
        from source_catalog import set_subscription
        try:
            set_subscription(db, sid, 'ai', data.active)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.commit()
        return {'status': 'success'}
    finally:
        db.close()


@router.post('/run')
async def run(user=Depends(get_current_user)):
    require_auth(user)
    port = int(os.getenv('INTERNAL_BRIDGE_PORT', '8001'))
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            result = await client.post(f'http://127.0.0.1:{port}/ai-digest-run', timeout=10)
        if result.status_code != 200:
            raise HTTPException(result.status_code, result.json().get('message', '任务未启动'))
        return result.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, '主进程不可用，AI任务未启动') from exc


@router.get('/export')
def export(user=Depends(get_current_user)):
    data = status(user)
    return Response(json.dumps(data, ensure_ascii=False, default=str, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="ai-digest.json"'})
