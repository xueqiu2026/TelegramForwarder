# P7 - 消息回填与 AI 沙盒数据源切换方案

> 创建时间：2026-06-11
> 状态：待执行

## 一、背景

AI 沙盒的「历史消息流聚合器」当前读取静态 JSON 文件（`scratch/messages_dump.json`），该文件需手动导入且无法自动更新，导致：

1. 24H 聚合经常为空（数据过期）
2. 每次测试都需要手动重新导出
3. 无法反映实时频道内容

## 二、方案概述

```
┌─────────────────────────────────────────────────┐
│  Step 1: 回填 7 天历史消息到 PostgreSQL          │
│  backfill_messages.py --days 7                   │
│  53 频道 → ForwardedMessage 表                   │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│  Step 2: 沙盒后端改为读数据库                     │
│  /sandbox/aggregate-messages → 查询 DB           │
│  /sandbox/history-samples   → 查询 DB            │
│  删除对 messages_dump.json 的依赖                 │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│  效果：沙盒实时读取最新消息，时间范围自由选择      │
└─────────────────────────────────────────────────┘
```

## 三、Step 1 — 回填脚本

### 文件：`backfill_messages.py`（已创建）

| 项目 | 说明 |
|---|---|
| 数据源 | Telegram API（`client.iter_messages`）|
| 写入目标 | `ForwardedMessage` 表 |
| 默认范围 | 7 天（`--days 7`）|
| 去重策略 | 按 `telegram_message_id + source_chat_id` 跳过已存在 |
| 限流策略 | 频道间 2s、批次间 0.5s、FloodWait 自动等待 |
| 断点续传 | 已存在的消息自动跳过，可重复运行 |
| Session 冲突 | ⚠️ 需先停止 main.py，避免两个客户端同时使用 `sessions/user` |

### 执行步骤

```bash
# 1. 停止主服务
# 2. 执行回填
python backfill_messages.py --days 7
# 3. 重启主服务
python main.py
```

### 预计耗时

- 53 频道 × 7 天 ≈ 3-5 分钟（取决于频道活跃度和限流）
- 转发服务暂停时间 ≈ 5 分钟

---

## 四、Step 2 — 沙盒后端切换数据源

### 修改文件：`rss/app/routes/console.py`

#### 4.1 `/console/sandbox/history-samples` 端点

**现状**：读取 `scratch/messages_dump.json`，返回频道列表和消息数量
**改为**：查询 `ForwardedMessage` 表，按 `source_chat_id` 分组统计

```python
# 改造前
with open(json_path) as f:
    data = json.load(f)
    
# 改造后
from sqlalchemy import func, distinct
results = db.query(
    ForwardedMessage.source_chat_id,
    ForwardedMessage.source_chat_name,
    func.count(ForwardedMessage.id)
).group_by(
    ForwardedMessage.source_chat_id,
    ForwardedMessage.source_chat_name
).all()
```

#### 4.2 `/console/sandbox/aggregate-messages` 端点

**现状**：从 JSON 文件按时间过滤消息，拼接成文本
**改为**：从数据库按 `message_date` + `source_chat_id` 过滤

```python
# 改造前
for link, info in data.items():
    for msg in info['messages']:
        if msg_time >= time_limit: ...

# 改造后
time_limit = datetime.now(timezone.utc) - timedelta(days=days)
messages = db.query(ForwardedMessage).filter(
    ForwardedMessage.source_chat_id.in_(channels),
    ForwardedMessage.message_date >= time_limit,
    ForwardedMessage.message_text.isnot(None)
).order_by(ForwardedMessage.message_date.desc()).all()
```

#### 4.3 可删除的端点

- `/console/sandbox/upload-json` — 不再需要手动导入 JSON
- 保留作为备用也可以，不影响主流程

---

## 五、前端改动

无需修改。前端已有时间范围选择器（24H/3D/7D），与后端 `days` 参数对应。

---

## 六、风险评估

| 风险 | 影响 | 缓解 |
|---|---|---|
| 回填期间 session 冲突 | 主服务和回填脚本抢 session 导致掉线 | 先停主服务再回填 |
| 频控（FloodWaitError）| 拉取中断 | 脚本已内置自动等待和重试（3次）|
| 数据量大导致沙盒聚合慢 | 7 天 × 53 频道可能上万条 | 数据库查询有索引，毫秒级 |
| 回填后主服务产生重复 | 正常转发的消息与回填的可能重复 | 后续总结时按 message_id 去重 |

## 七、回滚方案

```bash
# 回滚数据库（删除回填的数据）
# 可按 forwarded_at 时间范围精确删除
DELETE FROM forwarded_messages WHERE forwarded_at BETWEEN '回填开始时间' AND '回填结束时间';

# 回滚代码
git checkout HEAD -- rss/app/routes/console.py
```

## 八、验证步骤

1. 回填完成后查询：`SELECT COUNT(*) FROM forwarded_messages` → 应有数千条
2. 启动主服务 + 前端 → 沙盒页面频道列表显示实时消息数量
3. 选择 24H → 聚合 → 应显示最近 24 小时的真实消息
4. 选择 7D → 聚合 → 应显示 7 天的消息
5. 运行 A/B 测试 → 输出正常总结
