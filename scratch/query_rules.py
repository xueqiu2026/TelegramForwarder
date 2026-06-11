import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.models import get_session, ForwardRule, Chat
from dotenv import load_dotenv

load_dotenv()

def query_rules():
    session = get_session()
    try:
        rules = session.query(ForwardRule).filter_by(is_summary=True).all()
        print(f"找到 {len(rules)} 条启用了定时总结的规则：")
        for r in rules:
            print(f"规则 ID: {r.id}")
            print(f"  源频道: {r.source_chat.name} (TG ID: {r.source_chat.telegram_chat_id})")
            print(f"  目标群组: {r.target_chat.name} (TG ID: {r.target_chat.telegram_chat_id})")
            print(f"  总结时刻: {r.summary_time}")
            print(f"  AI模型: {r.ai_model}")
            print(f"  置顶总结: {r.is_top_summary}")
            print(f"  国内推送: {r.enable_push}")
            print("-" * 40)
    except Exception as e:
        print(f"查询出错: {e}")
    finally:
        session.close()

if __name__ == '__main__':
    query_rules()
