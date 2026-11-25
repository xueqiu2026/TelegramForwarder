from telethon import TelegramClient, types, utils
from telethon.tl.types import BotCommand
from telethon.tl.functions.bots import SetBotCommandsRequest
from models.models import init_db
from dotenv import load_dotenv
from message_listener import setup_listeners
import os
import asyncio
import logging
import uvicorn
import multiprocessing
from models.db_operations import DBOperations
from scheduler.chat_updater import ChatUpdater
from handlers.bot_handler import send_welcome_message
from rss.main import app as rss_app  # noqa: used by health check route
from utils.log_config import setup_logging

# 设置Docker日志的默认配置，如果docker-compose.yml中没有配置日志选项将使用这些值
os.environ.setdefault('DOCKER_LOG_MAX_SIZE', '10m')
os.environ.setdefault('DOCKER_LOG_MAX_FILE', '3')

# 设置日志配置
setup_logging()

logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 从环境变量获取配置
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
bot_token = os.getenv('BOT_TOKEN')
phone_number = os.getenv('PHONE_NUMBER')

# 创建 DBOperations 实例
db_ops = None

chat_updater = None
internal_runner = None


async def init_db_ops():
    """初始化 DBOperations 实例"""
    global db_ops
    if db_ops is None:
        db_ops = await DBOperations.create()
    return db_ops


async def start_internal_server(user_client, bot_client):
    global internal_runner
    from aiohttp import web
    app = web.Application()
    from alpha.pipeline import run_pipeline, AlreadyRunning
    alpha_runs = set()

    async def run_alpha(request):
        if alpha_runs:
            return web.json_response({'status': 'running', 'message': '机会任务正在运行'}, status=409)

        async def execute():
            try:
                await run_pipeline(client=user_client)
            except AlreadyRunning:
                logger.info('机会任务已有其他入口在执行')
            except Exception:
                logger.exception('手动机会任务失败，已保存状态')
        task = asyncio.create_task(execute())
        alpha_runs.add(task)
        task.add_done_callback(alpha_runs.discard)
        return web.json_response({'status': 'accepted', 'message': '已提交机会任务，按当前推送配置执行'})

    async def cleanup_alpha(app):
        for task in list(alpha_runs):
            task.cancel()
        await asyncio.gather(*list(alpha_runs), return_exceptions=True)

    app.on_cleanup.append(cleanup_alpha)
    app.router.add_post('/alpha-run', run_alpha)
    ai_runs = set()

    async def run_ai(request):
        if ai_runs:
            return web.json_response({'message': 'AI任务正在运行'}, status=409)
        from ai_digest.pipeline import run_pipeline as run_ai_pipeline
        async def execute():
            try:
                await run_ai_pipeline(client=user_client, publisher=bot_client)
            except AlreadyRunning:
                logger.info('AI任务已有其他入口在执行')
            except Exception:
                logger.exception('手动AI任务失败，状态已保存')
        task = asyncio.create_task(execute())
        ai_runs.add(task)
        task.add_done_callback(ai_runs.discard)
        return web.json_response({'status': 'accepted'})

    async def cleanup_ai(app):
        for task in list(ai_runs):
            task.cancel()
        await asyncio.gather(*list(ai_runs), return_exceptions=True)

    app.router.add_post('/ai-digest-run', run_ai)
    app.on_cleanup.append(cleanup_ai)
    
    async def resolve_handler(request):
        try:
            data = await request.json()
            link = data.get("link")
            if not link:
                return web.json_response({"status": "error", "message": "解析链接或用户名不能为空"}, status=400)
            
            # 清洗并转换 ID 格式
            link_clean = link.strip() if isinstance(link, str) else link
            if isinstance(link_clean, str):
                if (link_clean.startswith('-') and link_clean[1:].isdigit()) or link_clean.isdigit():
                    link_clean = int(link_clean)
            
            # 使用 user_client 获取实体
            entity = await user_client.get_entity(link_clean)
            # 1. 在内存 MessageBox 中注册状态，防止 Telethon 底层丢包
            user_client._message_box.try_set_channel_state(entity.id, 1)
            # 2. 通过 GetChannelDifference 主动建立握手以激活该长连接的实时 updates 推送
            from telethon.tl.functions.updates import GetChannelDifferenceRequest
            from telethon.tl.types import ChannelMessagesFilterEmpty
            await user_client(GetChannelDifferenceRequest(
                channel=entity,
                filter=ChannelMessagesFilterEmpty(),
                pts=1,
                limit=10
            ))
            
            # 获取最合适的展示名
            if hasattr(entity, 'title'):
                title = entity.title
            elif hasattr(entity, 'first_name'):
                title = f"{entity.first_name} {entity.last_name}" if hasattr(entity, 'last_name') and entity.last_name else entity.first_name
            else:
                title = "Private Chat"
                
            return web.json_response({
                "status": "success",
                "telegram_chat_id": str(entity.id),
                "name": title
            })
        except Exception as e:
            logger.error(f"内部解析服务处理错误: {str(e)}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)
            
    async def health_handler(request):
        try:
            user_ok = user_client.is_connected() if user_client else False
            bot_ok = bot_client.is_connected() if bot_client else False
            return web.json_response({
                "status": "success",
                "user_client_connected": user_ok,
                "bot_client_connected": bot_ok
            })
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    app.router.add_post("/resolve", resolve_handler)
    app.router.add_get("/health", health_handler)
    internal_runner = web.AppRunner(app)
    await internal_runner.setup()
    
    bridge_port = int(os.getenv('INTERNAL_BRIDGE_PORT', '8001'))
    site = web.TCPSite(internal_runner, '127.0.0.1', bridge_port)
    await site.start()
    logger.info(f"内部解析与健康监控桥接服务已成功启动在 127.0.0.1:{bridge_port}")


# 创建文件夹
os.makedirs('./sessions', exist_ok=True)
os.makedirs('./temp', exist_ok=True)


# 清空./temp文件夹
def clear_temp_dir():
    for file in os.listdir('./temp'):
        os.remove(os.path.join('./temp', file))


# 创建客户端
user_client = TelegramClient('./sessions/user', api_id, api_hash)
bot_client = TelegramClient('./sessions/bot', api_id, api_hash)

# 初始化数据库
engine = init_db()




async def start_message_poller(user_client, bot_client, source_ids):
    """
    源频道定时轮询兜底机制，以第一性原理彻底规避 Telegram 限制 left=True 频道的 updates 推送问题
    """
    logger.info("正在初始化源频道定时轮询器...")
    last_processed = {}
    
    # 1. 首次运行，记录所有频道的当前最新消息 ID，防止将历史消息转发
    for chat_id_str, name in source_ids:
        cid = int(chat_id_str)
        try:
            # 获取最新一条消息
            msgs = await user_client.get_messages(cid, limit=1)
            if msgs:
                last_processed[cid] = msgs[0].id
            else:
                last_processed[cid] = 0
            # 串行错开网络请求，防止高频
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"轮询器初始化频道 {name} ({chat_id_str}) 失败: {e}")
            last_processed[cid] = 0
            
    logger.info(f"源频道定时轮询器初始化完成，已记录 {len(last_processed)} 个频道初始状态。")
    
    # 2. 定时轮询主循环
    async def poll_loop():
        from message_listener import handle_user_message
        while True:
            from models.models import get_session, BusinessSource
            db = get_session()
            try:
                dynamic = {s.source_id: s.name for s in db.query(BusinessSource).filter_by(active=True)}
            finally:
                db.close()
            # 动态获取当前 MessageBox 中已激活的频道
            mb_map = getattr(user_client._message_box, 'map', {})
            # 只轮询那些【不在】MessageBox 中的源频道
            poll_targets = [
                (chat_id_str, name) for chat_id_str, name in {**dict(source_ids), **dynamic}.items()
                if utils.resolve_id(int(chat_id_str))[0] not in mb_map
            ]
            
            # 若所有频道均已通过实时 updates 覆盖，poller 进行休眠并重新检测
            if not poll_targets:
                await asyncio.sleep(15)
                continue
                
            logger.info(f"轮询器开始扫描未注册实时推送的 {len(poll_targets)} 个源频道...")
            for chat_id_str, name in poll_targets:
                cid = int(chat_id_str)
                last_id = last_processed.get(cid, 0)
                try:
                    # 获取最新消息
                    msgs = await user_client.get_messages(cid, limit=1)
                    if not msgs:
                        continue
                    current_id = msgs[0].id
                    if last_id == 0:
                        # 之前失败过的频道，在其首次正常拉取时，初始化其最新消息ID为当前值，防止转发历史消息
                        last_processed[cid] = current_id
                        continue
                        
                    if current_id > last_id:
                        logger.info(f"轮询器检测到频道 {name} ({chat_id_str}) 有新消息: {last_id} -> {current_id}")
                        # 拉取在此期间的所有新消息并正序排列
                        diff_count = min(current_id - last_id, 10) # 限制最大单次拉取 10 条，防被刷屏
                        new_msgs = await user_client.get_messages(cid, limit=diff_count)
                        
                        # 构造 FakeEvent 确保与 handle_user_message 完全兼容
                        class FakeEvent:
                            def __init__(self, message):
                                self.message = message
                            def __getattr__(self, name):
                                return getattr(self.message, name)
                            async def get_chat(self):
                                return await self.message.get_chat()
                            @property
                            def chat(self):
                                return self.message.chat
                            @property
                            def sender_id(self):
                                return self.message.sender_id

                        # 正序排序以按时间顺序转发
                        for msg in reversed(new_msgs):
                            if msg.id > last_id:
                                try:
                                    logger.info(f"轮询器触发消息转发: {name} | MsgID: {msg.id}")
                                    wrapped_event = FakeEvent(msg)
                                    await handle_user_message(wrapped_event, user_client, bot_client)
                                except Exception as he:
                                    logger.error(f"轮询器转发消息失败: {he}")
                        last_processed[cid] = current_id
                    await asyncio.sleep(0.3)  # 严格流控，防 FloodWait
                except Exception as e:
                    logger.warning(f"轮询器扫描频道 {name} ({chat_id_str}) 异常: {e}")
                    await asyncio.sleep(1.0)
            
            await asyncio.sleep(15)
                    
    asyncio.create_task(poll_loop())


async def start_clients():
    # 初始化 DBOperations
    global db_ops, chat_updater
    db_ops = await DBOperations.create()

    try:
        # 启动用户客户端
        await user_client.start(phone=phone_number)
        me_user = await user_client.get_me()
        print(f'用户客户端已启动: {me_user.first_name} (@{me_user.username})')

        # 注册所有频道到 Telethon MessageBox，确保能接收频道 updates
        # 策略：
        # 1. get_dialogs(limit=None) 自动注册对话中的频道（真实 pts，无额外 API 请求）
        # 2. 对不在 dialogs 的频道：仅填充 entity cache，等第一条实时消息自动注册
        # 注意：不批量调用 try_set_channel_state，避免触发大量并发 GetChannelDifference
        #       导致 Telegram 限流/断开连接
        try:
            # 1. 全量加载对话（自动注册到 MessageBox，使用服务器返回的真实 pts）
            dialogs = await user_client.get_dialogs(limit=None)
            mb_after_dialogs = len(getattr(user_client._message_box, 'map', {}))
            logger.info(f'已加载 {len(dialogs)} 个对话, MessageBox: {mb_after_dialogs}')

            # 2. 从数据库获取所有转发规则的源频道 ID
            from models.models import get_session as get_db_session, Chat, ForwardRule
            db_session = get_db_session()
            try:
                source_chats = db_session.query(Chat.telegram_chat_id, Chat.name).join(
                    ForwardRule, Chat.id == ForwardRule.source_chat_id
                ).filter(ForwardRule.enable_rule == True).distinct().all()
                from collection import sync_sources, source_id
                sync_sources(db_session)
                db_session.commit()
                from models.models import BusinessSource
                source_ids = list({**{source_id(c.telegram_chat_id): c.name for c in source_chats},
                                   **{s.source_id: s.name for s in db_session.query(BusinessSource).filter_by(active=True)}}.items())
            finally:
                db_session.close()

            # 3. 读取本地 SQLite 中的 session pts 映射关系
            import sqlite3
            session_pts_map = {}
            try:
                conn = sqlite3.connect('./sessions/user.session')
                c = conn.cursor()
                c.execute("SELECT id, pts FROM update_state WHERE id != 0")
                session_pts_map = {row[0]: row[1] for row in c.fetchall()}
                conn.close()
            except Exception as se:
                logger.warning(f"无法读取 session pts 状态: {se}")

            # 4. 对不在 MessageBox 的频道：预加载 entity 缓存并发送差分同步请求以激活推送
            mb_map = getattr(user_client._message_box, 'map', {})
            missing = []
            for chat_id_str, name in source_ids:
                cid = int(chat_id_str)
                channel_id = utils.resolve_id(cid)[0]
                if channel_id in mb_map:
                    continue
                missing.append(name)
                try:
                    entity = await user_client.get_entity(cid)
                    if hasattr(entity, 'access_hash') and entity.access_hash:
                        # 自动 Join 未加入的频道
                        if getattr(entity, 'left', False):
                            try:
                                logger.info(f'检测到账号未加入源频道，正在自动加入: {name} ({chat_id_str})')
                                from telethon.tl.functions.channels import JoinChannelRequest
                                await user_client(JoinChannelRequest(entity))
                                logger.info(f'成功加入源频道: {name}')
                                await asyncio.sleep(1.0)
                            except Exception as je:
                                logger.warning(f'自动加入频道失败: {name} ({chat_id_str}), 错误: {je}')

                        user_client._mb_entity_cache.extend([], [entity])
                        real_pts = session_pts_map.get(channel_id, 1)
                        # 1. 在内存 MessageBox 中注册状态，防止 Telethon 底层丢包
                        user_client._message_box.try_set_channel_state(channel_id, real_pts)
                        # 2. 发起 GetChannelDifference 主动差分同步，唤醒 Telegram updates 推送引擎
                        from telethon.tl.functions.updates import GetChannelDifferenceRequest
                        from telethon.tl.types import ChannelMessagesFilterEmpty
                        await user_client(GetChannelDifferenceRequest(
                            channel=entity,
                            filter=ChannelMessagesFilterEmpty(),
                            pts=real_pts,
                            limit=10
                        ))
                        # 限流保护：串行依次激活，每次间隔 1.0 秒，完全防范 Flood 限制
                        await asyncio.sleep(1.0)
                except Exception as e:
                    logger.warning(f'无法预加载并同步激活实体: {name} ({chat_id_str}), 错误: {e}')

            mb_count = len(getattr(user_client._message_box, 'map', {}))
            logger.info(f'频道注册完成: {mb_count} 个在 MessageBox, {len(missing)} 个等待首条消息自动注册')
        except Exception as e:
            logger.warning(f'启动时频道注册失败 (非致命): {e}')

        # 启动机器人客户端
        await bot_client.start(bot_token=bot_token)
        me_bot = await bot_client.get_me()
        print(f'机器人客户端已启动: {me_bot.first_name} (@{me_bot.username})')

        # 设置消息监听器
        await setup_listeners(user_client, bot_client)

        # 启动源频道定时轮询器以防推送不达
        try:
            await start_message_poller(user_client, bot_client, source_ids)
        except Exception as pe:
            logger.error(f"启动定时轮询兜底器失败: {pe}")

        # 注册命令
        await register_bot_commands(bot_client)

        # 创建并启动调度器
        from alpha.scheduler import scheduler_loop
        alpha_task = asyncio.create_task(scheduler_loop(user_client))
        ai_task = asyncio.create_task(scheduler_loop(user_client, "ai", publisher=bot_client))
        from alpha.mirror import mirror_loop
        mirror_task = asyncio.create_task(mirror_loop(bot_client, user_client))
        
        # 创建并启动聊天信息更新器
        chat_updater = ChatUpdater(user_client)
        await chat_updater.start()

        # 启动内部解析服务
        try:
            await start_internal_server(user_client, bot_client)
        except Exception as e:
            logger.error(f"启动内部解析服务失败: {str(e)}")

        # 如果启用了 RSS 服务
        if os.getenv('RSS_ENABLED', '').lower() == 'true':
            try:
                rss_host = os.getenv('RSS_HOST', '0.0.0.0')
                rss_port = int(os.getenv('RSS_PORT', '8000'))
                logger.info(f"正在启动 RSS 服务 (host={rss_host}, port={rss_port})")
                
                # 使用 spawn + 独立模块，避免子进程 import main.py 创建 TelegramClient
                from rss_runner import run_rss_server
                ctx = multiprocessing.get_context('spawn')
                rss_process = ctx.Process(
                    target=run_rss_server,
                    args=(rss_host, rss_port)
                )
                rss_process.start()
                logger.info("RSS 服务启动成功")
            except Exception as e:
                logger.error(f"启动 RSS 服务失败: {str(e)}")
                logger.exception(e)
        else:
            logger.info("RSS 服务未启用")

        # 发送欢迎消息
        await send_welcome_message(bot_client)

        # 无消息不代表断线；通过 RPC 探测并恢复用户连接。
        from utils.telegram_health import health_loop

        await asyncio.gather(
            user_client.run_until_disconnected(),
            bot_client.run_until_disconnected(),
            health_loop(user_client),
            return_exceptions=True
        )
    finally:
        # 停止内部解析服务
        if 'mirror_task' in locals():
            mirror_task.cancel()
            await asyncio.gather(mirror_task, return_exceptions=True)
        if 'ai_task' in locals():
            ai_task.cancel()
            await asyncio.gather(ai_task, return_exceptions=True)
        if 'alpha_task' in locals():
            alpha_task.cancel()
            await asyncio.gather(alpha_task, return_exceptions=True)
        if internal_runner:
            try:
                await internal_runner.cleanup()
                logger.info("内部解析服务已停止")
            except Exception as e:
                logger.error(f"停止内部解析服务时出错: {str(e)}")
        # 关闭 DBOperations
        if db_ops and hasattr(db_ops, 'close'):
            await db_ops.close()
        # 停止调度器
        # 停止聊天信息更新器
        if chat_updater:
            chat_updater.stop()
        # 如果 RSS 服务在运行，停止它
        if 'rss_process' in locals() and rss_process.is_alive():
            rss_process.terminate()
            rss_process.join()


async def register_bot_commands(bot):
    """注册机器人命令"""
    # # 先清空现有命令
    # try:
    #     await bot(SetBotCommandsRequest(
    #         scope=types.BotCommandScopeDefault(),
    #         lang_code='',
    #         commands=[]  # 空列表清空所有命令
    #     ))
    #     logger.info('已清空现有机器人命令')
    # except Exception as e:
    #     logger.error(f'清空机器人命令时出错: {str(e)}')

    commands = [
        # 基础命令
        BotCommand(
            command='start',
            description='开始使用'
        ),
        BotCommand(
            command='help',
            description='查看帮助'
        ),
        # 绑定和设置
        BotCommand(
            command='bind',
            description='绑定源聊天'
        ),
        BotCommand(
            command='settings',
            description='管理转发规则'
        ),
        BotCommand(
            command='switch',
            description='切换当前需要设置的聊天规则'
        ),
        # 关键字管理
        BotCommand(
            command='add',
            description='添加关键字'
        ),
        BotCommand(
            command='add_regex',
            description='添加正则关键字'
        ),
        BotCommand(
            command='add_all',
            description='添加普通关键字到所有规则'
        ),
        BotCommand(
            command='add_regex_all',
            description='添加正则表达式到所有规则'
        ),
        BotCommand(
            command='list_keyword',
            description='列出所有关键字'
        ),
        BotCommand(
            command='remove_keyword',
            description='删除关键字'
        ),
        BotCommand(
            command='remove_keyword_by_id',
            description='按ID删除关键字'
        ),
        BotCommand(
            command='remove_all_keyword',
            description='删除当前频道绑定的所有规则的指定关键字'
        ),
        # 替换规则管理
        BotCommand(
            command='replace',
            description='添加替换规则'
        ),
        BotCommand(
            command='replace_all',
            description='添加替换规则到所有规则'
        ),
        BotCommand(
            command='list_replace',
            description='列出所有替换规则'
        ),
        BotCommand(
            command='remove_replace',
            description='删除替换规则'
        ),
        # 导入导出功能
        BotCommand(
            command='export_keyword',
            description='导出当前规则的关键字'
        ),
        BotCommand(
            command='export_replace',
            description='导出当前规则的替换规则'
        ),
        BotCommand(
            command='import_keyword',
            description='导入普通关键字'
        ),
        BotCommand(
            command='import_regex_keyword',
            description='导入正则表达式关键字'
        ),
        BotCommand(
            command='import_replace',
            description='导入替换规则'
        ),
        # UFB相关功能
        BotCommand(
            command='ufb_bind',
            description='绑定ufb域名'
        ),
        BotCommand(
            command='ufb_unbind',
            description='解绑ufb域名'
        ),
        BotCommand(
            command='ufb_item_change',
            description='切换ufb同步配置类型'
        ),
        BotCommand(
            command='clear_all_keywords',
            description='清除当前规则的所有关键字'
        ),
        BotCommand(
            command='clear_all_keywords_regex',
            description='清除当前规则的所有正则关键字'
        ),
        BotCommand(
            command='clear_all_replace',
            description='清除当前规则的所有替换规则'
        ),
        BotCommand(
            command='copy_keywords',
            description='复制参数规则的关键字到当前规则'
        ),
        BotCommand(
            command='copy_keywords_regex',
            description='复制参数规则的正则关键字到当前规则'
        ),
        BotCommand(
            command='copy_replace',
            description='复制参数规则的替换规则到当前规则'
        ),
        BotCommand(
            command='copy_rule',
            description='复制参数规则到当前规则'
        ),
        BotCommand(
            command='changelog',
            description='查看更新日志'
        ),
        BotCommand(
            command='list_rule',
            description='列出所有转发规则'
        ),
        BotCommand(
            command='delete_rule',
            description='删除转发规则'
        ),
        BotCommand(
            command='delete_rss_user',
            description='删除RSS用户'
        ),


        # BotCommand(
        #     command='clear_all',
        #     description='慎用！清空所有数据'
        # ),
    ]

    try:
        result = await bot(SetBotCommandsRequest(
            scope=types.BotCommandScopeDefault(),
            lang_code='',  # 空字符串表示默认语言
            commands=commands
        ))
        if result:
            logger.info('已成功注册机器人命令')
        else:
            logger.error('注册机器人命令失败')
    except Exception as e:
        logger.error(f'注册机器人命令时出错: {str(e)}')


if __name__ == '__main__':
    # 运行事件循环
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_clients())
    except KeyboardInterrupt:
        print("正在关闭客户端...")
    finally:
        loop.close()
