from typing import Optional, List, Dict
from openai import AsyncOpenAI
from .openai_base_provider import OpenAIBaseProvider
import os
import logging

logger = logging.getLogger(__name__)

class DeepSeekProvider(OpenAIBaseProvider):
    def __init__(self):
        super().__init__(
            env_prefix='DEEPSEEK',
            default_model='deepseek-v4-pro-260425',
            default_api_base='https://ark.cn-beijing.volces.com/api/v3'
        )

    async def initialize(self, **kwargs) -> None:
        """初始化火山引擎 DeepSeek 客户端"""
        try:
            # 优先读取火山引擎专用密钥 ARK_API_KEY，其次回退至 DEEPSEEK_API_KEY
            api_key = os.getenv('ARK_API_KEY') or os.getenv('DEEPSEEK_API_KEY')
            if not api_key:
                raise ValueError("未设置 ARK_API_KEY 或 DEEPSEEK_API_KEY 环境变量")

            api_base = os.getenv('DEEPSEEK_API_BASE', '').strip() or self.default_api_base

            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_base
            )

            self.model = kwargs.get('model', self.default_model)
            logger.info(f"初始化火山引擎 DeepSeek 客户端。模型: {self.model}, 终结点: {api_base}")

        except Exception as e:
            error_msg = f"初始化火山引擎 DeepSeek 客户端出错: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise
