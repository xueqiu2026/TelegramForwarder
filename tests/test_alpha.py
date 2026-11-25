import asyncio
import gc
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from models.models import (Base, get_session, init_db, Chat, ForwardRule, ForwardedMessage,
                           CollectedMessage, AlphaEvent, AlphaCollectionState, AlphaJob)
from alpha.storage import (save_message, sync_sources, is_migrated, event_tier, valid_sources,
                           recover_history, snapshot, export_markdown, job)
from alpha.pipeline import run_pipeline, RunLock, AlreadyRunning
from alpha.scheduler import latest_slot, TZ
from alpha.prompts import ANALYZE
from alpha.editorial import TYPE_PROMPT
from utils.message_logger import log_forwarded_message


NOW = datetime(2026, 11, 8, 5)


def incoming(mid=1, body='Project Ember public mint, allowlist registration', date=None, **kwargs):
    return SimpleNamespace(id=mid, message=body, text=body, date=date or NOW - timedelta(hours=1),
                           edit_date=kwargs.get('edit_date'), entities=kwargs.get('entities', []),
                           media=kwargs.get('media'), photo=None, document=None,
                           grouped_id=kwargs.get('grouped_id'), reply_to_msg_id=kwargs.get('reply_id'))


class AlphaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tgbot-alpha-test-')
        self.env = patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///' + str(Path(self.tmp.name) / 'isolated.db')})
        self.env.start()
        self.engine = create_engine(os.environ['DATABASE_URL'])
        Base.metadata.create_all(self.engine)
        self.db = get_session()
        self.db.add_all([Chat(id=1, telegram_chat_id='1234567890', name='Source A'),
                         Chat(id=2, telegram_chat_id='2234567890', name='Source B'),
                         Chat(id=3, telegram_chat_id='3234567890', name='Target')])
        self.db.add_all([ForwardRule(id=1, source_chat_id=1, target_chat_id=3, enable_collect=True,
                                     enable_forward=False, is_summary=False),
                         ForwardRule(id=2, source_chat_id=2, target_chat_id=3, enable_collect=True,
                                     enable_forward=False, is_summary=False)])
        self.db.commit()
        sync_sources(self.db, NOW - timedelta(hours=40))
        self.db.commit()
        self.calls = []

    def tearDown(self):
        self.db.close()
        from models.models import _session_factory
        _session_factory(os.environ['DATABASE_URL']).kw['bind'].dispose()
        _session_factory.cache_clear()
        self.engine.dispose()
        gc.collect()
        self.env.stop()
        self.tmp.cleanup()

    def store(self, sid='-1001234567890', mid=1, body=None, **kwargs):
        row = save_message(self.db, sid, 'Source A' if sid == '-1001234567890' else 'Source B',
                           'channel_a' if sid == '-1001234567890' else 'channel_b',
                           incoming(mid, **({'body': body} if body is not None else {}), **kwargs))
        self.db.commit()
        return row

    async def ask(self, model, prompt, data):
        self.calls.append((prompt, data))
        if prompt == TYPE_PROMPT:
            return {'types': [{'id': e['id'], 'content_type': 'sale', 'detail_level': 'standard',
                               'event_status': 'open', 'type_reason': 'mint event', 'context_gaps': []}
                              for e in data['events']]}, {'total_tokens': 7}
        if prompt == ANALYZE:
            existing = data['candidates'][0]['id'] if data['candidates'] else None
            return {'decisions': [{'id': m['id'], 'verdict': 'crypto', 'reason': 'specific mint',
                                  'events': [{'event_id': existing, 'new_key': 'ember',
                                              'project': 'Ember', 'title': 'Genesis mint', 'stage': 'mint-1',
                                              'change': 'duplicate' if existing else 'new',
                                              'evidence': m['body']}]} for m in data['messages']]}, {'total_tokens': 19}
        return {'draft': '**Ember｜Genesis Mint**\n\n白名单登记已开放。\n参与：https://example.org/mint',
                'closed': False, 'blocked': False, 'reason': ''}, {'total_tokens': 21}

    async def run_job(self, ask=None, now=NOW):
        return await run_pipeline(model='test-model', ask=ask or self.ask, now=now)

    def test_default_model_uses_catalog_without_overriding_explicit_choice(self):
        from utils.settings import get_default_ai_model
        catalog = {'gpt': ['gpt-6.1-sol'], 'grok': ['grok-4.7']}
        with patch('utils.settings.load_ai_models', return_value=catalog):
            with patch.dict(os.environ, {'DEFAULT_AI_MODEL': 'gpt-5.5'}):
                self.assertEqual(get_default_ai_model(), 'gpt-6.1-sol')
                self.assertEqual(get_default_ai_model('grok'), 'grok-4.7')
            with patch.dict(os.environ, {'DEFAULT_AI_MODEL': 'grok-4.7'}):
                self.assertEqual(get_default_ai_model(), 'grok-4.7')
                self.assertEqual(get_default_ai_model('gpt'), 'gpt-6.1-sol')

    async def test_public_channel_name_leak_is_not_accepted(self):
        self.store()
        self.store(sid='-1002234567890')
        async def leaking(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] += '\nSource A'
            return result, usage
        await self.run_job(leaking)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')

    async def test_window_downgrade_hides_ready_draft_on_web(self):
        self.store(date=NOW - timedelta(hours=35))
        self.store(sid='-1002234567890', date=NOW - timedelta(hours=1))
        await self.run_job()
        later = snapshot(NOW + timedelta(hours=2))['events'][0]
        self.assertEqual(later['tier'], 'single')
        self.assertIsNone(later['draft'])

    async def test_can_disable_job_after_configured_model_removed(self):
        from rss.app.routes.alpha import configure, Config
        config = job(self.db)
        config.enabled, config.model = True, 'removed-model'
        self.db.commit()
        configure(Config(enabled=False, model='removed-model'), SimpleNamespace(username='test'))
        self.db.expire_all()
        self.assertFalse(job(self.db).enabled)

    async def test_source_message_link_variants_are_rejected(self):
        self.store()
        self.store(sid='-1002234567890')
        async def leaking(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] += '\nhttps://t.me/s/channel_a/1'
            return result, usage
        await self.run_job(leaking)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')

    async def test_draft_receives_only_this_events_evidence(self):
        self.store(body='Ember mint open. OtherProject private sale open.')
        self.store(sid='-1002234567890', body='Ember mint and OtherProject private sale.')
        await self.run_job()
        payload = [data for prompt, data in self.calls if prompt not in (ANALYZE, TYPE_PROMPT)][0]
        self.assertIn('event_evidence', payload)
        self.assertEqual({e['id'] for e in payload['event_evidence']}, {1, 2})

    async def test_actual_listener_collect_only_never_enters_forwarders(self):
        from message_listener import handle_user_message
        from telethon.tl.types import Channel, ChatPhotoEmpty
        from unittest.mock import AsyncMock
        entity = Channel(id=1234567890, title='Source A', photo=ChatPhotoEmpty(), date=NOW,
                         username='channel_a')
        class Event:
            message = incoming(50)
            chat = entity
            chat_id = -1001234567890
            sender_id = 42
            async def get_chat(self): return entity
        bot_forward, user_forward = AsyncMock(), AsyncMock()
        with patch('message_listener.process_forward_rule', bot_forward), \
             patch('message_listener.user_handler.process_forward_rule', user_forward):
            await handle_user_message(Event(), SimpleNamespace(), SimpleNamespace())
        bot_forward.assert_not_awaited()
        user_forward.assert_not_awaited()
        self.db.expire_all()
        self.assertEqual(self.db.query(CollectedMessage).count(), 1)
        self.assertEqual(self.db.query(ForwardedMessage).count(), 0)

    async def test_candidate_originals_only_for_related_project_or_alias(self):
        from alpha.pipeline import candidate_context
        from models.models import AlphaProjectAlias
        current = self.store(body='FungoLabs allowlist registration')
        related = self.store(mid=2, body='Fungo official allowlist details')
        unrelated = self.store(mid=3, body='OtherProject unrelated full announcement')
        def event(row, project):
            return AlphaEvent(id=row.id, project=project, title='Allowlist', stage='registration',
                first_seen=row.published_at, sources=[{'id': row.id, 'source_id': row.source_id,
                'revision': row.revision, 'date': row.published_at.isoformat()}])
        events = [event(related, 'Fungo'), event(unrelated, 'OtherProject')]
        data = candidate_context(self.db, events, [current],
                                 [AlphaProjectAlias(alias='FungoLabs', project='Fungo', evidence='test')], NOW)
        self.assertTrue(data[0]['detail_available'])
        self.assertEqual(data[0]['originals'][0]['body'], related.body)
        self.assertFalse(data[1]['detail_available'])
        self.assertNotIn('originals', data[1])

    async def test_project_name_matching_channel_name_is_not_source_attribution(self):
        first = self.store()
        first.source_name = 'Ember'
        self.db.commit()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'ready')

    async def test_short_project_name_does_not_match_inside_claim(self):
        from alpha.pipeline import candidate_context
        row = self.store(body='Claim an unrelated mint allocation')
        event = AlphaEvent(id=1, project='AI', title='AI registration', stage='mint',
            first_seen=row.published_at, sources=[{'id': row.id, 'source_id': row.source_id,
            'revision': row.revision, 'date': row.published_at.isoformat()}])
        result = candidate_context(self.db, [event], [row], [], NOW)
        self.assertFalse(result[0]['detail_available'])

    async def test_bot_entry_collects_same_message_once(self):
        from message_listener import handle_bot_message
        from alpha.storage import collect_event
        from telethon.tl.types import Channel, ChatPhotoEmpty
        from unittest.mock import AsyncMock
        entity = Channel(id=1234567890, title='Source A', photo=ChatPhotoEmpty(), date=NOW,
                         username='channel_a')
        class Event:
            message = incoming(60)
            chat = entity
            chat_id = -1001234567890
            sender_id = 42
            async def get_chat(self): return entity
        command = AsyncMock()
        with patch('message_listener.bot_handler.handle_command', command):
            await handle_bot_message(Event(), SimpleNamespace())
        self.db.expire_all()
        self.assertEqual(self.db.query(CollectedMessage).count(), 1)
        await collect_event(Event())
        self.db.expire_all()
        self.assertEqual(self.db.query(CollectedMessage).count(), 1)
        self.assertEqual(self.db.query(ForwardedMessage).count(), 0)

    async def test_project_channel_name_still_blocks_explicit_attribution(self):
        first = self.store()
        first.source_name = 'Ember'
        self.db.commit()
        self.store(sid='-1002234567890')
        async def leaking(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] += '\n来源：Ember'
            return result, usage
        await self.run_job(leaking)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')

    async def test_shared_campaign_steps_merge_and_rewrite_raw_links(self):
        from alpha.consolidate import consolidate_events
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.db.expire_all()
        first = self.db.query(AlphaEvent).one()
        second = AlphaEvent(project=first.project, title='Burn Box', stage='burn',
                            first_seen=first.first_seen, sources=list(first.sources))
        self.db.add(second)
        self.db.flush()
        second_id = second.id
        for row in self.db.query(CollectedMessage):
            row.analysis = {**row.analysis, 'events': [*row.analysis['events'],
                {**row.analysis['events'][0], 'event_id': second.id}]}
        self.db.commit()
        async def review(model, prompt, data):
            return {'groups': [{'ids': [first.id, second_id], 'project': first.project,
                                'title': 'Mint and redeem', 'stage': 'campaign',
                                'reason': 'same participation flow'}]}, {}
        await consolidate_events(self.db, 'test', NOW, review)
        self.assertEqual(first.draft_status, 'pending')
        self.assertEqual(len(first.sources), 2)
        self.assertEqual(self.db.get(AlphaEvent, second_id).draft_status, 'merged')
        self.assertFalse(self.db.get(AlphaEvent, second_id).sources)
        for row in self.db.query(CollectedMessage):
            self.assertEqual([x['event_id'] for x in row.analysis['events']], [first.id])

    async def test_independent_rounds_stay_separate_and_review_is_cached(self):
        from alpha.consolidate import consolidate_events
        self.store()
        await self.run_job()
        self.db.expire_all()
        first = self.db.query(AlphaEvent).one()
        second = AlphaEvent(project=first.project, title='Round 2', stage='round2',
                            first_seen=first.first_seen, sources=list(first.sources))
        self.db.add(second)
        self.db.commit()
        calls = []
        async def review(model, prompt, data):
            calls.append(data)
            return {'groups': [{'ids': [e['id']], 'project': e['project'], 'title': e['title'],
                                'stage': e['stage'], 'reason': 'different rounds'} for e in data['events']]}, {}
        await consolidate_events(self.db, 'test', NOW, review)
        await consolidate_events(self.db, 'test', NOW, review)
        self.assertEqual(len(calls), 1)
        self.assertTrue(second.sources)

    async def test_picture_publication_uses_original_media_and_caption_limit(self):
        from alpha.publish import publish_ready, make_parts
        from models.models import AlphaPublication
        from telethon import types, functions
        from unittest.mock import AsyncMock
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.db.expire_all()
        row = self.db.query(CollectedMessage).first()
        row.media = {'type': 'MessageMediaPhoto', 'id': '456'}
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.commit()
        client = AsyncMock()
        client.get_input_entity.return_value = types.InputPeerChannel(3234567890, 1)
        client.get_messages.return_value = SimpleNamespace(photo=types.Photo(
            id=456, access_hash=789, file_reference=b'fresh', date=NOW, sizes=[], dc_id=1))
        client.return_value = types.UpdateShortSentMessage(id=123, pts=1, pts_count=1, date=NOW)
        result = await publish_ready(self.db, client, config, NOW)
        self.assertEqual(result['sent'], 1)
        request = client.call_args.args[0]
        self.assertIsInstance(request, functions.messages.SendMediaRequest)
        self.assertEqual(request.media.id.id, 456)
        self.assertEqual(self.db.query(AlphaPublication).one().parts[0]['photo']['id'], '456')
        parts = make_parts('**Title**\n' + '中文😀 ' * 1000, {'id': '456'})
        self.assertTrue(all(len(p['text'].encode('utf-16-le')) // 2 <= 1024 for p in parts))
        self.assertNotIn('photo', parts[1])

    async def test_composite_photo_is_not_assigned_to_unrelated_projects(self):
        from alpha.publish import event_photo
        self.store()
        await self.run_job()
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        row = self.db.query(CollectedMessage).one()
        row.media = {'type': 'MessageMediaPhoto', 'id': '456'}
        row.analysis = {**row.analysis, 'events': [*row.analysis['events'], {'project': 'Other project'}]}
        self.db.commit()
        self.assertIsNone(event_photo(self.db, event))

    async def test_new_draft_photo_failure_retry_never_sends_old_queued_text(self):
        from alpha.publish import publish_ready, make_parts
        from models.models import AlphaPublication
        from telethon import types
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        row = self.db.query(CollectedMessage).first()
        row.media = {'type': 'MessageMediaPhoto', 'id': '456'}
        event.draft_zh, event.draft_hash = 'NEW verified terms', 'new-hash'
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.add(AlphaPublication(event_id=event.id, target_id=config.target_id,
                                    draft_hash='old-hash', parts=make_parts('OLD terms'), status='queued'))
        self.db.commit()
        class Client:
            fail = True
            texts = []
            async def get_input_entity(self, target): return types.InputPeerChannel(3234567890, 1)
            async def get_messages(self, *args, **kwargs):
                if self.fail: raise ConnectionError('temporary photo fetch error')
                return SimpleNamespace(photo=types.Photo(id=456, access_hash=789,
                    file_reference=b'fresh', date=NOW, sizes=[], dc_id=1))
            async def __call__(self, request):
                self.texts.append(request.message)
                return types.UpdateShortSentMessage(id=456, pts=1, pts_count=1, date=NOW)
        client = Client()
        self.assertEqual((await publish_ready(self.db, client, config, NOW))['errors'], 1)
        self.db.expire_all()
        publication = self.db.query(AlphaPublication).one()
        self.assertEqual(publication.draft_hash, 'new-hash')
        self.assertEqual(publication.parts[0]['text'], 'NEW verified terms')
        client.fail = False
        self.assertEqual((await publish_ready(self.db, client, config, NOW))['sent'], 1)
        self.assertEqual(client.texts, ['NEW verified terms'])

    async def test_publish_once_and_reconcile_unknown_without_resend(self):
        from alpha.publish import publish_ready
        from models.models import AlphaPublication
        from telethon import types
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.db.expire_all()
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.commit()
        class Client:
            calls = 0
            fail = True
            recent = []
            async def get_input_entity(self, target): return types.InputPeerChannel(3234567890, 1)
            async def get_messages(self, peer, **kwargs): return self.recent
            async def __call__(self, request):
                self.calls += 1
                self.recent = [SimpleNamespace(id=123, message=request.message,
                    date=datetime.now(timezone.utc) + timedelta(seconds=1))]
                if self.fail: raise ConnectionError('reply lost after send')
                return types.UpdateShortSentMessage(id=123, pts=1, pts_count=1, date=NOW)
        client = Client()
        result = await publish_ready(self.db, client, config, NOW)
        self.assertEqual(result['unknown'], 1)
        self.assertEqual(self.db.query(AlphaPublication).one().status, 'unknown')
        result = await publish_ready(self.db, client, config, NOW)
        self.assertEqual(result['sent'], 1)
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.db.query(AlphaPublication).one().parts[0]['message_id'], 123)
        await publish_ready(self.db, client, config, NOW)
        self.assertEqual(client.calls, 1)

    async def test_publication_waits_for_double_sources_and_keeps_formatting_parts(self):
        from alpha.publish import publish_ready, make_parts
        self.store()
        await self.run_job()
        self.db.expire_all()
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.commit()
        class Client:
            async def get_input_entity(self, target): return target
            async def __call__(self, request): raise AssertionError('single source must not send')
        self.assertEqual((await publish_ready(self.db, Client(), config, NOW))['sent'], 0)
        parts = make_parts('**Ember**\n' + '中文😀 ' * 2000)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p['text'].encode('utf-16-le')) // 2 <= 3500 for p in parts))
        self.assertTrue(parts[0]['entities'])

    async def test_old_summary_entry_disabled_for_migrated_target(self):
        from scheduler.summary_scheduler import SummaryScheduler
        config = job(self.db)
        config.target_id = '-1003234567890'
        self.db.commit()
        scheduler = SummaryScheduler(SimpleNamespace(), SimpleNamespace())
        await scheduler._execute_aggregated_summary('3234567890', {})

    async def test_editorial_process_stays_out_of_public_body(self):
        self.store()
        self.store(sid='-1002234567890')
        async def editorial(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] += '\n资料存在冲突，暂不能统一表述。'
            return result, usage
        await self.run_job(editorial)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')

    async def test_timezone_translation_note_is_blocked_but_original_dates_are_allowed(self):
        self.store()
        self.store(sid='-1002234567890')
        async def timezone_note(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] += '\n具体开放时刻及适用时区待公布，尚不能对应为北京时间。'
            return result, usage
        await self.run_job(timezone_note)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')
        async def dates_only(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] = '**Squeek Chain｜Genesis Mint预告**\n\n11月10日开放存款，11月12日关闭存款，11月13日开放领取。'
                result['internal_notes'] = '时区未提供，不能换算为北京时间。'
            return result, usage
        await self.run_job(dates_only)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'ready')
        self.assertNotIn('换算', export_markdown(now=NOW))
        self.assertIn('不能换算为北京时间', export_markdown(internal=True, now=NOW))

    async def test_shared_public_copy_check_catches_process_notes_preserves_rules(self):
        from alpha.public_copy import validate_public_copy
        bad = [
            '原文未提供领取的时区。', '源消息没有提供具体费用。', '具体时间及时区待确认。',
            '不同来源的开放时间存在冲突。', '未读取图片，以下仅按文字整理。',
            '本次翻译保留可确认信息。', '以下是整理后的中文文案：',
            '**内部备注**：这里只写一部分。', '译者注：此处不做推测。',
            'context_gaps: missing deadline', '<think>先核对资料</think>',
            'The source text does not specify the timezone.',
            'Unable to convert this time to Beijing time.',
        ]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_public_copy(body)
        good = [
            '领取需支付Gas，领取没有截止期限。',
            '存款锁定至销售结束，未获配的ETH需主动领取退款。',
            '具体时间待公布。', '仅开放配额查询，尚未开放领取。',
            'Gold优先，其次Silver；每钱包最多10个。',
            '不同阶段采用不同价格，公售0.05 ETH。',
            '北京时间11月10日02:00前销毁，04:00起领取。',
        ]
        for body in good:
            with self.subTest(body=body):
                validate_public_copy(body)

    async def test_stored_process_note_hidden_from_public_and_stopped_before_send(self):
        from alpha.publish import publish_ready
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        event.draft_zh += '\n译者注：原文未注明具体时区。'
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.commit()
        self.assertIsNone(snapshot(NOW)['events'][0]['draft'])
        self.assertNotIn('译者注', export_markdown(now=NOW))
        self.assertIn('译者注', export_markdown(internal=True, now=NOW))
        class Client:
            async def get_input_entity(self, target): return target
            async def __call__(self, request): raise AssertionError('process note must never send')
        result = await publish_ready(self.db, Client(), config, NOW)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(event.draft_status, 'failed')
        self.assertIsNone(event.draft_hash)
        config.publish_enabled = False
        self.db.commit()
        await self.run_job()
        self.db.expire_all()
        self.assertEqual(self.db.get(AlphaEvent, event.id).draft_status, 'ready')

    async def test_internal_copy_notes_are_not_in_public_export(self):
        self.store()
        self.store(sid='-1002234567890')
        async def notes(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['internal_notes'] = '资料冲突，仅保留可确认信息'
            return result, usage
        await self.run_job(notes)
        self.assertNotIn('资料冲突', export_markdown(now=NOW))
        self.assertIn('资料冲突', export_markdown(internal=True, now=NOW))

    async def test_unique_album_entities_and_no_legacy_write(self):
        link = SimpleNamespace(offset=0, length=4, url='https://example.org/register')
        self.store(grouped_id=999, entities=[link])
        self.store(grouped_id=999, entities=[link])
        self.store(mid=2, grouped_id=999)
        self.assertEqual(self.db.query(CollectedMessage).count(), 2)
        self.assertEqual(self.db.query(CollectedMessage).first().entities[0]['url'], link.url)
        rule = self.db.get(ForwardRule, 1)
        log_forwarded_message(rule, SimpleNamespace(message=incoming()))
        self.assertEqual(self.db.query(ForwardedMessage).count(), 0)
        rule.enable_collect = False
        sync_sources(self.db, NOW)
        self.db.commit()
        self.assertTrue(is_migrated(self.db, rule))
        log_forwarded_message(rule, SimpleNamespace(message=incoming()))
        self.assertEqual(self.db.query(ForwardedMessage).count(), 0)

    async def test_collect_only_listener_covers_album_and_edit(self):
        from alpha.storage import collect_event
        from telethon.tl.types import Channel, ChatPhotoEmpty
        entity = Channel(id=1234567890, title='Source A', photo=ChatPhotoEmpty(), date=NOW,
                         username='channel_a')
        class Event:
            def __init__(self, message): self.message = message
            async def get_chat(self): return entity
        await collect_event(Event(incoming(10, grouped_id=7)))
        await collect_event(Event(incoming(11, grouped_id=7)))
        self.db.expire_all()
        self.assertEqual(self.db.query(CollectedMessage).count(), 2)
        await collect_event(Event(incoming(10, body='New refund rules', grouped_id=7, edit_date=NOW)))
        self.db.expire_all()
        self.assertEqual(self.db.query(CollectedMessage).filter_by(message_id=10).one().revision, 2)
        self.assertEqual(self.db.query(ForwardedMessage).count(), 0)

    async def test_cross_batch_second_source_and_reexport_without_ai(self):
        self.store()
        await self.run_job()
        self.assertEqual(len(self.calls), 1)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertEqual(event_tier(event, NOW), 'single')
        self.store(sid='-1002234567890', body='Ember Genesis mint and allowlist are open')
        await self.run_job()
        self.assertEqual(len(self.calls), 4)  # second analysis, post-threshold typing, then draft
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).count(), 1)
        self.assertEqual(event_tier(self.db.query(AlphaEvent).one(), NOW), 'main')
        self.assertIn('Ember', export_markdown(now=NOW))
        self.assertNotIn('channel_a', export_markdown(now=NOW))
        self.assertIn('channel_a', export_markdown(internal=True, now=NOW))
        await self.run_job()
        self.assertEqual(len(self.calls), 4)

    async def test_exact_copy_reuses_analysis_and_keeps_second_source(self):
        self.store()
        await self.run_job()
        self.store(sid='-1002234567890')
        await self.run_job()
        self.assertEqual(sum(p == ANALYZE for p, _ in self.calls), 1)
        self.db.expire_all()
        self.assertEqual(event_tier(self.db.query(AlphaEvent).one(), NOW), 'main')
        self.assertEqual(self.db.query(CollectedMessage).filter_by(source_id='-1002234567890').one().usage,
                         {'reused_from': 1})

    async def test_same_batch_identical_text_sent_only_once(self):
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        analysis = [data for prompt, data in self.calls if prompt == ANALYZE]
        self.assertEqual(len(analysis), 1)
        self.assertEqual(len(analysis[0]['messages']), 1)
        self.db.expire_all()
        self.assertEqual(len(self.db.query(AlphaEvent).one().sources), 2)

    async def test_relative_dates_are_not_reused(self):
        self.store(body='Ember mint tomorrow')
        await self.run_job()
        self.store(sid='-1002234567890', body='Ember mint tomorrow')
        await self.run_job()
        self.assertEqual(sum(p == ANALYZE for p, _ in self.calls), 2)

    async def test_deadline_expires_without_another_model_call(self):
        self.store()
        self.store(sid='-1002234567890', body='Ember registration closes soon')
        async def deadline(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT): result['deadline_utc'] = (NOW + timedelta(hours=1)).isoformat() + 'Z'
            return result, usage
        await self.run_job(deadline)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertEqual(event_tier(event, NOW), 'main')
        self.assertEqual(event_tier(event, NOW + timedelta(hours=2)), 'closed')
        calls = len(self.calls)
        await self.run_job(deadline, now=NOW+timedelta(hours=2))
        self.assertEqual(len(self.calls), calls)

    async def test_36h_boundary_and_original_time(self):
        a = self.store(date=NOW - timedelta(hours=36))
        b = self.store(sid='-1002234567890', date=NOW - timedelta(hours=1))
        event = AlphaEvent(project='Ember', title='Mint', stage='mint', first_seen=a.published_at,
                           sources=[{'id': a.id, 'source_id': a.source_id, 'revision': 1,
                                     'date': a.published_at.isoformat()},
                                    {'id': b.id, 'source_id': b.source_id, 'revision': 1,
                                     'date': b.published_at.isoformat()}])
        self.db.add(event)
        self.db.commit()
        self.assertEqual(event_tier(event, NOW), 'main')
        self.assertEqual(event_tier(event, NOW + timedelta(seconds=1)), 'single')
        self.store(date=NOW - timedelta(hours=36), edit_date=NOW, body='Edited old text')
        self.assertEqual(self.db.get(CollectedMessage, a.id).published_at, NOW - timedelta(hours=36))

    async def test_edit_detaches_evidence_and_clears_draft(self):
        self.store()
        self.store(sid='-1002234567890', body='Ember mint registration')
        await self.run_job()
        self.db.expire_all()
        self.assertEqual(event_tier(self.db.query(AlphaEvent).one(), NOW), 'main')
        self.store(body='Unrelated news', edit_date=NOW)
        self.assertEqual(event_tier(self.db.query(AlphaEvent).one(), NOW), 'single')
        self.assertIsNone(self.db.query(AlphaEvent).one().draft_zh)
        self.assertEqual(self.db.get(CollectedMessage, 1).analysis_status, 'pending')

    async def test_draft_failure_only_retries_draft(self):
        self.store()
        self.store(sid='-1002234567890', body='Ember mint registration')
        fail = True
        async def ask(model, prompt, data):
            if prompt not in (ANALYZE, TYPE_PROMPT) and fail:
                self.calls.append((prompt, data))
                raise RuntimeError('test network failure')
            return await self.ask(model, prompt, data)
        await self.run_job(ask)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')
        fail = False
        await self.run_job(ask)
        self.assertEqual(sum(p == ANALYZE for p, _ in self.calls), 1)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'ready')

    async def test_invalid_json_reference_stays_failed_and_usage_saved(self):
        self.store()
        async def bad(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            result['decisions'][0]['events'][0]['event_id'] = 999
            return result, usage
        await self.run_job(bad)
        self.db.expire_all()
        row = self.db.query(CollectedMessage).one()
        self.assertEqual(row.analysis_status, 'failed')
        self.assertEqual(row.usage['calls'][0]['total_tokens'], 19)
        self.assertEqual(self.db.query(AlphaEvent).count(), 0)

    async def test_public_source_leak_is_not_accepted(self):
        self.store()
        self.store(sid='-1002234567890', body='Ember mint registration')
        async def leak(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result['draft'] += '\nTG 来源：https://t.me/channel_a/1'
            return result, usage
        await self.run_job(leak)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'failed')
        self.assertNotIn('t.me/channel_a', export_markdown(now=NOW))

    async def test_recovery_25_messages_failure_and_restart(self):
        state = self.db.get(AlphaCollectionState, '-1001234567890')
        state.enabled_at = NOW - timedelta(hours=2)
        self.db.get(ForwardRule, 2).enable_collect = False
        self.db.commit()
        class Client:
            fail = True
            async def get_entity(self, sid): return SimpleNamespace(username='channel_a', title='Source A')
            async def get_messages(self, entity, **kwargs):
                return [incoming(1, date=NOW-timedelta(hours=3))] if 'offset_date' in kwargs else [incoming(26)]
            async def iter_messages(self, entity, **kwargs):
                for mid in range(2, 27):
                    if self.fail and mid == 15: raise RuntimeError('test disconnect')
                    if mid > kwargs['min_id']: yield incoming(mid)
        client = Client()
        self.assertTrue(await recover_history(client))
        self.db.expire_all()
        self.assertEqual(self.db.get(AlphaCollectionState, '-1001234567890').scanned_id, 1)
        client.fail = False
        self.assertEqual(await recover_history(client), {})
        self.db.expire_all()
        self.assertEqual(self.db.query(CollectedMessage).count(), 25)
        self.assertEqual(self.db.get(AlphaCollectionState, '-1001234567890').scanned_id, 26)
        await recover_history(client)
        self.assertEqual(self.db.query(CollectedMessage).count(), 25)
        self.assertEqual(self.db.query(ForwardedMessage).count(), 0)

    async def test_rules_deleted_keep_raw_and_excluded_source(self):
        self.store()
        self.db.delete(self.db.get(ForwardRule, 1))
        self.db.commit()
        sync_sources(self.db, NOW)
        self.db.commit()
        self.assertFalse(self.db.get(AlphaCollectionState, '-1001234567890').active)
        self.assertEqual(self.db.query(CollectedMessage).count(), 1)
        self.db.add(Chat(id=10, telegram_chat_id='1861267084', name='Korean Alpha'))
        self.db.add(ForwardRule(source_chat_id=10, target_chat_id=3, enable_collect=True))
        self.db.commit()
        self.assertNotIn('-1001861267084', sync_sources(self.db, NOW))

    async def test_lock_and_schedule_defaults(self):
        with RunLock():
            with self.assertRaises(AlreadyRunning):
                with RunLock(): pass
        self.assertFalse(job(self.db).enabled)
        for hour, expected in [(6, 19), (7, 7), (12, 7), (13, 13), (18, 13), (19, 19)]:
            slot = datetime.fromisoformat(latest_slot(TZ.localize(NOW.replace(hour=hour))))
            self.assertEqual(slot.hour, expected)

    async def test_edit_during_analysis_never_marks_old_result_done(self):
        row = self.store()
        async def edit(model, prompt, data):
            other = get_session()
            try:
                save_message(other, '-1001234567890', 'Source A', 'channel_a', incoming(body='Edited mint terms', edit_date=NOW))
                other.commit()
            finally: other.close()
            return await self.ask(model, prompt, data)
        await self.run_job(edit)
        self.db.expire_all()
        self.assertEqual(self.db.get(CollectedMessage, row.id).analysis_status, 'pending')
        self.assertEqual(self.db.query(AlphaEvent).count(), 0)

    async def test_types_only_after_threshold_and_cached_until_facts_change(self):
        self.store()
        await self.run_job()
        self.assertFalse(any(p == TYPE_PROMPT for p, _ in self.calls))
        self.store(sid='-1002234567890')
        await self.run_job()
        self.assertEqual(sum(p == TYPE_PROMPT for p, _ in self.calls), 1)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertEqual(event.content_type, 'sale')
        self.assertEqual(snapshot(NOW)['events'][0]['content_type'], 'sale')
        calls = len(self.calls)
        await self.run_job()
        self.assertEqual(len(self.calls), calls)
        self.store(sid='-1002234567890', body='Ember revised price 0.02 ETH', edit_date=NOW)
        self.assertIsNone(self.db.get(AlphaEvent, event.id).draft_zh)
        await self.run_job()
        self.assertEqual(sum(p == TYPE_PROMPT for p, _ in self.calls), 2)

    async def test_all_routes_include_only_the_matching_example(self):
        labels = {'tutorial': '活动参与教程', 'sale': '发行与申购', 'claim_check': '资格查询与领取',
                  'allocation': '积分与分配规则', 'update': '重要进展与提醒', 'ipo': '全球IPO',
                  'launchpad': '平台上线预告'}
        for kind, label in labels.items():
            with self.subTest(kind=kind):
                self.store(body='Ember details for ' + kind, edit_date=NOW)
                self.store(sid='-1002234567890', body='Ember details for ' + kind, edit_date=NOW)
                async def typed(model, prompt, data):
                    result, usage = await self.ask(model, prompt, data)
                    if prompt == TYPE_PROMPT:
                        result['types'][0]['content_type'] = kind
                    elif prompt != ANALYZE:
                        self.assertEqual(data['content_type'], kind)
                        self.assertIn('【' + label + '】', prompt)
                        self.assertEqual(prompt.count('【只学写法的输入输出示例'), 1)
                        for other, other_label in labels.items():
                            if other != kind:
                                self.assertNotIn('【' + other_label + '】', prompt)
                    return result, usage
                await self.run_job(typed)
                self.db.expire_all()
                event = self.db.query(AlphaEvent).order_by(AlphaEvent.id.desc()).first()
                self.assertEqual(event.content_type, kind)
                self.assertEqual(event.draft_status, 'ready')

    async def test_launchpad_preview_single_to_double_source_drafts_and_publishes_once(self):
        from alpha.publish import publish_ready
        from models.models import AlphaPublication
        from telethon import types
        from unittest.mock import AsyncMock

        body = 'Moss is a Solana meme launch platform. Public beta opens November 20, 2026.'
        draft = '**Moss｜11月20日开放公开测试**\n\nMoss是面向Solana的Meme发射平台，将于11月20日开放公开测试。'
        async def preview(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == ANALYZE:
                for decision in result['decisions']:
                    for item in decision['events']:
                        item.update(project='Moss', title='Public beta preview', stage='beta-preview')
            elif prompt == TYPE_PROMPT:
                for item in result['types']:
                    item.update(content_type='launchpad', event_status='preview', detail_level='brief')
            else:
                self.assertEqual(data['content_type'], 'launchpad')
                self.assertIn('【平台上线预告】', prompt)
                result.update(draft=draft, deadline_utc=None)
            return result, usage

        self.store(body=body)
        self.store(mid=2, body=body)  # Two posts in one channel still count as one source.
        await self.run_job(preview)
        self.assertFalse(any(p == TYPE_PROMPT for p, _ in self.calls))
        self.assertEqual(snapshot(NOW)['events'][0]['tier'], 'single')

        self.store(sid='-1002234567890', body=body)
        await self.run_job(preview)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertEqual((event.content_type, event.draft_status), ('launchpad', 'ready'))
        self.assertIsNone(event.deadline)  # Opening time is not a participation deadline.
        self.assertEqual(snapshot(NOW)['events'][0]['source_count'], 2)
        self.assertEqual(snapshot(NOW)['events'][0]['draft'], draft)
        self.assertIn(draft, export_markdown(now=NOW))

        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.commit()
        client = AsyncMock()
        client.get_input_entity.return_value = types.InputPeerChannel(3234567890, 1)
        client.return_value = types.UpdateShortSentMessage(id=789, pts=1, pts_count=1, date=NOW)
        self.assertEqual((await publish_ready(self.db, client, config, NOW))['sent'], 1)
        publication = self.db.query(AlphaPublication).one()
        self.assertEqual(publication.status, 'sent')
        self.assertEqual(publication.parts[0]['message_id'], 789)
        self.assertEqual((await publish_ready(self.db, client, config, NOW))['sent'], 0)
        self.assertEqual(client.call_count, 1)

    async def test_mirror_copies_sent_parts_once_without_ai_or_history_backfill(self):
        from alpha.mirror import mirror_published
        from models.models import AlphaPublication
        from telethon import types, functions
        from unittest.mock import AsyncMock
        self.store()
        self.store(sid='-1002234567890')
        await self.run_job()
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        original = AlphaPublication(event_id=1, target_id=config.target_id, draft_hash='original',
            parts=[{'text': 'Actual published caption', 'entities': [], 'message_id': 55,
                    'random_id': '1', 'status': 'sent', 'photo': {'id': '777'}}],
            status='sent', created_at=NOW)
        self.db.add(original)
        self.db.commit()
        client = AsyncMock()
        client.get_input_entity.side_effect = lambda target: types.InputPeerChannel(abs(target), 1)
        async def send(request):
            self.assertIsInstance(request, functions.messages.ForwardMessagesRequest)
            self.assertEqual(request.id, [55])
            self.assertTrue(request.drop_author)
            return SimpleNamespace(updates=[types.UpdateMessageID(id=99, random_id=request.random_id[0])])
        client.side_effect = send
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW + timedelta(seconds=1)))['sent'], 0)
        config.publish_enabled = False
        self.db.commit()
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW))['sent'], 0)
        config.publish_enabled = True
        self.db.commit()
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW))['sent'], 1)
        copy = self.db.query(AlphaPublication).filter_by(target_id='-1004444444444').one()
        self.assertEqual(copy.parts[0]['message_id'], 99)
        self.assertEqual(copy.parts[0]['source_message_id'], 55)
        self.assertEqual(copy.parts[0]['photo'], {'id': '777'})
        self.assertEqual(original.parts[0]['message_id'], 55)
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW))['sent'], 0)
        self.assertEqual(client.call_count, 1)

    async def test_mirror_unknown_receipt_reconciles_without_resending(self):
        from alpha.mirror import mirror_published
        from models.models import AlphaPublication
        from telethon import types
        from unittest.mock import AsyncMock
        config = job(self.db)
        config.publish_enabled, config.target_id = True, '-1003234567890'
        self.db.add(AlphaPublication(event_id=7, target_id=config.target_id, draft_hash='hash',
            parts=[{'text': 'Published', 'entities': [], 'message_id': 56, 'status': 'sent'}],
            status='sent', created_at=NOW))
        self.db.commit()
        client = AsyncMock()
        client.get_input_entity.return_value = types.InputPeerChannel(4444444444, 1)
        client.side_effect = ConnectionError('response lost')
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW))['unknown'], 1)
        reader = AsyncMock()
        reader.get_messages.return_value = []
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW, reader))['unknown'], 1)
        self.assertEqual(client.call_count, 1)
        reader.get_messages.return_value = [SimpleNamespace(id=100, message='Published',
                                            date=datetime.now(timezone.utc)+timedelta(seconds=1))]
        self.assertEqual((await mirror_published(self.db, client, '-1004444444444', NOW, reader))['sent'], 1)
        self.assertEqual(client.call_count, 1)
        client.get_messages.assert_not_awaited()

    async def test_unknown_type_blocks_without_a_draft_call_and_keeps_notes_internal(self):
        self.store()
        self.store(sid='-1002234567890')
        async def uncertain(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == TYPE_PROMPT:
                result['types'][0].update(content_type=None, context_gaps=['INTERNAL_TYPE_GAP'])
            return result, usage
        await self.run_job(uncertain)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertEqual(event.draft_status, 'blocked')
        self.assertIsNone(event.content_type)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn('INTERNAL_TYPE_GAP', export_markdown(now=NOW))
        self.assertIn('INTERNAL_TYPE_GAP', export_markdown(internal=True, now=NOW))
        self.store(mid=2)
        await self.run_job(uncertain)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'blocked')
        self.assertEqual(len(self.calls), 2)

    async def test_malformed_type_response_retries_typing_without_reanalysis(self):
        self.store()
        self.store(sid='-1002234567890')
        async def invalid(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == TYPE_PROMPT:
                result['types'].append(dict(result['types'][0]))
            return result, usage
        await self.run_job(invalid)
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().editorial['status'], 'failed')
        await self.run_job()
        self.db.expire_all()
        self.assertEqual(self.db.query(AlphaEvent).one().draft_status, 'ready')
        self.assertEqual(sum(p == ANALYZE for p, _ in self.calls), 1)

    async def test_empty_blocked_draft_is_saved_and_not_retried_unchanged(self):
        self.store()
        self.store(sid='-1002234567890')
        async def blocked(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt not in (ANALYZE, TYPE_PROMPT):
                result.update(draft='', blocked=True, reason='missing burn entry',
                              context_gaps=['burn entry'], internal_notes='internal only')
            return result, usage
        await self.run_job(blocked)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertEqual(event.draft_status, 'blocked')
        self.assertEqual(event.draft_zh, '')
        self.assertEqual(event.draft_usage['context_gaps'], ['burn entry'])
        count = len(self.calls)
        await self.run_job(blocked)
        self.assertEqual(len(self.calls), count)

    async def test_ended_type_cannot_be_sent_as_open_even_if_draft_returns_false(self):
        self.store()
        self.store(sid='-1002234567890')
        async def ended(model, prompt, data):
            result, usage = await self.ask(model, prompt, data)
            if prompt == TYPE_PROMPT:
                result['types'][0]['event_status'] = 'ended'
            return result, usage
        await self.run_job(ended)
        self.db.expire_all()
        event = self.db.query(AlphaEvent).one()
        self.assertTrue(event.closed)
        self.assertEqual(event_tier(event, NOW), 'closed')

    async def test_additive_migration_idempotent(self):
        self.db.close()
        with self.engine.begin() as connection:
            connection.execute(text('ALTER TABLE forward_rules DROP COLUMN enable_collect'))
            connection.execute(text('ALTER TABLE alpha_events DROP COLUMN content_type'))
            connection.execute(text('ALTER TABLE alpha_events DROP COLUMN editorial'))
        first = init_db()
        second = init_db()
        first.dispose()
        second.dispose()
        self.db = get_session()
        self.assertFalse(self.db.get(ForwardRule, 1).enable_collect)
        self.assertFalse(self.db.get(ForwardRule, 1).enable_forward)
        self.assertFalse(self.db.get(ForwardRule, 1).is_summary)
        self.db.add(ForwardRule(source_chat_id=1, target_chat_id=2))
        self.db.commit()
        self.assertFalse(self.db.query(ForwardRule).filter_by(target_chat_id=2).one().enable_collect)

    async def test_web_auth_rule_config_and_export(self):
        import httpx
        from fastapi import FastAPI
        from rss.app.routes.alpha import router as alpha_router
        from rss.app.routes.console import router as console_router
        from rss.app.routes.auth import get_current_user
        app = FastAPI()
        app.include_router(alpha_router)
        app.include_router(console_router)
        app.dependency_overrides[get_current_user] = lambda: None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/alpha/status')).status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username='test')
            self.assertEqual((await client.get('/api/alpha/status')).status_code, 200)
            self.assertEqual((await client.put('/api/rules/1', json={'enable_collect': 'true'})).status_code, 400)
            self.assertEqual((await client.put('/api/rules/1', json={'enable_collect': False})).status_code, 200)
            detail = (await client.get('/api/rules/1')).json()
            self.assertFalse(detail['enable_collect'])
            self.assertFalse(detail['enable_forward'])
            self.assertFalse(detail['is_summary'])
            self.assertEqual((await client.put('/api/alpha/config', json={'enabled': True, 'model': ''})).status_code, 400)
            self.assertEqual((await client.get('/api/alpha/export')).status_code, 200)
            self.assertEqual((await client.get('/api/alpha/export?internal=true')).status_code, 200)


if __name__ == '__main__':
    unittest.main()
