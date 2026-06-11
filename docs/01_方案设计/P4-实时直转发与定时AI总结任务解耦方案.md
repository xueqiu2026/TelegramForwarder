# P4 实时直转发与定时AI总结任务解耦方案

## 1. 背景与痛点

在当前系统设计中，每一个源频道（Source Chat）到目标群组（Target Chat）只能配置一条唯一的转发规则（`ForwardRule`）。该规则同时承载了**实时直转发**和**定时AI总结**两项逻辑：
1. **实时直转发**：在 `message_listener.py` 监听到源频道新消息事件时立即进行过滤、AI转换或原样转发。
2. **定时AI总结**：在 `summary_scheduler.py` 中每到规定时间（如 `06:00, 16:00, 20:00`）定时聚合过去一段时间内的历史消息做 AI 汇总发送。

**痛点与设计漏洞**：
* 两个任务共享同一个规则的启用状态（`enable_rule`）。如果用户希望“**只启用定时AI总结，而不进行实时消息直发**”，目前无法单独控制。一旦开启规则，实时到来的消息就会直接被过滤器链发送出去。
* 反之，即时转发的参数（如各种过滤、替换逻辑）也跟总结揉在一起，无法进行完全独立的配置解耦。

为了彻底消除这一耦合漏洞，我们提出在 `ForwardRule` 中引入专用的实时转发控制开关，使**即时直转发**与**定时AI总结**作为独立的任务并发运行。

---

## 2. 详细解耦设计

### 2.1 数据库结构升级（无缝热更）
*   **改动文件**：[models.py](file:///D:/Axiangmu/TelegramForwarder/models/models.py)
*   **属性新增**：在 `ForwardRule` 类中新增 `enable_forward` 列：
    ```python
    enable_forward = Column(Boolean, default=True)  # 是否启用实时直转发，默认开启
    ```
*   **数据库平滑迁移**：在 `forward_rules_new_columns` 热更迁移映射中增加迁移 SQL：
    ```python
    'enable_forward': 'ALTER TABLE forward_rules ADD COLUMN enable_forward BOOLEAN DEFAULT TRUE'
    ```
    这能保证在服务重启时，旧的 SQLite/PostgreSQL 数据库能自动追加此列，无需手动干预，且存量规则默认开启即时转发，保障业务无感过渡。

### 2.2 后端消息监听器解耦
*   **改动文件**：[message_listener.py](file:///D:/Axiangmu/TelegramForwarder/message_listener.py)
*   **处理逻辑**：在 `handle_user_message` 遍历转发规则的地方加入针对 `enable_forward` 开关的拦截（[message_listener.py#L148-L158](file:///D:/Axiangmu/TelegramForwarder/message_listener.py#L148-L158)）：
    ```python
    # 处理每条转发规则
    for rule in rules:
        target_chat = rule.target_chat
        if not rule.enable_rule:
            logger.info(f'规则 {rule.id} 未启用')
            continue
            
        # 拦截点：如果关闭了实时转发开关，则跳过即时直发逻辑，但不影响其定时总结调度
        if not getattr(rule, 'enable_forward', True):
            logger.info(f'规则 {rule.id} 已关闭即时转发，跳过发送')
            continue
            
        logger.info(f'处理即时转发规则 ID: {rule.id} (从 {source_chat.name} 转发到: {target_chat.name})')
        if rule.use_bot:
            await process_forward_rule(bot_client, event, str(chat_id), rule)
        else:
            await user_handler.process_forward_rule(user_client, event, str(chat_id), rule)
    ```

### 2.3 后端 API 控制层适配
*   **改动文件**：[console.py](file:///D:/Axiangmu/TelegramForwarder/rss/app/routes/console.py)
*   **API 调整**：
    1. 在获取规则明细及规则列表的响应序列化字典中，增加 `"enable_forward": rule.enable_forward`。
    2. 在更新规则属性（修改、创建）的入参字段过滤白名单中，加入 `enable_forward`。
    3. 在一键同步接口（`sync-to-all`）的属性复制中包含 `enable_forward`。

### 2.4 前端 React 控制台 UI 适配
*   **改动文件**：
    * `frontend/src/types.ts` (定义 `ForwardRule` 的 TypeScript 接口增加 `enable_forward: boolean`)
    * `frontend/src/pages/RulesPage.tsx` (在“概览”或“消息处理”的 Tab 面板中增加一个“启用即时直发”的 Switch 开关，控制并提交 `enable_forward` 字段)

---

## 3. 任务解耦后的执行场景对照表

| 场景需求 | enable_rule 开关 | enable_forward (即时直发) | is_summary (定时总结) | 系统运行表现 |
| :--- | :---: | :---: | :---: | :--- |
| **既要直发又要总结** | **True** | **True** | **True** | 实时收到新消息立刻直接转发；每天在设定时间拉取消息推送 AI 总结报告。 |
| **仅做定时总结** | **True** | **False** | **True** | 实时消息不进行任何直转发送；每天到规定时间拉取增量消息推送 AI 总结报告。 |
| **仅做即时直转发** | **True** | **True** | **False** | 实时收到新消息立刻转发；后台不创建任何定时总结任务。 |
| **完全关闭规则** | **False** | 任意值 | 任意值 | 该通道完全处于挂起静默状态，即时直发与定时总结均不执行。 |

---

## 4. 实施与验证步骤

1. **步骤一：执行数据库及监听逻辑更新**
   * 修改 `models.py` 与 `message_listener.py`，增加 `enable_forward` 热更新并编写实时过滤旁路。
   * 重启后端任务，观察日志输出以确认新数据库字段已成功添加。
2. **步骤二：后端 API 与前端 UI 联调**
   * 更新后端 `console.py`，并将前端对应页面和 TS 类型补齐。
   * 在控制台前端测试切换“启用即时直发”开关，观察是否能正常调用 API 且数据库成功落库。
3. **步骤三：逻辑验证**
   * 配置一测试规则：`enable_rule=True`, `enable_forward=False`, `is_summary=True`。
   * 触发新消息事件，核对后端日志中是否输出 `规则 X 已关闭即时转发，跳过发送`。
   * 触发定时任务，验证在非直发状态下定时总结能否正常发送。
