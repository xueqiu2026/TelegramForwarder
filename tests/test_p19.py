import asyncio
import gc
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine, text, inspect
from models.models import (Base, get_session, CollectedMessage, AlphaProcessing, AIProcessing, AIItem,
                           AIPublication, BusinessSource, BusinessMigration, AlphaEvent, AlphaJob)
from collection import save_message, sync_sources, recover_history
from alpha.pipeline import RunLock, AlreadyRunning
from ai_digest.pipeline import run_pipeline, publish, job, selected_message
from ai_digest.prompts import ANALYZE, OUTPUT, SUPPLEMENT

NOW = datetime(2026, 11, 26, 6)


def incoming(mid=1, body='Dahl AI API new account welcome credits 100 million tokens.', **kwargs):
    return SimpleNamespace(id=mid, message=body, entities=kwargs.get('entities', []), media=None,
        photo=None, document=None, grouped_id=None, reply_to_msg_id=None,
        date=kwargs.get('date', NOW - timedelta(days=5)), edit_date=kwargs.get('edit_date'))


class Client:
    def __init__(self):
        self.sent = []
    async def get_input_entity(self, target):
        from telethon.types import InputPeerChannel
        return InputPeerChannel(1, 2)
    async def get_entity(self, source):
        return SimpleNamespace(username='fixture', title='fixture')
    async def get_messages(self, peer, **kwargs):
        return []
    async def __call__(self, request):
        self.sent.append(request)
        return SimpleNamespace(id=len(self.sent))


class P19Tests(unittest.IsolatedAsyncioTestCase):
    def test_ai_service_context_recalls_genuine_grants_without_accepting_non_ai_offers(self):
        from ai_digest.keywords import recall
        for body in ('硅基流动首次实名领16元代金券，用于模型调用。',
                     'SiliconFlow new account welcome credit $10.',
                     '语音模型服务注册赠送200美元试用额度。'):
            with self.subTest(body=body):
                state, categories = recall(body)
                self.assertEqual(state, 'hit')
                self.assertTrue({'B1', 'B5'} & set(categories))
        for body in ('银行卡注册赠送16元代金券。', 'VPS signup $10 credit; free API for server management.'):
            with self.subTest(body=body):
                self.assertEqual(recall(body)[0], 'unmatched')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p19-test-')
        self.url = 'sqlite:///' + str(Path(self.tmp.name) / 'test.db')
        self.env = patch.dict(os.environ, {'DATABASE_URL': self.url})
        self.env.start()
        self.engine = create_engine(self.url)
        Base.metadata.create_all(self.engine)
        self.db = get_session()
        self.db.add(BusinessMigration(name='sources-v1'))
        self.db.add_all([BusinessSource(business=b, source_id='-1001234567890', name='Source',
            enabled_at=NOW-timedelta(days=7)) for b in ('alpha', 'ai')])
        sync_sources(self.db)
        self.db.commit()
        self.calls = []

    def tearDown(self):
        self.db.close()
        from models.models import _session_factory
        _session_factory(self.url).kw['bind'].dispose()
        _session_factory.cache_clear()
        self.engine.dispose()
        gc.collect()
        self.env.stop()
        self.tmp.cleanup()

    def store(self, mid=1, **kwargs):
        raw = save_message(self.db, '-1001234567890', 'Source', 'fixture', incoming(mid, **kwargs))
        self.db.commit()
        return raw

    async def ask(self, model, prompt, data):
        # JSON mode requires an explicit JSON instruction even when inputs lack it.
        self.assertIn('json', prompt.lower())
        self.calls.append((prompt, data))
        if prompt == SUPPLEMENT:
            return {'decisions': [{'id': m['id'], 'include': False, 'reason': 'non AI'} for m in data['messages']]}, {'total_tokens': 2}
        if prompt == ANALYZE:
            existing = data['candidates'][0]['id'] if data['candidates'] else None
            return {'decisions': [{'id': m['id'], 'decision': 'candidate', 'reason': 'explicit API grant',
                'items': [{'item_id': existing, 'new_key': 'dahl-grant', 'subject': 'Dahl',
                    'goal': 'new account credit grant', 'category': 'B1', 'conditions': 'new account',
                    'excerpt': m['body'], 'status': 'candidate', 'deadline_utc': None, 'missing': [],
                    'dedup': {'relation': 'same' if existing else 'new', 'related_item_id': existing,
                              'related_new_key': None, 'reason': 'same grant' if existing else 'new grant', 'change': ''}}]}
                for m in data['messages']]}, {'total_tokens': 10}
        return {'text': 'Dahl新用户注册可获1亿Token的API试用额度。', 'blocked': False}, {'total_tokens': 5}

    async def test_old_single_source_classifies_without_translating_or_touching_alpha(self):
        raw = self.store()
        await run_pipeline(model='fixture', ask=self.ask, now=NOW)
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing, raw.id).status, 'candidate')
        self.assertEqual(self.db.get(AlphaProcessing, raw.id).analysis_status, 'pending')
        self.assertEqual(self.db.query(AIItem).count(), 1)
        self.assertIsNone(self.db.query(AIItem).one().output_zh)
        self.assertEqual([p for p, _ in self.calls], [ANALYZE])
        self.assertFalse(any(c['name'].startswith('analysis') for c in inspect(self.engine).get_columns('collected_messages')))

    async def test_same_item_outputs_once_and_retry_does_not_call_ai(self):
        self.store()
        self.store(2, body='Dahl AI API welcome grant for new accounts: 100 million tokens; use weekly.')
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        client = Client()
        result = await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        self.assertEqual(result['publish']['sent'], 1)
        self.assertEqual(len(client.sent), 1)
        payload = [d for p, d in self.calls if p == OUTPUT][0]
        self.assertEqual(len(payload['messages']), 2)
        self.db.expire_all()
        self.assertEqual(self.db.query(AIPublication).one().parts[0]['text'], 'Dahl新用户注册可获1亿Token的API试用额度。')
        count = len(self.calls)
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(len(client.sent), 1)

    async def test_duplicate_before_analysis_reuses_in_same_batch(self):
        self.store()
        self.store(2)
        await run_pipeline(model='fixture', ask=self.ask, now=NOW)
        self.db.expire_all()
        self.assertEqual(len(self.calls[0][1]['messages']), 1)
        self.assertEqual(len(self.db.query(AIItem).one().sources), 2)
        self.assertEqual(self.db.get(AIProcessing, 2).usage['calls'][0]['reused_from'], 1)

    async def test_edit_invalidates_both_businesses_but_retains_sent_receipt(self):
        raw = self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        await run_pipeline(client=Client(), model='fixture', ask=self.ask, now=NOW)
        self.store(body='Dahl AI API credit grant revised eligibility.', edit_date=NOW)
        self.db.expire_all()
        self.assertEqual(self.db.get(CollectedMessage, raw.id).revision, 2)
        self.assertEqual(self.db.get(AIProcessing, raw.id).status, 'pending')
        self.assertEqual(self.db.get(AlphaProcessing, raw.id).analysis_status, 'pending')
        self.assertEqual(self.db.query(AIPublication).one().status, 'sent')
        self.assertIsNone(self.db.query(AIItem).one().output_zh)

    async def test_failed_batch_is_not_retried_but_new_messages_are_processed(self):
        self.store()
        async def broken(*args):
            raise RuntimeError('provider unavailable')
        await run_pipeline(model='fixture', ask=broken, now=NOW)
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing, 1).status, 'failed')
        self.store(2, body='Dahl AI API new account grant, use weekly.', date=NOW + timedelta(hours=1))
        result = await run_pipeline(model='fixture', ask=self.ask, now=NOW + timedelta(hours=6))
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing, 1).status, 'failed')
        self.assertEqual(self.db.get(AIProcessing, 2).status, 'candidate')
        self.assertEqual(result['analysis_errors'], 0)
        self.assertEqual(job(self.db).status, 'success')
        self.assertEqual([m['id'] for m in self.calls[0][1]['messages']], [2])
        self.assertEqual(len(self.calls), 1)

    async def test_pending_backlog_is_not_analyzed_in_next_batch(self):
        old = self.store()
        config = job(self.db)
        config.analysis_until = NOW
        config.last_slot = 'previous-slot'
        self.db.commit()
        fresh = self.store(2, body='Dahl AI API new account grant, recent.', date=NOW + timedelta(hours=1))
        await run_pipeline(model='fixture', ask=self.ask, now=NOW + timedelta(hours=6), slot='next-slot')
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing, old.id).status, 'pending')
        self.assertEqual(self.db.get(AIProcessing, fresh.id).status, 'candidate')
        self.assertEqual([m['id'] for m in self.calls[0][1]['messages']], [fresh.id])
        self.assertEqual(job(self.db).analysis_until, NOW + timedelta(hours=6))
        self.assertEqual(job(self.db).last_slot, 'next-slot')

    async def test_failure_before_analysis_does_not_replay_pending_on_next_batch(self):
        old = self.store()
        with patch('ai_digest.pipeline.get_default_ai_model', return_value=''):
            with self.assertRaisesRegex(ValueError, '请选择AI版块模型'):
                await run_pipeline(ask=self.ask, now=NOW)
        await run_pipeline(model='fixture', ask=self.ask, now=NOW + timedelta(hours=6))
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing, old.id).status, 'pending')
        self.assertEqual(self.calls, [])

    async def test_analysis_boundary_upgrade_preserves_previous_run_and_data(self):
        from models.models import init_db
        raw = self.store()
        config = job(self.db)
        config.last_run = NOW
        self.db.commit()
        with self.engine.begin() as connection:
            connection.execute(text('ALTER TABLE ai_job DROP COLUMN analysis_until'))
        for _ in range(2):
            upgraded = init_db()
            upgraded.dispose()
        self.db.expire_all()
        self.assertEqual(job(self.db).analysis_until, NOW)
        self.assertEqual(self.db.get(CollectedMessage, raw.id).body, raw.body)

    async def test_non_ai_unmatched_is_sampled_without_full_classification(self):
        self.store(body='普通服务器年付促销，配置和价格清单。')
        self.store(2, body='普通服务器第二条年付促销。')
        await run_pipeline(model='fixture', ask=self.ask, now=NOW)
        self.db.expire_all()
        self.assertEqual([p for p, _ in self.calls], [SUPPLEMENT])
        self.assertEqual(self.db.get(AIProcessing, 2).status, 'unmatched')
        self.assertFalse(any(p == ANALYZE for p, _ in self.calls))

    async def test_merge_retains_unresolved_gap_and_existing_deadline(self):
        from ai_digest.pipeline import apply
        raws = [self.store(), self.store(2, body='Dahl AI new account grant details.')]
        def decision(raw, eid, missing, limit):
            return {'id': raw.id, 'decision': 'candidate', 'reason': 'fixture', 'items': [{
                'item_id': eid, 'new_key': 'dahl', 'subject': 'Dahl', 'goal': 'grant', 'category': 'B1',
                'conditions': 'new account', 'excerpt': raw.body, 'status': 'candidate',
                'deadline_utc': limit, 'missing': missing,
                'dedup': {'relation': 'same' if eid else 'new', 'related_item_id': eid,
                          'related_new_key': None, 'reason': 'same grant' if eid else 'new grant', 'change': ''}}]}
        apply(self.db, [decision(raws[0], None, ['eligibility conflict'], '2026-11-25T00:00:00Z')], 'fixture', {})
        item = self.db.query(AIItem).one()
        apply(self.db, [decision(raws[1], item.id, [], None)], 'fixture', {})
        self.assertEqual(item.status, 'needs_info')
        self.assertEqual(item.deadline, datetime(2026, 11, 25))

    async def test_state_changed_during_output_does_not_send(self):
        self.store()
        await run_pipeline(model='fixture', ask=self.ask, now=NOW)
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        async def changed(*args):
            with get_session() as other:
                other.query(AIItem).one().status = 'needs_info'
                other.commit()
            return {'text': 'Dahl新用户注册可获1亿Token。', 'blocked': False}, {}
        client = Client()
        await publish(self.db, client, config, 'fixture', NOW, changed)
        self.assertEqual(client.sent, [])
        self.assertEqual(self.db.query(AIPublication).count(), 0)

    async def test_hidden_link_offsets_survive_selected_fragment(self):
        from telethon.types import MessageEntityTextUrl
        body = '😀其他消息\nDahl注册领取'
        raw = self.store(body=body, entities=[MessageEntityTextUrl(offset=14, length=2, url='https://example.org')])
        # Compute real Telegram UTF-16 offset, including the leading emoji.
        from telethon.helpers import add_surrogate
        raw.entities = [{'type': 'MessageEntityTextUrl', 'offset': len(add_surrogate('😀其他消息\nDahl注册')),
                         'length': 2, 'url': 'https://example.org'}]
        self.db.commit()
        selected = selected_message(raw, 'Dahl注册领取')
        self.assertEqual(selected['entities'][0]['offset'], 6)
        self.assertEqual(selected['entities'][0]['url'], 'https://example.org')

    async def test_expired_item_does_not_translate_or_send(self):
        self.store()
        await run_pipeline(model='fixture', ask=self.ask, now=NOW)
        item = self.db.query(AIItem).one()
        self.db.refresh(item)
        item.deadline = NOW - timedelta(hours=1)
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        result = await publish(self.db, Client(), config, 'fixture', NOW, self.ask)
        self.assertEqual(result['sent'], 0)
        self.assertEqual(len(self.calls), 1)

    async def test_locks_are_separate(self):
        with RunLock('alpha'):
            with RunLock('ai'):
                with self.assertRaises(AlreadyRunning):
                    with RunLock('ai'):
                        pass

    async def test_startup_recognizes_business_source_in_messagebox(self):
        from unittest.mock import AsyncMock
        import main
        user = SimpleNamespace(start=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(first_name='test', username=None)),
                               get_dialogs=AsyncMock(return_value=[]), get_entity=AsyncMock(),
                               _message_box=SimpleNamespace(map={1234567890: object()}))
        stop = RuntimeError('stop before starting bot')
        bot = SimpleNamespace(start=AsyncMock(side_effect=stop))
        with patch.object(main, 'user_client', user), patch.object(main, 'bot_client', bot), \
             patch.object(main.DBOperations, 'create', AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))):
            with self.assertRaisesRegex(RuntimeError, 'stop before starting bot'):
                await main.start_clients()
        user.get_entity.assert_not_awaited()

    async def test_source_union_does_not_enroll_ai_source_in_alpha(self):
        self.db.add(BusinessSource(business='ai', source_id='-1002234567890', enabled_at=NOW-timedelta(days=7)))
        sync_sources(self.db)
        raw = save_message(self.db, '-1002234567890', 'AI only', 'aionly', incoming())
        self.db.commit()
        self.assertIsNone(self.db.get(AlphaProcessing, raw.id))
        self.assertIsNotNone(self.db.get(AIProcessing, raw.id))
        self.db.get(BusinessSource, ('alpha', '-1001234567890')).active = False
        self.assertIn('-1001234567890', sync_sources(self.db))

    async def test_config_auth_export_and_no_retired_routes(self):
        import httpx
        from fastapi import FastAPI
        from rss.app.routes.auth import get_current_user
        from rss.app.routes.ai_digest import router
        from rss.app.routes.console import router as console
        app = FastAPI()
        app.include_router(router)
        app.include_router(console)
        app.dependency_overrides[get_current_user] = lambda: None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/ai-digest/status')).status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username='test')
            self.store()
            await run_pipeline(model='fixture', ask=self.ask, now=NOW)
            status = (await client.get('/api/ai-digest/status')).json()
            self.assertEqual(status['messages'][0]['body'], incoming().message)
            self.assertIsNone(status['items'][0]['output_zh'])
            self.assertEqual((await client.get('/api/ai-digest/export')).status_code, 200)
            self.assertEqual((await client.post('/api/trigger-summary-now')).status_code, 404)
            self.assertEqual((await client.get('/api/summaries')).status_code, 404)
            self.assertEqual((await client.post('/api/sandbox/apply-prompt', json={})).status_code, 404)

    async def test_migration_preserves_analysis_and_explicit_cleanup_is_idempotent(self):
        from models.p19_migration import migrate_business_data, finalize
        raw = self.store()
        self.db.query(AlphaProcessing).delete()
        self.db.commit()
        with self.engine.begin() as conn:
            for name, kind in [('analysis_status', 'VARCHAR'), ('analysis', 'JSON'), ('analysis_version', 'VARCHAR'), ('error', 'VARCHAR'), ('usage', 'JSON')]:
                conn.execute(text(f'ALTER TABLE collected_messages ADD COLUMN {name} {kind}'))
            conn.execute(text("UPDATE collected_messages SET analysis_status='done', analysis_version='old-v1', analysis=:analysis, usage=:usage"),
                         {'analysis': json.dumps({'events': [1]}), 'usage': json.dumps({'total_tokens': 19})})
            conn.execute(text('CREATE TABLE forwarded_messages (id INTEGER PRIMARY KEY)'))
            conn.execute(text('CREATE TABLE summary_history (id INTEGER PRIMARY KEY)'))
        migrate_business_data(self.engine)
        migrate_business_data(self.engine)
        self.db.expire_all()
        self.assertEqual(self.db.get(AlphaProcessing, raw.id).analysis['events'], [1])
        self.assertEqual(self.db.get(AlphaProcessing, raw.id).usage['total_tokens'], 19)
        backup = str(Path(self.tmp.name) / 'rollback.db')
        with sqlite3.connect(self.engine.url.database) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
        finalize(self.engine)
        finalize(self.engine)
        self.assertNotIn('forwarded_messages', inspect(self.engine).get_table_names())
        self.assertEqual(self.db.query(CollectedMessage).count(), 1)
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('SELECT analysis_status FROM collected_messages').fetchone()[0], 'done')
            self.assertTrue(db.execute("SELECT name FROM sqlite_master WHERE name='summary_history'").fetchone())

    async def test_distinct_technical_methods_are_not_forced_into_one_item(self):
        self.store(body='Claude API setup for Linux using endpoint A.')
        self.store(2, body='Claude API setup for Windows using endpoint B.')
        async def methods(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            for decision in result['decisions']:
                item = decision['items'][0]
                item.update(new_key=str(decision['id']), subject='Claude', goal=item['excerpt'],
                            category='T1', conditions='Linux' if decision['id'] == 1 else 'Windows')
            return result, usage
        await run_pipeline(model='fixture', ask=methods, now=NOW)
        self.db.expire_all()
        self.assertEqual(self.db.query(AIItem).count(), 2)
        self.assertEqual({i.conditions for i in self.db.query(AIItem)}, {'Linux', 'Windows'})

    async def test_same_batch_different_keys_and_aliases_merge_before_output(self):
        self.store(body='Dahl AI API grants new accounts 100 million tokens.')
        self.db.add(BusinessSource(business='ai',source_id='-1002234567890',enabled_at=NOW-timedelta(days=7)))
        sync_sources(self.db)
        save_message(self.db,'-1002234567890','Second source','second',incoming(2,body='达尔 AI API 新用户赠送一亿 tokens。'))
        self.db.commit()
        async def aliases(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == ANALYZE:
                first, second = [d['items'][0] for d in result['decisions']]
                first['new_key'] = 'english'
                second.update(new_key='chinese', subject='达尔')
                second['dedup'].update(relation='same', related_new_key='english', reason='same offer in Chinese')
            return result, usage
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        client = Client()
        await run_pipeline(client=client, model='fixture', ask=aliases, now=NOW)
        self.db.expire_all()
        self.assertEqual((self.db.query(AIItem).count(), len(client.sent)), (1, 1))
        self.assertEqual(len(self.db.query(AIItem).one().sources), 2)
        self.assertEqual(sum(p == OUTPUT for p, _ in self.calls), 1)

    async def test_cross_batch_and_midnight_duplicates_keep_sent_snapshot(self):
        from ai_digest.dedup import day_bounds
        self.store(date=NOW-timedelta(minutes=1))
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        client = Client()
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        receipt = self.db.query(AIPublication).one()
        receipt.created_at = datetime(2026, 11, 26, 15, 59)  # Beijing 23:59
        original_parts = json.loads(json.dumps(receipt.parts))
        self.db.query(AIItem).one().output_zh = 'Changed current draft must never replace published evidence'
        self.db.commit()
        for index, hour in enumerate((9, 16), start=2):
            self.store(index, body=f'Dahl AI API 新用户一亿 tokens，其他频道转载 {index}', date=NOW.replace(hour=hour))
            # Deliberately leave item_id null: the independent semantic relation must still merge it.
            async def cross_id(model, prompt, data):
                result, usage = await self.ask(model, prompt, data)
                if prompt == ANALYZE:
                    history = data['publication_history']
                    self.assertEqual(history[0]['parts'][0]['text'], original_parts[0]['text'])
                    for d in result['decisions']:
                        d['items'][0]['item_id'] = None
                return result, usage
            await run_pipeline(client=client, model='fixture', ask=cross_id, now=NOW.replace(hour=hour)+timedelta(minutes=1))
        self.db.expire_all()
        self.assertEqual((len(client.sent), self.db.query(AIItem).count()), (1, 1))
        self.assertEqual(self.db.query(AIPublication).one().parts, original_parts)
        self.assertEqual(sum(p == OUTPUT for p, _ in self.calls), 1)
        self.assertFalse(self.calls[-1][1]['publication_history'][0]['created_today'])
        self.assertEqual(day_bounds(datetime(2026, 11, 26, 16)),
                         (datetime(2026, 11, 26, 16), datetime(2026, 11, 27, 16)))

    async def test_cross_id_exact_fragment_collision_blocks_generation(self):
        raw = self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        client = Client()
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        original = self.db.query(AIItem).one()
        duplicate = AIItem(subject='another spelling', goal='same grant', category='B1',
                           status='candidate', sources=list(original.sources), dedup=dict(original.dedup))
        self.db.add(duplicate)
        self.db.commit()
        count = len(self.calls)
        await publish(self.db, client, config, 'fixture', NOW, self.ask)
        self.db.refresh(duplicate)
        self.assertEqual((len(self.calls), len(client.sent)), (count, 1))
        self.assertEqual(duplicate.status, 'needs_info')
        self.assertEqual(duplicate.dedup['blocked_by_item_id'], original.id)
        self.assertIsNone(duplicate.output_zh)
        self.assertEqual(duplicate.dedup['duplicate_of'], original.id)
        fresh = self.store(2,body='Dahl AI API welcome grant new account: one hundred million tokens.',date=NOW+timedelta(hours=1))
        async def points_to_split(model,prompt,data):
            result,usage = await self.ask(model,prompt,data)
            if prompt == ANALYZE:
                for d in result['decisions']:
                    d['items'][0]['item_id'] = duplicate.id
                    d['items'][0]['dedup']['related_item_id'] = duplicate.id
            return result,usage
        await run_pipeline(client=client,model='fixture',ask=points_to_split,now=NOW+timedelta(hours=3))
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing,fresh.id).result['items'][0]['item_id'],original.id)
        self.assertEqual(len(client.sent),1)

    async def test_distinct_methods_and_real_update_publish_separately(self):
        self.store(body='Claude API setup for Linux using endpoint A.')
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        client = Client()
        async def classified(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == SUPPLEMENT:
                for decision in result['decisions']:
                    decision['include'] = True
            elif prompt == ANALYZE:
                for decision in result['decisions']:
                    item = decision['items'][0]
                    item.update(subject='Claude', category='T1', goal=item['excerpt'])
                    if data['candidates']:
                        relation = 'update' if 'quota' in item['excerpt'] else 'distinct'
                        item.update(item_id=None, new_key='change-'+str(decision['id']))
                        item['dedup'].update(relation=relation, reason='specific platform or quota difference',
                                             change='quota increased to 200' if relation == 'update' else '')
            elif prompt == OUTPUT:
                result['text'] = '适用环境与步骤：' + data['messages'][0]['body']
            return result, usage
        await run_pipeline(client=client, model='fixture', ask=classified, now=NOW)
        self.store(2, body='Claude API setup for Windows using endpoint B.', date=NOW+timedelta(hours=1))
        await run_pipeline(client=client, model='fixture', ask=classified, now=NOW+timedelta(hours=3))
        self.store(3, body='Claude API quota increased to 200 for Linux.', date=NOW+timedelta(hours=4))
        await run_pipeline(client=client, model='fixture', ask=classified, now=NOW+timedelta(hours=6))
        self.db.expire_all()
        self.assertEqual((len(client.sent), self.db.query(AIItem).count()), (3, 3))
        items = self.db.query(AIItem).order_by(AIItem.id).all()
        self.assertEqual([i.dedup['relation'] for i in items], ['new', 'distinct', 'update'])
        self.assertEqual(items[-1].dedup['related_item_id'], items[0].id)
        output = [d for p, d in self.calls if p == OUTPUT][-1]
        self.assertEqual(output['update']['change'], 'quota increased to 200')

    async def test_same_brand_different_eligibility_does_not_merge(self):
        self.store(body='Claude AI API credit grant for new users.')
        self.store(2, body='Claude AI API credit grant for university students.')
        async def eligibility(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == ANALYZE:
                first, second = [d['items'][0] for d in result['decisions']]
                first.update(subject='Claude', new_key='new-users', conditions='new users')
                second.update(subject='Claude', new_key='students', conditions='university students')
                second['dedup'].update(relation='distinct', related_new_key='new-users', reason='different eligibility')
            return result, usage
        await run_pipeline(model='fixture', ask=eligibility, now=NOW)
        self.db.expire_all()
        items = self.db.query(AIItem).order_by(AIItem.id).all()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1].dedup['related_item_id'], items[0].id)

    async def test_review_or_missing_semantic_decision_cannot_generate(self):
        self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        async def review(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == ANALYZE:
                for d in result['decisions']:
                    d['decision'] = 'needs_info'
                    d['items'][0]['status'] = 'needs_info'
                    d['items'][0]['dedup'].update(relation='review', reason='cannot confirm eligibility')
            return result, usage
        client = Client()
        await run_pipeline(client=client, model='fixture', ask=review, now=NOW)
        self.assertEqual(client.sent, [])
        self.assertFalse(any(p == OUTPUT for p, _ in self.calls))
        self.store(2, body='Dahl AI API new account welcome credits trial.', date=NOW+timedelta(hours=1))
        async def missing(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == ANALYZE:
                del result['decisions'][0]['items'][0]['dedup']
            return result, usage
        await run_pipeline(client=client, model='fixture', ask=missing, now=NOW+timedelta(hours=3))
        self.db.expire_all()
        self.assertEqual(self.db.get(AIProcessing, 2).status, 'failed')
        self.assertEqual(client.sent, [])

    async def test_same_batch_cyclic_relation_is_rejected(self):
        from ai_digest.pipeline import validate
        raw = self.store()
        result, _ = await self.ask('fixture', ANALYZE, {'messages':[{'id':raw.id,'body':raw.body}], 'candidates':[]})
        first = result['decisions'][0]['items'][0]
        second = json.loads(json.dumps(first))
        first['new_key'], second['new_key'] = 'a', 'b'
        first['dedup'].update(relation='same', related_new_key='b')
        second['dedup'].update(relation='same', related_new_key='a')
        result['decisions'][0]['items'].append(second)
        with self.assertRaisesRegex(ValueError, '循环'):
            validate(result, [raw], set())

    async def test_restart_and_empty_batch_use_no_ai_and_do_not_resend(self):
        self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        client = Client()
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        calls = len(self.calls)
        self.db.close()
        from models.models import _session_factory
        _session_factory(self.url).kw['bind'].dispose()
        _session_factory.cache_clear()
        self.db = get_session()
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW+timedelta(hours=3))
        self.assertEqual((len(self.calls),len(client.sent)), (calls,1))

    async def test_unknown_cross_id_reserves_original_before_translation(self):
        self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        class LostReceipt(Client):
            async def __call__(self, request):
                self.sent.append(request)
                raise TimeoutError('unknown delivery')
        client = LostReceipt()
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        original = self.db.query(AIItem).one()
        # An existing historical split must not bypass its unknown publication via a fresh ID.
        self.db.add(AIItem(subject='alias',goal='grant',category='B1',status='candidate',sources=list(original.sources)))
        self.db.commit()
        calls = len(self.calls)
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW+timedelta(hours=3))
        self.assertEqual((len(self.calls),len(client.sent)), (calls,1))
        self.assertEqual(self.db.query(AIPublication).one().status, 'unknown')

    async def test_schedule_and_dedup_migration_is_once_and_preserves_boundaries(self):
        from models.models import init_db
        from models.p19_migration import migrate_ai_dedup
        raw = self.store()
        config = job(self.db)
        config.schedule_hours = [7,13,19,22]
        config.last_slot, config.analysis_until = 'kept-slot', NOW
        self.db.add(AlphaJob(id=1,schedule_hours=[7,13,19,22],last_slot='alpha-kept'))
        self.db.commit()
        with self.engine.begin() as connection:
            connection.execute(text('ALTER TABLE ai_items DROP COLUMN dedup'))
        migrate_ai_dedup(self.engine)
        self.db.expire_all()
        self.assertEqual(job(self.db).schedule_hours, [1,4,7,10,13,16,19,22])
        self.assertEqual((job(self.db).analysis_until,job(self.db).last_slot), (NOW,'kept-slot'))
        self.assertEqual(self.db.get(AlphaJob,1).schedule_hours, [7,13,19,22])
        job(self.db).schedule_hours = [8,20]
        self.db.commit()
        initialized = init_db()
        initialized.dispose()
        self.db.expire_all()
        self.assertEqual(job(self.db).schedule_hours, [8,20])
        self.assertEqual(self.db.get(CollectedMessage,raw.id).body,raw.body)

    async def test_dedup_fields_reach_existing_detail_and_export(self):
        import httpx
        from fastapi import FastAPI
        from rss.app.routes.auth import get_current_user
        from rss.app.routes.catalog import router as catalog
        from rss.app.routes.ai_digest import router as ai
        self.store()
        await run_pipeline(model='fixture',ask=self.ask,now=NOW)
        app=FastAPI()
        app.include_router(catalog);app.include_router(ai)
        app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            listing=(await client.get('/api/business/ai/items')).json()
            item_id=listing['items'][0]['id']
            detail=(await client.get(f'/api/business/ai/items/{item_id}')).json()
            exported=(await client.get('/api/ai-digest/export')).json()
            self.assertEqual(detail['dedup']['relation'],'new')
            self.assertEqual(exported['items'][0]['dedup'],detail['dedup'])
            self.assertIn('checked_publications',detail['dedup'])

    async def test_shared_background_does_not_block_a_real_update(self):
        from ai_digest.pipeline import apply
        from ai_digest.prompts import VERSION
        from ai_digest.dedup import record_checks,reservations,publication_facts
        first=self.store()
        config=job(self.db);config.publish_enabled=True;self.db.commit()
        client=Client()
        await run_pipeline(client=client,model='fixture',ask=self.ask,now=NOW)
        original=self.db.query(AIItem).one()
        fresh=self.store(2,body='Dahl AI API new account grant increased to 200 million tokens.',date=NOW+timedelta(hours=1))
        check={'relation':'update','related_item_id':original.id,'related_new_key':None,'reason':'amount changed','change':'grant increased to 200 million'}
        decisions=[{'id':r.id,'decision':'candidate','reason':'new supported grant amount','items':[{
            'item_id':None,'new_key':'new-amount','subject':'Dahl','goal':'new grant amount','category':'B1','conditions':'new account',
            'excerpt':r.body,'status':'candidate','missing':[],'deadline_utc':None,'dedup':dict(check)}]} for r in (first,fresh)]
        touched=apply(self.db,decisions,VERSION+':fixture',{},checked=True)
        record_checks(self.db,touched,config.target_id,{original.id,*[i.id for i in touched]},
                      {str(p.id):publication_facts(p) for p in reservations(self.db,config.target_id)})
        self.db.commit()
        await publish(self.db,client,config,'fixture',NOW+timedelta(hours=3),self.ask)
        self.assertEqual(len(client.sent),2)

    async def test_new_receipt_during_output_is_held_before_actual_send(self):
        self.store()
        await run_pipeline(model='fixture', ask=self.ask, now=NOW)
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        async def intervening(model, prompt, data):
            result, usage = await self.ask(model,prompt,data)
            if prompt == OUTPUT:
                with get_session() as other:
                    item = AIItem(subject='unknown other',goal='new published facts',category='B1',sources=[])
                    other.add(item);other.flush()
                    other.add(AIPublication(item_id=item.id,target_id=config.target_id,draft_hash='other',
                                           status='sent',parts=[{'text':'other publication','entities':[], 'status':'sent','message_id':999}]))
                    other.commit()
            return result, usage
        client = Client()
        await publish(self.db,client,config,'fixture',NOW,intervening)
        self.db.expire_all()
        self.assertEqual(client.sent, [])
        item = self.db.query(AIItem).filter_by(subject='Dahl').one()
        self.assertEqual(item.status,'needs_info')
        self.assertIn('未比较',item.dedup['blocked_reason'])

    async def test_real_web_to_main_bridge_runs_pipeline_and_persists_receipt(self):
        import httpx
        import socket
        with patch('telethon.TelegramClient', return_value=Client()):
            import main
        from fastapi import FastAPI
        from rss.app.routes.auth import get_current_user
        from rss.app.routes.ai_digest import router
        self.store()
        config = job(self.db)
        config.model, config.publish_enabled = 'fixture', True
        self.db.commit()
        client = Client()
        class ReadOnlyUser(Client):
            async def __call__(self, request):
                raise AssertionError('采集账号没有频道发布权限，必须使用BOT')
        reader = ReadOnlyUser()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username='test')
        with patch.dict(os.environ, {'INTERNAL_BRIDGE_PORT': str(port)}), patch('ai_digest.pipeline.call_model', self.ask):
            await main.start_internal_server(reader, client)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as web:
                    accepted = await web.post('/api/ai-digest/run')
                    self.assertEqual(accepted.status_code, 200)
                    async def completed():
                        while True:
                            self.db.expire_all()
                            if job(self.db).status in {'success', 'partial', 'failed'}:
                                return
                            await asyncio.sleep(0.01)
                    await asyncio.wait_for(completed(), timeout=5)
                    result = (await web.get('/api/ai-digest/status')).json()
                    self.assertEqual(result['publications'][0]['status'], 'sent')
                    self.assertEqual(result['publications'][0]['parts'][0]['message_id'], 1)
                    exported = (await web.get('/api/ai-digest/export')).json()
                    self.assertIn('Dahl新用户', exported['items'][0]['output_zh'])
                    self.assertEqual(len(client.sent), 1)
                    # A second actual bridge run must reuse the persisted judgment and receipt.
                    count = len(self.calls)
                    job(self.db).status = 'idle'
                    self.db.commit()
                    await web.post('/api/ai-digest/run')
                    await asyncio.wait_for(completed(), timeout=5)
                    self.assertEqual(len(self.calls), count)
                    backlog = self.store(9, body='Telnyx AI API new account welcome credits 25 dollars.')
                    # Ordinary manual run also skips old pending originals.
                    job(self.db).status = 'idle'
                    self.db.commit()
                    ordinary = await web.post('/api/ai-digest/run')
                    self.assertEqual(ordinary.status_code, 200)
                    await asyncio.wait_for(completed(), timeout=5)
                    self.db.expire_all()
                    self.assertEqual(self.db.get(AIProcessing, backlog.id).status, 'pending')
                    self.assertEqual(len(self.calls), count)
            finally:
                await main.internal_runner.cleanup()
                main.internal_runner = None
                main.engine.dispose()

    async def test_shared_slot_recovers_union_once(self):
        client = Client()
        calls = []
        original = client.get_entity
        async def entity(sid):
            calls.append(sid)
            return await original(sid)
        client.get_entity = entity
        await recover_history(client, slot='fixture-slot')
        await recover_history(client, slot='fixture-slot')
        self.assertEqual(len(calls), 1)

    async def test_unknown_receipt_does_not_retranslate_or_resend(self):
        self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        class Ambiguous(Client):
            async def __call__(self, request):
                self.sent.append(request)
                raise TimeoutError('receipt lost')
        client = Ambiguous()
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        self.db.expire_all()
        self.assertEqual(self.db.query(AIPublication).one().status, 'unknown')
        count = len(self.calls)
        self.store(2)  # Additional identical evidence must not regenerate an ambiguous publication.
        await run_pipeline(client=client, model='fixture', ask=self.ask, now=NOW)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(len(client.sent), 1)

    async def test_bot_unknown_receipt_uses_user_history_without_resending(self):
        from telethon.types import InputPeerChannel
        self.store()
        config = job(self.db)
        config.publish_enabled = True
        self.db.commit()
        class Bot(Client):
            async def __call__(self, request):
                self.sent.append(request)
                raise TimeoutError('receipt lost')
            async def get_messages(self, *args, **kwargs):
                raise AssertionError('BOT不能承担历史核对')
        class Reader(Client):
            async def get_messages(self, peer, **kwargs):
                if isinstance(peer, InputPeerChannel):
                    return [SimpleNamespace(message='Dahl新用户注册可获1亿Token的API试用额度。',
                        id=99, date=datetime.now(timezone.utc) + timedelta(seconds=1))]
                return []
        bot, user = Bot(), Reader()
        await run_pipeline(client=user, publisher=bot, model='fixture', ask=self.ask, now=NOW)
        self.db.expire_all()
        self.assertEqual(self.db.query(AIPublication).one().status, 'unknown')
        calls = len(self.calls)
        await run_pipeline(client=user, publisher=bot, model='fixture', ask=self.ask, now=NOW)
        self.db.expire_all()
        receipt = self.db.query(AIPublication).one()
        self.assertEqual(receipt.status, 'sent')
        self.assertEqual(receipt.parts[0]['message_id'], 99)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(self.calls), calls)

    async def test_ai_three_hour_schedule_and_alpha_configure_independently(self):
        from rss.app.routes.ai_digest import configure, Config
        from alpha.storage import job as alpha_job
        from alpha.scheduler import latest_slot, TZ
        user = SimpleNamespace(username='test')
        self.assertEqual(job(self.db).schedule_hours, [1, 4, 7, 10, 13, 16, 19, 22])
        self.assertEqual(alpha_job(self.db).schedule_hours, [7, 13, 19, 22])
        self.db.commit()
        with patch('utils.settings.load_ai_models', return_value={'fixture': []}):
            configure(Config(enabled=True, model='fixture', publish_enabled=False, schedule_hours=[8, 20]), user)
        self.db.expire_all()
        self.assertEqual(job(self.db).schedule_hours, [8, 20])
        self.assertEqual(alpha_job(self.db).schedule_hours, [7, 13, 19, 22])
        self.assertEqual(datetime.fromisoformat(latest_slot(TZ.localize(NOW.replace(hour=6)), [8, 20])).hour, 20)

    async def test_scheduler_continues_after_config_database_failure(self):
        from alpha.scheduler import scheduler_loop
        real_session = get_session
        attempts = []
        def sessions():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError('temporary database error')
            return real_session()
        ticks = []
        async def tick(_):
            ticks.append(1)
            if len(ticks) == 2:
                raise asyncio.CancelledError()
        with patch('alpha.scheduler.get_session', sessions), patch('alpha.scheduler.asyncio.sleep', tick):
            with self.assertRaises(asyncio.CancelledError):
                await scheduler_loop(Client(), 'ai')
        self.assertEqual(len(attempts), 2)

    async def test_explicit_migration_command_backs_up_before_schema_change(self):
        import subprocess
        import sys
        raw = self.store()
        self.db.query(AlphaProcessing).delete()
        self.db.commit()
        with self.engine.begin() as conn:
            conn.execute(text("ALTER TABLE collected_messages ADD COLUMN analysis_status VARCHAR NOT NULL DEFAULT 'pending'"))
            conn.execute(text("ALTER TABLE collected_messages ADD COLUMN analysis JSON"))
            conn.execute(text("UPDATE collected_messages SET analysis_status='done', analysis=:data"), {'data': json.dumps({'events': [9]})})
            conn.execute(text('CREATE INDEX ix_old_analysis_status ON collected_messages (analysis_status)'))
            conn.execute(text('CREATE TABLE summary_history (id INTEGER PRIMARY KEY)'))
        backup = Path(self.tmp.name) / 'before-p19.db'
        result = subprocess.run([sys.executable, 'scripts/migrate_p19.py', '--backup', str(backup), '--finalize'],
                                env=dict(os.environ), capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.db.expire_all()
        self.assertEqual(self.db.get(AlphaProcessing, raw.id).analysis_status, 'done')
        self.assertNotIn('analysis_status', {c['name'] for c in inspect(self.engine).get_columns('collected_messages')})
        restored = Path(self.tmp.name) / 'restored.db'
        with sqlite3.connect(str(backup)) as before, sqlite3.connect(str(restored)) as rollback:
            before.backup(rollback)
            self.assertEqual(rollback.execute('SELECT analysis_status FROM collected_messages').fetchone()[0], 'done')
            self.assertTrue(rollback.execute("SELECT name FROM sqlite_master WHERE name='summary_history'").fetchone())
