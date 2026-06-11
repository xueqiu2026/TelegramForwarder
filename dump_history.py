import os
import sys
import json
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, errors
from dotenv import load_dotenv
from models.models import get_session, Chat, ForwardRule

# 解决 Windows 控制台打印编码问题
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

def get_source_channels():
    """从数据库获取所有需要总结的源频道"""
    session = get_session()
    try:
        rules = session.query(ForwardRule).filter_by(is_summary=True).all()
        channels = []
        seen = set()
        for rule in rules:
            chat_id = rule.source_chat.telegram_chat_id
            if chat_id not in seen:
                seen.add(chat_id)
                channels.append({
                    'chat_id': int(chat_id),
                    'name': rule.source_chat.name,
                })
        return channels
    finally:
        session.close()

OUTPUT_FILE = "scratch/messages_dump.json"

async def main():
    os.makedirs("scratch", exist_ok=True)
    
    # 建立客户端，使用现有用户 session
    client = TelegramClient('./sessions/user', api_id, api_hash)
    await client.start(phone=phone_number)
    
    # 30天的截止时间
    time_limit = datetime.now(timezone.utc) - timedelta(days=30)
    print(f"数据抓取时间范围：从 {time_limit.astimezone().strftime('%Y-%m-%d %H:%M:%S')} 到现在 (共30天)。")
    
    # 如果已存在部分抓取数据，载入它以支持追加或跳过
    all_data = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                all_data = json.load(f)
            print(f"载入已存在的数据文件，已抓取 {len(all_data)} 个频道的数据。")
        except Exception:
            pass
            
    channels = get_source_channels()
    print(f"\n开始抓取历史消息，共 {len(channels)} 个频道...")
    
    for idx, channel_info in enumerate(channels, 1):
        chat_id = channel_info['chat_id']
        key = str(chat_id)
        
        # 如果已经抓过该频道，可以根据需要跳过
        if key in all_data and len(all_data[key].get("messages", [])) > 0:
            print(f"[{idx}/{len(channels)}] 跳过已存在的频道: {channel_info['name']}")
            continue
            
        print(f"[{idx}/{len(channels)}] 正在抓取: {channel_info['name']} (ID: {chat_id}) ...")
        
        retry_count = 0
        while retry_count < 3:
            try:
                # 获取实体
                entity = await client.get_entity(chat_id)
                title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or 'Private Chat'
                
                channel_messages = []
                
                # 使用 iter_messages 自动分页拉取
                async for message in client.iter_messages(entity, offset_date=datetime.now(timezone.utc)):
                    # 遇到30天前消息，结束拉取
                    if message.date < time_limit:
                        break
                    
                    # 只保存带有文本内容的消息
                    if message.text:
                        channel_messages.append({
                            "id": message.id,
                            "date": message.date.astimezone().isoformat(),
                            "text": message.text
                        })
                
                print(f"  -> 成功抓取到 {len(channel_messages)} 条消息。")
                
                # 存入字典
                all_data[key] = {
                    "channel_name": title,
                    "channel_id": entity.id,
                    "messages": channel_messages
                }
                
                # 每次抓完一个频道实时保存，防止中断丢失
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(all_data, f, ensure_ascii=False, indent=2)
                
                # 成功后休眠，防频控
                await asyncio.sleep(1.0)
                break
                
            except errors.FloodWaitError as e:
                print(f"⚠️ 触发频控，需要休眠 {e.seconds} 秒...")
                await asyncio.sleep(e.seconds + 2)
                retry_count += 1
            except Exception as e:
                print(f"❌ 通道 {channel_info['name']} 处理失败: {repr(e)}")
                # 发生其他异常，记录为空并继续
                all_data[key] = {
                    "channel_name": channel_info['name'],
                    "error": str(e),
                    "messages": []
                }
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(all_data, f, ensure_ascii=False, indent=2)
                break
                
    total_msgs = sum(len(c.get("messages", [])) for c in all_data.values() if "messages" in c)
    print("\n" + "="*40)
    print(f"抓取完成！数据已保存至: {OUTPUT_FILE}")
    print(f"总计成功抓取频道数: {len(all_data)}")
    print(f"总消息条数: {total_msgs}")
    print("="*40)
    
    try:
        await client.disconnect()
    except Exception:
        pass

if __name__ == '__main__':
    asyncio.run(main())
