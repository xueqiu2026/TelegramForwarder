"""Strict calls for the opportunity path; leave legacy provider behavior unchanged."""
import json


class ModelOutputError(ValueError):
    def __init__(self, message, usage):
        super().__init__(message)
        self.usage = usage


async def call_model(model, prompt, data):
    from ai import get_ai_provider
    from ai.openai_base_provider import OpenAIBaseProvider
    provider = await get_ai_provider(model)
    await provider.initialize(model=model)
    adapter = getattr(provider, 'provider', None) or provider
    if isinstance(adapter, OpenAIBaseProvider):
        try:
            response = await adapter.client.chat.completions.create(
                model=model, messages=[{'role': 'system', 'content': prompt},
                                       {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
                response_format={'type': 'json_object'})
            usage = response.usage.model_dump() if response.usage else {'available': False}
            if response.choices[0].finish_reason == 'length':
                raise ModelOutputError('模型输出达到长度上限，结果未保存为成功', usage)
            body = response.choices[0].message.content
        finally:
            await adapter.client.close()
    else:
        body = await provider.process_message(json.dumps(data, ensure_ascii=False), prompt=prompt, model=model)
        usage = {'available': False, 'reason': '现有供应商接口未返回 usage'}
    if not body or body.startswith(('AI处理失败', '处理失败', 'Error:')):
        raise ModelOutputError('模型未返回有效分析结果', usage)
    if body.strip().startswith('```'):
        body = body.strip().split('\n', 1)[1].rsplit('```', 1)[0]
    try:
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError('模型结果必须为对象')
    except (ValueError, TypeError) as exc:
        raise ModelOutputError('模型返回的JSON无效', usage) from exc
    return result, usage
