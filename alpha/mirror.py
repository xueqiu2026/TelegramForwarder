"""Copy confirmed primary publications with the bot, without another AI pass."""
import asyncio
import logging
import os
import secrets
from datetime import datetime, timezone

from telethon import errors, functions, types, utils

from alpha.storage import job
from models.models import AlphaPublication, get_session

logger = logging.getLogger(__name__)


async def mirror_published(db, client, target_id, since, history_client=None):
    config = job(db)
    result = {'sent': 0, 'errors': 0, 'unknown': 0}
    if not config.publish_enabled:
        return result
    if target_id == config.target_id:
        raise ValueError('同步目标不能等于主频道')
    source = await client.get_input_entity(int(config.target_id))
    target = await client.get_input_entity(int(target_id))
    originals = db.query(AlphaPublication).filter(
        AlphaPublication.target_id == config.target_id,
        AlphaPublication.status == 'sent', AlphaPublication.created_at >= since,
    ).order_by(AlphaPublication.id).all()
    for original in originals:
        db.refresh(config)
        if not config.publish_enabled:
            break
        copy = db.query(AlphaPublication).filter_by(event_id=original.event_id, target_id=target_id).first()
        if copy and copy.status == 'sent':
            continue
        if copy is None:
            # Use the actual sent parts rather than a later regenerated event draft.
            parts = [{**part, 'source_message_id': part['message_id'], 'message_id': None,
                      'random_id': str(secrets.randbits(63)), 'status': 'queued'} for part in original.parts]
            copy = AlphaPublication(event_id=original.event_id, target_id=target_id,
                                    draft_hash=original.draft_hash, parts=parts, status='queued')
            db.add(copy)
            db.commit()
        parts = [dict(p) for p in copy.parts]
        if any(p['status'] in {'sending', 'unknown'} for p in parts):
            # Bots cannot read channel history through GetHistory; use the user reader.
            reader = history_client or client
            recent = await reader.get_messages(int(target_id), limit=100)
            for part in parts:
                if part['status'] not in {'sending', 'unknown'}:
                    continue
                found = next((m for m in recent if m.message == part['text']
                              and (not part.get('photo') or str(getattr(getattr(m, 'photo', None), 'id', '')) == part['photo']['id'])
                              and m.date.astimezone(timezone.utc).replace(tzinfo=None) >= copy.created_at), None)
                if found:
                    part.update(status='sent', message_id=found.id)
            copy.parts = parts
            db.commit()
            if any(p['status'] in {'sending', 'unknown'} for p in parts):
                copy.status, copy.error = 'unknown', '同步发送结果待核对，不自动重发'
                db.commit()
                result['unknown'] += 1
                continue
        for part in parts:
            if part['status'] == 'sent':
                continue
            part['status'] = 'sending'
            copy.parts, copy.status = [dict(p) for p in parts], 'sending'
            db.commit()
            try:
                response = await client(functions.messages.ForwardMessagesRequest(
                    from_peer=source, to_peer=target, id=[part['source_message_id']],
                    random_id=[int(part['random_id'])], drop_author=True))
                mid = next((u.id for u in getattr(response, 'updates', [])
                            if isinstance(u, types.UpdateMessageID) and u.random_id == int(part['random_id'])), None)
                if mid is None:
                    raise RuntimeError('同步发送未取得可确认的消息回执')
                part.update(status='sent', message_id=mid)
                copy.parts, copy.error = [dict(p) for p in parts], None
                db.commit()
            except BaseException as exc:
                known = isinstance(exc, errors.RPCError) and not isinstance(exc, errors.ServerError)
                part['status'] = 'queued' if known else 'unknown'
                copy.parts = [dict(p) for p in parts]
                copy.status, copy.error = ('failed' if known else 'unknown'), type(exc).__name__ + ': ' + str(exc)
                db.commit()
                if not isinstance(exc, Exception):
                    raise
                result['errors' if known else 'unknown'] += 1
                break
        else:
            copy.status, copy.error = 'sent', None
            db.commit()
            result['sent'] += 1
    return result


async def mirror_loop(client, history_client=None):
    target = os.getenv('ALPHA_MIRROR_TARGET', '').strip()
    if not target:
        return
    try:
        target = int(target) if target.lstrip('-').isdigit() else target
        since = datetime.fromisoformat(os.environ['ALPHA_MIRROR_SINCE'].replace('Z', '+00:00'))
        if since.tzinfo is None:
            raise ValueError('ALPHA_MIRROR_SINCE必须带时区')
        since = since.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, KeyError):
        logger.exception('同步频道配置无效，同步未启动')
        return
    target_id = None
    while True:
        db = get_session()
        try:
            if target_id is None:
                target_id = str(utils.get_peer_id(await client.get_entity(target)))
                if history_client is not None:
                    await history_client.get_entity(target)
            result = await mirror_published(db, client, target_id, since, history_client)
            if any(result.values()):
                logger.info('Alpha频道同步 %s: %s', target, result)
        except Exception:
            db.rollback()
            logger.exception('Alpha频道同步失败，下轮重试；主频道不受影响')
        finally:
            db.close()
        await asyncio.sleep(30)
