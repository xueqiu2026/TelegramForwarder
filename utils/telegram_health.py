"""Check Telegram RPC health independently of channel message frequency."""
import asyncio
import logging

from telethon.tl.functions.updates import GetStateRequest

logger = logging.getLogger(__name__)


async def check_connection(client):
    if not client.is_connected():
        await client.connect()
    try:
        await asyncio.wait_for(client(GetStateRequest()), timeout=30)
    except (OSError, asyncio.TimeoutError):
        logger.warning('Telegram RPC 探测失败，重新建立用户连接', exc_info=True)
        await client.disconnect()
        await client.connect()
        await asyncio.wait_for(client(GetStateRequest()), timeout=30)


async def health_loop(client):
    while True:
        await asyncio.sleep(60)
        try:
            await check_connection(client)
        except Exception:
            logger.exception('Telegram 用户连接恢复失败；下一周期重试')
