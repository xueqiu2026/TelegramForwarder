# P17 - 关闭 Poller 轮询器修复消息重复转发

**状态**: 待执行
**日期**: 2026-06-14
**关联**: P13（创建 Poller）、P15（Watchdog 修复）

---

## 1. 问题

P14 翻译功能上线后发现**每条消息被转发两次**。

### 根因

系统存在两条并行的消息处理路径，且无去重：

| 路径 | 机制 | 来源 |
|:---|:---|:---|
| Event Handler | Telethon updates 推送（被动接收） | 原始设计 |
| Poller 轮询器 | 每 5s 并发 `get_messages(limit=1)` 主动检查（P13 新增） | main.py:381 |

两条路径同时检测到同一条新消息 → 各触发一次 `handle_user_message` → 转发两次。

### 日志证据

```
20:40:38 - 轮询器检测到频道 AU Trading Journal 有新消息: 5879 -> 5880  ← Poller
20:40:48 - 添加发送者信息: AU Trading Journal                          ← Poller 转发完成
20:40:49 - 添加发送者信息: AU Trading Journal                          ← Event Handler 转发完成
```

---

## 2. 方案

**关闭 Poller 轮询器**（注释 main.py:381 一行代码）。

### 为什么不选"加去重"

| 方案 | 优点 | 缺点 |
|:---|:---|:---|
| 关闭 Poller | 彻底消除重复 + 消除 FloodWait | 失去兜底补漏能力 |
| 加去重 | 保留兜底 | Poller 仍消耗 API 配额，FloodWait 风险仍在 |

### 为什么关闭 Poller 是安全的

1. **P15 已修复根因**：Watchdog 不再误杀进程，Event Handler 连接稳定
2. **混合 Watchdog 兜底**：`_patched_dispatch`（被动心跳）+ `get_me()`（主动探测）双重检测
3. **实测数据**（P15 上线后 6 小时）：
   - Event Handler 收到 96 条消息
   - Poller 触发 49 条（全部是重复的，无独占补漏）
   - FloodWait 0 次（但 Poller 仍在消耗 API 配额）
4. **Poller 无其他模块依赖**：仅在 main.py 中引用

---

## 3. 改动

### [MODIFY] main.py

```diff
         # 启动源频道定时轮询器以防推送不达
-        try:
-            await start_message_poller(user_client, bot_client, source_ids)
-        except Exception as pe:
-            logger.error(f"启动定时轮询兜底器失败: {pe}")
+        # P17: 关闭 Poller 轮询器，P15 已修复连接稳定性，Poller 导致消息重复转发
+        # try:
+        #     await start_message_poller(user_client, bot_client, source_ids)
+        # except Exception as pe:
+        #     logger.error(f"启动定时轮询兜底器失败: {pe}")
```

仅注释，不删除代码，保留回滚能力。

---

## 4. 回滚

取消注释 main.py:381-383 即可恢复 Poller。

## 5. 验证

- 重启后观察 TG 群组，确认消息不再重复
- `journalctl -u telegram-forwarder -f | grep '轮询器'` 应无输出
- Event Handler 正常收到消息（`grep '收到新消息'`）
