"""消息落库过滤器 — 在 SenderFilter 发送成功后将消息写入数据库"""
import logging
from filters.base_filter import BaseFilter
from utils.message_logger import log_forwarded_message

logger = logging.getLogger(__name__)


class MessageLogFilter(BaseFilter):
    """
    消息落库过滤器。
    位于 FilterChain 中 SenderFilter 之后，将消息原始文本写入 forwarded_messages 表。
    写库失败不中断 FilterChain，返回 True 继续后续过滤器。
    """

    async def _process(self, context):
        try:
            # 只有实际发送成功的消息才落库（should_forward=True 且到达此 filter 说明 SenderFilter 已通过）
            if not context.should_forward:
                return True

            rule = context.rule
            event = context.event

            # 使用原始文本，而非经过 ReplaceFilter/AIFilter 处理后的文本
            log_forwarded_message(rule, event, is_summarized=False)

        except Exception as e:
            logger.error(f"MessageLogFilter 执行异常(不影响转发): {e}")

        return True  # 无论成功失败，都不中断后续 filter
