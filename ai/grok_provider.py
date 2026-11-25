from typing import Optional, List, Dict
from openai import AsyncOpenAI
from .openai_base_provider import OpenAIBaseProvider
import os
import logging
from utils.settings import get_default_ai_model

logger = logging.getLogger(__name__)

class GrokProvider(OpenAIBaseProvider):
    def __init__(self):
        super().__init__(
            env_prefix='GROK',
            default_model=get_default_ai_model('grok'),
            default_api_base='https://api.x.ai/v1'
        )