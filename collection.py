"""Shared original ingestion. Business analysis state belongs to each consumer."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from models.models import (get_session, Chat, ForwardRule, CollectedMessage, AlphaCollectionState,
                           BusinessSource, BusinessMigration, AlphaProcessing, AIProcessing, AlphaEvent, AIItem)

EXCLUDED_SOURCES = {'-1001861267084'}


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def source_id(value):
    value = str(value)
    if value.startswith('-'):
        return value
    return '-' + value if value.startswith('100') and len(value) > 10 else '-100' + value


def source_variants(value):
    value = source_id(value)
    return {value, value.lstrip('-'), value[4:] if value.startswith('-100') else value.lstrip('-')}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()




def business_sources(db, business, enabled_only=True):
    query = db.query(BusinessSource).filter_by(business=business)
    if enabled_only:
        query = query.filter_by(active=True)
    return {s.source_id for s in query} - EXCLUDED_SOURCES


def sync_sources(db, now=None):
    now = now or datetime.utcnow()
    # One-time import freezes existing Alpha membership before AI is enrolled.
    if db.get(BusinessMigration, 'sources-v1') is None:
        rules = db.query(ForwardRule, Chat).join(Chat, ForwardRule.source_chat_id == Chat.id).filter(
            ForwardRule.enable_rule.is_(True), ForwardRule.enable_collect.is_(True)).all()
        for _, chat in rules:
            sid = source_id(chat.telegram_chat_id)
            if sid not in EXCLUDED_SOURCES and db.get(BusinessSource, ('alpha', sid)) is None:
                old = db.get(AlphaCollectionState, sid)
                db.add(BusinessSource(business='alpha', source_id=sid, name=chat.name,
                                      enabled_at=old.enabled_at if old else now))
        db.add(BusinessMigration(name='sources-v1'))
        db.flush()
    wanted = business_sources(db, 'alpha') | business_sources(db, 'ai')
    for state in db.query(AlphaCollectionState):
        state.active = state.source_id in wanted
    for sid in wanted:
        if db.get(AlphaCollectionState, sid) is None:
            enrollments = db.query(BusinessSource).filter_by(source_id=sid, active=True).all()
            db.add(AlphaCollectionState(source_id=sid, enabled_at=min(s.enabled_at for s in enrollments),
                                       active=True, scanned_id=0))
    db.flush()
    return wanted


def detach_evidence(db, message_id, business='alpha'):
    for event in db.query(AlphaEvent if business == 'alpha' else AIItem):
        kept = [s for s in (event.sources or []) if s['id'] != message_id]
        if len(kept) != len(event.sources or []):
            event.sources = kept
            if business == 'alpha':
                event.draft_status, event.draft_zh, event.draft_hash = 'pending', None, None
            else:
                event.output_zh, event.output_hash, event.status = None, None, 'needs_info'


def enroll_processing(db, row):
    for business, cls in [('alpha', AlphaProcessing), ('ai', AIProcessing)]:
        subscription = db.get(BusinessSource, (business, row.source_id))
        if subscription and subscription.active and row.published_at >= subscription.enabled_at:
            state = db.get(cls, row.id)
            if state is None:
                state = cls(message_id=row.id, revision=row.revision)
                db.add(state)
            if business == 'alpha':
                row.alpha = state
    db.flush()


def save_message(db, sid, name, username, message):
    entities = [{'type': type(e).__name__, 'offset': e.offset, 'length': e.length,
                 'url': getattr(e, 'url', None)} for e in message.entities or []]
    obj = getattr(message, 'photo', None) or getattr(message, 'document', None)
    media = {'type': type(message.media).__name__, 'id': str(getattr(obj, 'id', ''))} if message.media else {}
    body = message.message or ''
    grouped = str(message.grouped_id) if message.grouped_id else None
    content_hash = digest([body, entities, media, grouped, message.reply_to_msg_id])
    row = db.query(CollectedMessage).filter_by(source_id=sid, message_id=message.id).first()
    if row and row.content_hash == content_hash:
        enroll_processing(db, row)
        return row
    if row:
        for business, cls in [('alpha', AlphaProcessing), ('ai', AIProcessing)]:
            detach_evidence(db, row.id, business)
            state = db.get(cls, row.id)
            if state:
                state.revision = row.revision + 1
                if business == 'alpha':
                    state.analysis_status, state.analysis, state.error = 'pending', None, None
                else:
                    state.status, state.result, state.error = 'pending', None, None
        row.revision += 1
    else:
        row = CollectedMessage(source_id=sid, message_id=message.id, received_at=datetime.utcnow())
        db.add(row)
    row.source_name, row.username = name, username or ''
    row.published_at = utc(message.date)
    row.edited_at = utc(message.edit_date) if message.edit_date else None
    row.body, row.entities, row.media = body, entities, media
    row.grouped_id, row.reply_id = grouped, message.reply_to_msg_id
    row.source_url = (f'https://t.me/{username}/{message.id}' if username else
                      f'https://t.me/c/{sid[4:]}/{message.id}' if sid.startswith('-100') else '')
    row.content_hash = content_hash
    db.flush()
    enroll_processing(db, row)
    return row


async def collect_event(event):
    from telethon import utils
    entity = await event.get_chat()
    if (getattr(entity, 'username', '') or '').casefold() == 'korean_alpha':
        return
    sid = str(utils.get_peer_id(entity))
    if sid in EXCLUDED_SOURCES:
        return
    db = get_session()
    try:
        sync_sources(db)
        from source_catalog import source_blocked
        if source_blocked(db, sid):
            return
        state = db.get(AlphaCollectionState, sid)
        if state and state.active and utc(event.message.date) >= state.enabled_at:
            save_message(db, sid, getattr(entity, 'title', ''), getattr(entity, 'username', ''), event.message)
        db.commit()
    except IntegrityError:
        # A live update and history recovery may reach the same unique source ID together.
        db.rollback()
        state = db.get(AlphaCollectionState, sid)
        if state and state.active:
            save_message(db, sid, getattr(entity, 'title', ''), getattr(entity, 'username', ''), event.message)
            db.commit()
    finally:
        db.close()


async def _recover_history(client):
    """Paginate to a captured boundary. Failed pages never advance confirmed progress."""
    db = get_session()
    try:
        sync_sources(db)
        db.commit()
        from source_catalog import source_blocked
        sources = [s.source_id for s in db.query(AlphaCollectionState).filter_by(active=True)
                   if not source_blocked(db, s.source_id)]
    finally:
        db.close()
    failures = {}
    for sid in sources:
        db = get_session()
        try:
            state = db.get(AlphaCollectionState, sid)
            entity = await client.get_entity(int(sid))
            if (getattr(entity, 'username', '') or '').casefold() == 'korean_alpha':
                state.active = False
                db.commit()
                continue
            newest = await client.get_messages(entity, limit=1)
            if not newest:
                continue
            boundary = newest[0].id
            # Establish the initial lower bound without replaying months of old messages.
            if not state.scanned_id:
                prior = await client.get_messages(entity, limit=1,
                                                 offset_date=state.enabled_at.replace(tzinfo=timezone.utc))
                state.scanned_id = prior[0].id if prior else 0
                db.commit()
            count = 0
            async for message in client.iter_messages(entity, min_id=state.scanned_id,
                                                     max_id=boundary + 1, reverse=True):
                if utc(message.date) >= state.enabled_at and hasattr(message, 'message'):
                    save_message(db, sid, getattr(entity, 'title', ''), getattr(entity, 'username', ''), message)
                state.scanned_id = message.id
                count += 1
                if count % 100 == 0:
                    db.commit()
            state.scanned_id, state.scanned_at, state.error = boundary, datetime.utcnow(), None
            db.commit()
        except Exception as exc:
            db.rollback()
            state = db.get(AlphaCollectionState, sid)
            state.error = type(exc).__name__ + ': ' + str(exc)
            failures[sid] = state.error
            db.commit()
        finally:
            db.close()
    return failures



_recovery_lock = asyncio.Lock()


async def recover_history(client, slot=None):
    # Both business jobs at a slot share the completed recovery result.
    async with _recovery_lock:
        db = get_session()
        try:
            signature = tuple(sorted((s.business, s.source_id, s.active) for s in db.query(BusinessSource)))
        finally:
            db.close()
        if slot is not None and getattr(client, '_collection_slot', None) == (slot, signature):
            return getattr(client, '_collection_result', {})
        result = await _recover_history(client)
        if slot is not None:
            client._collection_slot, client._collection_result = (slot, signature), result
        return result
