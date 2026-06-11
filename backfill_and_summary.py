import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from dotenv import load_dotenv
from models.models import get_session, Chat, ForwardRule
from ai import get_ai_provider
from utils.message_logger import log_forwarded_message

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
                    'rule': rule,
                })
        return channels
    finally:
        session.close()

# 消息分段辅助函数
def split_message(text, max_length=3800):
    if not text:
        return []
    parts = []
    while len(text) > 0:
        text = text.lstrip()
        if not text:
            break
        if len(text) <= max_length:
            parts.append(text)
            break
        split_pos = -1
        for sep in ('\n\n', '\n', ' '):
            pos = text.rfind(sep, 0, max_length)
            if pos > 0:
                split_pos = pos
                break
        if split_pos == -1:
            split_pos = max_length
        parts.append(text[:split_pos])
        text = text[split_pos:]
    return parts

async def main():
    target_chat_id_str = '3905915992'  # 目标群组 2026 的 telegram_chat_id
    
    # 建立客户端，使用现有用户 session
    client = TelegramClient('./sessions/user', api_id, api_hash)
    await client.start(phone=phone_number)
    
    # 获取目标群组实体，带 -100 前缀以指示为 Channel
    target_id_int = int(f"-100{target_chat_id_str}")
    target_entity = await client.get_entity(target_id_int)
    
    # 计算过去 24 小时的时间范围
    time_limit = datetime.now(timezone.utc) - timedelta(hours=24)
    print(f"时间范围：从 {time_limit.astimezone().strftime('%Y-%m-%d %H:%M:%S')} 到现在。")
    
    total_forwarded = 0
    all_texts = []
    failed_links = []
    
    channels = get_source_channels()
    print(f"\n开始抓取并转发 24H 消息，共 {len(channels)} 个频道...")
    for idx, channel_info in enumerate(channels, 1):
        try:
            chat_id = channel_info['chat_id']
            print(f"[{idx}/{len(channels)}] 正在处理: {channel_info['name']} (ID: {chat_id}) ...")
            # 直接使用 ID 获取实体
            entity = await client.get_entity(chat_id)
            title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or 'Private Chat'
            
            # 拉取消息
            messages_batch = await client.get_messages(
                entity,
                limit=100,  # 过去24小时一般不会超过100条消息
                offset_date=datetime.now(timezone.utc),
                reverse=False
            )
            
            # 筛选出 24H 内有文本或媒体的消息
            valid_messages = []
            for msg in messages_batch:
                if msg.date >= time_limit:
                    valid_messages.append(msg)
            
            if not valid_messages:
                print(f"  -> 过去 24 小时内无新消息。")
                continue
                
            # 我们希望按时间正序（从旧到新）转发历史消息
            valid_messages.reverse()
            print(f"  -> 找到 {len(valid_messages)} 条 24H 消息，准备转发...")
            
            # 执行转发
            await client.forward_messages(target_entity, valid_messages)
            total_forwarded += len(valid_messages)
            
            # 历史回补落库，直接置 is_summarized=True，避免定时总结任务二次抓取重复总结
            for msg in valid_messages:
                class DummyMessage:
                    def __init__(self, m):
                        self.id = m.id
                        self.text = m.text
                        self.media = m.media
                        self.date = m.date
                class DummyEvent:
                    def __init__(self, m):
                        self.message = DummyMessage(m)
                log_forwarded_message(
                    channel_info['rule'], 
                    DummyEvent(msg), 
                    source_chat_name=channel_info['name'], 
                    is_summarized=True
                )
            
            # 收集文本信息供 AI 总结
            for msg in valid_messages:
                if msg.text:
                    all_texts.append(f"[{title}] {msg.date.astimezone().strftime('%H:%M')}: {msg.text}")
            
            # 防频控延迟
            await asyncio.sleep(0.5)
            
        except Exception as e:
            print(f"❌ 频道 {channel_info['name']} 处理失败: {repr(e)}")
            failed_links.append((channel_info['name'], str(e)))
            
    print(f"\n消息补发完成！共转发 {total_forwarded} 条原始历史消息。")
    
    # 开始进行 AI 聚合总结
    if all_texts:
        print(f"\n收集到 {len(all_texts)} 条文本消息，准备生成 AI 聚合总结...")
        combined_text = "\n".join(all_texts)
        
        # 动态读取默认模型，若无则兜底使用 gpt-5.5
        model_name = os.getenv('DEFAULT_AI_MODEL', 'gpt-5.5')
        summary_prompt = (
            "请分析以下加密货币/MEME币行业电报频道的信息，提取出最有价值的Alpha投资机会、"
            "正在探讨的MEME币热点趋势以及关键的行业动态。用中文生成一份结构非常清晰、视觉效果突出的总结报告。"
            "每个板块分条目整理，如果提到特定代币，请附带分析其投资逻辑、关键数据或关联的智能合约地址(如果有的话)。"
        )
            
        try:
            provider = await get_ai_provider(model_name)
            print(f"正在调用 AI 提供商 ({model_name}) ...")
            summary_result = await provider.process_message(combined_text, prompt=summary_prompt, model=model_name)
            
            if summary_result:
                print("AI 总结生成成功，正在发送到目标群组...")
                
                header = f"📋 【53频道 24H 消息聚合 AI 总结】\n"
                header += f"🕐 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                header += f"📊 消息基底数: {len(all_texts)} 条\n"
                header += f"🤖 使用模型: {model_name}\n\n"
                
                parts = split_message(summary_result, 3500)
                for i, part in enumerate(parts):
                    if i == 0:
                        msg_to_send = header + part
                    else:
                        msg_to_send = f"📋 【53频道 24H 聚合总结 (续 {i+1}/{len(parts)})】\n\n" + part
                    
                    await client.send_message(target_entity, msg_to_send)
                print("AI 总结发送完成！")
            else:
                print("❌ AI 总结返回内容为空。")
        except Exception as e:
            print(f"❌ AI 总结生成或发送失败: {repr(e)}")
    else:
        print("\n没有抓取到任何 24H 文本消息，跳过 AI 总结。")
        
    try:
        await client.disconnect()
        print("Telegram 客户端成功断开连接。")
    except Exception as e:
        print(f"断开连接时发生异常(可忽略): {repr(e)}")

if __name__ == '__main__':
    asyncio.run(main())
