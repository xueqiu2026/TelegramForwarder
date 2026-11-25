"""Publish each approved event once; retain every part and Telegram receipt."""
from models.models import AlphaEvent, AlphaPublication, CollectedMessage
from alpha.storage import event_tier
from alpha.editorial import TYPES
from alpha.public_copy import validate_public_copy
from delivery import make_parts, resolve_photo, deliver


def event_photo(db, event):
    # Reuse one original image; composite posts spanning projects cannot identify a cover safely.
    for ref in event.sources or []:
        row = db.get(CollectedMessage, ref['id'])
        if row is None or (row.media or {}).get('type') != 'MessageMediaPhoto':
            continue
        projects = {item.get('project', '').casefold() for item in (row.alpha.analysis or {}).get('events', [])}
        if projects and projects != {event.project.casefold()}:
            continue
        return {'source_id': row.source_id, 'message_id': row.message_id, 'id': row.media['id']}


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
        outcome = await deliver(db, client, config, publication, peer, event.draft_hash,
                                event.draft_zh, event_photo(db, event))
        for key in result:
            result[key] += outcome[key]
    return result
