# P2-TelegramForwarder 前端控制台与数据调试大屏设计方案（终版）

> 版本：v2.1 | 更新日期：2026-06-08
> 状态：已纳入技术核查反馈，待审批执行

---

## 一、方案背景与目标

当前 TelegramForwarder 的所有配置完全依赖 Telegram 机器人交互。核心痛点：

1. **AI Prompt 调试体验差**：多行复杂提示词在聊天窗口内编辑极其不便
2. **复盘阅读不便**：AI 总结发到 TG 后无法搜索、归档和回顾
3. **VPN 依赖**：TG 需要 VPN，国内网络不稳定时收不到推送
4. **配置不直观**：50+ 频道的转发链路无法全景查看

**目标**：构建一体化 Web 前端控制台，与 TG Bot **同等权限**双端操控，同时实现 AI 总结归档复盘和国内平台推送。

---

## 二、已确认的核心决策

| 决策项 | 结论 |
|---|---|
| 数据库 | **PostgreSQL**（解决双进程并发写入锁问题，复用团队已有 PG 18 实例） |
| 前端定位 | 配置调试 + 复盘阅读，与 TG Bot 同等操作权限 |
| AI 定时总结 | 每天 4 次（06:00 / 12:00 / 18:00 / 00:00），`summary_time` 字段改为逗号分隔多时间点 |
| 多语言翻译 | 韩文/英文频道开启 `is_ai`，用 DeepSeek 实时翻译成中文转发 |
| 规则管理 | 50+ 频道统一配置，主规则 + 同步规则功能一改全生效 |
| 国内推送 | 全量消息 + AI 总结均推送，三平台留接口（企业微信优先），基于已有 Apprise 集成 |
| AI 总结归档 | 新建 `summary_history` 表永久保存，支持全文搜索 |
| 推特辅助 | 不做 AI 生成推文，仅在总结归档页提供"复制全文"按钮 |

---

## 三、前端设计标准（2026 现代设计规范）

### 3.1 设计原则

遵循 2026 年主流 Web Dashboard 设计标准，拒绝"AI 生成的廉价感"：

- **配色体系**：基于 HSL 色彩空间构建，主色调采用低饱和度深色系（如 `hsl(220, 15%, 12%)` 背景 + `hsl(210, 100%, 65%)` 强调色），避免刺眼的纯色
- **排版层次**：使用 Inter / Noto Sans SC 混排字体，正文 14px、标题 18-24px，行高 1.6，严格控制信息密度
- **空间节奏**：8px 基础网格系统，组件间距 16/24/32px 递进，大量留白呼吸感
- **卡片风格**：圆角 12-16px，微妙 border（`1px solid rgba(255,255,255,0.06)`），不使用浮夸阴影
- **交互反馈**：按钮/开关带 150ms ease 过渡动画，hover 态有明确视觉变化
- **数据可视化**：图表采用渐变填充 + 柔和配色，避免硬边色块

### 3.2 色板定义

```
背景层：   #0f1117 (深墨)  →  #1a1d27 (卡片)  →  #252830 (悬浮)
文字层：   #e4e4e7 (主文字)  →  #a1a1aa (次要)  →  #71717a (弱化)
强调色：   #3b82f6 (蓝-主操作)  →  #22c55e (绿-成功)  →  #f59e0b (橙-警告)  →  #ef4444 (红-危险)
渐变：     linear-gradient(135deg, #3b82f6, #8b5cf6) (品牌渐变)
```

### 3.3 技术选型

| 层级 | 技术 | 理由 |
|---|---|---|
| 前端框架 | React 19 + Vite 6 | SPA 交互流畅，天然支持 Monaco 编辑器和 WebSocket 日志流 |
| UI 组件 | Radix UI + 手写 CSS | 无障碍友好、无预设样式污染，完全自主控制视觉 |
| 图表 | Recharts | 轻量、React 原生、声明式 API |
| 代码编辑 | Monaco Editor（Prompt 编辑器专用） | VS Code 同源，语法高亮、多行编辑体验一流 |
| HTTP 通信 | Axios + SWR | 自动缓存、乐观更新、断线重连 |
| 实时日志 | WebSocket | 服务端推送日志流到前端 |

---

## 四、前端页面架构（4 大看板）

```mermaid
graph TD
    NAV["顶部导航栏"] --> P1["📡 RSS 信息流"]
    NAV --> P2["⚙️ 规则配置中心"]
    NAV --> P3["🤖 AI 调试沙盒"]
    NAV --> P4["📊 运维监控"]
    
    P2 --> P2A["转发规则列表"]
    P2 --> P2B["开关矩阵面板"]
    P2 --> P2C["关键字 & 替换规则"]
    P2 --> P2D["媒体过滤设置"]
    P2 --> P2E["推送通道管理"]
    P2 --> P2F["规则同步管理"]
    P2 --> P2G["模板编辑器"]
    
    P3 --> P3A["Prompt 编辑器"]
    P3 --> P3B["JSON 数据导入"]
    P3 --> P3C["实时预览 & 对比"]
    
    P4 --> P4A["定时任务看板"]
    P4 --> P4B["频道健康监控"]
    P4 --> P4C["AI 总结历史归档"]
    P4 --> P4D["系统日志流"]
    P4 --> P4E["AI 模型管理"]
```

---

### 页面 1：📡 RSS 结构化信息流大屏

基于已有 FastAPI RSS 接口，将 TG 消息转为可视化卡片流。

**布局**：三栏响应式
- **左侧边栏**：频道源列表，按更新频率/健康状态快速筛选
- **中部卡片流**：消息列表，展示源频道图标、时间、AI 提取的标题与摘要
- **右侧详情**：点击卡片展开全文，高亮代币符号/CA 地址/融资数据，一键复制 CA

**复用现有接口**：`/rss/dashboard`、`/rss/config`、`/rss/patterns` 等 8 个已实现的 API 直接对接。

---

### 页面 2：⚙️ 全参数规则配置中心

100% 映射 TG Bot 现有配置能力，与 TG Bot 同等操作权限。

#### 2.1 转发规则列表
- 表格展示所有规则：源频道 → 目标频道、启用状态、AI 开关
- 支持新建规则（频道选择器从 `chats` 表读取）、删除规则
- 点击某行展开该规则的完整配置

#### 2.2 开关矩阵面板（18 个参数）

分 4 组卡片布局：

**转发核心控制**
| 参数 | 类型 | 说明 |
|---|---|---|
| `enable_rule` | 开关 | 规则总开关 |
| `use_bot` | 切换 | Bot 发送 / User 发送 |
| `handle_mode` | 切换 | 转发模式 / 编辑模式 |
| `forward_mode` | 四选一 | 黑名单/白名单/先黑后白/先白后黑 |
| `add_mode` | 切换 | 关键字默认加到黑名单/白名单 |
| `enable_delay` | 开关 | 延迟处理 |
| `delay_seconds` | 数字输入 | 延迟秒数 |

**内容排版控制**
| 参数 | 类型 | 说明 |
|---|---|---|
| `is_replace` | 开关 | 替换模式 |
| `message_mode` | 切换 | Markdown / HTML |
| `is_preview` | 三选一 | 链接预览：开/关/跟随原消息 |
| `is_original_link` | 开关 | 附带原消息链接 |
| `is_original_sender` | 开关 | 显示原始发送者 |
| `is_original_time` | 开关 | 显示发送时间 |
| `is_delete_original` | 开关 | 删除源频道原消息 |

**AI 相关**
| 参数 | 类型 | 说明 |
|---|---|---|
| `is_ai` | 开关 | 逐条 AI 处理（翻译/改写） |
| `ai_model` | 下拉选择 | 选择 AI 模型 |
| `ai_prompt` | 文本编辑 | AI 处理提示词 |
| `is_summary` | 开关 | 定时 AI 总结 |
| `summary_time` | 多时间选择 | 总结时间点（支持逗号分隔多个） |
| `summary_prompt` | 文本编辑 | AI 总结提示词 |
| `enable_ai_upload_image` | 开关 | 发图片给 AI 分析 |
| `is_keyword_after_ai` | 开关 | AI 处理后再过关键字 |
| `is_top_summary` | 开关 | 置顶总结消息 |

**其他**
| 参数 | 类型 | 说明 |
|---|---|---|
| `enable_comment_button` | 开关 | 评论区直达按钮 |
| `enable_sync` | 开关 | 规则同步 |
| `only_rss` | 开关 | 只转发到 RSS |

#### 2.3 关键字 & 替换规则管理
- 可编辑表格，支持普通文本和正则两种模式
- 黑名单/白名单分栏展示
- 批量粘贴添加、导入/导出 CSV

#### 2.4 媒体过滤设置
- 媒体类型多选（图片/视频/文档/音频/语音）
- 大小限制滑块（MB）
- 扩展名黑白名单标签编辑

#### 2.5 推送通道管理
- 推送通道列表：Apprise URL + 启用开关 + 删除
- 新增通道时提供平台选择器（企业微信/飞书/钉钉/Bark/邮件）
- 自动生成 URL 模板，用户只需填 Token
- 三平台均留接口，优先适配企业微信

#### 2.6 规则同步管理
- 选择主规则，勾选要同步跟随的从规则
- 修改主规则配置时自动同步到所有从规则

#### 2.7 模板编辑器
- 用户信息模板：`{name}` 变量插值
- 时间模板：`{time}` 变量插值
- 原始链接模板：`{original_link}` 变量插值
- 实时预览渲染效果

---

### 页面 3：🤖 AI 调试与 Prompt 管理沙盒

为 Prompt 调优专门设计的离线测试工作台。

#### 3.1 数据源管理
- **导入本地 JSON**：加载 `dump_history.py` 下载的 `messages_dump.json`（53 频道历史消息）
- **时间窗口滑动条**：自由调整测试范围（1D / 3D / 7D）
- **频道筛选**：勾选参与测试的频道子集

#### 3.2 Prompt 编辑器（Monaco Editor）
- **左右分栏**：左编辑 Prompt → 右实时渲染 AI 处理结果
- **双 Prompt 切换**：AI 消息处理 Prompt / AI 定时总结 Prompt
- **模型热切换**：下拉选择（GPT / Claude / Gemini / DeepSeek / Grok）
- **一键测试**：调用后端 API 生成结果并渲染 Markdown
- **一键保存至生产**：Prompt 调好后直接写入数据库对应规则字段

---

### 页面 4：📊 运维监控台

#### 4.1 定时任务状态看板
- 任务列表：规则 ID / 频道名 / 下次执行时间 / 上次执行结果（成功/失败/跳过）
- 手动触发总结按钮
- 6H 间隔可视化时间轴

#### 4.2 频道健康监控
- 频道列表：名称 / 最后消息时间 / 24H 消息数 / 状态（正常/低活跃/已停更）
- 按状态排序，异常频道置顶

#### 4.3 AI 总结历史归档（核心复盘功能）
- 按日期/频道/关键字搜索历史总结报告
- Markdown 全文渲染展示
- **复制全文按钮**（用于到 ChatGPT/Claude 官网生成推特内容）
- 永久保存，不设过期清理

#### 4.4 AI 模型管理
- 可视化编辑 `config/ai_models.json`
- 按提供商分组（OpenAI / Claude / Gemini / DeepSeek / Grok）
- 模型增删操作

#### 4.5 系统日志流
- 实时 WebSocket 推送后台日志
- 关键字过滤 + ERROR/WARNING 高亮
- 前期调试排错专用

---

## 五、数据库变更

### 5.1 PostgreSQL 迁移
- `models/models.py` 中 2 处硬编码 `sqlite:///./db/forward.db`（L464、L476）改为 `os.getenv('DATABASE_URL')`
- 迁移脚本中双引号默认值改为单引号（ANSI SQL 兼容）
- `.env` 新增 `DATABASE_URL=postgresql://user:password@localhost:5432/telegram_forwarder`

#### 5.1.1 SQLite 特有 DDL 迁移兼容性处理（阻断性）

现有 `migrate_db()` 包含以下 SQLite 特有逻辑，迁移至 PostgreSQL 后会直接导致进程启动崩溃：

| 代码位置 | SQLite 特有语法 | PostgreSQL 替代方案 |
|---|---|---|
| `models.py:L405-L409` | `SELECT name FROM sqlite_master` 查系统表 | 改为 `SELECT indexname FROM pg_indexes WHERE indexname = ?` |
| `models.py:L418` | `INTEGER PRIMARY KEY AUTOINCREMENT` | 改为 `SERIAL PRIMARY KEY` |
| `models.py:L416-L448` | 通过临时表重建变更约束 | 直接使用 `ALTER TABLE ... ADD CONSTRAINT` |

实施策略：在 `migrate_db()` 入口增加数据库类型判断，若为 PostgreSQL 则跳过全部 SQLite 临时表逻辑，改用标准 ANSI SQL DDL 操作。

### 5.2 新增表

#### `summary_history`（AI 总结归档）
```sql
CREATE TABLE summary_history (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES forward_rules(id),
    source_channel_name VARCHAR,
    summary_text TEXT NOT NULL,
    message_count INTEGER,
    time_range_start TIMESTAMP,
    time_range_end TIMESTAMP,
    ai_model VARCHAR,
    prompt_used TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_summary_history_created ON summary_history(created_at DESC);
CREATE INDEX idx_summary_history_rule ON summary_history(rule_id);
```

### 5.3 字段修改

#### `forward_rules.summary_time`
```sql
-- 从 VARCHAR(5) 扩展为 VARCHAR(50)，支持逗号分隔多时间点
-- 示例值："06:00,12:00,18:00,00:00"
ALTER TABLE forward_rules ALTER COLUMN summary_time TYPE VARCHAR(50);
```

---

## 六、API 路由规划

### 6.1 服务架构：合并至同一进程同一端口

新增的控制台 API 直接并入现有 `rss/` 服务（整体更名为 `backend` 服务），统一由 Uvicorn 启动、共用 8000 端口。避免多进程管理、多端口占用和前端跨域配置。

现有 RSS 接口（8 个）保持不变，已实现在 `rss/app/routes/rss.py`：
- `GET /rss/dashboard` / `POST /rss/config` / `GET /rss/toggle/{rule_id}`
- `GET /rss/delete/{rule_id}` / `GET /rss/patterns/{config_id}`
- `POST /rss/pattern` / `DELETE /rss/pattern/{id}` / `POST /rss/test-regex`

### 6.2 新增 API 接口

| 路由 | 方法 | 功能 |
|---|---|---|
| **鉴权** | | |
| `/api/auth/login` | POST | 登录 |
| **规则 CRUD** | | |
| `/api/chats` | GET | 获取所有已注册频道列表 |
| `/api/rules` | GET / POST | 获取所有规则 / 创建新规则 |
| `/api/rules/{id}` | GET / PUT / DELETE | 获取 / 修改 / 删除规则（含全部开关字段） |
| **关键字 & 替换** | | |
| `/api/rules/{id}/keywords` | GET / POST / DELETE | 关键字 CRUD |
| `/api/rules/{id}/replace` | GET / POST / DELETE | 替换规则 CRUD |
| `/api/rules/{id}/keywords/import` | POST | 批量导入关键字 |
| `/api/rules/{id}/keywords/export` | GET | 导出关键字 |
| **媒体过滤** | | |
| `/api/rules/{id}/media-types` | GET / PUT | 读取/修改媒体类型过滤 |
| `/api/rules/{id}/media-extensions` | GET / POST / DELETE | 扩展名黑白名单 |
| **推送** | | |
| `/api/rules/{id}/push-config` | GET / POST / PUT / DELETE | 推送通道 CRUD |
| **同步** | | |
| `/api/rules/{id}/sync` | GET / POST / DELETE | 规则同步绑定 |
| **AI 沙盒** | | |
| `/api/sandbox/load-data` | POST | 加载本地 JSON 数据 |
| `/api/sandbox/run-test` | POST | 执行 Prompt 测试 |
| `/api/sandbox/apply-prompt` | POST | 保存 Prompt 到生产规则 |
| **总结归档** | | |
| `/api/summaries` | GET | 查询总结历史（分页、搜索） |
| `/api/summaries/{id}` | GET | 获取单条总结详情 |
| **监控** | | |
| `/api/scheduler/status` | GET | 获取所有定时任务状态 |
| `/api/scheduler/trigger/{rule_id}` | POST | 手动触发总结 |
| `/api/channels/health` | GET | 频道健康状态 |
| `/api/models` | GET / PUT | AI 模型配置读写 |
| `/ws/logs` | WebSocket | 实时日志流 |

---

## 七、后端代码改造清单

### 7.1 AI 总结推送到国内平台
- 修改 `scheduler/summary_scheduler.py` 的 `_execute_summary()` 方法
- 总结发送到 TG 后，额外调用 Apprise 推送到已配置的国内平台通道
- 国内平台格式适配：企业微信 Markdown 上限 4096 字节，推送前检测目标平台类型（从 URL scheme 判断如 `wecom://`），对超长内容智能截断或分段发送，精简不兼容的 Markdown 语法
- 先以原格式推送测试，根据实测结果逐步完善适配规则

### 7.2 多时间点定时总结（3 项配套改造）

现有代码存在 3 个与多时间点不兼容的硬编码，改造时必须同步处理：

#### 7.2.1 解析逻辑改造（修复冷启动崩溃）

现有 `_get_next_run_time()` 直接 `target_time.split(':')` 解析单时间点，传入逗号分隔格式会抛出 `ValueError` 导致调度器崩溃。改造：新增 `_parse_time_points()` 方法先按逗号分割为列表再逐个解析，`_get_next_run_time()` 遍历所有时间点取距离当前最近的未来时间。

#### 7.2.2 消息时间窗口改造（修复 18 小时重叠）

现有 `_execute_summary()` 的 `start_time` 硬编码 `- timedelta(days=1)` 前推 24 小时。一天 4 次总结时每次仍拉 24 小时数据，相邻两次内容重叠 18 小时，造成 AI Token 浪费。改造：`start_time` 改为动态计算距离当前最近的上一个总结时间点，每次只拉取相邻时间点间隔内的新消息（约 6 小时）。

#### 7.2.3 任务管理策略（避免幽灵任务）

维持现有 `{rule_id: task}` 一对一关系不变，不为每个时间点单独创建 asyncio 任务。在 `_run_summary_task()` 的 `while` 循环内，每次执行完总结后动态计算下一个最近时间点并 `sleep`，避免配置变更时因只能取消一个任务而产生幽灵任务残留。

### 7.3 热重载机制（Web 前端写入感知）
- 在守护进程中增加轻量心跳协程，每 10 秒比对数据库规则变更
- 检测到变更后自动调用 `scheduler.schedule_rule()` 重置定时任务


### 7.4 并发信号量修复
- 在 `_execute_summary()` 的 AI API 调用外层使用 `async with self.request_semaphore`
- 当前代码已在 L158 包裹了 TG 消息拉取，但 AI 调用（L228）在信号量外部，需要调整作用域

---

## 八、实施分期

### Phase 1：基础框架 + 核心配置（最高优先级）
- PostgreSQL 迁移
- FastAPI 新增 API 层
- React 前端工程搭建 + 设计系统
- 规则列表 + 开关矩阵面板
- 关键字 & 替换规则管理
- 推送通道管理

### Phase 2：AI 调试 + 总结归档（核心功能）
- AI Prompt 编辑器（Monaco Editor + 左右分栏）
- JSON 数据导入与预览
- `summary_history` 表 + 归档页面
- 多时间点定时总结改造
- AI 总结推送到国内平台

### Phase 3：运维监控 + 完善
- 定时任务状态看板
- 频道健康监控
- AI 模型可视化管理
- 系统日志流（WebSocket）
- 热重载机制
- RSS 信息流大屏对接

---

## 九、验证计划

### 自动化测试
- API 接口：pytest + httpx 覆盖所有 CRUD 端点
- 前端：Playwright E2E 测试核心流程（登录→配置→保存→验证数据库）

### 手动验证
- 前端修改配置后，TG Bot 侧验证配置已生效
- TG Bot 修改配置后，刷新前端页面验证同步
- AI 总结触发后，验证 TG 群 + 企业微信均收到推送
- Prompt 沙盒测试后一键保存，验证下次定时总结使用新 Prompt
