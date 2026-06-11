"""
backfill_messages.py — 从 Telegram 53 个源频道回填 7 天历史消息到数据库
用法: python backfill_messages.py --days 7

注意：需要先停止主服务（main.py），避免 Telegram session 冲突。
"""
import os
import sys
import asyncio
import argparse
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, errors
from dotenv import load_dotenv
from models.models import get_session, ForwardedMessage, ForwardRule
from sqlalchemy.orm import joinedload
from sqlalchemy import and_

# Windows 控制台编码修复
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

load_dotenv()

api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
phone_number = os.getenv('PHONE_NUMBER')

# ── 限流配置 ──
DELAY_BETWEEN_CHANNELS = 2.0       # 频道间等待(秒)
DELAY_BETWEEN_BATCHES = 0.5        # 批次间等待(秒)
BATCH_SIZE = 100                   # 每批消息数
MAX_RETRIES = 3                    # 最大重试次数


def get_source_channels():
    """从数据库获取所有 is_summary=True 的源频道"""
    session = get_session()
    try:
        rules = session.query(ForwardRule).filter_by(is_summary=True).options(
            joinedload(ForwardRule.source_chat),
            joinedload(ForwardRule.target_chat)
        ).all()

        channels = []
        seen = set()
        for rule in rules:
            src = rule.source_chat
            tgt = rule.target_chat
            chat_id = int(src.telegram_chat_id)
            if chat_id not in seen:
                seen.add(chat_id)
                channels.append({
                    'chat_id': chat_id,
                    'name': src.name,
                    'source_chat_id': str(src.telegram_chat_id),
                    'target_chat_id': str(tgt.telegram_chat_id),
                    'rule_id': rule.id,
                })
        return channels
    finally:
        session.close()


def get_existing_message_ids(source_chat_id: str, since: datetime):
    """获取数据库中已存在的消息 ID 集合，用于去重"""
    session = get_session()
    try:
        existing = session.query(ForwardedMessage.telegram_message_id).filter(
            and_(
                ForwardedMessage.source_chat_id == source_chat_id,
                ForwardedMessage.message_date >= since
            )
        ).all()
        return {row[0] for row in existing if row[0] is not None}
    finally:
        session.close()


def save_messages_batch(messages_batch):
    """批量写入数据库"""
    if not messages_batch:
        return 0
    session = get_session()
    try:
        for msg_data in messages_batch:
            record = ForwardedMessage(
                rule_id=msg_data['rule_id'],
                source_chat_id=msg_data['source_chat_id'],
                source_chat_name=msg_data['source_chat_name'],
                target_chat_id=msg_data['target_chat_id'],
                telegram_message_id=msg_data['telegram_message_id'],
                message_text=msg_data['message_text'],
                has_media=msg_data.get('has_media', False),
                message_date=msg_data['message_date'],
                forwarded_at=datetime.now(timezone.utc),
                is_summarized=False,
            )
            session.add(record)
        session.commit()
        return len(messages_batch)
    except Exception as e:
        session.rollback()
        print(f"  ❌ 批量写入失败: {e}")
        return 0
    finally:
        session.close()


async def fetch_channel_messages(client, channel_info, time_limit):
    """从单个频道拉取消息并写入数据库"""
    chat_id = channel_info['chat_id']
    source_chat_id = channel_info['source_chat_id']
    ch_name = channel_info['name']

    # 获取已存在的消息 ID，用于去重
    existing_ids = get_existing_message_ids(source_chat_id, time_limit)

    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            entity = await client.get_entity(chat_id)
            title = getattr(entity, 'title', None) or ch_name

            batch = []
            total_fetched = 0
            total_saved = 0
            total_skipped = 0

            async for message in client.iter_messages(entity, offset_date=datetime.now(timezone.utc)):
                # 超出时间范围，停止
                if message.date < time_limit:
                    break

                # 只保存有文本的消息
                if not message.text:
                    continue

                total_fetched += 1

                # 去重
                if message.id in existing_ids:
                    total_skipped += 1
                    continue

                batch.append({
                    'rule_id': channel_info['rule_id'],
                    'source_chat_id': source_chat_id,
                    'source_chat_name': title,
                    'target_chat_id': channel_info['target_chat_id'],
                    'telegram_message_id': message.id,
                    'message_text': message.text,
                    'has_media': message.media is not None,
                    'message_date': message.date,
                })

                # 每 BATCH_SIZE 条写一次数据库
                if len(batch) >= BATCH_SIZE:
                    saved = save_messages_batch(batch)
                    total_saved += saved
                    batch = []
                    await asyncio.sleep(DELAY_BETWEEN_BATCHES)

            # 写入剩余
            if batch:
                saved = save_messages_batch(batch)
                total_saved += saved

            print(f"  ✅ {title}: 拉取 {total_fetched} 条, 新增 {total_saved} 条, 跳过 {total_skipped} 条(已存在)")
            return total_saved

        except errors.FloodWaitError as e:
            wait_time = e.seconds + 5
            print(f"  ⚠️ 频控！等待 {wait_time} 秒...")
            await asyncio.sleep(wait_time)
            retry_count += 1

        except errors.ChannelPrivateError:
            print(f"  ⚠️ 频道 {ch_name} 为私有/已离开，跳过")
            return 0

        except Exception as e:
            print(f"  ❌ {ch_name} 失败: {repr(e)}")
            return 0

    print(f"  ❌ {ch_name} 重试 {MAX_RETRIES} 次后仍失败")
    return 0


async def main():
    parser = argparse.ArgumentParser(description='回填 Telegram 频道历史消息到数据库')
    parser.add_argument('--days', type=int, default=7, help='回填天数 (默认 7)')
    args = parser.parse_args()

    days = args.days
    time_limit = datetime.now(timezone.utc) - timedelta(days=days)

    print("=" * 60)
    print(f"📥 Telegram 历史消息回填工具")
    print(f"   回填范围: 过去 {days} 天")
    print(f"   起始时间: {time_limit.astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   限流策略: 频道间隔 {DELAY_BETWEEN_CHANNELS}s, 批次间隔 {DELAY_BETWEEN_BATCHES}s")
    print("=" * 60)

    # 获取频道列表
    channels = get_source_channels()
    print(f"\n共 {len(channels)} 个源频道待回填\n")

    # 建立 Telegram 客户端
    client = TelegramClient('./sessions/user', api_id, api_hash)
    await client.start(phone=phone_number)
    print("✅ Telegram 客户端已连接\n")

    grand_total = 0
    for idx, ch in enumerate(channels, 1):
        print(f"[{idx}/{len(channels)}] {ch['name']} (ID: {ch['chat_id']})")
        saved = await fetch_channel_messages(client, ch, time_limit)
        grand_total += saved
        # 频道间限流
        if idx < len(channels):
            await asyncio.sleep(DELAY_BETWEEN_CHANNELS)

    print("\n" + "=" * 60)
    print(f"🎉 回填完成！")
    print(f"   频道数: {len(channels)}")
    print(f"   新增消息总数: {grand_total}")
    print("=" * 60)

    try:
        await client.disconnect()
    except Exception:
        pass


if __name__ == '__main__':
    asyncio.run(main())
