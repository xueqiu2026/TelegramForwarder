import json
import re
from datetime import datetime, timezone
from telethon.helpers import add_surrogate
from models.models import (get_session, CollectedMessage, AIProcessing, AIItem, AIJob, AIPublication)
from collection import sync_sources, business_sources, recover_history, detach_evidence
from alpha.storage import digest
from alpha.pipeline import RunLock, reuse_key, message_data
from alpha.model import call_model
from alpha.public_copy import validate_public_copy
from ai_digest.keywords import recall
from ai_digest.prompts import VERSION, OUTPUT_VERSION, CATEGORIES, ANALYZE, OUTPUT, SUPPLEMENT
from utils.settings import get_default_ai_model


def job(db):
    row = db.get(AIJob, 1)
    if row is None:
        row = AIJob(id=1, enabled=False, publish_enabled=False, target_id='@wanshenmeAI')
        db.add(row)
        db.flush()
    return row


def deadline(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('截止时间缺时区')
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def validate(data, rows, candidates):
    decisions = data.get('decisions')
    by_id = {r.id: r for r in rows}
    if not isinstance(decisions, list) or any(not isinstance(d, dict) or type(d.get('id')) is not int for d in decisions):
        raise ValueError('分类结果格式错误')
    if sorted(d['id'] for d in decisions) != sorted(by_id):
        raise ValueError('分类结果ID遗漏/重复/越界')
    new_keys = {}
    for decision in decisions:
        if decision.get('decision') not in {'candidate', 'needs_info', 'excluded'} or not isinstance(decision.get('reason'), str):
            raise ValueError('分类状态或理由错误')
        items = decision.get('items')
        if not isinstance(items, list) or bool(items) == (decision['decision'] == 'excluded'):
            raise ValueError('分类与事项不一致')
        for item in items:
            if item.get('category') not in CATEGORIES or item.get('status') not in {'candidate', 'needs_info', 'expired'}:
                raise ValueError('事项类别或状态错误')
            for key in ('subject', 'goal', 'excerpt'):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise ValueError('事项缺少 ' + key)
            if item['excerpt'] not in by_id[decision['id']].body:
                raise ValueError('事项片段不是原文子串')
            if not isinstance(item.get('conditions', ''), str):
                raise ValueError('事项条件格式错误')
            missing = item.get('missing', [])
            if not isinstance(missing, list) or any(not isinstance(x, str) for x in missing):
                raise ValueError('事项缺口格式错误')
            deadline(item.get('deadline_utc'))
            eid = item.get('item_id')
            if eid is not None:
                if type(eid) is not int or eid not in candidates:
                    raise ValueError('事项关联ID越界')
            else:
                key = item.get('new_key')
                identity = (item['subject'], item['goal'], item.get('conditions', ''))
                if not isinstance(key, str) or not key or (key in new_keys and new_keys[key] != identity):
                    raise ValueError('新事项标识错误或冲突')
                new_keys[key] = identity
    return decisions


def selected_message(row, excerpt):
    # Telegram offsets are UTF-16; adjust the selected substring before final translation.
    original, selected = add_surrogate(row.body), add_surrogate(excerpt)
    start = original.index(selected)
    entities = [{**e, 'offset': e['offset'] - start} for e in row.entities or []
                if start <= e['offset'] and e['offset'] + e['length'] <= start + len(selected)]
    return {**message_data(row), 'body': excerpt, 'entities': entities}


def apply(db, decisions, version, usage):
    new = {}
    for decision in decisions:
        raw = db.get(CollectedMessage, decision['id'])
        state = db.get(AIProcessing, raw.id)
        detach_evidence(db, raw.id, 'ai')
        saved = []
        for item in decision['items']:
            event = db.get(AIItem, item['item_id']) if item.get('item_id') else new.get(item['new_key'])
            if event is None:
                event = AIItem(subject=item['subject'], goal=item['goal'], category=item['category'],
                               conditions=item.get('conditions', ''), sources=[])
                db.add(event)
                db.flush()
                new[item['new_key']] = event
            duplicate = any(ref['excerpt'] == item['excerpt'] for ref in event.sources or [])
            event.sources = [*(event.sources or []), {'id': raw.id, 'revision': raw.revision,
                'source_id': raw.source_id, 'date': raw.published_at.isoformat(),
                'excerpt': item['excerpt'], 'missing': item.get('missing', []),
                'status': item['status'], 'deadline_utc': item.get('deadline_utc')}]
            if not duplicate:
                refs = event.sources
                event.status = ('needs_info' if any(r.get('missing') or r.get('status') == 'needs_info' for r in refs)
                                else 'expired' if any(r.get('status') == 'expired' for r in refs) else item['status'])
                limits = [deadline(r['deadline_utc']) for r in refs if r.get('deadline_utc')]
                if event.deadline:
                    limits.append(event.deadline)
                event.deadline = min(limits) if limits else None
                event.output_hash, event.output_zh = None, None
            saved.append({**item, 'item_id': event.id})
        state.result = {**decision, 'items': saved}
        state.status, state.version, state.error = decision['decision'], version, None
        state.revision = raw.revision
        state.usage = {'calls': [*(state.usage or {}).get('calls', []), usage]} if decision is decisions[0] else {'shared_request': decisions[0]['id']}


async def analyze(db, model, now, ask, lower=None):
    version = VERSION + ':' + model
    active = business_sources(db, 'ai')
    query = db.query(CollectedMessage).join(AIProcessing).filter(
        CollectedMessage.source_id.in_(active), CollectedMessage.published_at <= now)
    query = query.filter(AIProcessing.status == 'pending')
    if lower is not None:
        query = query.filter(CollectedMessage.published_at > lower)
    pending = query.order_by(CollectedMessage.published_at, CollectedMessage.id).all()
    supplements, sampled_sources, selected = [], set(), []
    for row in pending:
        state = db.get(AIProcessing, row.id)
        state.recall, _ = recall(row.body, row.entities or [])
        if state.recall == 'hit':
            selected.append(row)
        elif state.recall == 'needs_check' or row.source_id not in sampled_sources:
            supplements.append(row)
            if state.recall == 'unmatched':
                sampled_sources.add(row.source_id)
        else:
            state.status, state.version = 'unmatched', version
            state.result = {'reason': '无AI线索且未入跨来源抽查，保留原文', 'items': []}
    db.commit()
    for start in range(0, len(supplements), 12):
        rows = supplements[start:start + 12]
        revisions = {r.id: r.revision for r in rows}
        usage = None
        try:
            result, usage = await ask(model, SUPPLEMENT, {'messages': [message_data(r) for r in rows]})
            decisions = result.get('decisions')
            if (not isinstance(decisions, list) or any(not isinstance(d, dict) or type(d.get('id')) is not int
                or type(d.get('include')) is not bool or not isinstance(d.get('reason'), str) for d in decisions)
                or sorted(d['id'] for d in decisions) != sorted(revisions)):
                raise ValueError('召回补判格式或原文ID错误')
            db.expire_all()
            by_id = {r.id: r for r in rows}
            for d in decisions:
                raw = db.get(CollectedMessage, d['id'])
                if raw.revision != revisions[raw.id]:
                    continue
                state = db.get(AIProcessing, raw.id)
                state.usage = {'calls': [*(state.usage or {}).get('calls', []), usage]} if d is decisions[0] else {'shared_request': decisions[0]['id']}
                if d['include']:
                    selected.append(by_id[raw.id])
                else:
                    state.status, state.version, state.error = 'excluded', version, None
                    state.result = {'reason': d['reason'], 'items': []}
            db.commit()
        except Exception as exc:
            db.rollback()
            for rid, revision in revisions.items():
                if db.get(CollectedMessage, rid).revision == revision:
                    state = db.get(AIProcessing, rid)
                    state.status, state.error = 'failed', str(exc)
            actual_usage = usage or getattr(exc, 'usage', None)
            if actual_usage:
                anchor = db.get(AIProcessing, rows[0].id)
                anchor.usage = {'calls': [*(anchor.usage or {}).get('calls', []), actual_usage]}
            db.commit()
    pending = sorted(selected, key=lambda r: (r.published_at, r.id))
    for start in range(0, len(pending), 12):
        rows, copies, by_content = [], [], {}
        for row in pending[start:start + 12]:
            state = db.get(AIProcessing, row.id)
            state.recall, _ = recall(row.body, row.entities or [])
            if not row.body.strip():
                state.status, state.result = 'needs_info', {'reason': '无文字，未做OCR', 'items': []}
                continue
            previous = db.query(CollectedMessage).join(AIProcessing).filter(
                CollectedMessage.id != row.id, CollectedMessage.content_hash == row.content_hash,
                AIProcessing.version == version, AIProcessing.status.in_(['candidate', 'excluded'])).all()
            cached = next((p for p in previous if reuse_key(row) is not None and reuse_key(p) == reuse_key(row)), None)
            if cached:
                saved = db.get(AIProcessing, cached.id)
                decision = json.loads(json.dumps(saved.result))
                decision['id'] = row.id
                apply(db, [decision], version, {'reused_from': cached.id})
                continue
            key = reuse_key(row)
            if key is not None and key in by_content:
                copies.append((row, by_content[key]))
            else:
                rows.append(row)
                if key is not None:
                    by_content[key] = row
        db.commit()
        if not rows:
            continue
        candidates = db.query(AIItem).all()
        revisions = {r.id: r.revision for r in [*rows, *(p[0] for p in copies)]}
        incoming_text = '\n'.join(r.body for r in rows).casefold()
        context = [{'id': i.id, 'subject': i.subject, 'goal': i.goal, 'category': i.category,
                    'conditions': i.conditions, 'status': i.status,
                    **({'evidence': i.sources} if i.subject.casefold() in incoming_text else {})} for i in candidates]
        usage = None
        try:
            result, usage = await ask(model, ANALYZE, {'now': now.isoformat() + 'Z',
                'messages': [{**message_data(r), 'recall': db.get(AIProcessing, r.id).recall,
                              'hints': recall(r.body, r.entities or [])[1]} for r in rows], 'candidates': context})
            decisions = validate(result, rows, {i.id for i in candidates})
            db.expire_all()
            if any(db.get(CollectedMessage, rid).revision != rev for rid, rev in revisions.items()):
                continue
            apply(db, decisions, version, usage)
            for copied, representative in copies:
                saved = db.get(AIProcessing, representative.id)
                decision = json.loads(json.dumps(saved.result))
                decision['id'] = copied.id
                apply(db, [decision], version, {'reused_from': representative.id})
            db.commit()
        except Exception as exc:
            db.rollback()
            for rid, revision in revisions.items():
                current = db.get(CollectedMessage, rid)
                state = db.get(AIProcessing, rid)
                if current.revision == revision:
                    state.status, state.error = 'failed', str(exc)
            actual_usage = usage or getattr(exc, 'usage', None)
            if actual_usage:
                anchor = db.get(AIProcessing, rows[0].id)
                anchor.usage = {'calls': [*(anchor.usage or {}).get('calls', []), actual_usage]}
            db.commit()


def validate_copy(text, rows):
    validate_public_copy(text)
    for row in rows:
        path = re.escape(row.source_url.split('t.me/', 1)[-1])
        if re.search(r'(?:t\.me|telegram\.me)/(?:s/)?' + path + r'(?![\w])', text, re.I):
            raise ValueError('公开稿包含采集原帖链接')
        if row.username and '@' + row.username.casefold() in text.casefold():
            raise ValueError('公开稿包含采集署名')
        if row.source_name and re.search(r'(?:来源|转载|消息来自|via|source)\s*[:：]?\s*' + re.escape(row.source_name), text, re.I):
            raise ValueError('公开稿包含采集署名')


async def publish(db, client, config, model, now, ask):
    from delivery import make_parts, deliver
    result = {'sent': 0, 'errors': 0, 'unknown': 0}
    db.refresh(config)
    if not config.publish_enabled:
        return result
    if client is None or not config.target_id:
        raise ValueError('AI发送缺少TG客户端或目标')
    target = int(config.target_id) if config.target_id.lstrip('-').isdigit() else config.target_id.lstrip('@')
    peer = await client.get_input_entity(target)
    active = business_sources(db, 'ai')
    for item in db.query(AIItem).order_by(AIItem.created_at, AIItem.id).all():
        db.refresh(config)
        if not config.publish_enabled:
            break
        sent = db.query(AIPublication).filter_by(item_id=item.id, target_id=config.target_id).first()
        if sent and sent.status == 'sent':
            continue
        if item.status != 'candidate' or not item.sources or (item.deadline and item.deadline <= now):
            continue
        rows, messages = [], []
        for ref in item.sources:
            raw = db.get(CollectedMessage, ref['id'])
            if raw is None or raw.revision != ref['revision'] or raw.source_id not in active:
                continue
            rows.append(raw)
            messages.append(selected_message(raw, ref['excerpt']))
        if not rows:
            continue
        signature = digest([sorted({digest([m['body'], m['entities']]) for m in messages}), OUTPUT_VERSION, model])
        usage, usage_recorded = None, False
        try:
            if item.output_hash != signature or not item.output_zh:
                expected = list(item.sources)
                response, usage = await ask(model, OUTPUT, {'now': now.isoformat() + 'Z', 'subject': item.subject,
                    'goal': item.goal, 'messages': messages, 'private_sources': [
                        {'name': r.source_name, 'username': r.username, 'url': r.source_url} for r in rows]})
                item.output_usage = {'calls': [*(item.output_usage or {}).get('calls', []), usage]}
                db.commit()
                usage_recorded = True
                if type(response.get('blocked')) is not bool or not isinstance(response.get('text'), str):
                    raise ValueError('最终中文输出格式错误')
                db.expire_all()
                current = db.get(AIItem, item.id)
                if current.sources != expected or any(db.get(CollectedMessage, ref['id']).revision != ref['revision'] for ref in expected):
                    continue
                if current.status != 'candidate' or (current.deadline and current.deadline <= max(now, datetime.utcnow())):
                    continue
                if response['blocked']:
                    current.status, current.error = 'needs_info', '最终输出存在信息缺口或冲突'
                    db.commit()
                    continue
                if not response['text'].strip():
                    raise ValueError('最终中文正文为空')
                validate_copy(response['text'], rows)
                current.output_zh, current.output_hash, current.output_version = response['text'], signature, OUTPUT_VERSION
                current.error = None
                db.commit()
            validate_copy(item.output_zh, rows)
            db.refresh(item)
            if item.status != 'candidate' or (item.deadline and item.deadline <= max(now, datetime.utcnow())):
                continue
            if sent is None:
                sent = AIPublication(item_id=item.id, target_id=config.target_id, draft_hash=item.output_hash,
                                     parts=make_parts(item.output_zh), sources=list(item.sources))
                db.add(sent)
                db.commit()
            if sent.draft_hash != item.output_hash and not any(p['status'] in {'sent', 'sending', 'unknown'} for p in sent.parts):
                sent.sources = list(item.sources)
                db.commit()
            outcome = await deliver(db, client, config, sent, peer, item.output_hash, item.output_zh)
            for key in result:
                result[key] += outcome[key]
        except Exception as exc:
            db.rollback()
            current = db.get(AIItem, item.id)
            current.error = str(exc)
            failed_usage = usage or getattr(exc, 'usage', None)
            if failed_usage and not usage_recorded:
                current.output_usage = {'calls': [*(current.output_usage or {}).get('calls', []), failed_usage]}
            db.commit()
            result['errors'] += 1
    return result


async def run_pipeline(client=None, model=None, ask=None, now=None, slot=None):
    now = now or datetime.utcnow()
    ask = ask or call_model
    with RunLock('ai'):
        db = get_session()
        try:
            config = job(db)
            model = model or config.model or get_default_ai_model()
            lower = config.analysis_until
            # Advance before external work: failure/interruption must not replay this batch next time.
            config.analysis_until = now
            config.status, config.error, config.last_run = 'running', None, now
            sync_sources(db)
            db.commit()
            failures = await recover_history(client, slot=slot) if client else {}
            active = business_sources(db, "ai")
            failures = {sid: error for sid, error in failures.items() if sid in active}
            if not model:
                raise ValueError('请选择AI版块模型')
            await analyze(db, model, now, ask, lower=lower)
            config = job(db)
            delivered = await publish(db, client, config, model, now, ask)
            errors = db.query(AIProcessing).filter_by(status='failed').count()
            config.status = 'partial' if failures or errors or delivered['errors'] or delivered['unknown'] else 'success'
            config.result = {'recovery_errors': failures, 'analysis_errors': errors, 'publish': delivered,
                             'analysis_batch': {'mode': 'incremental',
                                                'after': lower.isoformat() if lower else None,
                                                'until': now.isoformat()}}
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
