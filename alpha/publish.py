"""Publish each approved event once; retain every part and Telegram receipt."""
import secrets
import re
from datetime import datetime, timezone
from telethon import functions, types, errors, utils
from telethon.extensions import markdown
from models.models import AlphaEvent, AlphaPublication, CollectedMessage
from alpha.storage import event_tier
from alpha.editorial import TYPES
from alpha.public_copy import validate_public_copy


def make_parts(draft, photo=None):
    draft = re.sub(r'^#{1,6}\s+(.+)$', lambda m: '**' + m[1].strip('*') + '**', draft, flags=re.M)
    text, entities = markdown.parse(draft)
    parts = [{'text': body, 'entities': [e.to_dict() for e in formatting],
             'random_id': str(secrets.randbits(63)), 'status': 'queued', 'message_id': None}
            for body, formatting in utils.split_text(text, entities, limit=1024 if photo else 3500)]
    if photo:
        parts[0]['photo'] = photo
    return parts


def event_photo(db, event):
    # Reuse one original image; composite posts spanning projects cannot identify a cover safely.
    for ref in event.sources or []:
        row = db.get(CollectedMessage, ref['id'])
        if row is None or (row.media or {}).get('type') != 'MessageMediaPhoto':
            continue
        projects = {item.get('project', '').casefold() for item in (row.analysis or {}).get('events', [])}
        if projects and projects != {event.project.casefold()}:
            continue
        return {'source_id': row.source_id, 'message_id': row.message_id, 'id': row.media['id']}


async def resolve_photo(client, photo):
    peer = await client.get_input_entity(int(photo['source_id']))
    original = await client.get_messages(peer, ids=photo['message_id'])
    if not original or not original.photo or str(original.photo.id) != photo['id']:
        raise ValueError('原消息图片已不可用，保留待重试，不静默丢图')
    return types.InputMediaPhoto(utils.get_input_photo(original.photo))


async def publish_ready(db, client, config, now):
    result = {'sent': 0, 'errors': 0, 'unknown': 0}
    db.refresh(config)
    if not config.publish_enabled:
        return result
    if client is None or not config.target_id:
        raise ValueError('自动推送缺少 Telegram 客户端或目标')
    peer = await client.get_input_entity(int(config.target_id))
    for event in db.query(AlphaEvent).order_by(AlphaEvent.first_seen, AlphaEvent.id).all():
        db.refresh(config)
        if not config.publish_enabled:
            break
        publication = db.query(AlphaPublication).filter_by(event_id=event.id, target_id=config.target_id).first()
        if publication and publication.status == 'sent':
            continue
        if (event.content_type not in TYPES or (event.editorial or {}).get('status') != 'ready'
                or (event.editorial or {}).get('event_status') == 'ended'
                or event_tier(event, now) != 'main' or event.draft_status != 'ready'):
            continue
        try:
            validate_public_copy(event.draft_zh)
        except ValueError as exc:
            event.draft_status, event.draft_hash, event.error = 'failed', None, str(exc)
            db.commit()
            result['errors'] += 1
            continue
        if publication is None:
            publication = AlphaPublication(event_id=event.id, target_id=config.target_id,
                draft_hash=event.draft_hash, parts=make_parts(event.draft_zh, event_photo(db, event)), status='queued')
            db.add(publication)
            db.commit()
        parts = [dict(p) for p in publication.parts]
        # An interrupted/ambiguous send is reconciled against actual history before anything else.
        if any(p['status'] in {'sending', 'unknown'} for p in parts):
            recent = await client.get_messages(peer, limit=100)
            for part in parts:
                if part['status'] not in {'sending', 'unknown'}:
                    continue
                found = next((m for m in recent if m.message == part['text']
                              and (not part.get('photo') or str(getattr(getattr(m, 'photo', None), 'id', '')) == part['photo']['id'])
                              and m.date.astimezone(timezone.utc).replace(tzinfo=None) >= publication.created_at), None)
                if found:
                    part.update(status='sent', message_id=found.id)
            publication.parts = parts
            if any(p['status'] in {'sending', 'unknown'} for p in parts):
                publication.status, publication.error = 'unknown', '发送结果待核对；不自动重发'
                db.commit()
                result['unknown'] += 1
                continue
            db.commit()
        if publication.draft_hash != event.draft_hash and any(p['status'] == 'sent' for p in parts):
            publication.status, publication.error = 'unknown', '分段发送期间稿件发生变化，请核对已发部分'
            db.commit()
            result['unknown'] += 1
            continue
        if publication.draft_hash != event.draft_hash:
            parts = make_parts(event.draft_zh, event_photo(db, event))
            publication.draft_hash = event.draft_hash
            # Persist the new body with its hash before media lookup can fail.
            publication.parts = [dict(p) for p in parts]
            db.commit()
        for index, part in enumerate(parts):
            db.refresh(config)
            if not config.publish_enabled:
                return result
            if part['status'] == 'sent':
                continue
            # Fetch a fresh file_reference before marking a send in flight.
            try:
                media = await resolve_photo(client, part['photo']) if part.get('photo') else None
            except Exception as exc:
                publication.status, publication.error = 'failed', str(exc)
                db.commit()
                result['errors'] += 1
                break
            part['status'] = 'sending'
            publication.parts, publication.status = [dict(p) for p in parts], 'sending'
            db.commit()
            try:
                entities = [getattr(types, e['_'])(**{k: v for k, v in e.items() if k != '_'})
                            for e in part['entities']]
                if media:
                    request = functions.messages.SendMediaRequest(peer=peer, media=media, message=part['text'],
                        entities=entities, random_id=int(part['random_id']))
                else:
                    request = functions.messages.SendMessageRequest(peer=peer, message=part['text'],
                        entities=entities, random_id=int(part['random_id']), no_webpage=True)
                response = await client(request)
                mid = getattr(response, 'id', None)
                for update in getattr(response, 'updates', []):
                    if isinstance(update, types.UpdateMessageID) and update.random_id == int(part['random_id']):
                        mid = update.id
                    elif isinstance(update, (types.UpdateNewChannelMessage, types.UpdateNewMessage)):
                        if update.message.message == part['text']:
                            mid = update.message.id
                if mid is None:
                    raise RuntimeError('Telegram 未返回可确认的消息回执')
                part.update(status='sent', message_id=mid)
                publication.parts, publication.error = [dict(p) for p in parts], None
                db.commit()
            except BaseException as exc:
                known_failure = isinstance(exc, errors.RPCError) and not isinstance(exc, errors.ServerError)
                part['status'] = 'queued' if known_failure else 'unknown'
                publication.parts = [dict(p) for p in parts]
                publication.status = 'failed' if known_failure else 'unknown'
                publication.error = type(exc).__name__ + ': ' + str(exc)
                db.commit()
                if not isinstance(exc, Exception):
                    raise
                result['errors' if known_failure else 'unknown'] += 1
                break
        else:
            publication.status, publication.error = 'sent', None
            db.commit()
            result['sent'] += 1
    return result
