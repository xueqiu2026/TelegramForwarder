"""Catalog and console contracts, with an isolated database and no TG/model calls."""
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine, inspect, text
from httpx import AsyncClient, ASGITransport
from models.models import (Base, Chat, BusinessSource, BusinessMigration, CollectedMessage,
                           AlphaProcessing, AIProcessing, AlphaJob, AIJob, AlphaEvent,
                           AlphaPublication, AIItem, AIPublication, TelegramFolder, TelegramFolderSource,
                           get_session, _session_factory)
from source_catalog import ensure_catalog, set_subscription, catalog_rows, effective_sources
from collection import save_message, sync_sources
from models.p20_migration import migrate_catalog
from rss.main import app
from rss.app.routes.auth import get_current_user


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p20-')
        self.url = 'sqlite:///' + str(Path(self.tmp.name)/'db.sqlite')
        self.env = patch.dict(os.environ, {'DATABASE_URL': self.url})
        self.env.start()
        self.engine = create_engine(self.url)
        Base.metadata.create_all(self.engine)
        self.db = get_session()
        self.sid = '-1001234567890'
        self.db.add(BusinessMigration(name='sources-v1'))
        self.db.add_all([BusinessSource(business=b, source_id=self.sid, name='Source',
                         enabled_at=datetime.utcnow()-timedelta(days=1)) for b in ('alpha','ai')])
        ensure_catalog(self.db)
        self.db.commit()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username='fixture')
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()
        self.db.close()
        _session_factory(self.url).kw['bind'].dispose()
        _session_factory.cache_clear()
        self.engine.dispose()
        self.env.stop()
        self.tmp.cleanup()

    def message(self, mid=1, body='Original'):
        return SimpleNamespace(id=mid, message=body, entities=[], media=None, grouped_id=None,
            reply_to_msg_id=None, date=datetime.utcnow()-timedelta(minutes=1), edit_date=None)

    async def test_business_catalog_and_independent_switches(self):
        self.assertEqual(len(catalog_rows(self.db)), 1)
        since = self.db.get(BusinessSource, ('alpha',self.sid)).enabled_at
        set_subscription(self.db,self.sid,'alpha',False)
        self.assertTrue(catalog_rows(self.db)[0]['collection']['enabled'])
        self.assertFalse(catalog_rows(self.db)[0]['subscriptions']['alpha']['active'])
        set_subscription(self.db,self.sid,'alpha',True)
        self.assertEqual(self.db.get(BusinessSource,('alpha',self.sid)).enabled_at,since)
        set_subscription(self.db,self.sid,'alpha',False)
        self.db.commit()
        self.db.close()
        self.db = get_session()
        restored = catalog_rows(self.db)[0]
        self.assertFalse(restored['subscriptions']['alpha']['active'])
        self.assertTrue(restored['subscriptions']['ai']['active'])
        self.assertIn(self.sid, effective_sources(self.db))

    async def test_left_source_blocks_runtime_and_activation(self):
        chat = self.db.query(Chat).one()
        chat.member_state = 'left'
        self.db.flush()
        self.assertNotIn(self.sid,effective_sources(self.db))
        with self.assertRaises(ValueError):
            set_subscription(self.db,self.sid,'ai',True)

    async def test_first_receipt_and_revisions_preserve_identity(self):
        row = save_message(self.db,self.sid,'Source','source',self.message())
        first, identity = row.received_at,row.id
        edited = self.message(body='Edited')
        edited.edit_date = datetime.utcnow()
        row = save_message(self.db,self.sid,'Source','source',edited)
        self.assertEqual((row.received_at,row.id,row.revision),(first,identity,2))
        self.assertEqual(self.db.query(CollectedMessage).count(),1)

    async def test_raw_pagination_uses_one_original_not_two_businesses(self):
        for mid in (1,2,3):
            save_message(self.db,self.sid,'Source','source',self.message(mid))
        self.db.commit()
        result = (await self.client.get('/api/collected-messages?page_size=2')).json()
        self.assertEqual((result['total'],len(result['items'])),(3,2))
        self.assertTrue(result['items'][0]['published_at'].endswith('+00:00'))
        self.assertEqual(set(result['items'][0]['business_states']),{'alpha','ai'})
        self.assertEqual((await self.client.get('/api/collected-messages?status=pending')).status_code,400)

    async def test_config_patches_do_not_reset_slot_or_other_fields(self):
        for business,cls,path in [('alpha',AlphaJob,'alpha'),('ai',AIJob,'ai-digest')]:
            self.db.add(cls(id=1,enabled=True,model='kept',publish_enabled=True,last_slot='preserve'))
        self.db.commit()
        for path,cls in [('alpha',AlphaJob),('ai-digest',AIJob)]:
            hours = self.db.get(cls,1).schedule_hours
            response = await self.client.put(f'/api/{path}/config',json={'schedule_hours':hours})
            self.assertEqual(response.status_code,200,response.text)
            self.db.expire_all()
            config = self.db.get(cls,1)
            self.assertEqual((config.model,config.last_slot,config.enabled),('kept','preserve',True))
            response = await self.client.put(f'/api/{path}/config',json={'publish_enabled':False})
            self.assertEqual(response.status_code,200,response.text)
            self.db.expire_all()
            self.assertEqual(self.db.get(cls,1).last_slot,'preserve')
            self.assertEqual((await self.client.put(f'/api/{path}/config',json={'enabled':None})).status_code,400)

    async def test_published_parts_survive_current_draft_invalidation(self):
        event = AlphaEvent(project='Project',title='Event',stage='mint',first_seen=datetime.utcnow(),draft_zh=None)
        self.db.add(event); self.db.flush()
        self.db.add(AlphaPublication(event_id=event.id,target_id='-1001',draft_hash='old',status='sent',
            parts=[{'text':'Actual sent text','message_id':8,'status':'sent'}]))
        self.db.commit()
        detail = (await self.client.get(f'/api/business/alpha/items/{event.id}')).json()
        self.assertIsNone(detail['draft_zh'])
        self.assertEqual(detail['publications'][0]['parts'][0]['text'],'Actual sent text')

    async def test_rename_preserved_and_active_directory_delete_blocked(self):
        chat = self.db.query(Chat).one()
        response = await self.client.put(f'/api/chats/{chat.id}',json={'name':'Local name'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((await self.client.delete(f'/api/chats/{chat.id}')).status_code,400)

    async def test_removed_inactive_source_stays_removed_without_losing_history(self):
        raw = save_message(self.db,self.sid,'Historical source','source',self.message())
        chat_id, raw_id = self.db.query(Chat).one().id, raw.id
        for business in ('alpha','ai'):
            set_subscription(self.db,self.sid,business,False)
        self.db.commit()
        self.assertEqual((await self.client.delete(f'/api/chats/{chat_id}')).status_code,200)
        for _ in range(2):
            self.assertEqual((await self.client.get('/api/sources')).json()['total'],0)
        self.db.close();self.db = get_session()
        self.assertEqual(catalog_rows(self.db),[])
        self.assertEqual(self.db.query(BusinessSource).count(),2)
        self.assertIsNotNone(self.db.get(CollectedMessage,raw_id))
        self.assertEqual(self.db.get(CollectedMessage,raw_id).source_name,'Historical source')

    async def test_business_views_use_directory_display_name_without_rewriting_original(self):
        raw = save_message(self.db,self.sid,'Historical source','source',self.message())
        self.db.add_all([AlphaJob(id=1), AIJob(id=1)]);self.db.commit()
        cid = self.db.query(Chat).one().id
        self.assertEqual((await self.client.put(f'/api/chats/{cid}',json={'name':'Manual name'})).status_code,200)
        from alpha.storage import snapshot
        self.assertEqual(snapshot()['sources'][0]['name'],'Manual name')
        exported=(await self.client.get('/api/ai-digest/status')).json()
        self.assertEqual(exported['sources'][0]['name'],'Manual name')
        self.assertEqual(effective_sources(self.db)[self.sid],'Manual name')
        self.db.refresh(raw)
        self.assertEqual(raw.source_name,'Historical source')

    async def test_username_only_daily_refresh_preserves_manual_name(self):
        from scheduler.chat_updater import ChatUpdater
        chat = self.db.query(Chat).one()
        chat.name,chat.telegram_title,chat.username='Manual name','TG title','old_username'
        self.db.commit()
        client = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(title='TG title',username='new_username')))
        with patch('scheduler.chat_updater.asyncio.sleep',new=AsyncMock()):
            await ChatUpdater(client)._update_all_chats()
        self.db.refresh(chat)
        self.assertEqual((chat.name,chat.telegram_title,chat.username),('Manual name','TG title','new_username'))
        client.get_entity.assert_awaited_once_with(int(self.sid))

    async def test_details_keep_conditions_type_status_and_actual_sent_entities(self):
        from delivery import make_parts
        deadline = datetime.utcnow()+timedelta(days=1)
        event = AlphaEvent(project='Example',title='Tutorial',stage='mint',first_seen=datetime.utcnow(),
            editorial={'status':'ready','detail_level':'detailed'},deadline=deadline)
        item = AIItem(subject='API offer',goal='Claim credits',category='B1',conditions='New accounts only',deadline=deadline)
        self.db.add_all([event,item]);self.db.flush()
        parts=make_parts('**Entry**: [Official site](https://example.org/claim)')
        parts[0].update(message_id=99,status='sent')
        self.db.add(AIPublication(item_id=item.id,target_id='@wanshenmeAI',draft_hash='sent',parts=parts,status='sent'))
        self.db.commit()
        alpha=(await self.client.get(f'/api/business/alpha/items/{event.id}')).json()
        self.assertEqual((alpha['type_status'],alpha['detail_level']),('ready','detailed'))
        ai=(await self.client.get(f'/api/business/ai/items/{item.id}')).json()
        self.assertEqual(ai['conditions'],'New accounts only')
        self.assertTrue(ai['deadline'].endswith('+00:00'))
        self.assertIsNone(ai['output_zh'])
        self.assertEqual(ai['publications'][0]['parts'],parts)

    async def test_sync_previews_username_and_folder_only_changes_and_rejects_stale_folders(self):
        from source_catalog import catalog_snapshot,normalize_times
        from telegram_catalog import describe_changes
        from rss.app.routes.catalog import subscription_signature
        chat=self.db.query(Chat).one()
        chat.telegram_title,chat.username,chat.member_state='TG title','old_username','joined'
        self.db.add(TelegramFolder(id=2,title='Old folder'));self.db.flush()
        self.db.add(TelegramFolderSource(folder_id=2,peer_id=self.sid));self.db.commit()
        previous=catalog_snapshot(self.db)
        result={'sources':[{'peer_id':self.sid,'peer_type':'channel','telegram_title':'TG title',
            'username':'new_username','member_state':'joined'}],
            'folders':[{'id':3,'title':'New folder','complete':True,'peers':[self.sid]}],
            'complete':True,'folders_complete':True,'errors':{}}
        describe_changes(previous,result)
        row=result['sources'][0]
        self.assertEqual(row['change'],'updated')
        self.assertEqual({c['field'] for c in row['changes']},{'username','folders'})
        self.assertEqual(len(result['folder_changes']),2)
        preview={'status':'ready','token':'fixture','baseline':normalize_times(subscription_signature(self.db)),'result':result}
        self.db.get(TelegramFolder,2).title='Changed after preview';self.db.commit()
        with patch('rss.app.routes.catalog.sync_bridge',new=AsyncMock(return_value=preview)):
            response=await self.client.post('/api/sources/sync/apply',json={'token':'fixture'})
            self.assertEqual(response.status_code,409,response.text)
        result['folders_complete']=False
        describe_changes(previous,result)
        self.assertEqual([c['field'] for c in row['changes']],['username'])
        self.assertEqual(result['folder_changes'],[])

    async def test_unauthenticated_private_routes_and_websocket_rejected(self):
        app.dependency_overrides.clear()
        for path in ('/api/sources','/api/collected-messages','/api/business/ai/status'):
            self.assertEqual((await self.client.get(path)).status_code,401)
        from rss.app.routes.console import websocket_endpoint
        class Socket:
            cookies = {}
            async def close(self, code):
                self.code = code
        socket = Socket()
        await websocket_endpoint(socket)
        self.assertEqual(socket.code,1008)

    async def test_migration_preserves_legacy_rows_and_can_repeat(self):
        legacy = create_engine('sqlite:///'+str(Path(self.tmp.name)/'legacy.sqlite'))
        with legacy.begin() as conn:
            conn.execute(text('CREATE TABLE chats(id INTEGER PRIMARY KEY,telegram_chat_id VARCHAR,name VARCHAR)'))
            conn.execute(text("INSERT INTO chats VALUES(4,'1001234567890','Original name')"))
            conn.execute(text('CREATE TABLE collected_messages(id INTEGER PRIMARY KEY,body VARCHAR)'))
            conn.execute(text("INSERT INTO collected_messages VALUES(8,'Original text')"))
        migrate_catalog(legacy); migrate_catalog(legacy)
        with legacy.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT id,name,peer_id FROM chats')).one(),(4,'Original name',None))
            self.assertEqual(conn.execute(text('SELECT id,body,received_at FROM collected_messages')).one(),(8,'Original text',None))
        legacy.dispose()

    async def test_sync_apply_preserves_name_and_detects_concurrent_subscription(self):
        from source_catalog import normalize_times
        from rss.app.routes.catalog import subscription_signature
        chat = self.db.query(Chat).one()
        chat.name = 'Manual display name'
        self.db.commit()
        preview = {'status':'ready','token':'fixture','baseline':normalize_times(subscription_signature(self.db)),
            'result':{'sources':[{'peer_id':self.sid,'peer_type':'channel','telegram_title':'Updated TG title',
                       'username':'fixture','member_state':'left'}], 'folders_complete':False,'errors':{}}}
        with patch('rss.app.routes.catalog.sync_bridge',new=AsyncMock(return_value=preview)):
            response = await self.client.post('/api/sources/sync/apply',json={'token':'fixture'})
            self.assertEqual(response.status_code,200,response.text)
            self.db.expire_all()
            self.assertEqual(chat.name,'Manual display name')
            self.assertEqual(chat.telegram_title,'Updated TG title')
            self.assertEqual(chat.member_state,'left')
            self.assertFalse(self.db.get(BusinessSource,('ai',self.sid)).active)
            response = await self.client.post('/api/sources/sync/apply',json={'token':'fixture'})
            self.assertEqual(response.status_code,409,response.text)

    async def test_partial_telegram_scan_never_infers_departure(self):
        from telegram_catalog import scan_catalog
        class Client:
            async def iter_dialogs(self):
                if False:
                    yield None
                raise RuntimeError('temporary network failure')
            async def get_entity(self, peer):
                raise RuntimeError('not in local entity cache')
            async def __call__(self, request):
                return SimpleNamespace(filters=[])
        result = await scan_catalog(Client(),{self.sid})
        self.assertFalse(result['complete'])
        self.assertFalse(result['folders_complete'])
        self.assertEqual(result['sources'],[])

    async def test_folder_rules_include_exclude_and_multiple_membership(self):
        from telethon import types
        from telegram_catalog import folder_contains
        entity=types.Channel(id=123,title='Channel',photo=types.ChatPhotoEmpty(),date=datetime.utcnow(),broadcast=True)
        dialog=SimpleNamespace(entity=entity,dialog=SimpleNamespace(folder_id=None,unread_count=1,unread_mark=False,notify_settings=None))
        generic=types.DialogFilter(id=2,title='Broadcasts',pinned_peers=[],include_peers=[],exclude_peers=[],broadcasts=True)
        explicit=types.DialogFilter(id=3,title='Manual',pinned_peers=[],include_peers=[types.InputPeerChannel(123,1)],exclude_peers=[])
        excluded=types.DialogFilter(id=4,title='Excluded',pinned_peers=[],include_peers=[],exclude_peers=[types.InputPeerChannel(123,1)],broadcasts=True)
        self.assertEqual(folder_contains(generic,dialog),(True,True))
        self.assertEqual(folder_contains(explicit,dialog),(True,True))
        self.assertEqual(folder_contains(excluded,dialog),(False,True))

    async def test_shared_recovery_cache_invalidates_after_subscription_change(self):
        from collection import recover_history
        client=SimpleNamespace()
        recovery=AsyncMock(return_value={})
        with patch('collection._recover_history',new=recovery):
            await recover_history(client,'slot')
            await recover_history(client,'slot')
            self.assertEqual(recovery.await_count,1)
            set_subscription(self.db,self.sid,'alpha',False);self.db.commit()
            await recover_history(client,'slot')
            self.assertEqual(recovery.await_count,2)

    async def test_basic_group_link_does_not_fake_channel_url(self):
        row=save_message(self.db,'-12345','Group','',self.message())
        self.assertEqual(row.source_url,'')

    async def test_real_bridge_runs_one_sync_and_applies_folder_metadata(self):
        import socket, asyncio
        with patch('telethon.TelegramClient', return_value=SimpleNamespace()):
            import main
        from telethon import types
        from httpx import AsyncClient
        from models.models import TelegramFolderSource
        entity=types.Channel(id=1234567890,title='TG title',photo=types.ChatPhotoEmpty(),date=datetime.utcnow(),broadcast=True)
        folder=types.DialogFilter(id=2,title='Research',pinned_peers=[],include_peers=[],exclude_peers=[],broadcasts=True)
        class Client:
            scans=0
            async def iter_dialogs(self):
                self.scans+=1
                yield SimpleNamespace(entity=entity,dialog=SimpleNamespace(folder_id=None))
            async def __call__(self,request):
                return SimpleNamespace(filters=[folder])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        with patch.dict(os.environ,{'INTERNAL_BRIDGE_PORT':str(port)}):
            client=Client()
            await main.start_internal_server(client,client)
            try:
                response=await self.client.post('/api/sources/sync/preview')
                self.assertEqual(response.status_code,200,response.text)
                for _ in range(30):
                    preview=(await self.client.get('/api/sources/sync/preview')).json()
                    if preview['status']=='ready':
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(preview['status'],'ready')
                self.assertEqual(client.scans,1)
                response=await self.client.post('/api/sources/sync/apply',json={'token':preview['token']})
                self.assertEqual(response.status_code,200,response.text)
                self.db.expire_all()
                self.assertEqual(self.db.query(Chat).one().telegram_title,'TG title')
                self.assertEqual(self.db.query(TelegramFolderSource).one().peer_id,self.sid)
            finally:
                await main.internal_runner.cleanup()
                main.internal_runner = None
                main.engine.dispose()

    async def test_legacy_rule_subscription_write_removed(self):
        response = await self.client.put('/api/rules/999',json={'enable_collect':True})
        self.assertEqual(response.status_code,400,response.text)

    async def test_password_change_reuses_existing_hash_verification(self):
        from models.models import User
        from werkzeug.security import generate_password_hash,check_password_hash
        account=User(username='fixture',password=generate_password_hash('original-test-password'))
        self.db.add(account);self.db.commit()
        response=await self.client.post('/api/auth/change-password',json={
            'current_password':'wrong','new_password':'new-test-password','confirm_password':'new-test-password'})
        self.assertEqual(response.status_code,400,response.text)
        response=await self.client.post('/api/auth/change-password',json={
            'current_password':'original-test-password','new_password':'new-test-password','confirm_password':'new-test-password'})
        self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all()
        self.assertTrue(check_password_hash(account.password,'new-test-password'))


if __name__ == '__main__':
    unittest.main()
