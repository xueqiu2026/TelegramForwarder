import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from filelock import FileLock, Timeout
from models.models import (get_session, CollectedMessage, AlphaEvent, AlphaProjectAlias)
from alpha.storage import (WINDOW, digest, job, sync_sources, detach_evidence,
                           valid_sources, event_tier, recover_history)
from alpha.prompts import ANALYZE, DRAFT, ANALYSIS_VERSION, DRAFT_VERSION
from alpha.editorial import classify_events, draft_prompt, draft_version, type_signature, TYPES
from alpha.public_copy import validate_public_copy
from alpha.model import call_model
from utils.settings import get_default_ai_model


class AlreadyRunning(Exception):
    pass


class RunLock:
    """CLI and the main-process scheduler share one lock for this database."""
    def __enter__(self):
        url = os.environ['DATABASE_URL']
        self.engine, self.conn, self.lock = None, None, None
        if url.startswith('postgres'):
            self.engine = create_engine(url)
            self.conn = self.engine.connect()
            if not self.conn.execute(text('SELECT pg_try_advisory_lock(72119036)')).scalar():
                self.conn.close()
                self.engine.dispose()
                raise AlreadyRunning('机会任务正在运行')
        else:
            import tempfile
            self.lock = FileLock(str(Path(tempfile.gettempdir()) / ('tgbot-alpha-' + digest(url)[:16] + '.lock')))
            try:
                self.lock.acquire(timeout=0)
            except Timeout:
                raise AlreadyRunning('机会任务正在运行')
        return self

    def __exit__(self, *args):
        if self.conn:
            try:
                self.conn.execute(text('SELECT pg_advisory_unlock(72119036)'))
            finally:
                self.conn.close()
                self.engine.dispose()
        if self.lock:
            self.lock.release()


def message_data(row):
    return {'id': row.id, 'source_id': row.source_id, 'date': row.published_at.isoformat() + 'Z',
            'body': row.body, 'entities': row.entities, 'media': row.media,
            'reply_id': row.reply_id, 'grouped_id': row.grouped_id}



def message_search_text(row):
    return row.body + '\n' + '\n'.join(e.get('url') or '' for e in row.entities or [])


def candidate_context(db, candidates, rows, aliases, now):
    """Keep a compact event index; attach full originals only to matching candidates."""
    incoming_text = '\n'.join(message_search_text(r) for r in rows)
    incoming_urls = set(re.findall(r'https?://[^\s<>"\\]+', incoming_text))
    result = []
    for event in candidates:
        refs = valid_sources(event, now)
        originals = [db.get(CollectedMessage, ref['id']) for ref in refs]
        originals = [r for r in originals if r]
        names = [event.project, *(a.alias for a in aliases if a.project.casefold() == event.project.casefold())]
        name_match = False
        for name in names:
            parts = re.findall(r'[^\W_]+', name.casefold())
            if len(''.join(parts)) < 2:
                continue
            pattern = r'[\W_]*'.join(re.escape(part) for part in parts)
            if name.isascii():
                pattern = r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])'
            if re.search(pattern, incoming_text, re.I):
                name_match = True
                break
        original_urls = set(re.findall(r'https?://[^\s<>"\\]+',
                                      '\n'.join(message_search_text(r) for r in originals)))
        detailed = name_match or bool(incoming_urls & original_urls)
        result.append({'id': event.id, 'project': event.project, 'title': event.title,
                       'stage': event.stage, 'detail_available': detailed,
                       **({'evidence': refs, 'originals': [message_data(r) for r in originals]}
                          if detailed else {})})
    return result


def evidence(row, item):
    return {'id': row.id, 'source_id': row.source_id, 'revision': row.revision,
            'date': row.published_at.isoformat(), 'evidence': item['evidence'],
            'change': item['change']}


def reuse_key(row):
    # Relative dates and dependent/visual messages cannot safely reuse text-only judgments.
    relative = r'today|tomorrow|yesterday|tonight|now|hour|minute|今天|明天|昨天|今晚|小时|分钟|今日|本日|今日|내일|오늘|어제|지금|kemarin|hari ini|besok'
    if row.media or row.reply_id or row.grouped_id or re.search(relative, row.body, re.I):
        return None
    return row.content_hash, row.published_at.date()


def reusable(row, previous, version):
    return (previous.analysis_status in {'done', 'reject'} and previous.analysis_version == version
            and reuse_key(row) is not None and reuse_key(row) == reuse_key(previous))


def validate_analysis(data, rows, candidates):
    decisions = data.get('decisions')
    if not isinstance(decisions, list):
        raise ValueError('decisions 必须为数组')
    if any(not isinstance(d, dict) or type(d.get('id')) is not int for d in decisions):
        raise ValueError('分析输入ID格式错误')
    if sorted(d['id'] for d in decisions) != sorted(r.id for r in rows):
        raise ValueError('分析结果遗漏、重复或出现额外输入ID')
    new_keys = {}
    for d in decisions:
        if d.get('verdict') not in {'crypto', 'ipo', 'both', 'reject', 'uncertain'} or not isinstance(d.get('reason'), str):
            raise ValueError('分析状态或理由无效')
        events = d.get('events')
        if not isinstance(events, list) or bool(events) != (d['verdict'] in {'crypto', 'ipo', 'both'}):
            raise ValueError('相关性与事件列表不一致')
        refs = set()
        for item in events:
            for key in ('project', 'title', 'stage', 'evidence'):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise ValueError('事件缺少 ' + key)
            if item.get('change') not in {'duplicate', 'new', 'update', 'conflict'}:
                raise ValueError('无效事件变化类型')
            eid = item.get('event_id')
            if eid is not None:
                if type(eid) is not int or eid not in candidates:
                    raise ValueError('引用了未提供的历史事件')
                ref = ('old', eid)
            else:
                key = item.get('new_key')
                if not isinstance(key, str) or not key:
                    raise ValueError('新事件标识缺失')
                identity = (item['project'], item['title'], item['stage'])
                if key in new_keys and new_keys[key] != identity:
                    raise ValueError('同一新事件标识对应冲突身份')
                new_keys[key] = identity
                ref = ('new', key)
            if ref in refs:
                raise ValueError('同一消息重复关联同一事件')
            refs.add(ref)
    return decisions


def apply_decisions(db, decisions, rows, version, usage):
    by_id = {r.id: r for r in rows}
    new_events = {}
    for decision in decisions:
        row = by_id[decision['id']]
        # Live edits while a call is in flight must not receive the old version's result.
        current = db.get(CollectedMessage, row.id, populate_existing=True)
        if current.content_hash != row.content_hash:
            raise ValueError('分析期间原文已编辑，请续接新版本')
        detach_evidence(db, row.id)
        saved = []
        for item in decision['events']:
            event = db.get(AlphaEvent, item['event_id']) if item.get('event_id') else new_events.get(item['new_key'])
            if event is None:
                event = AlphaEvent(project=item['project'], title=item['title'], stage=item['stage'],
                                   first_seen=row.published_at, sources=[])
                db.add(event)
                db.flush()
                new_events[item['new_key']] = event
            event.sources = [*(event.sources or []), evidence(row, item)]
            event.first_seen = min(event.first_seen, row.published_at)
            event.updated_at = datetime.utcnow()
            event.draft_status, event.draft_zh = 'pending', None
            saved.append({**item, 'event_id': event.id})
        current.analysis = {**decision, 'events': saved}
        current.analysis_status = ('uncertain' if decision['verdict'] == 'uncertain' else
                                   'reject' if decision['verdict'] == 'reject' else 'done')
        current.analysis_version, current.error = version, None
        # One usage record per request, not multiplied by the number of decisions.
        if decision is decisions[0]:
            current.usage = {'calls': [*(current.usage or {}).get('calls', []), usage]}
        else:
            current.usage = {**(current.usage or {}), 'shared_request': decisions[0]['id']}


def refresh_cached(db, row, previous, version):
    data = json.loads(json.dumps(previous.analysis))
    data['id'] = row.id
    if any(db.get(AlphaEvent, e['event_id']) is None for e in data['events']):
        return False
    for item in data['events']:
        event = db.get(AlphaEvent, item['event_id'])
        event.sources = [*(event.sources or []), evidence(row, {**item, 'change': 'duplicate'})]
    row.analysis, row.analysis_status, row.analysis_version = data, previous.analysis_status, version
    row.error, row.usage = None, {'reused_from': previous.id}
    return True


async def analyze_pending(db, model, now, ask):
    active = sync_sources(db)
    db.commit()
    version = ANALYSIS_VERSION + ':' + model
    pending = db.query(CollectedMessage).filter(CollectedMessage.source_id.in_(active),
        CollectedMessage.analysis_status.in_(['pending', 'failed'])).order_by(CollectedMessage.published_at).all()
    # Old backlog remains visible as history, never falsely promoted to current opportunities.
    pending = [r for r in pending if r.published_at <= now]
    for row in pending:
        if row.published_at < now - WINDOW:
            row.analysis_status, row.error = 'history', '超过36小时，不作为当前机会分析'
            continue
        if not row.body.strip():
            row.analysis_status, row.error = 'uncertain', '无可用文字，媒体待补'
            continue
        cached = db.query(CollectedMessage).filter_by(content_hash=row.content_hash,
                                                     analysis_version=version).filter(
                                                         CollectedMessage.analysis_status.in_(['done', 'reject'])).first()
        if cached and reusable(row, cached, version):
            refresh_cached(db, row, cached, version)
    db.commit()
    pending = [r for r in pending if r.analysis_status in {'pending', 'failed'}]
    batch_size = max(1, int(os.getenv('ALPHA_BATCH_SIZE', '12')))
    for start in range(0, len(pending), batch_size):
        rows = pending[start:start + batch_size]
        representatives, copies, by_content = [], [], {}
        for row in rows:
            # Prior batches in this same run are now reusable as well.
            cached = db.query(CollectedMessage).filter_by(content_hash=row.content_hash,
                analysis_version=version).filter(CollectedMessage.analysis_status.in_(['done', 'reject'])).first()
            if cached and reusable(row, cached, version) and refresh_cached(db, row, cached, version):
                continue
            key = reuse_key(row)
            if key is not None and key in by_content:
                copies.append((row, by_content[key]))
            else:
                representatives.append(row)
                if key is not None:
                    by_content[key] = row
        db.commit()
        if not representatives:
            continue
        originals = {r.id: (r.content_hash, r.revision)
                     for r in [*representatives, *(pair[0] for pair in copies)]}
        candidates = [e for e in db.query(AlphaEvent) if valid_sources(e, now)]
        aliases = db.query(AlphaProjectAlias).all()
        data = {'now': now.isoformat() + 'Z', 'messages': [message_data(r) for r in representatives],
                'candidates': candidate_context(db, candidates, representatives, aliases, now),
                'aliases': [{'alias': a.alias, 'project': a.project, 'evidence': a.evidence}
                            for a in aliases]}
        usage = None
        try:
            response, usage = await ask(model, ANALYZE, data)
            decisions = validate_analysis(response, representatives, {e.id for e in candidates})
            # Expire the identity map before comparing the stored revision.
            db.expire_all()
            for rid, (content_hash, revision) in originals.items():
                current = db.get(CollectedMessage, rid)
                if current.content_hash != content_hash or current.revision != revision:
                    raise ValueError('分析期间消息已改变，本批保留待续接')
            apply_decisions(db, decisions, representatives, version, usage)
            for copied, original in copies:
                refresh_cached(db, copied, original, version)
            db.commit()
        except Exception as exc:
            usage = usage or getattr(exc, 'usage', None)
            db.rollback()
            for rid, (_, revision) in originals.items():
                row = db.get(CollectedMessage, rid)
                if row.revision == revision:
                    row.analysis_status, row.error = 'failed', str(exc)
            if usage is not None:
                anchor = db.get(CollectedMessage, representatives[0].id)
                anchor.usage = {'calls': [*(anchor.usage or {}).get('calls', []), usage]}
            db.commit()


async def write_drafts(db, model, now, ask):
    for event in db.query(AlphaEvent).order_by(AlphaEvent.first_seen).all():
        if len({s['source_id'] for s in valid_sources(event, now)}) < 2:
            continue
        if event.content_type not in TYPES or (event.editorial or {}).get('status') != 'ready':
            continue
        refs = list(event.sources or [])
        rows = [db.get(CollectedMessage, r['id']) for r in refs]
        rows = [r for r in rows if r and any(s['id'] == r.id and s['revision'] == r.revision for s in refs)]
        if (event.editorial or {}).get('signature') != type_signature(event, rows, model):
            continue
        version = [DRAFT_VERSION, draft_version(event)]
        signature = digest([[(r.id, r.revision) for r in rows], model, version])
        # A copied source adds provenance, not new prose.
        content_signature = digest([sorted({r.content_hash for r in rows}), model, version])
        if event.draft_status in {'ready', 'blocked'} and event.draft_hash == content_signature:
            continue
        usage = None
        try:
            response, usage = await ask(model, draft_prompt(event), {'now': now.isoformat() + 'Z',
                'project': event.project, 'title': event.title, 'stage': event.stage,
                'content_type': event.content_type,
                'detail_level': event.editorial['detail_level'],
                'event_status': event.editorial['event_status'],
                'context_gaps': event.editorial.get('context_gaps', []),
                'messages': [message_data(r) for r in rows],
                'event_evidence': refs,
                'private_sources': [{'name': r.source_name, 'username': r.username, 'url': r.source_url}
                                    for r in rows]})
            if type(response.get('closed')) is not bool or type(response.get('blocked')) is not bool:
                raise ValueError('草稿状态必须为布尔值')
            draft = response.get('draft')
            if not isinstance(draft, str) or (not draft.strip() and not response['blocked']):
                raise ValueError('中文草稿为空')
            validate_public_copy(draft)
            for row in rows:
                source_path = row.source_url.split('t.me/', 1)[-1]
                source_link = re.search(r'(?:t\.me|telegram\.me)/(?:s/)?' + re.escape(source_path)
                                        + r'(?![\w])', draft, re.I)
                attribution = (r'(?:来源|消息来自|转载自?|转自|关注|订阅|source|via|from|출처)'
                               r'\s*[:：]?\s*(?:\[|\*|_)*' + re.escape(row.source_name))
                name_footer = row.source_name.casefold() != event.project.casefold() and any(
                    line.strip(' \t*_`').casefold() == row.source_name.casefold()
                    for line in draft.splitlines())
                name_attribution = row.source_name and (name_footer or re.search(attribution, draft, re.I))
                if (source_link or row.source_url in draft or (row.username and '@' + row.username.casefold() in draft.casefold())
                        or name_attribution):
                    raise ValueError('公开草稿泄露采集来源回链或署名')
            if re.search(r'TG\s*来源|Telegram\s*来源', draft, re.I):
                raise ValueError('公开草稿包含采集来源脚注')
            db.expire_all()
            current = db.get(AlphaEvent, event.id)
            current_rows = [db.get(CollectedMessage, r['id']) for r in (current.sources or [])]
            current_signature = digest([[(r.id, r.revision) for r in current_rows if r], model, [DRAFT_VERSION, draft_version(current)]])
            if current_signature != signature:
                raise ValueError('生成期间原文已变，保留待续接')
            gaps = response.get('context_gaps', [])
            if not isinstance(gaps, list) or any(not isinstance(gap, str) for gap in gaps):
                raise ValueError('草稿缺口格式错误')
            current.draft_zh, current.closed = draft, response['closed'] or current.editorial.get('event_status') == 'ended'
            deadline = response.get('deadline_utc')
            if deadline is not None:
                parsed = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError('截止日期缺少时区')
                current.deadline = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                current.deadline = None
            current.draft_hash = content_signature
            current.draft_usage = {**(current.draft_usage or {}), 'calls': [*(current.draft_usage or {}).get('calls', []), usage],
                                   'internal_notes': response.get('internal_notes', ''), 'context_gaps': gaps}
            current.draft_status = 'blocked' if response['blocked'] else 'ready'
            current.error = response.get('reason') if response['blocked'] else None
            db.commit()
        except Exception as exc:
            usage = usage or getattr(exc, 'usage', None)
            db.rollback()
            current = db.get(AlphaEvent, event.id)
            current.draft_status, current.error = 'failed', str(exc)
            if usage is not None:
                current.draft_usage = {'calls': [*(current.draft_usage or {}).get('calls', []), usage]}
            db.commit()


async def run_pipeline(client=None, model=None, ask=call_model, now=None, slot=None):
    now = now or datetime.utcnow()
    with RunLock():
        db = get_session()
        try:
            config = job(db)
            model = model or config.model or get_default_ai_model()
            config.status, config.error, config.last_run = 'running', None, now
            db.commit()
            failures = await recover_history(client) if client is not None else {'offline': '本次未连接TG补采，处理已存原文'}
            active = sync_sources(db)
            db.commit()
            has_work = db.query(CollectedMessage.id).filter(CollectedMessage.source_id.in_(active),
                CollectedMessage.analysis_status.in_(['pending', 'failed']),
                CollectedMessage.published_at >= now - WINDOW).first()
            has_draft = any(event_tier(e, now) in {'main', 'closed'} and e.draft_status != 'ready'
                            for e in db.query(AlphaEvent))
            if (has_work or has_draft) and not model:
                raise ValueError('请先选择机会分析模型')
            await analyze_pending(db, model, now, ask)
            from alpha.consolidate import consolidate_events
            await consolidate_events(db, model, now, ask)
            await classify_events(db, model, now, ask)
            await write_drafts(db, model, now, ask)
            config = job(db)
            from alpha.publish import publish_ready
            delivery = await publish_ready(db, client, config, now)
            pending_errors = db.query(CollectedMessage).filter(CollectedMessage.source_id.in_(active),
                CollectedMessage.published_at >= now - WINDOW, CollectedMessage.analysis_status == 'failed').count()
            draft_errors = sum(e.draft_status in {'failed', 'blocked'} and event_tier(e, now) in {'main', 'closed'}
                               for e in db.query(AlphaEvent))
            config.status = 'partial' if failures or pending_errors or draft_errors or delivery['errors'] or delivery['unknown'] else 'success'
            config.result = {'recovery_errors': failures, 'analysis_errors': pending_errors,
                             'draft_errors': draft_errors, 'publish': delivery if config.publish_enabled else 'none'}
            config.last_slot = slot or config.last_slot
            db.commit()
            return dict(config.result)
        except Exception as exc:
            db.rollback()
            config = job(db)
            config.status, config.error = 'failed', str(exc)
            config.last_slot = slot or config.last_slot
            db.commit()
            raise
        finally:
            db.close()
