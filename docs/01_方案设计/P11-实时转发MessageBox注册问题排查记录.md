# P11 - 实时转发 MessageBox 注册问题全链路排查记录

**日期**: 2026-06-13  
**状态**: 🟢 已解决（Watchdog 属性修复，v6 实体缓存预加载服务稳定运行中）

---

## 一、问题现象

用户反映：订阅的 57 个 Telegram 频道发消息后，「2026」汇总群收不到转发。

- 新建测试频道（测试24）：✅ 可以转发
- megagroup 类型群组（海豚群）：✅ 可以转发  
- 原迁移的 53 个老频道：❌ 基本全部不转发

---

## 二、排查时间线

### Step 1：确认 DB 和规则正常

**检查**：PostgreSQL 中所有频道 `forward_rules.enable_rule = true`，账号已加入。  
**结论**：DB 无问题，问题在 Telethon 接收层。

---

### Step 2：发现 MessageBox 未注册（根因一）

**机制**：Telethon 1.38.x 内部维护 `MessageBox.map`（内存字典），只有注册到此字典的频道才接收实时 updates。

```
get_dialogs() → apply_pts_info() → MessageBox.map[channel_id] = State(pts)
```

服务器是新 session，`get_dialogs()` 默认 100 条，57 个源频道只有约 8 个被注册。

**修复**：改为 `get_dialogs(limit=None)` 拉全量 → MessageBox: 8 → 34，但仍有 23 个不在对话列表。

---

### Step 3：手动注册 pts=1（错误）

```python
user_client._message_box.try_set_channel_state(cid, 1)
```

**问题**：pts=1 = 从第 1 条消息追历史，57 个频道同时发 `GetChannelDifference(pts=1)` → 更新循环堵塞，新消息无法处理。

**症状**：`Got difference` 归零，5 分钟无任何消息。

---

### Step 4：手动注册 pts=2147483647（错误）

```python
user_client._message_box.try_set_channel_state(cid, 2147483647)
```

**问题**：  
1. 被写入 SQLite `update_state` 持久化  
2. 下次 `GetChannelDifference(pts=2147483647)` → Telegram 返回 `PersistentTimestampInvalidError`  
3. Telethon 暂停这些频道的追踪 → 消息丢失

**发现并清理**：
```sql
-- 找到 48 条脏数据
SELECT count(*) FROM update_state WHERE pts = 2147483647 AND id != 0;
-- 清理
DELETE FROM update_state WHERE pts = 2147483647 AND id != 0;
```

---

### Step 5：获取真实 pts 写入 session

**方案**：对每个缺失频道调用 `GetFullChannelRequest` 获取真实 pts，写入 `update_state`。

**碰到的坑 - database is locked**：  
脚本启动 `TelegramClient.connect()` 同时用 raw sqlite3 写同一个 session 文件 → 死锁。  
**解决**：先 connect + 收集 pts → `client.disconnect()` → 再写 sqlite3。

**结果**：55/57 频道写入真实 pts，`update_state` 共 81 行。

---

### Step 6：session pts 未加载到内存 MessageBox（根因二）

**发现**：重启后 MessageBox 仍然只有 37 个，session 中有 81 条 pts 记录。

**根因**：Telethon 启动时从 session 加载 MessageBox，但只加载**有对应 entity cache 的频道**。  
`get_dialogs()` 未返回的频道在 session 有 pts，内存无 entity → 不会加载到 MessageBox。

**修复尝试**：
```python
entity = await user_client.get_entity(cid)
user_client._mb_entity_cache.extend([], [entity])
real_pts = session_pts_map.get(cid)
user_client._message_box.try_set_channel_state(cid, real_pts)
```

**结果**：MessageBox: 37 → 81。但下一个问题随即出现。

---

### Step 7：81 个并发 GetChannelDifference → 连接僵死（根因三）

**现象**：注册 81 个频道后，Telegram 在 5 分钟内停止推送任何消息。进程存活，TCP 连接在，但完全静默。

**根因**：81 个频道同时注册 → Telethon 立即发 81 个并发 `GetChannelDifference` → **Telegram 对该连接软封禁**。

**这是当前核心未解决问题。**

---

### Step 8：当前方案（已部署）

**注册策略**（保守）：
```python
# 只用 get_dialogs() 自动注册（约 37 个），不批量调用 try_set_channel_state
dialogs = await user_client.get_dialogs(limit=None)
# 对其余频道只填充 entity cache，等 Telegram 第一次推送时自动注册
user_client._mb_entity_cache.extend([], [entity])
```

**Watchdog**（新增）：
```python
# 每 60s 检查，超 5 分钟无任何 update → 强制断开 → systemd Restart=always 自动重启
async def _watchdog_loop():
    while True:
        await asyncio.sleep(60)
        elapsed = time.time() - _last_update[0]
        if elapsed > 300:
            await user_client.disconnect()

# 拦截 _handle_update 更新心跳时间戳
_orig_dispatch = user_client._handle_update
async def _patched_dispatch(update):
    _last_update[0] = time.time()
    return await _orig_dispatch(update)
user_client._handle_update = _patched_dispatch
```

---

## 三、修改版本对比

| 版本 | 注册策略 | pts 值 | 结果 |
|------|---------|--------|------|
| 原始 | 无（依赖本地 session） | 真实 | 本地正常 |
| v2 | try_set_channel_state | 1 | 更新循环堵塞 |
| v3 | try_set_channel_state | 2147483647 | 脏数据破坏连接 |
| v4 | get_dialogs(limit=None) only | 真实 | 37 个注册 |
| v5 | get_dialogs + try_set_channel_state | 真实 session pts | 81 个注册，被封禁 |
| v6 | get_dialogs only + entity cache + watchdog | 真实 | _handle_update 拼写错误导致 Crash Loop |
| **v7（当前）** | get_dialogs only + entity cache + watchdog | 真实 | 🟢 属性修复为 _dispatch_update，服务正常稳定运行，老频道预填充 cache 激活中 |

---

## 四、今日新增频道

| 频道名 | 用户名 | 类型 | 结果 |
|--------|--------|------|------|
| Top 7 Crypto | top7ico | broadcast | ✅ 已加入，chat id=57 |
| muur_posts | muur_posts | = Stacy in Dataland | 已存在 id=17 |

---

## 五、推荐的根本解决方案（未执行）

> **将本地正常运行的 `sessions/user.session` 覆盖服务器 session 文件。**

本地 session 长期积累了所有频道的真实 pts，无需任何注册操作，连接后 Telegram 直接推送。

```bash
# 步骤
systemctl stop telegram-forwarder
scp local:sessions/user.session root@server:/opt/TelegramForwarder/sessions/user.session
systemctl start telegram-forwarder
```

**风险**：IP/设备指纹变更可能触发 Telegram 二次验证。

---

## 六、经验教训

1. **新 session ≠ 旧 session**：迁移项目必须同时迁移 session 文件，否则所有频道 pts 从零开始
2. **不要批量 GetChannelDifference**：同时注册 50+ 频道会触发 Telegram 连接封禁
3. **pts 必须用真实值**：1 和 INT_MAX 都会导致问题
4. **watchdog 是必要的**：Telegram 长连接会在某些条件下进入僵死状态，必须有自动重连机制
5. **属性名劫持必须精确**：Telethon 的消息分发方法为 `_dispatch_update`，不可错写为 `_handle_update`，否则会导致反射抛出 `AttributeError` 崩溃。
