"""消息落库工具函数 — 将已转发的消息记录到 forwarded_messages 表"""
import logging
from models.models import get_session, ForwardedMessage, ForwardRule

logger = logging.getLogger(__name__)


def log_forwarded_message(rule, event, source_chat_name=None, is_summarized=False):
    """
    将已转发的消息写入 forwarded_messages 表。
    写库失败不抛异常，仅记日志，不影响主转发链路。

    Args:
        rule: ForwardRule 对象
        event: Telethon 消息事件
        source_chat_name: 源频道名称（可选，默认取 rule.source_chat.name）
        is_summarized: 是否已被总结（如果是历史数据补回生成的总结，直接设为 True）
    """
    session = get_session()
    try:
        # [Bug修复] 传入的 rule 实例通常为 detached 状态，需要使用当前 session 重新获取以允许正常 lazy load 关系属性
        db_rule = session.query(ForwardRule).filter_by(id=rule.id).first()
        if not db_rule:
            logger.error(f"消息落库失败：未在数据库中找到对应的规则 ID={rule.id}")
            return

        record = ForwardedMessage(
            rule_id=db_rule.id,
            source_chat_id=db_rule.source_chat.telegram_chat_id,
            source_chat_name=source_chat_name or db_rule.source_chat.name,
            target_chat_id=db_rule.target_chat.telegram_chat_id,
            telegram_message_id=event.message.id,
            message_text=event.message.text or '',    # 存原始文本
            has_media=event.message.media is not None,
            message_date=event.message.date,          # UTC 时间
            is_summarized=is_summarized
        )
        session.add(record)
        session.commit()
        logger.debug(f"消息已落库: rule={db_rule.id}, msg_id={event.message.id}, is_summarized={is_summarized}")
    except Exception as e:
        logger.error(f"消息落库失败(不影响转发): {e}")
        session.rollback()
    finally:
        session.close()
