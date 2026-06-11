"""手动触发一次聚合总结测试（含写作推送）
使用方法：先停止 main.py，再运行本脚本
"""
import asyncio
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telethon import TelegramClient
from dotenv import load_dotenv
load_dotenv()

from scheduler.summary_scheduler import SummaryScheduler
from models.models import get_session, ForwardRule
from sqlalchemy.orm import joinedload

async def main():
    api_id = int(os.getenv('API_ID'))
    api_hash = os.getenv('API_HASH')
    bot_token = os.getenv('BOT_TOKEN')

    user_client = TelegramClient('sessions/user', api_id, api_hash)
    bot_client = TelegramClient('sessions/bot', api_id, api_hash)

    await user_client.start()
    await bot_client.start(bot_token=bot_token)
    print("✅ Telegram 客户端已连接")

    scheduler = SummaryScheduler(user_client, bot_client)

    db = get_session()
    rule = db.query(ForwardRule).filter_by(is_summary=True).options(
        joinedload(ForwardRule.target_chat)
    ).first()

    if not rule:
        print("❌ 没有找到启用总结的规则")
        db.close()
        return

    target_id = rule.target_chat.telegram_chat_id
    config = {
        'target_chat_id': target_id,
        'target_chat_name': rule.target_chat.name,
        'summary_time': '06:00',
        'summary_prompt': rule.summary_prompt,
        'ai_model': rule.ai_model,
    }
    db.close()

    print(f"📋 目标群组: {config['target_chat_name']} ({target_id})")
    print("🚀 开始执行聚合总结 + 写作推送...")

    await scheduler._execute_aggregated_summary(target_id, config, is_now=True)

    print("✅ 全部完成！检查 Telegram 群组是否收到 3 条消息。")

    await user_client.disconnect()
    await bot_client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
