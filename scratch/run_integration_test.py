import os
import sys
import asyncio
from datetime import datetime, timezone

# 解决路径加载
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telethon import TelegramClient
from models.models import get_session, ForwardedMessage, ForwardRule
from scheduler.summary_scheduler import SummaryScheduler

# 解决 Windows 控制台编码
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

async def test_pipeline():
    print("=" * 60)
    print("开始 P5 聚合总结端到端集成测试")
    print("=" * 60)

    session = get_session()
    try:
        # 1. 查找或指定测试规则 (关联目标群组 3905915992, 源频道 2478953325, 规则 ID=1)
        rule = session.query(ForwardRule).filter_by(id=1).first()
        if not rule:
            print("错误: 数据库中未找到规则 ID=1 的测试规则，请确认迁移数据完整！")
            return

        target_chat_id = rule.target_chat.telegram_chat_id
        source_chat_id = rule.source_chat.telegram_chat_id
        print(f"测试规则加载成功: 目标群组={rule.target_chat.name} ({target_chat_id}), 源频道={rule.source_chat.name} ({source_chat_id})")

        # 2. 手动往 forwarded_messages 插入 3 条模拟的测试消息
        print("\n[Step 1] 正在往数据库中插入 3 条模拟未总结消息...")
        
        # 清除数据库中以前的残留未总结测试消息，保持干净环境
        session.query(ForwardedMessage).filter_by(target_chat_id=target_chat_id, is_summarized=False).delete()
        session.commit()

        messages = [
            ForwardedMessage(
                rule_id=rule.id,
                source_chat_id=source_chat_id,
                source_chat_name=rule.source_chat.name,
                target_chat_id=target_chat_id,
                telegram_message_id=99991,
                message_text="测试消息1: 以太坊 Layer2 扩容网络 TVL 本周突破历史新高，Meme 币在 Arbitrum 链上产生大量交易活动。",
                has_media=False,
                message_date=datetime.now(timezone.utc),
                is_summarized=False
            ),
            ForwardedMessage(
                rule_id=rule.id,
                source_chat_id=source_chat_id,
                source_chat_name=rule.source_chat.name,
                target_chat_id=target_chat_id,
                telegram_message_id=99992,
                message_text="测试消息2: 币安宣布上线全新 MEME 代币交易对，引发全网流动性集中暴涨，日内涨幅达 150%。",
                has_media=False,
                message_date=datetime.now(timezone.utc),
                is_summarized=False
            ),
            ForwardedMessage(
                rule_id=rule.id,
                source_chat_id=source_chat_id,
                source_chat_name=rule.source_chat.name,
                target_chat_id=target_chat_id,
                telegram_message_id=99993,
                message_text="测试消息3: 加密货币分析师指出，当前市场 Meme 板块热度仍在持续，散户情绪高涨，建议关注链上 gas 费用变动。",
                has_media=False,
                message_date=datetime.now(timezone.utc),
                is_summarized=False
            )
        ]
        
        session.add_all(messages)
        session.commit()
        print("✅ 3 条未总结消息插入成功！")

        # 3. 实例化并启动 Telethon Bot 客户端
        print("\n[Step 2] 正在连接 Telegram Bot 客户端...")
        api_id = int(os.getenv('API_ID'))
        api_hash = os.getenv('API_HASH')
        bot_token = os.getenv('BOT_TOKEN')
        
        # 启动 bot 客户端
        bot_client = TelegramClient('./sessions/bot_test', api_id, api_hash)
        await bot_client.start(bot_token=bot_token)
        print("✅ Telegram Bot 客户端连接成功！")

        # 4. 实例化并启动 Telethon User 客户端（部分调度器方法可能需要）
        user_client = TelegramClient('./sessions/user', api_id, api_hash)
        await user_client.start(phone=os.getenv('PHONE_NUMBER'))

        # 5. 构建调度器并手动触发聚合总结
        print("\n[Step 3] 构建调度器并手动触发该目标群组的聚合总结...")
        scheduler = SummaryScheduler(user_client, bot_client)
        
        # 提取配置 (使用 gemini-3.5-flash 确保测试高可用)
        ai_model = 'gemini-3.5-flash'
        config = {
            'target_chat_id': target_chat_id,
            'target_chat_name': rule.target_chat.name,
            'summary_time': rule.summary_time,
            'summary_prompt': "请用一两句话简明总结以下电报群消息，标明这是集成测试消息，并提取出代币关键字即可。",
            'ai_model': ai_model
        }
        
        print(f"聚合总结参数: 目标群组={config['target_chat_name']}, AI模型={config['ai_model']}")
        print("正在执行 _execute_aggregated_summary ...")
        
        await scheduler._execute_aggregated_summary(target_chat_id, config, is_now=True)
        print("✅ _execute_aggregated_summary 执行方法结束。")

        # 6. 后置验证：检查这 3 条消息是否已经被成功标记为 is_summarized=True
        print("\n[Step 4] 校验数据库状态归档...")
        session.expire_all()
        rem_msgs = session.query(ForwardedMessage).filter(
            ForwardedMessage.telegram_message_id.in_([99991, 99992, 99993])
        ).all()
        
        for m in rem_msgs:
            print(f"  -> 消息 ID={m.telegram_message_id} | 原始文本: {m.message_text[:15]}... | 是否已总结: {m.is_summarized}")
            
        success = all(m.is_summarized for m in rem_msgs)
        if success:
            print("\n🎉 端到端集成测试成功！数据库状态已正确流转为已总结 (is_summarized=True)！")
        else:
            print("\n❌ 校验失败：部分消息状态未流转，请检查控制台 AI 调用及 Telegram 发送日志！")

        # 7. 清理测试产生的模拟数据
        session.query(ForwardedMessage).filter(
            ForwardedMessage.telegram_message_id.in_([99991, 99992, 99993])
        ).delete()
        session.commit()
        print("✅ 物理删除测试消息模拟数据，数据库环境已复原。")

        # 关闭连接
        await bot_client.disconnect()
        await user_client.disconnect()
        print("Telegram 客户端成功断开。")

    except Exception as e:
        print(f"\n❌ 测试过程发生异常阻断: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    asyncio.run(test_pipeline())
