"""P20 console reads: source catalog, paginated originals and persisted publications."""
import os
from datetime import datetime
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StrictBool
from sqlalchemy.orm import load_only
from .auth import get_current_user
from .console import require_auth
from models.models import (get_session, Chat, BusinessSource, CollectedMessage, AlphaProcessing,
                           AIProcessing, AlphaEvent, AIItem, AlphaPublication, AIPublication,
                           TelegramFolder, TelegramFolderSource)
from source_catalog import (BUSINESSES, catalog_rows, catalog_snapshot, ensure_catalog, set_subscription,
                            normalize_times)

router = APIRouter(prefix='/api')


@router.get('/businesses')
def businesses(user=Depends(get_current_user)):
    require_auth(user)
    return BUSINESSES


def subscription_signature(db):
    return {'subscriptions': sorted((s.business, s.source_id, s.active) for s in db.query(BusinessSource)),
            'chats': sorted((c.id, c.name, c.peer_id, c.member_state) for c in db.query(Chat)),
            'metadata': catalog_snapshot(db)}


def serialize(row):
    return normalize_times({c.name: getattr(row, c.name) for c in row.__table__.columns})


def paged(query, page, page_size):
    return {'items': [serialize(r) for r in query.offset((page - 1) * page_size).limit(page_size)],
            'total': query.count(), 'page': page, 'page_size': page_size}


@router.get('/sources')
def sources(q: str = '', folder: int | None = None, business: str = '', member_state: str = '', collection_status: str = '',
            page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100), user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        rows = catalog_rows(db)
        db.commit()
        rows = [r for r in rows if (not q or q.casefold() in ' '.join(str(r[k] or '') for k in ('name', 'peer_id', 'username', 'telegram_chat_id')).casefold())
                and (folder is None or any(f['id'] == folder for f in r['folders']))
                and (not business or r['subscriptions'].get(business, {}).get('active'))
                and (not member_state or r['member_state'] == member_state)]
        if collection_status:
            rows = [r for r in rows if (
                collection_status == 'error' and r['collection']['error'] or
                collection_status == 'enabled' and r['collection']['enabled'] or
                collection_status == 'disabled' and not r['collection']['enabled'] or
                collection_status == 'received' and r['latest']['received_at'] or
                collection_status == 'unknown' and r['collection']['enabled'] and not r['latest']['received_at'])]
        return {'items': rows[(page-1)*page_size:page*page_size], 'total': len(rows), 'page': page,
                'page_size': page_size, 'businesses': BUSINESSES,
                'folders': [serialize(f) for f in db.query(TelegramFolder).order_by(TelegramFolder.id)]}
    finally:
        db.close()


class Subscription(BaseModel):
    model_config = {'extra': 'forbid'}
    active: StrictBool


@router.put('/sources/{chat_id}/subscriptions/{business}')
def subscription(chat_id: int, business: str, data: Subscription, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        ensure_catalog(db)
        chat = db.get(Chat, chat_id)
        if not chat:
            raise HTTPException(404, '来源不存在')
        try:
            result = set_subscription(db, chat.peer_id, business, data.active)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.commit()
        return result
    finally:
        db.close()


async def sync_bridge(method):
    port = int(os.getenv('INTERNAL_BRIDGE_PORT', '8001'))
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.request(method, f'http://127.0.0.1:{port}/sources-sync', timeout=10)
        if response.status_code != 200:
            raise HTTPException(response.status_code, response.json().get('message', '同步不可用'))
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, '主进程不可用，未启动新的TG会话') from exc


@router.post('/sources/sync/preview')
async def start_sync(user=Depends(get_current_user)):
    require_auth(user)
    return await sync_bridge('POST')


@router.get('/sources/sync/preview')
async def sync_status(user=Depends(get_current_user)):
    require_auth(user)
    return await sync_bridge('GET')


class ApplySync(BaseModel):
    model_config = {'extra': 'forbid'}
    token: str


@router.post('/sources/sync/apply')
async def apply_sync(data: ApplySync, user=Depends(get_current_user)):
    require_auth(user)
    preview = await sync_bridge('GET')
    if preview.get('status') != 'ready' or preview.get('token') != data.token:
        raise HTTPException(409, '同步预览已失效，请重新核对')
    db = get_session()
    try:
        ensure_catalog(db)
        if normalize_times(subscription_signature(db)) != preview['baseline']:
            raise HTTPException(409, '预览后目录或订阅发生变化，请重新核对')
        result = preview['result']
        now = datetime.utcnow()
        for row in result['sources']:
            chat = db.query(Chat).filter_by(peer_id=row['peer_id']).first()
            if not chat and row.get('entity_id'):
                chat = db.query(Chat).filter(Chat.peer_id.is_(None), Chat.telegram_chat_id == row['entity_id']).first()
                if chat:
                    chat.peer_id = row['peer_id']
            if not chat:
                chat = Chat(telegram_chat_id=row['peer_id'], peer_id=row['peer_id'], name=row['telegram_title'])
                db.add(chat)
            for field in ('peer_type', 'telegram_title', 'username'):
                setattr(chat, field, row[field])
            if row['member_state'] != 'unknown':
                chat.member_state, chat.verified_at = row['member_state'], now
            if row['member_state'] == 'left':
                for sub in db.query(BusinessSource).filter_by(source_id=row['peer_id']):
                    sub.active = False
        if result['folders_complete']:
            db.query(TelegramFolderSource).delete()
            db.query(TelegramFolder).delete()
            for folder in result['folders']:
                db.add(TelegramFolder(id=folder['id'], title=folder['title'], complete=folder['complete']))
            db.flush()
            for folder in result['folders']:
                db.add_all([TelegramFolderSource(folder_id=folder['id'], peer_id=p) for p in folder['peers']])
        db.flush()
        from collection import sync_sources
        sync_sources(db)
        db.commit()
        return {'status': 'success', 'sources': len(result['sources']), 'errors': result['errors']}
    finally:
        db.close()


@router.get('/collected-messages')
def originals(source: str = '', business: str = '', status: str = '', q: str = '',
              after: datetime | None = None, until: datetime | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(get_current_user)):
    require_auth(user)
    if (status and not business) or business not in {'', 'alpha', 'ai'}:
        raise HTTPException(400, '处理状态需指定有效业务')
    db = get_session()
    try:
        query = db.query(CollectedMessage)
        if source:
            query = query.filter_by(source_id=source)
        if business:
            cls = AlphaProcessing if business == 'alpha' else AIProcessing
            query = query.join(cls)
            if status:
                query = query.filter((cls.analysis_status if business == 'alpha' else cls.status) == status)
        if q:
            query = query.filter(CollectedMessage.body.ilike('%' + q + '%'))
        from collection import utc
        if after:
            query = query.filter(CollectedMessage.published_at >= utc(after))
        if until:
            query = query.filter(CollectedMessage.published_at < utc(until))
        result = paged(query.order_by(CollectedMessage.published_at.desc(), CollectedMessage.id.desc()), page, page_size)
        for row in result['items']:
            row['business_states'] = {b: serialize(s) if (s := db.get(cls, row['id'])) else None
                                      for b, cls in [('alpha', AlphaProcessing), ('ai', AIProcessing)]}
        return result
    finally:
        db.close()


@router.get('/collected-messages/{message_id}')
def original_detail(message_id: int, user=Depends(get_current_user)):
    require_auth(user)
    db = get_session()
    try:
        row = db.get(CollectedMessage, message_id)
        if not row:
            raise HTTPException(404, '原文不存在')
        result = serialize(row)
        result['business_states'] = {b: serialize(state) if (state := db.get(cls, message_id)) else None
                                     for b, cls in [('alpha', AlphaProcessing), ('ai', AIProcessing)]}
        return result
    finally:
        db.close()


def business_classes(business):
    if business == 'alpha':
        from alpha.storage import job
        return AlphaEvent, AlphaPublication, job
    if business == 'ai':
        from ai_digest.pipeline import job
        return AIItem, AIPublication, job
    raise HTTPException(404, '未知业务')


@router.get('/business/{business}/status')
def business_status(business: str, user=Depends(get_current_user)):
    require_auth(user)
    _, _, job = business_classes(business)
    db = get_session()
    try:
        config = job(db)
        result = serialize(config)
        result['source_count'] = db.query(BusinessSource).filter_by(business=business, active=True).count()
        db.commit()
        return result
    finally:
        db.close()


@router.get('/business/{business}/items')
def business_items(business: str, status: str = '', category: str = '', tier: str = '', publication_status: str = '',
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(get_current_user)):
    require_auth(user)
    cls, publication, _ = business_classes(business)
    db = get_session()
    try:
        query = db.query(cls)
        if publication_status:
            relation = publication.event_id if business == 'alpha' else publication.item_id
            query = query.filter(cls.id.in_(db.query(relation).filter(publication.status == publication_status)))
        if business == 'alpha':
            from alpha.storage import event_tier, valid_sources
            now = datetime.utcnow()
            if tier:
                ids = [ev.id for ev in db.query(cls).options(load_only(
                    cls.id, cls.sources, cls.closed, cls.deadline)) if event_tier(ev, now) == tier]
                query = query.filter(cls.id.in_(ids))
            if status:
                query = query.filter(cls.draft_status == status)
            query = query.order_by(cls.first_seen, cls.id)
        else:
            if status:
                query = query.filter(cls.status == status)
            if category:
                query = query.filter(cls.category == category)
            query = query.order_by(cls.created_at.desc(), cls.id.desc())
        result = paged(query, page, page_size)
        relation = publication.event_id if business == 'alpha' else publication.item_id
        publications = {}
        for sent in db.query(publication).filter(relation.in_([r['id'] for r in result['items']])):
            publications.setdefault(getattr(sent, 'event_id' if business == 'alpha' else 'item_id'), []).append(
                {'id': sent.id, 'target_id': sent.target_id, 'status': sent.status})
        for row in result['items']:
            row['publication_summaries'] = publications.get(row['id'], [])
            row.pop('sources', None)
            if business == 'alpha':
                event = db.get(cls, row['id'])
                row['type_status'] = (event.editorial or {}).get('status')
                row['detail_level'] = (event.editorial or {}).get('detail_level')
                row['tier'] = event_tier(event, now)
                row['source_count'] = len({s['source_id'] for s in valid_sources(event, now)})
            row.pop('draft_zh', None)
            row.pop('output_zh', None)
        return result
    finally:
        db.close()


@router.get('/business/{business}/items/{item_id}')
def business_detail(business: str, item_id: int, user=Depends(get_current_user)):
    require_auth(user)
    cls, publication, _ = business_classes(business)
    db = get_session()
    try:
        item = db.get(cls, item_id)
        if not item:
            raise HTTPException(404, '内容不存在')
        result = serialize(item)
        if business == 'alpha':
            result['type_status'] = (item.editorial or {}).get('status')
            result['detail_level'] = (item.editorial or {}).get('detail_level')
            from alpha.storage import event_tier
            from alpha.public_copy import has_process_note
            if item.draft_status != 'ready' or event_tier(item, datetime.utcnow()) not in {'main', 'closed'} or has_process_note(item.draft_zh):
                result['draft_zh'] = None
        key = publication.event_id if business == 'alpha' else publication.item_id
        result['publications'] = [serialize(p) for p in db.query(publication).filter(key == item_id).order_by(publication.id)]
        result['originals'] = [serialize(raw) for ref in item.sources or []
                               if (raw := db.get(CollectedMessage, ref['id']))]
        return result
    finally:
        db.close()
