import os
import sys
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
from models.models import get_session, Chat, ForwardRule, MediaTypes

# 解决 Windows 控制台打印 UTF-8 字符崩溃问题
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

links = [
    "https://t.me/justicekingsman",
    "t.me/OWfullchain",
    "t.me/Barry_Degen",
    "t.me/jiuyicall",
    "t.me/cryptogoodreads",
    "t.me/groupdigest",
    "t.me/Thoughts_BFox",
    "t.me/moneygrid",
    "t.me/cjblockchain",
    "t.me/au_call",
    "t.me/cryptonarratives1",
    "t.me/yondcallsbeta",
    "t.me/EnhengAlpha",
    "t.me/EleveAlpha",
    "t.me/yueya_fnf",
    "t.me/muur_posts",
    "t.me/fionasdailynews",
    "t.me/rohaninvestor",
    "t.me/tlsrltnf",
    "t.me/Ed_blockdaily",
    "t.me/CryptoMarketAggregator",
    "t.me/mujammin123",
    "t.me/sleepingclub0",
    "t.me/ai_9684xtpa",
    "t.me/magonia_b",
    "t.me/cutepandacalls",
    "t.me/crypt0_sea",
    "t.me/allforweb3",
    "t.me/higerblock",
    "t.me/alvinmurmur",
    "t.me/dolchanchain",
    "t.me/journey_of_someone",
    "t.me/XipzAlpha",
    "t.me/wendy_alpha",
    "t.me/PowsGemCalls",
    "t.me/dollcall",
    "t.me/xxk_hype",
    "t.me/overdose_gems_calls",
    "t.me/mrblock_info",
    "t.me/alphageeks",
    "t.me/picklecati",
    "t.me/CyptoForest",
    "t.me/AlphaBatcher",
    "t.me/anitaloveyouall",
    "t.me/xuegaoz",
    "t.me/kaizeai",
    "t.me/LamIsRealGoat",
    "t.me/garyplaya",
    "t.me/Sacccgx",
    "t.me/CryptoFamily_ilhyun",
    "t.me/Fencun_call",
    "t.me/Veil_Y",
    "t.me/BTCdayu2"
]

# 统一处理链接前缀
clean_links = []
for link in links:
    link = link.strip()
    if not link:
        continue
    if not link.startswith("https://") and not link.startswith("http://"):
        if link.startswith("t.me/"):
            link = "https://" + link
        else:
            link = "https://t.me/" + link
    if link not in clean_links:
        clean_links.append(link)

async def main():
    # 目标群组在数据库中的ID是2 (对应 '2026', '3905915992')
    target_chat_id = 2
    
    # 建立客户端，使用现有用户 session
    client = TelegramClient('./sessions/user', api_id, api_hash)
    await client.start(phone=phone_number)
    
    session = get_session()
    
    success_count = 0
    failed_links = []
    
    print(f"开始处理，共 {len(clean_links)} 个唯一频道链接...")
    
    for link in clean_links:
        try:
            print(f"正在获取实体: {link} ...")
            entity = await client.get_entity(link)
            chat_id_str = str(entity.id)
            title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or 'Private Chat'
            
            # 查找或插入Chat表
            db_chat = session.query(Chat).filter(Chat.telegram_chat_id == chat_id_str).first()
            if not db_chat:
                db_chat = Chat(telegram_chat_id=chat_id_str, name=title)
                session.add(db_chat)
                session.flush()
                print(f"已往 chats 表添加新频道: {title} (ID: {chat_id_str})")
            else:
                # 更新一下可能变化的名称
                if db_chat.name != title:
                    db_chat.name = title
                    session.flush()
                print(f"chats 表已存在频道: {title} (ID: {chat_id_str})")
                
            # 查找或插入转发规则表
            db_rule = session.query(ForwardRule).filter(
                ForwardRule.source_chat_id == db_chat.id,
                ForwardRule.target_chat_id == target_chat_id
            ).first()
            
            if not db_rule:
                db_rule = ForwardRule(
                    source_chat_id=db_chat.id,
                    target_chat_id=target_chat_id
                )
                session.add(db_rule)
                session.flush()
                
                # 创建默认的媒体类型屏蔽项
                media_types = MediaTypes(
                    rule_id=db_rule.id,
                    photo=False,
                    document=False,
                    video=False,
                    audio=False,
                    voice=False
                )
                session.add(media_types)
                session.flush()
                
                print(f"已建立转发规则: {title} -> target_chat(id={target_chat_id})")
                success_count += 1
            else:
                print(f"转发规则已存在: {title} -> target_chat(id={target_chat_id})")
                
        except Exception as e:
            # 用 repr 包装异常消息以保证打印安全
            print(f"❌ 链接 {link} 解析失败: {repr(e)}")
            failed_links.append((link, str(e)))
            
    session.commit()
    session.close()
    
    print("\n" + "="*40)
    print(f"处理完成！成功新增绑定: {success_count} 个频道")
    if failed_links:
        print(f"失败的链接 ({len(failed_links)} 个):")
        for f_link, err in failed_links:
            print(f" - {f_link}: {err}")
    print("="*40)

if __name__ == '__main__':
    asyncio.run(main())
