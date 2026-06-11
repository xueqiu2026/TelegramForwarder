import os
import sys
import json
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from ai import get_ai_provider

# 解决 Windows 控制台打印编码问题
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

load_dotenv()

INPUT_FILE = "scratch/messages_dump.json"
OUTPUT_FILE = "scratch/test_prompt_output.md"

# ==================== 调试配置项 ====================
# 1. 待测试的 AI 模型名称
MODEL_NAME = "gpt-5.5" # 可选: "gemini-3.5-flash" 或 "gpt-5.5" 等

# 2. 待调试的 AI 总结提示词 (Prompt)
TEST_PROMPT = """
你是一个顶级的加密货币研究员和 Alpha 挖掘分析师。
请阅读下方 53 个电报频道过去 {days} 天的消息，生成一份深度、精准的总结报告。

要求：
1. **严格排除干扰**：过滤掉垃圾广告、返佣链接、单纯的情绪化内容。
2. **拒绝 MEME 刷屏偏向**：重点提取真正的技术型 Alpha 机会、公链生态进展、大额融资项目、创新的 DeFi 协议或安全事件。对于单纯的无逻辑 MEME 币喊单，仅在有极高讨论热度时做一笔带过的简单归纳，不得作为报告主体。
3. **结构化呈现**：
   - 🌟 **核心 Alpha 机会与新项目分析**（项目名称、主要亮点、参与机会、合约地址或推特链接）
   - ⚙️ **主流公链与基础设施动态**（如以太坊、Solana 等生态的技术演进和硬核热点）
   - 💰 **关键融资与产业变动**（VC 投资走向、官方合作）
   - ⚠️ **风险提示与安全阻击**（黑客攻击、撤池子等）
4. **格式规范**：使用中文编写，分条目罗列，重点代币和逻辑使用加粗突出。
"""

# 3. 指定用来测试的消息时间跨度 (天)
TEST_DAYS = 1 # 可以是 1 (过去24小时), 3 (过去3天), 7 (过去7天) 等
# ===================================================

async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ 数据文件 {INPUT_FILE} 不存在。请先运行 dump_history.py 下载数据。")
        return
        
    print(f"正在读取本地消息数据: {INPUT_FILE} ...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
        
    # 计算截止时间
    now = datetime.now(timezone.utc)
    time_limit = now - timedelta(days=TEST_DAYS)
    print(f"正在提取最近 {TEST_DAYS} 天的消息 (从 {time_limit.astimezone().strftime('%Y-%m-%d %H:%M:%S')} 至今) ...")
    
    selected_texts = []
    total_found_channels = 0
    
    for link, item in all_data.items():
        channel_name = item.get("channel_name", link)
        messages = item.get("messages", [])
        
        channel_msgs_count = 0
        for msg in messages:
            try:
                # 解析ISO格式时间
                msg_date = datetime.fromisoformat(msg["date"])
                if msg_date >= time_limit:
                    selected_texts.append(f"[{channel_name}] {msg_date.astimezone().strftime('%m-%d %H:%M')}: {msg['text']}")
                    channel_msgs_count += 1
            except Exception:
                pass
        
        if channel_msgs_count > 0:
            total_found_channels += 1
            
    print(f"  -> 共在 {total_found_channels} 个活跃频道中提取到 {len(selected_texts)} 条文本消息。")
    
    if not selected_texts:
        print("❌ 提取到的消息数为 0，无法进行总结测试。")
        return
        
    combined_text = "\n".join(selected_texts)
    print(f"合并后的文本长度: {len(combined_text)} 字符。")
    
    # 格式化 Prompt 中的 days 变量
    formatted_prompt = TEST_PROMPT.format(days=TEST_DAYS)
    
    try:
        provider = await get_ai_provider(MODEL_NAME)
        print(f"正在调用 AI ({MODEL_NAME}) 运行总结...")
        
        summary_result = await provider.process_message(combined_text, prompt=formatted_prompt, model=MODEL_NAME)
        
        if summary_result:
            # 写入本地文件
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                f.write(f"# Prompt 调试测试输出报告\n")
                f.write(f"- 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"- 使用模型: `{MODEL_NAME}`\n")
                f.write(f"- 消息范围: 最近 `{TEST_DAYS}` 天 (包含来自 `{total_found_channels}` 个频道的 `{len(selected_texts)}` 条消息)\n\n")
                f.write(f"## 调试总结内容：\n\n")
                f.write(summary_result)
                
            print("\n" + "="*40)
            print(f"测试总结生成完毕！已写入文件: {OUTPUT_FILE}")
            print("您可以双击打开该文件查看渲染排版效果，并根据效果微调 test_prompt.py 中的 Prompt。")
            print("="*40)
        else:
            print("❌ AI 总结返回了空内容。")
            
    except Exception as e:
        print(f"❌ 调用 AI 失败: {repr(e)}")

if __name__ == '__main__':
    asyncio.run(main())
