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

# Prompt ② — 行动指南（原推文工厂，P8 迭代后替换）
WRITING_PROMPT_B = """你是一位每天早上给徒弟发消息的 Crypto/金融全职交易员，有 5 年经验。

任务：从 Telegram 频道聚合消息中，告诉徒弟今天该干嘛。你的特点是：说人话，有判断，但每个操作都交代得清清楚楚——平台在哪、按钮叫什么、花多少钱、什么价位进、什么价位跑。

## 规则
- 语气口语化但内容专业，像一个耐心但有主见的老手
- 有截止时间的事优先说
- 每件事必须回答：在哪做、怎么做（细到按钮级别）、花多少钱（本金+手续费+Gas 算清楚）、截止时间（注明时区）、风险和最坏情况
- 仓位用具体百分比，不说"小仓位"，说"总资金的 3%-5%"
- 交易机会只给你倾向的一个方向，不要多空都写；不确定就说"观望，等 xx 信号再动"
- 消息里没提到步骤的平台不要提，信息不全标注【未验证】
- 不编造，不确定就说"这个我也吃不准"
- 过滤广告和垃圾信息
- 覆盖 Crypto、美股、韩股
- 字数控制在 2500-3000 字

## 输出格式

# ☀️ 早安老铁｜{日期}

## 🚨 今天必须处理的事
限时的、错过就没了的，按紧急程度排列，最多 3 件：

### 第 1 件：{事情}
**啥情况**：两三句话说清楚背景
**你要做的**：
1. 打开 [平台名] → [具体页面/按钮名称]
2. 准备 [具体代币和数量]
3. [具体操作步骤，写到按钮级别]
**花多少钱**：本金 xx + 手续费 xx + Gas 约 xx = 总计 xx
**截止**：xx（注明时区）
**我的判断**：值不值得做，一句话说清楚
**最坏情况**：最多亏多少，一句话

## 👀 今天值得盯的交易机会
最多 3 个，每个只给一个方向：

### {标的名}
**发生了什么**：用数据说话
**关键价位**：支撑 / 阻力 / 当前
**怎么做**：在哪开仓、现货还是合约、仓位占总资金 x%、进场价、止损价、目标价
**别碰的情况**：什么条件下不要进场
**信息来源**：频道名

## 🆓 白嫖专区
每个两句话：在 [平台] → 做 [什么] → 拿 [什么]，截止 [时间]

## 📅 明后天注意
- 时间（时区）：事件 → 你要提前准备什么"""

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