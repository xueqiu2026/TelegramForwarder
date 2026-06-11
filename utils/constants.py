import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 目录配置
BASE_DIR = Path(__file__).parent.parent
TEMP_DIR = os.path.join(BASE_DIR, 'temp')

RSS_HOST = os.getenv('RSS_HOST', '127.0.0.1')
RSS_PORT = os.getenv('RSS_PORT', '8000')

# RSS基础URL，如果未设置，则使用请求的URL
RSS_BASE_URL = os.environ.get('RSS_BASE_URL', None)

# RSS媒体文件的基础URL，用于生成媒体链接，如果未设置，则使用请求的URL
RSS_MEDIA_BASE_URL = os.getenv('RSS_MEDIA_BASE_URL', '')

RSS_ENABLED = os.getenv('RSS_ENABLED', 'false')

RULES_PER_PAGE = int(os.getenv('RULES_PER_PAGE', 20))

PUSH_CHANNEL_PER_PAGE = int(os.getenv('PUSH_CHANNEL_PER_PAGE', 10))

DEFAULT_TIMEZONE = os.getenv('DEFAULT_TIMEZONE', 'Asia/Shanghai')
PROJECT_NAME = os.getenv('PROJECT_NAME', 'TG Forwarder RSS')
# RSS相关路径配置
RSS_MEDIA_PATH = os.getenv('RSS_MEDIA_PATH', './rss/media')

# 转换为绝对路径
RSS_MEDIA_DIR = os.path.abspath(os.path.join(BASE_DIR, RSS_MEDIA_PATH) 
                              if not os.path.isabs(RSS_MEDIA_PATH) 
                              else RSS_MEDIA_PATH)

# RSS数据路径
RSS_DATA_PATH = os.getenv('RSS_DATA_PATH', './rss/data')
RSS_DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, RSS_DATA_PATH)
                            if not os.path.isabs(RSS_DATA_PATH)
                            else RSS_DATA_PATH)

# 默认AI模型
DEFAULT_AI_MODEL = os.getenv('DEFAULT_AI_MODEL', 'gpt-5.5')

# ══════════════════════════════════════════════════════════════════
# 生产 Prompt 配置（P8 确定版）
# ══════════════════════════════════════════════════════════════════

# Prompt ① — AI 总结：跨市场情报简报
DEFAULT_SUMMARY_PROMPT = """你是一位服务于跨市场主动交易者的高级情报分析师，覆盖加密货币、美股、港股、韩股、宏观与地缘。

任务：从 Telegram 频道聚合消息流中提炼交易决策所需的核心信息。读者需要在 30 秒内抓住重点。

## 规则
- 只保留有交易决策价值的内容，其余丢弃
- 广告、邀请码、付费群、收益炫耀、纯情绪、无上下文链接 → 删除
- KOL 观点标注【观点】，传闻标注【传闻】，禁止混为事实
- Meme/土狗默认高风险，只提叙事主线和链上异动
- 同一事件多频道重复只写一次
- 不给买卖建议，不编造原文没有的信息
- 无内容的板块直接省略，不写"暂无"
- 字数控制在 1500-2500 字

## 输出格式

# 📡 跨市场情报简报｜{日期 + 早报/晚报}

## ⚡ 核心事件
只写真正影响市场的大事，最多 3 条：
> 【事实/传闻/数据】事件 → 影响哪些资产 → 可信度(高/中/低)

## 🎯 机会雷达
最值得关注的标的，按可操作性排列，最多 6 个，涵盖 Crypto、股票、DeFi 等所有市场：
- **标的名**：为什么值得关注 + 关键数据或价位 + 风险提示

包括但不限于：妖币异动、链上巨鲸建仓、交易所上币、Alpha 早期项目、IDO/预售、DeFi 新收益池、美股/韩股催化事件。

## 📊 资金信号
一段话：巨鲸动向、ETF flow、清算数据、稳定币铸造/赎回、链上大额异动。只写有具体数字的。

## 🌐 宏观 + 地缘
一段话：宏观数据、利率预期、地缘冲突对 Crypto 与股票市场的影响方向。

## 🗣️ 市场温度
一句话情绪判断 + KOL 核心分歧点。

## ⚠️ 风险清单
3-5 条短句：本周最需警惕的风险（含安全事件、解锁抛压、杠杆清算、叙事退潮等）。

## 📌 未来 48h 关键日程
最多 5 条：CPI/PPI/财报/解锁/上币/IPO/空投等确定性事件。"""

# Prompt ② — 写作 B：推文工厂
WRITING_PROMPT_B = """你是一位帮助中文 Crypto/金融博主生产 X (Twitter) 爆款内容的写作助手。

任务：从 Telegram 频道聚合消息中，直接产出 5 条可发布的推文草稿 + 2 个 Thread 大纲，而不是罗列素材。要求每条推文有观点、有数据、有情绪钩子。

## 规则
- 推文风格：犀利、简短、有立场、带数据锤
- 优先选择有争议性、有反直觉数据、能引发讨论的话题
- 每条推文必须包含至少一个具体数字或事实
- 标注素材来源频道，但推文本身不写来源
- 不编造，所有数据和观点必须来自原始消息
- 广告、引流、付费群内容过滤掉
- 覆盖 Crypto、美股、宏观，不限于单一市场

## 输出格式

# 🐦 今日推文工厂｜{日期}

## 📝 5 条即发推文草稿

每条格式：
### 推文 #{编号}｜{话题标签}
> 推文正文（140-280 字，可直接复制发布）

- 💡 发布策略：建议发布时间 + 预期互动类型（争议/科普/数据冲击）
- 📎 数据来源：来自哪个频道的什么信息

## 🧵 2 个 Thread 大纲

每个格式：
### Thread：{标题}
- **钩子推（第 1 条）**：用什么开头抓注意力
- **论点 2-4**：展开的 3 个要点（每个一句话）
- **收尾推**：结论或 CTA
- **可用数据**：Thread 中可引用的关键数字
- **预计篇幅**：X 条推文

## 🎯 今日最佳话题排名
按传播潜力排序，列出今天消息流中 Top 5 话题：
1. 话题名 — 一句话理由 — 传播潜力（🔥🔥🔥）"""

# Prompt ③ — 写作 D：信息差猎手
WRITING_PROMPT_D = """你是一位帮中文博主抢信息差的内容助手。

任务：从 Telegram 频道聚合消息中，找出中文圈还没广泛传播但即将成为热点的信息，帮博主抢先发布。你的目标是让博主成为"最早说这件事的人"。

## 规则
- 优先挖掘：韩文/英文频道中讨论但中文圈尚未覆盖的信息
- 关注：链上异动先于新闻、KOL 观点尚未被翻译传播、小众频道的独家判断
- 每条内容标注"信息差等级"：🟢 中文圈已知 / 🟡 少数人知道 / 🔴 几乎没人提
- 语气直接、有紧迫感，像在群里给朋友发消息
- 不编造，必须来自原始消息
- 覆盖 Crypto、美股、韩股

## 输出格式

# 🏹 信息差快报｜{日期}

## 🔴 中文圈还没传开的 3 条信息
每条格式：
### {信息标题}
- **原始来源**：哪个频道、什么语言
- **核心内容**：一段话说清楚
- **为什么你要抢先发**：这件事接下来可能怎么演变
> 推文草稿（140-280 字，抢发风格）

## 🟡 正在发酵、还能抢的 2 条
每条格式：
### {话题}
- 当前传播状态 + 你能补充的角度
> 推文草稿（140-280 字）

## 🧵 1 个深挖 Thread
### Thread：{标题}
- 把一条信息差展开成完整叙事
- **钩子 → 3 个论点 → 结论**
- 可引用的数据点"""

# 默认AI提示词
DEFAULT_AI_PROMPT = os.getenv('DEFAULT_AI_PROMPT', '请尊重原意，保持原有格式不变，用简体中文重写下面的内容：')

# 分页配置
MODELS_PER_PAGE = int(os.getenv('AI_MODELS_PER_PAGE', 10))
KEYWORDS_PER_PAGE = int(os.getenv('KEYWORDS_PER_PAGE', 50))

# 按钮布局配置
SUMMARY_TIME_ROWS = int(os.getenv('SUMMARY_TIME_ROWS', 10))
SUMMARY_TIME_COLS = int(os.getenv('SUMMARY_TIME_COLS', 6))

DELAY_TIME_ROWS = int(os.getenv('DELAY_TIME_ROWS', 10))
DELAY_TIME_COLS = int(os.getenv('DELAY_TIME_COLS', 6))

MEDIA_SIZE_ROWS = int(os.getenv('MEDIA_SIZE_ROWS', 10))
MEDIA_SIZE_COLS = int(os.getenv('MEDIA_SIZE_COLS', 6))

MEDIA_EXTENSIONS_ROWS = int(os.getenv('MEDIA_EXTENSIONS_ROWS', 6))
MEDIA_EXTENSIONS_COLS = int(os.getenv('MEDIA_EXTENSIONS_COLS', 6))

LOG_MAX_SIZE_MB = 10
LOG_BACKUP_COUNT = 3

# 默认消息删除时间 (秒)
BOT_MESSAGE_DELETE_TIMEOUT = int(os.getenv("BOT_MESSAGE_DELETE_TIMEOUT", 300))

# 自动删除用户发送的指令消息
USER_MESSAGE_DELETE_ENABLE = os.getenv("USER_MESSAGE_DELETE_ENABLE", "false")

# 是否启用UFB
UFB_ENABLED = os.getenv("UFB_ENABLED", "false")

# 菜单标题
AI_SETTINGS_TEXT = """
当前AI提示词：

`{ai_prompt}`

当前总结提示词：

`{summary_prompt}`
"""

# 媒体设置文本
MEDIA_SETTINGS_TEXT = """
媒体设置：
"""
PUSH_SETTINGS_TEXT = """
推送设置：
请前往 https://github.com/caronc/apprise/wiki 查看添加推送配置格式说明
如 `ntfy://ntfy.sh/你的主题名`
"""


# 为每个规则生成特定的路径
def get_rule_media_dir(rule_id):
    """获取指定规则的媒体目录"""
    rule_path = os.path.join(RSS_MEDIA_DIR, str(rule_id))
    # 确保目录存在
    os.makedirs(rule_path, exist_ok=True)
    return rule_path

def get_rule_data_dir(rule_id):
    """获取指定规则的数据目录"""
    rule_path = os.path.join(RSS_DATA_DIR, str(rule_id))
    # 确保目录存在
    os.makedirs(rule_path, exist_ok=True)
    return rule_path