import hashlib
import json
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import IntegrityError
from alpha.public_copy import has_process_note
from models.models import (get_session, Chat, ForwardRule, CollectedMessage,
                           AlphaCollectionState, AlphaEvent, AlphaJob)


WINDOW = timedelta(hours=36)
EXCLUDED_SOURCES = {'-1001861267084'}  # User explicitly retired @korean_alpha.


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


def job(db):
    row = db.get(AlphaJob, 1)
    if row is None:
        row = AlphaJob(id=1, enabled=False)
        db.add(row)
        db.flush()
    return row


def sync_sources(db, now=None):
    """Remember enrollment even after collection is disabled: never resume legacy double writes."""
    now = now or datetime.utcnow()
    rules = db.query(ForwardRule, Chat).join(Chat, ForwardRule.source_chat_id == Chat.id).filter(
        ForwardRule.enable_rule.is_(True), ForwardRule.enable_collect.is_(True)).all()
    wanted = {source_id(chat.telegram_chat_id) for _, chat in rules} - EXCLUDED_SOURCES
    for state in db.query(AlphaCollectionState).all():
        state.active = state.source_id in wanted
    for sid in wanted:
        if db.get(AlphaCollectionState, sid) is None:
            db.add(AlphaCollectionState(source_id=sid, enabled_at=now, active=True, scanned_id=0))
    db.flush()
    return wanted


def is_migrated(db, rule):
    return bool(rule.enable_collect
                or db.get(AlphaCollectionState, source_id(rule.source_chat.telegram_chat_id))
                or db.query(ForwardRule.id).filter_by(source_chat_id=rule.source_chat_id,
                                                      enable_collect=True).first())


def detach_evidence(db, message_id):
    for event in db.query(AlphaEvent).all():
        kept = [s for s in (event.sources or []) if s['id'] != message_id]
        if len(kept) != len(event.sources or []):
            event.sources = kept
            event.draft_status = 'pending'
            event.draft_zh = None
            event.draft_hash = None


def save_message(db, sid, name, username, message):
    """Upsert one source message; the caller commits before advancing a scan cursor."""
    entities = []
    for item in message.entities or []:
        entities.append({'type': type(item).__name__, 'offset': item.offset,
                         'length': item.length, 'url': getattr(item, 'url', None)})
    media = {}
    if message.media:
        obj = getattr(message, 'photo', None) or getattr(message, 'document', None)
        media = {'type': type(message.media).__name__, 'id': str(getattr(obj, 'id', ''))}
    body = message.message or ''
    grouped = str(message.grouped_id) if message.grouped_id else None
    reply_id = message.reply_to_msg_id
    content_hash = digest([body, entities, media, grouped, reply_id])
    row = db.query(CollectedMessage).filter_by(source_id=sid, message_id=message.id).first()
    if row and row.content_hash == content_hash:
        return row
    if row:
        detach_evidence(db, row.id)
        row.revision += 1
    else:
        row = CollectedMessage(source_id=sid, message_id=message.id)
        db.add(row)
    row.source_name, row.username = name, username or ''
    row.published_at = utc(message.date)
    row.edited_at = utc(message.edit_date) if message.edit_date else None
    row.body, row.entities, row.media = body, entities, media
    row.grouped_id, row.reply_id = grouped, reply_id
    row.source_url = (f'https://t.me/{username}/{message.id}' if username else
                      f'https://t.me/c/{sid[4:]}/{message.id}')
    row.content_hash = content_hash
    row.analysis_status, row.analysis, row.error = 'pending', None, None
    db.flush()
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


async def recover_history(client):
    """Paginate to a captured boundary. Failed pages never advance confirmed progress."""
    db = get_session()
    try:
        sync_sources(db)
        db.commit()
        sources = [s.source_id for s in db.query(AlphaCollectionState).filter_by(active=True)]
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


def valid_sources(event, now):
    return [s for s in (event.sources or [])
            if now - WINDOW <= datetime.fromisoformat(s['date']) <= now]


def event_tier(event, now):
    count = len({s['source_id'] for s in valid_sources(event, now)})
    if (event.closed or (event.deadline and event.deadline <= now)) and count >= 2:
        return 'closed'
    return 'main' if count >= 2 else 'single' if count else 'history'


def snapshot(now=None):
    now = now or datetime.utcnow()
    db = get_session()
    try:
        config = job(db)
        states = db.query(AlphaCollectionState).all()
        counts = {}
        for status, in db.query(CollectedMessage.analysis_status).all():
            counts[status] = counts.get(status, 0) + 1
        result = {'publish_enabled': config.publish_enabled, 'target_id': config.target_id, 'enabled': config.enabled, 'model': config.model or '', 'status': config.status,
                  'last_run': config.last_run.isoformat() if config.last_run else None,
                  'error': config.error, 'result': config.result or {}, 'counts': counts,
                  'sources': [{'id': s.source_id, 'active': s.active, 'error': s.error,
                               'scanned_id': s.scanned_id} for s in states], 'events': []}
        for ev in db.query(AlphaEvent).order_by(AlphaEvent.first_seen, AlphaEvent.id):
            result['events'].append({'id': ev.id, 'project': ev.project, 'title': ev.title,
                                     'stage': ev.stage, 'tier': event_tier(ev, now),
                                     'content_type': ev.content_type,
                                     'type_status': (ev.editorial or {}).get('status'),
                                     'detail_level': (ev.editorial or {}).get('detail_level'),
                                     'source_count': len({s['source_id'] for s in valid_sources(ev, now)}),
                                     'draft': ev.draft_zh if ev.draft_status == 'ready' and not has_process_note(ev.draft_zh) and event_tier(ev, now) in {'main', 'closed'} else None,
                                     'draft_status': ev.draft_status, 'error': ev.error,
                                     'closed': ev.closed,
                                     'first_seen': ev.first_seen.isoformat()})
        db.commit()
        return result
    finally:
        db.close()


def export_markdown(internal=False, now=None):
    now = now or datetime.utcnow()
    db = get_session()
    try:
        lines = ['# 机会中文草稿' if not internal else '# 机会内部核对材料', '',
                 '生成于 UTC ' + now.isoformat(timespec='seconds'),
                 '当前来源计数窗口：最近36小时；推送状态见网站运行结果。', '']
        for ev in db.query(AlphaEvent).order_by(AlphaEvent.first_seen, AlphaEvent.id):
            tier = event_tier(ev, now)
            if not internal and tier == 'history':
                continue
            label = {'main': '主清单', 'single': '单源附录', 'history': '历史', 'closed': '已结束更新'}[tier]
            lines += [f'## {ev.project}｜{ev.title}（{label}）', '']
            if ev.draft_status == 'ready' and tier in {'main', 'closed'} and (internal or not has_process_note(ev.draft_zh)):
                lines += [ev.draft_zh, '']
            else:
                lines += ['草稿待生成或条件尚不足。', '']
            if internal:
                lines += ['文案类型：' + (ev.content_type or '待判定') + '；类型状态：' + (ev.editorial or {}).get('status', 'pending'),
                          '类型内部依据：' + json.dumps(ev.editorial or {}, ensure_ascii=False), '']
                if (ev.draft_usage or {}).get('internal_notes'):
                    lines += ['内部文案核对：' + ev.draft_usage['internal_notes'], '']
                lines += ['状态：' + ev.draft_status + ('；' + ev.error if ev.error else ''), '']
                for ref in ev.sources or []:
                    msg = db.get(CollectedMessage, ref['id'])
                    if msg:
                        lines += [f'### {msg.source_name} {msg.published_at.isoformat()} UTC', '',
                                  msg.source_url, '', msg.body, '',
                                  '链接实体：' + json.dumps(msg.entities, ensure_ascii=False), '']
        if internal:
            for msg in db.query(CollectedMessage).filter(CollectedMessage.analysis_status.in_(
                    ['pending', 'failed', 'uncertain'])):
                lines += [f'## 待处理消息 {msg.id}（{msg.analysis_status}）', '',
                          msg.source_url, msg.error or '', msg.body or '[无文字]', '']
        return '\n'.join(lines)
    finally:
        db.close()
