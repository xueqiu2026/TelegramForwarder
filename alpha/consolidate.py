"""Review only same-project events sharing raw evidence, with cached decisions."""
import re

from models.models import AlphaEvent, CollectedMessage
from alpha.storage import digest, valid_sources
from alpha.prompts import CONSOLIDATE, CONSOLIDATE_VERSION


async def consolidate_events(db, model, now, ask):
    events = [e for e in db.query(AlphaEvent).order_by(AlphaEvent.id) if valid_sources(e, now)]
    pending = list(events)
    while pending:
        component = [pending.pop(0)]
        for event in component:
            key = re.sub(r'\W+', '', event.project.casefold())
            ids = {s['id'] for s in event.sources or []}
            matches = [other for other in pending
                       if re.sub(r'\W+', '', other.project.casefold()) == key
                       and ids & {s['id'] for s in other.sources or []}]
            component.extend(matches)
            pending = [other for other in pending if other not in matches]
        if len(component) < 2:
            continue
        signature = digest([CONSOLIDATE_VERSION, model,
                            [(e.id, e.title, e.stage, e.sources) for e in component]])
        if all((e.draft_usage or {}).get('consolidation_checked') == signature for e in component):
            continue
        rows = [db.get(CollectedMessage, rid) for rid in sorted({s['id'] for e in component for s in e.sources})]
        response, usage = await ask(model, CONSOLIDATE, {
            'events': [{'id': e.id, 'project': e.project, 'title': e.title,
                        'stage': e.stage, 'evidence': e.sources} for e in component],
            'originals': [{'id': r.id, 'body': r.body, 'entities': r.entities} for r in rows if r]})
        groups = response.get('groups')
        if not isinstance(groups, list) or any(not isinstance(g, dict) or not isinstance(g.get('ids'), list) for g in groups):
            raise ValueError('活动合并结果格式无效')
        ids = [i for g in groups for i in g['ids']]
        if any(type(i) is not int for i in ids) or sorted(ids) != sorted(e.id for e in component):
            raise ValueError('活动合并遗漏、重复或包含未提供事件')
        if any(not g['ids'] or any(not isinstance(g.get(k), str) or not g[k].strip()
                                  for k in ('project', 'title', 'stage', 'reason')) for g in groups):
            raise ValueError('活动合并缺少身份或依据')
        for group in groups:
            members = sorted((e for e in component if e.id in group['ids']), key=lambda e: e.id)
            primary = members[0]
            if len(members) > 1:
                refs = {}
                for e in members:
                    for ref in e.sources:
                        previous = refs.get(ref['id'])
                        refs[ref['id']] = ({**ref, 'evidence': previous.get('evidence', '') + '\n' + ref.get('evidence', '')}
                                           if previous else dict(ref))
                primary.sources = list(refs.values())
                primary.project, primary.title, primary.stage = group['project'], group['title'], group['stage']
                primary.draft_zh, primary.draft_hash, primary.draft_status = None, None, 'pending'
                primary.first_seen = min(e.first_seen for e in members)
                primary.draft_usage = {**(primary.draft_usage or {}), 'merged_ids': [e.id for e in members[1:]],
                                       'merge_reason': group['reason'], 'merge_usage': usage}
                for other in members[1:]:
                    other.sources, other.draft_status = [], 'merged'
                    other.draft_usage = {**(other.draft_usage or {}), 'merged_into': primary.id}
                member_ids = {e.id for e in members}
                for row in rows:
                    if not row or not row.alpha.analysis:
                        continue
                    items, merged = [], None
                    for item in row.alpha.analysis.get('events', []):
                        if item.get('event_id') in member_ids:
                            if merged is None:
                                merged = {**item, 'event_id': primary.id, 'project': primary.project,
                                          'title': primary.title, 'stage': primary.stage}
                                items.append(merged)
                            else:
                                merged['evidence'] += '\n' + item.get('evidence', '')
                        else:
                            items.append(item)
                    row.alpha.analysis = {**row.alpha.analysis, 'events': items}
            else:
                primary.draft_usage = {**(primary.draft_usage or {}), 'consolidation_checked': signature}
        db.commit()
