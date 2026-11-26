"""AI-only semantic decisions and persisted-publication checks, before Chinese output."""
from datetime import datetime, time, timedelta, timezone
import pytz
from collection import digest
from models.models import AIItem, AIPublication, CollectedMessage

VERSION = 'p19-dedup-v1'


def day_bounds(now):
    tz = pytz.timezone('Asia/Shanghai')
    local = now.replace(tzinfo=timezone.utc).astimezone(tz)
    start = tz.localize(datetime.combine(local.date(), time()))
    return tuple(t.astimezone(timezone.utc).replace(tzinfo=None) for t in (start, start + timedelta(days=1)))


def reservations(db, target):
    return [p for p in db.query(AIPublication).filter_by(target_id=target)
            if p.status in {'sent', 'sending', 'unknown'}
            or any(part['status'] in {'sent', 'sending', 'unknown'} for part in p.parts)]


def item_facts(item):
    return digest([item.subject, item.goal, item.category, item.conditions, item.sources])


def publication_facts(publication):
    # Receipt status/message IDs may be reconciled without changing the content being compared.
    return digest([publication.item_id, publication.target_id, publication.sources,
                   [{k: p.get(k) for k in ('text', 'entities', 'photo')} for p in publication.parts]])


def publication_context(db, target, now):
    start, end = day_bounds(now)
    return [{'id': p.id, 'item_id': p.item_id, 'status': p.status,
             'created_today': start <= p.created_at < end,
             'created_at': p.created_at.isoformat() + 'Z',
             'sources': p.sources, 'parts': [{k: part.get(k) for k in ('text', 'entities', 'photo')} for part in p.parts]}
            for p in reservations(db, target)]


def validate_relations(decisions, candidates):
    items = [i for d in decisions for i in d['items']]
    keys = {i['new_key'] for i in items if i.get('item_id') is None}
    for item in items:
        check = item.get('dedup')
        if not isinstance(check, dict) or check.get('relation') not in {'new', 'same', 'distinct', 'update', 'review'}:
            raise ValueError('缺少有效去重判定')
        if not isinstance(check.get('reason'), str) or not check['reason'].strip() or not isinstance(check.get('change'), str):
            raise ValueError('去重依据或变化格式错误')
        related, local = check.get('related_item_id'), check.get('related_new_key')
        if related is not None and (type(related) is not int or related not in candidates):
            raise ValueError('去重关联ID越界')
        if local is not None and (not isinstance(local, str) or local not in keys or local == item.get('new_key')):
            raise ValueError('去重关联本批标识错误')
        relation = check['relation']
        if related is not None and local is not None:
            raise ValueError('去重关联只能选择一个目标')
        if relation in {'same', 'distinct', 'update'} and related is None and local is None:
            raise ValueError('去重判定缺关联')
        if relation == 'new' and (related is not None or local is not None or item.get('item_id') is not None):
            raise ValueError('新事项不能关联旧事项')
        if relation in {'distinct', 'update', 'review'} and item.get('item_id') is not None:
            raise ValueError('非合并判定不能覆盖历史事项')
        if relation == 'same' and item.get('item_id') not in (None, related):
            raise ValueError('同事项与去重关联冲突')
        if relation == 'update' and not check['change'].strip():
            raise ValueError('更新缺少实质新增事实')
        if relation != 'update' and check['change']:
            raise ValueError('非更新事项不能附带更新事实')
        if relation == 'review' and item['status'] != 'needs_info':
            raise ValueError('待核事项不能自动放行')
    by_key = {}
    for item in items:
        if item.get('item_id') is None:
            by_key.setdefault(item['new_key'], item)

    def root(item, seen):
        check = item['dedup']
        if check['relation'] != 'same':
            return item.get('item_id'), item.get('new_key')
        if check.get('related_item_id') is not None:
            return check['related_item_id'], None
        key = check['related_new_key']
        if key in seen:
            raise ValueError('去重关联循环')
        return root(by_key[key], seen | {key})

    roots = [(item, root(item, {item.get('new_key')})) for item in items]
    for item, (eid, key) in roots:
        local = item['dedup'].get('related_new_key')
        if local is not None:
            target_id, target_key = root(by_key[local], {local})
            item['dedup']['related_item_id'] = target_id
            item['dedup']['related_new_key'] = target_key
        if item['dedup']['relation'] == 'same':
            item['item_id'] = eid
            if key is not None:
                item['new_key'] = key


def record_checks(db, events, target, compared_ids, receipts):
    # All these facts were part of the single batch comparison, including its new items.
    facts = {str(i.id): item_facts(i) for i in db.query(AIItem).filter(AIItem.id.in_(compared_ids))}
    for event in events:
        event.dedup = {**event.dedup, 'version': VERSION, 'target_id': target,
                       'checked_items': facts, 'checked_publications': receipts}


def fragment_keys(db, refs):
    from ai_digest.pipeline import selected_message
    keys = set()
    for ref in refs:
        raw = db.get(CollectedMessage, ref['id'])
        if raw is not None and raw.revision == ref['revision'] and ref['excerpt'] in raw.body:
            selected = selected_message(raw, ref['excerpt'])
            keys.add(digest([selected['body'], selected['entities']]))
    return keys


def canonical_item(db, item_id):
    item = db.get(AIItem, item_id)
    while (item.dedup or {}).get('duplicate_of'):
        item = db.get(AIItem, item.dedup['duplicate_of'])
    return item


def check_before_output(db, item, target):
    """Return (hold kind, related ID, reason), using original fragments and delivery snapshots."""
    metadata = item.dedup or {}
    own = fragment_keys(db, item.sources)
    canonical = canonical_item(db, item.id)
    for receipt in reservations(db, target):
        if receipt.item_id == item.id:
            continue
        if canonical.id == receipt.item_id or (own and own <= fragment_keys(db, receipt.sources)):
            return 'same', receipt.item_id, '全部原文片段已在已发或待核对事项中'
        checked = metadata.get('checked_publications', {}).get(str(receipt.id))
        if checked == publication_facts(receipt):
            continue
        previous = db.get(AIItem, receipt.item_id)
        if (previous is not None and metadata.get('checked_items', {}).get(str(previous.id)) == item_facts(previous)
                and all(ref in (previous.sources or []) for ref in receipt.sources)):
            continue
        return 'review', receipt.item_id, '去重判断后出现未比较的发布内容，需核对'
    if metadata.get('version') != VERSION or metadata.get('target_id') != target:
        return 'review', None, '缺少当前目标的去重判定，需核对'
    if metadata.get('relation') == 'review':
        return 'review', metadata.get('related_item_id'), metadata['reason']
    return '', None, ''


def allow_output(db, item, target):
    kind, related, reason = check_before_output(db, item, target)
    if not kind:
        return True
    item.status, item.error = 'needs_info', reason
    metadata = {**(item.dedup or {}), 'blocked_by_item_id': related, 'blocked_reason': reason}
    if kind == 'same':
        original = canonical_item(db, related)
        original.sources = [*original.sources, *(r for r in item.sources if r not in original.sources)]
        if original.id != item.id:
            metadata.update(relation='same', related_item_id=original.id, duplicate_of=original.id, reason=reason, change='')
    item.dedup = metadata
    db.commit()
    return False
