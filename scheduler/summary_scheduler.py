import asyncio
from datetime import datetime, timedelta
import pytz
from models.models import get_session, ForwardRule, SummaryHistory, PushConfig, ForwardedMessage, Chat
import logging
import os
import apprise
from dotenv import load_dotenv
from telethon import TelegramClient, errors
from ai import get_ai_provider
import traceback
from utils.constants import DEFAULT_TIMEZONE, DEFAULT_AI_MODEL, DEFAULT_SUMMARY_PROMPT

logger = logging.getLogger(__name__)

# Telegram's maximum message size limit (4096 characters)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
# Maximum length for each summary message part, leaving headroom for formatting
MAX_MESSAGE_PART_LENGTH = TELEGRAM_MAX_MESSAGE_LENGTH - 300
# Maximum number of attempts for sending messages
MAX_SEND_ATTEMPTS = 2

class SummaryScheduler:
    def __init__(self, user_client: TelegramClient, bot_client: TelegramClient):
        self.tasks = {}  # 存储所有定时任务 {target_chat_id: task}
        self.timezone = pytz.timezone(DEFAULT_TIMEZONE)
        self.user_client = user_client
        self.bot_client = bot_client
        # 添加信号量来限制并发请求
        self.request_semaphore = asyncio.Semaphore(2)  # 最多同时执行2个请求
        # 从环境加载超长截断限制
        self.max_input_chars = int(os.getenv('MAX_SUMMARY_INPUT_CHARS', 80000))

    async def schedule_rule(self, rule):
        """[审计修正] 为单个规则所属的目标群组重新调度聚合定时总结任务"""
        session = get_session()
        try:
            target_id = rule.target_chat.telegram_chat_id
            
            # 取消该目标群组现有的聚合任务
            if target_id in self.tasks:
                self.tasks[target_id].cancel()
                logger.info(f"已取消目标群组 {target_id} 的旧聚合任务")
                del self.tasks[target_id]

            # 查询该目标群组下所有启用了总结的规则
            rules = session.query(ForwardRule).filter_by(
                is_summary=True
            ).join(ForwardRule.target_chat).filter(
                Chat.telegram_chat_id == target_id
            ).all()

            if rules:
                # 合并该目标群组下所有子规则的时间时刻点
                all_time_points = set()
                for r in rules:
                    if r.summary_time:
                        all_time_points.update([t.strip() for t in r.summary_time.split(',') if t.strip()])
                merged_summary_time = ",".join(sorted(list(all_time_points))) if all_time_points else '07:00'

                ref_rule = rules[0]
                config = {
                    'target_chat_id': target_id,
                    'target_chat_name': ref_rule.target_chat.name,
                    'summary_time': merged_summary_time,
                    'summary_prompt': ref_rule.summary_prompt,
                    'ai_model': ref_rule.ai_model,
                }
                logger.info(f"为目标群组 {config['target_chat_name']} ({target_id}) 创建/更新定时总结任务，合并时刻: {config['summary_time']}")
                task = asyncio.create_task(
                    self._run_aggregated_task(target_id, config)
                )
                self.tasks[target_id] = task
            else:
                logger.info(f"目标群组 {target_id} 下没有开启总结的规则，无需创建调度任务")
        except Exception as e:
            logger.error(f"更新规则 {rule.id} 的聚合总结任务调度时出错: {str(e)}")
        finally:
            session.close()

    async def _run_aggregated_task(self, target_chat_id, config):
        """按目标群组运行定时聚合总结"""
        while True:
            try:
                now = datetime.now(self.timezone)
                target_time = self._get_next_run_time(now, config['summary_time'])
                wait_seconds = (target_time - now).total_seconds()
                await asyncio.sleep(wait_seconds)
                await self._execute_aggregated_summary(target_chat_id, config)
            except asyncio.CancelledError:
                logger.info(f"目标群组 {target_chat_id} 的聚合总结任务已取消")
                break
            except Exception as e:
                logger.error(f"目标群组 {target_chat_id} 的聚合总结出错: {str(e)}")
                await asyncio.sleep(60)

    async def _execute_summary(self, rule_id, is_now=False):
        """[审计修正] 桥接兼容旧单条规则执行接口（主要是按钮回调 `ai_callback.py` 发起）"""
        session = get_session()
        try:
            rule = session.query(ForwardRule).get(rule_id)
            if not rule:
                logger.error(f"立即总结失败：未找到规则 {rule_id}")
                return
            
            target_id = rule.target_chat.telegram_chat_id
            config = {
                'target_chat_id': target_id,
                'target_chat_name': rule.target_chat.name,
                'summary_time': rule.summary_time,
                'summary_prompt': rule.summary_prompt,
                'ai_model': rule.ai_model,
            }
            await self._execute_aggregated_summary(target_id, config, is_now=is_now)
        except Exception as e:
            logger.error(f"运行桥接立即总结时发生错误: {e}")
        finally:
            session.close()

    async def _execute_aggregated_summary(self, target_chat_id, config, is_now=False):
        """执行按目标群组的聚合总结"""
        session = get_session()
        try:
            target_chat_id_int = int(target_chat_id)
            if not str(target_chat_id_int).startswith('-'):
                target_chat_id_int = int(f'-100{target_chat_id}')

            target_chat_id_str = str(target_chat_id)
            now = datetime.now(self.timezone)
            start_time = self._get_last_run_time(now, config['summary_time'])

            messages = session.query(ForwardedMessage).filter(
                ForwardedMessage.target_chat_id == target_chat_id_str,
                ForwardedMessage.is_summarized == False,
                ForwardedMessage.message_text.isnot(None),
                ForwardedMessage.message_text != '',
                ForwardedMessage.message_date >= start_time.astimezone(pytz.utc).replace(tzinfo=None)
            ).order_by(ForwardedMessage.message_date.asc()).all()

            if not messages:
                logger.info(f"目标群组 {target_chat_id} 没有需要总结的消息")
                return

            # 2. 格式化消息文本
            unique_sources = set()
            formatted_lines = []
            for msg in messages:
                unique_sources.add(msg.source_chat_name or '未知频道')
                msg_time = msg.message_date.strftime('%H:%M') if msg.message_date else ''
                source_name = msg.source_chat_name or '未知频道'
                formatted_lines.append(f"【{source_name}】 {msg_time} - {msg.message_text}")

            combined_text = '\n'.join(formatted_lines)

            # 3. 超长截断
            truncated = False
            if len(combined_text) > self.max_input_chars:
                truncated = True
                combined_text = combined_text[-self.max_input_chars:]
                logger.warning(f"目标群组 {target_chat_id} 消息超长，已截断至 {self.max_input_chars} 字符")

            # 4. 调用 AI
            ai_model = config.get('ai_model') or DEFAULT_AI_MODEL
            summary_prompt = config.get('summary_prompt') or DEFAULT_SUMMARY_PROMPT

            async with self.request_semaphore:
                provider = await get_ai_provider(ai_model)
                summary = await provider.process_message(
                    combined_text,
                    prompt=summary_prompt,
                    model=ai_model
                )

            if not summary:
                logger.warning(f"目标群组 {target_chat_id} AI 总结返回为空")
                return

            # 5. 写入 SummaryHistory
            try:
                end_time = now
                history_entry = SummaryHistory(
                    rule_id=None,
                    target_chat_id=target_chat_id,
                    source_channel_name="聚合总结",
                    source_count=len(unique_sources),
                    summary_text=summary,
                    message_count=len(messages),
                    time_range_start=start_time,
                    time_range_end=end_time,
                    ai_model=ai_model,
                    prompt_used=summary_prompt
                )
                session.add(history_entry)
                session.commit()
            except Exception as e:
                logger.error(f"总结归档失败: {e}")

            # 6. 发送到目标群组
            duration_hours = round((now - start_time).total_seconds() / 3600)
            header = f"📋 【{len(unique_sources)}个频道 {duration_hours}小时 聚合总结】\n"
            header += f"🕐 {start_time.strftime('%m-%d %H:%M')} - {now.strftime('%m-%d %H:%M')}\n"
            header += f"📊 消息: {len(messages)} 条 | 频道: {len(unique_sources)} 个\n"
            if truncated:
                header += f"⚠️ 部分早期消息因超长被截断\n"
            header += "\n"

            summary_parts = self._split_message(summary, MAX_MESSAGE_PART_LENGTH)
            summary_message = None

            for i, part in enumerate(summary_parts):
                if i == 0:
                    message_to_send = header + part
                else:
                    message_to_send = f"📋 聚合总结 (续 {i+1}/{len(summary_parts)})\n\n" + part

                current_message = None
                use_markdown = True
                attempt = 0
                while attempt < MAX_SEND_ATTEMPTS:
                    try:
                        if use_markdown:
                            current_message = await self.bot_client.send_message(
                                target_chat_id_int, message_to_send, parse_mode='markdown'
                            )
                        else:
                            current_message = await self.bot_client.send_message(
                                target_chat_id_int, message_to_send
                            )
                        break
                    except errors.MarkupInvalidError:
                        if use_markdown:
                            use_markdown = False
                            continue
                        raise
                    except errors.FloodWaitError as fwe:
                        if attempt < MAX_SEND_ATTEMPTS - 1:
                            await asyncio.sleep(fwe.seconds)
                            attempt += 1
                        else:
                            raise
                    except Exception:
                        if attempt >= MAX_SEND_ATTEMPTS - 1:
                            raise
                        await asyncio.sleep(1)
                        attempt += 1

                if i == 0:
                    summary_message = current_message

            # 7. [审计修正] 置顶逻辑 (群组内任意关联子规则开启了 is_top_summary 则置顶聚合消息)
            has_top_summary = False
            rules = session.query(ForwardRule).filter_by(
                is_summary=True
            ).join(ForwardRule.target_chat).filter(
                Chat.telegram_chat_id == target_chat_id_str
            ).all()
            for r in rules:
                if getattr(r, 'is_top_summary', False):
                    has_top_summary = True
                    break

            if has_top_summary and summary_message:
                try:
                    await self.bot_client.pin_message(target_chat_id_int, summary_message)
                    logger.info(f"目标群组 {target_chat_id} 聚合总结消息置顶成功")
                except Exception as pin_error:
                    logger.warning(f"置顶总结消息失败: {str(pin_error)}")

            # 8. [审计修正] 调用国内通道进行聚合推送
            try:
                await self._send_domestic_push_aggregated(target_chat_id_str, summary)
            except Exception as push_err:
                logger.error(f"发送国内推送时出错: {str(push_err)}")

            # 9. 标记已总结
            message_ids = [msg.id for msg in messages]
            session.query(ForwardedMessage).filter(
                ForwardedMessage.id.in_(message_ids)
            ).update({ForwardedMessage.is_summarized: True}, synchronize_session='fetch')
            session.commit()

            # 10. [审计修正] 自动清理已总结且超期保留的历史落库消息
            try:
                self._cleanup_expired_messages()
            except Exception as clean_err:
                logger.error(f"自动清理过期落库消息出错: {clean_err}")

            logger.info(f"目标群组 {target_chat_id} 聚合总结完成: {len(messages)} 条消息, {len(unique_sources)} 个频道")

        except Exception as e:
            logger.error(f"聚合总结执行失败: {str(e)}")
            logger.error(f"错误详情: {traceback.format_exc()}")
        finally:
            session.close()

    async def start(self):
        """启动调度器 — 按目标群组聚合调度"""
        logger.info("开始启动聚合总结调度器...")
        session = get_session()
        try:
            rules = session.query(ForwardRule).filter_by(is_summary=True).all()
            logger.info(f"找到 {len(rules)} 个启用了总结功能的规则")

            # 按 target_chat_id 分组，并合并同一个目标群组的时间配置
            target_groups = {}
            for rule in rules:
                target_id = rule.target_chat.telegram_chat_id
                if target_id not in target_groups:
                    target_groups[target_id] = {
                        'target_chat_id': target_id,
                        'target_chat_name': rule.target_chat.name,
                        'summary_time_set': set(),
                        'summary_prompt': rule.summary_prompt,
                        'ai_model': rule.ai_model,
                    }
                if rule.summary_time:
                    target_groups[target_id]['summary_time_set'].update(
                        [t.strip() for t in rule.summary_time.split(',') if t.strip()]
                    )

            for target_id, config in target_groups.items():
                time_list = sorted(list(config['summary_time_set']))
                config['summary_time'] = ",".join(time_list) if time_list else '07:00'
                del config['summary_time_set']

                logger.info(f"为目标群组 {config['target_chat_name']} ({target_id}) 创建聚合总结任务，合并时刻: {config['summary_time']}")
                task = asyncio.create_task(
                    self._run_aggregated_task(target_id, config)
                )
                self.tasks[target_id] = task

            if not target_groups:
                logger.info("没有找到需要聚合总结的目标群组")

            logger.info("聚合总结调度器启动完成")
        except Exception as e:
            logger.error(f"启动调度器时出错: {str(e)}")
            logger.error(f"错误详情: {traceback.format_exc()}")
        finally:
            session.close()

    def stop(self):
        """停止所有任务"""
        for task in self.tasks.values():
            task.cancel()
        self.tasks.clear()

    async def execute_all_summaries(self):
        """立即执行所有目标群组的聚合总结"""
        session = get_session()
        try:
            rules = session.query(ForwardRule).filter_by(is_summary=True).all()
            target_groups = {}
            for rule in rules:
                target_id = rule.target_chat.telegram_chat_id
                if target_id not in target_groups:
                    target_groups[target_id] = {
                        'target_chat_id': target_id,
                        'target_chat_name': rule.target_chat.name,
                        'summary_time_set': set(),
                        'summary_prompt': rule.summary_prompt,
                        'ai_model': rule.ai_model,
                    }
                if rule.summary_time:
                    target_groups[target_id]['summary_time_set'].update(
                        [t.strip() for t in rule.summary_time.split(',') if t.strip()]
                    )

            for target_id, config in target_groups.items():
                time_list = sorted(list(config['summary_time_set']))
                config['summary_time'] = ",".join(time_list) if time_list else '07:00'
                del config['summary_time_set']
                await self._execute_aggregated_summary(target_id, config, is_now=True)
                await asyncio.sleep(1)
        finally:
            session.close()

    def _split_message(self, text: str, max_length: int = MAX_MESSAGE_PART_LENGTH):
        if not text:
            return []

        parts = []
        while len(text) > 0:
            text = text.lstrip()
            if not text:
                break

            if len(text) <= max_length:
                parts.append(text)
                break

            split_pos = -1
            for sep in ('\n\n', '\n', ' '):
                pos = text.rfind(sep, 0, max_length)
                if pos > 0:
                    split_pos = pos
                    break
            if split_pos == -1:
                split_pos = max_length

            parts.append(text[:split_pos])
            text = text[split_pos:]

        return parts

    def _get_next_run_time(self, now, target_time_str):
        time_points = [t.strip() for t in target_time_str.split(',') if t.strip()]
        if not time_points:
            time_points = ['07:00']
            
        candidates = []
        for tp in time_points:
            try:
                hour, minute = map(int, tp.split(':'))
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= now:
                    candidate += timedelta(days=1)
                candidates.append(candidate)
            except ValueError:
                logger.error(f"无效的时间配置格式: {tp}")
                
        return min(candidates) if candidates else now + timedelta(days=1)

    def _get_last_run_time(self, now, target_time_str):
        time_points = [t.strip() for t in target_time_str.split(',') if t.strip()]
        if not time_points:
            time_points = ['07:00']
            
        candidates = []
        for tp in time_points:
            try:
                hour, minute = map(int, tp.split(':'))
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate >= now:
                    candidate -= timedelta(days=1)
                candidates.append(candidate)
            except ValueError:
                pass
                
        return max(candidates) if candidates else now - timedelta(days=1)

    def _format_for_wecom(self, text):
        if not text:
            return ""
        encoded = text.encode('utf-8')
        if len(encoded) > 4090:
            truncated = encoded[:4000].decode('utf-8', errors='ignore')
            return truncated + "\n\n...(内容超长已截断)..."
        return text

    async def _send_domestic_push_aggregated(self, target_chat_id, body):
        """[审计修正] 通过 Apprise 将聚合总结发送至该目标群组下所有配置了且启用了的推送通道"""
        session = get_session()
        try:
            target_chat = session.query(Chat).filter_by(telegram_chat_id=str(target_chat_id)).first()
            if not target_chat:
                logger.error(f"国内推送失败：未找到目标群组 {target_chat_id} 在 chats 表的记录")
                return

            configs = session.query(PushConfig).join(
                ForwardRule, PushConfig.rule_id == ForwardRule.id
            ).filter(
                ForwardRule.target_chat_id == target_chat.id,
                ForwardRule.is_summary == True,
                PushConfig.enable_push_channel == True
            ).all()

            if not configs:
                return

            seen_channels = set()
            for config in configs:
                service_url = config.push_channel
                if service_url in seen_channels:
                    continue
                seen_channels.add(service_url)

                try:
                    apobj = apprise.Apprise()
                    if not apobj.add(service_url):
                        logger.error(f"总结推送: 无法添加 Apprise 通道: {service_url}")
                        continue

                    push_body = body
                    if "wecom" in service_url or "wxwork" in service_url:
                        push_body = self._format_for_wecom(body)

                    logger.info(f"发送国内聚合总结推送，通道: {service_url}")
                    await asyncio.to_thread(apobj.notify, body=push_body)
                except Exception as push_err:
                    logger.error(f"国内推送通道 {service_url} 发送失败: {str(push_err)}")
        except Exception as err:
            logger.error(f"发送国内聚合总结推送失败: {str(err)}")
        finally:
            session.close()

    def _cleanup_expired_messages(self):
        """[审计修正] 定期物理清理已总结且超期保留的历史落库消息"""
        retention_days = int(os.getenv('MESSAGE_RETENTION_DAYS', 7))
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
        cutoff_date_unsummarized = datetime.utcnow() - timedelta(days=30)  # 保底未总结老消息 30 天清理
        session = get_session()
        try:
            from sqlalchemy import or_, and_
            deleted = session.query(ForwardedMessage).filter(
                or_(
                    and_(ForwardedMessage.is_summarized == True, ForwardedMessage.forwarded_at < cutoff_date),
                    and_(ForwardedMessage.is_summarized == False, ForwardedMessage.forwarded_at < cutoff_date_unsummarized)
                )
            ).delete()
            session.commit()
            if deleted > 0:
                logger.info(f"已自动物理清理 {deleted} 条已过期历史消息数据(含超期未总结老消息)")
        except Exception as e:
            logger.error(f"物理清理历史消息失败: {e}")
            session.rollback()
        finally:
            session.close()
