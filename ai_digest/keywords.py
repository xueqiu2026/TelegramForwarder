"""Combination recall only; an AI-context fallback retains unrecognized actions."""
import re
import unicodedata

AI = r'\b(?:ai|llm|gpt[ -]?[0-9.]*|chatgpt|codex|claude|anthropic|gemini|grok|antigravity|qoder|deepseek|glm|minimax|inference|tokens?|openai|sonnet|opus|haiku)\b|人工智能|大模型|反重力|豆包|文心|推理模型'
ACTIONS = {
 'B1': r'注册|申请|新号|新用户|赠金|赠送|额度|signup|sign up|new account|apply|welcome|starter|credit|регистрац|кредиты|daftar|akun baru|kredit',
 'B5': r'学生|教育邮箱|研究者|开源|贡献者|维护者|地区|渠道|设备|student|education|researcher|maintainer|contributor|eligible|regional|device offer|студент',
 'B6': r'登录|邀请|兑换|工作区|新增|老用户|双倍|赠积分|重置|login|sign in|existing user|workspace|bonus|claim|redeem|referral|invite|double usage|klaim|undangan',
 'T1': r'接入|配置|兼容|环境|登录报错|连接|connect|setup|config|integration|endpoint|base_url|api.?key|responses api|настройка|подключение',
 'T5': r'缓存|命中|压缩|省|速度|额度优化|限流|吞吐|延迟|cache|context compression|rate limit|latency|throughput|batch|tok/s|tokens/s',
 'T7': r'注册|验证|免k|绑卡|订阅|账单|续费|取消|退款|候补|waitlist|verification|kyc|subscription|billing|renewal|cancel|refund|payment|подписка|langganan|tanpa kartu',
 'U': r'发布|上线|升级|新模型|重置|恢复配额|release|launch|model update|available|reset|refill|сброс|лимит|kuota'
}
HOT = r'claude|anthropic|gpt|chatgpt|codex|grok|gemini|antigravity|反重力'


def recall(body, entities=()):
    value = unicodedata.normalize('NFKC', body).casefold()
    context = value + ' ' + ' '.join(e.get('url') or '' for e in entities)
    if not re.search(AI, context, re.I):
        return 'unmatched', []
    hits = [category for category, pattern in ACTIONS.items() if re.search(pattern, value, re.I)
            and (category != 'U' or re.search(HOT, context, re.I))]
    return ('hit' if hits else 'needs_check'), hits
