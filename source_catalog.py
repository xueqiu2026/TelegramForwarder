"""Shared source identity, subscriptions and read-only catalog metadata."""
from datetime import datetime, timezone
from sqlalchemy import func
from models.models import (Chat, BusinessSource, CollectedMessage, ForwardRule,
                           AlphaCollectionState, TelegramFolder, TelegramFolderSource, BusinessMigration)
from collection import EXCLUDED_SOURCES, sync_sources

BUSINESSES = [{'key': 'alpha', 'name': 'Alpha机会', 'path': 'alpha'},
              {'key': 'ai', 'name': 'AI内容', 'path': 'ai-digest'}]


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value and value.tzinfo is None else value.isoformat() if value else None


def normalize_times(value):
    if isinstance(value, datetime):
        return timestamp(value)
    if isinstance(value, dict):
        return {k: normalize_times(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_times(v) for v in value]
    return value


def canonical_legacy(value, known):
    value = str(value)
    if value.startswith('-'):
        return value
    if value.startswith('100') and len(value) > 10:
        return '-' + value
    candidate = '-100' + value
    return candidate if candidate in known else None


def ensure_catalog(db):
    sync_sources(db)
    subscriptions = db.query(BusinessSource).all()
    initial_import = db.get(BusinessMigration, 'source-catalog-v1') is None
    known = {s.source_id for s in subscriptions}
    by_peer = {c.peer_id: c for c in db.query(Chat) if c.peer_id}
    for chat in db.query(Chat).filter(Chat.peer_id.is_(None)):
        peer = canonical_legacy(chat.telegram_chat_id, known)
        if peer and peer not in by_peer:
            chat.peer_id = peer
            chat.peer_type = 'channel' if peer.startswith('-100') else 'group'
            by_peer[peer] = chat
    for sub in subscriptions:
        # Import historical subscriptions once; later reads must not undo a directory removal.
        if sub.source_id not in by_peer and (initial_import or sub.active):
            chat = Chat(telegram_chat_id=sub.source_id, peer_id=sub.source_id,
                        peer_type='channel' if sub.source_id.startswith('-100') else 'group', name=sub.name)
            db.add(chat)
            by_peer[sub.source_id] = chat
    if initial_import:
        db.add(BusinessMigration(name='source-catalog-v1'))
    db.flush()


def source_names(db):
    return {c.peer_id: c.name or c.telegram_title or c.peer_id
            for c in db.query(Chat) if c.peer_id}


def catalog_snapshot(db):
    folders = {str(f.id): {'title': f.title, 'peers': []} for f in db.query(TelegramFolder)}
    membership = {}
    for link in db.query(TelegramFolderSource):
        folder = folders[str(link.folder_id)]
        folder['peers'].append(link.peer_id)
        membership.setdefault(link.peer_id, []).append(f'{link.folder_id}: {folder["title"]}')
    for folder in folders.values():
        folder['peers'].sort()
    return {'sources': {c.peer_id: {'telegram_title': c.telegram_title, 'username': c.username,
                                  'member_state': c.member_state, 'folders': sorted(membership.get(c.peer_id, []))}
                        for c in db.query(Chat) if c.peer_id}, 'folders': folders}


def source_blocked(db, peer):
    return peer in EXCLUDED_SOURCES or db.query(Chat.id).filter_by(peer_id=peer, member_state='left').first() is not None


def set_subscription(db, peer, business, active):
    if business not in {b['key'] for b in BUSINESSES}:
        raise ValueError('未知业务')
    ensure_catalog(db)
    chat = db.query(Chat).filter_by(peer_id=peer).first()
    if not chat or chat.peer_type not in {'channel', 'group'}:
        raise ValueError('该来源尚未核对为支持的频道或群组')
    if active and source_blocked(db, peer):
        raise ValueError('该来源已退出或明确停用，请先核对TG成员状态')
    sub = db.get(BusinessSource, (business, peer))
    if sub is None:
        sub = BusinessSource(business=business, source_id=peer, name=chat.name or chat.telegram_title or '')
        db.add(sub)
    sub.active = active
    db.flush()
    sync_sources(db)
    return {'source_id': peer, 'business': business, 'active': active,
            'collection_enabled': any(s.active for s in db.query(BusinessSource).filter_by(source_id=peer))}


def effective_sources(db):
    ensure_catalog(db)
    blocked = {c.peer_id for c in db.query(Chat).filter_by(member_state='left')} | EXCLUDED_SOURCES
    names = source_names(db)
    sources = {s.source_id: names.get(s.source_id, s.name) for s in db.query(BusinessSource).filter_by(active=True)
               if s.business in {b['key'] for b in BUSINESSES} and s.source_id not in blocked}
    for rule, chat in db.query(ForwardRule, Chat).join(Chat, ForwardRule.source_chat_id == Chat.id).filter(
            ForwardRule.enable_rule.is_(True), ForwardRule.enable_forward.is_(True)):
        peer = chat.peer_id or canonical_legacy(chat.telegram_chat_id, set(sources))
        if peer and peer not in blocked:
            sources[peer] = chat.name or chat.telegram_title or peer
    return sources


def catalog_rows(db):
    ensure_catalog(db)
    subscriptions = {}
    for sub in db.query(BusinessSource):
        subscriptions.setdefault(sub.source_id, {})[sub.business] = {'active': sub.active, 'enabled_at': timestamp(sub.enabled_at)}
    stats = {sid: {'raw_count': count, 'published_at': timestamp(published), 'received_at': timestamp(received)}
             for sid, count, published, received in db.query(CollectedMessage.source_id, func.count(CollectedMessage.id),
                 func.max(CollectedMessage.published_at), func.max(CollectedMessage.received_at)).group_by(CollectedMessage.source_id)}
    states = {s.source_id: s for s in db.query(AlphaCollectionState)}
    folders = {}
    for link, folder in db.query(TelegramFolderSource, TelegramFolder).join(TelegramFolder):
        folders.setdefault(link.peer_id, []).append({'id': folder.id, 'title': folder.title, 'complete': folder.complete})
    source_rules, target_rules = {}, {}
    for rule in db.query(ForwardRule):
        source_rules.setdefault(rule.source_chat_id, []).append(rule)
        target_rules[rule.target_chat_id] = target_rules.get(rule.target_chat_id, 0) + 1
    rows = []
    for chat in db.query(Chat).order_by(Chat.id):
        peer = chat.peer_id
        state = states.get(peer)
        rules = source_rules.get(chat.id, [])
        latest = stats.get(peer, {'raw_count': 0, 'published_at': None, 'received_at': None})
        rows.append({'chat_id': chat.id, 'peer_id': peer, 'telegram_chat_id': chat.telegram_chat_id,
            'peer_type': chat.peer_type, 'name': chat.name or chat.telegram_title or chat.telegram_chat_id,
            'telegram_title': chat.telegram_title, 'username': chat.username,
            'member_state': chat.member_state or 'unknown', 'verified_at': timestamp(chat.verified_at),
            'folders': folders.get(peer, []), 'subscriptions': subscriptions.get(peer, {}),
            'can_subscribe': bool(peer and chat.peer_type in {'channel', 'group'} and peer not in EXCLUDED_SOURCES and chat.member_state != 'left'),
            'collection': {'enabled': bool(state and state.active), 'scanned_at': timestamp(state.scanned_at) if state else None,
                           'error': state.error if state else None},
            'latest': latest, 'raw_count': latest['raw_count'],
            'direct': {'rules': len(rules), 'enabled': sum(bool(r.enable_rule and r.enable_forward and not r.only_rss and not r.enable_only_push) for r in rules),
                       'blocked': bool(peer in EXCLUDED_SOURCES or chat.member_state == 'left')},
            'target_rule_count': target_rules.get(chat.id, 0)})
    return rows
