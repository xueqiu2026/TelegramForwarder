import json
from datetime import datetime, timedelta

from alpha.public_copy import has_process_note
from models.models import (get_session, CollectedMessage,
                           AlphaCollectionState, AlphaEvent, AlphaJob, AlphaProcessing)


WINDOW = timedelta(hours=36)


def job(db):
    row = db.get(AlphaJob, 1)
    if row is None:
        row = AlphaJob(id=1, enabled=False)
        db.add(row)
        db.flush()
    return row


from collection import (sync_sources, save_message, collect_event, recover_history, detach_evidence,
                        utc, source_id, source_variants, digest, EXCLUDED_SOURCES)


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
        from collection import business_sources
        active = business_sources(db, 'alpha', enabled_only=False)
        states = db.query(AlphaCollectionState).filter(AlphaCollectionState.source_id.in_(active)).all()
        counts = {}
        for status, in db.query(AlphaProcessing.analysis_status).join(CollectedMessage).filter(CollectedMessage.source_id.in_(active)).all():
            counts[status] = counts.get(status, 0) + 1
        result = {'publish_enabled': config.publish_enabled, 'target_id': config.target_id, 'enabled': config.enabled, 'schedule_hours': config.schedule_hours, 'model': config.model or '', 'status': config.status,
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
            for msg in db.query(CollectedMessage).join(AlphaProcessing).filter(AlphaProcessing.analysis_status.in_(
                    ['pending', 'failed', 'uncertain'])):
                lines += [f'## 待处理消息 {msg.id}（{msg.alpha.analysis_status}）', '',
                          msg.source_url, msg.alpha.error or '', msg.body or '[无文字]', '']
        return '\n'.join(lines)
    finally:
        db.close()
