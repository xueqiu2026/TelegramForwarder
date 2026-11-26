export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Business { key: 'alpha' | 'ai'; name: string; path: string }
export interface Folder { id: number; title: string; complete: boolean }
export interface SourceRow {
  chat_id: number; peer_id: string | null; telegram_chat_id: string; peer_type: string | null
  name: string; telegram_title: string | null; username: string | null
  member_state: string; verified_at: string | null; folders: Folder[]
  subscriptions: Record<string, { active: boolean; enabled_at: string }>; can_subscribe: boolean
  collection: { enabled: boolean; scanned_at: string | null; error: string | null }
  latest: { published_at: string | null; received_at: string | null }
  raw_count: number; direct: { rules: number; enabled: number; blocked: boolean }; target_rule_count: number
}
export interface SourcePage extends Page<SourceRow> { businesses: Business[]; folders: Folder[] }
export interface ProcessState { analysis_status?: string; status?: string; recall?: string; error: string | null }
export interface Original {
  id: number; source_id: string; message_id: number; source_name: string; published_at: string
  edited_at: string | null; received_at: string | null; body: string; source_url: string; revision: number
  media: Record<string, unknown>; entities: {url?:string|null}[]; business_states?: Record<string, ProcessState | null>
}
export interface SyncPreview {
  status: string; token?: string; error?: string
  result?: { complete: boolean; folders_complete: boolean; errors: Record<string, string>
    sources: { peer_id: string; telegram_title: string; member_state: string; change:string; affected_businesses:string[]
      changes: {field:string;before:string|null;after:string|null}[] }[]; folders: Folder[]
    folder_changes: {id:number;before:{title:string;peers:string[]}|null;after:{title:string;peers:string[]}|null}[] }
}
export interface JobStatus {
  enabled: boolean; publish_enabled: boolean; model: string; target_id: string; schedule_hours: number[]
  last_run: string | null; status: string; error: string | null; source_count: number
  analysis_until?: string | null; result: Record<string, unknown>
}
export interface Publication {
  id: number; target_id: string; status: string; created_at: string; error: string | null
  parts: { text: string; entities?: TelegramEntity[]; message_id: number | null; status: string; photo?: unknown }[]
}
export interface TelegramEntity { _: string; offset: number; length: number; url?: string; language?: string }
export interface ContentItem {
  id: number; project?: string; title?: string; subject?: string; goal?: string; conditions?: string
  tier?: string; category?: string; content_type?: string; draft_status?: string; status?: string
  type_status?: string | null; detail_level?: string | null; deadline?: string | null
  source_count?: number; draft_zh?: string | null; output_zh?: string | null; error: string | null
  publications?: Publication[]; originals?: Original[]
  dedup?: {relation?: string; related_item_id?: number | null; reason?: string; change?: string; blocked_by_item_id?: number | null; blocked_reason?: string} | null
  publication_summaries?: {id:number;target_id:string;status:string}[]
}
export function formatTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '未知'
}
export function errorText(error: unknown): string {
  const e = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = e.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => typeof item?.msg === 'string' ? item.msg : '输入格式有误').join('；')
  return e.message || '操作失败'
}
export const stateNames: Record<string, string> = {
  idle: '未运行', running: '运行中', success: '完成', partial: '部分完成', failed: '失败',
  pending: '待处理', candidate: '符合筛选', needs_info: '待补信息', expired: '已截止', excluded: '已排除',
  unmatched: '未命中', done: '已分析', uncertain: '待核对', ready: '已就绪', blocked: '已拦截',
  queued: '待发送', sending: '发送中', sent: '已发送', unknown: '待核对',
  main: '主清单', single: '单源附录', history: '历史', closed: '已结束',
  brief: '简短', standard: '标准', detailed: '详细',
}
export const categories: Record<string, string> = {
  B1: '注册/申请赠API额度', B5: '身份/地区/渠道权益', B6: '登录/邀请/老用户权益',
  T1: '接入/环境配置', T5: 'Token/速度/额度优化', T7: '注册/订阅/资格/账单', U: '热门模型/额度更新',
  tutorial: '活动教程', sale: '发行申购', claim_check: '查询领取', allocation: '积分分配',
  update: '进展提醒', ipo: '全球IPO', launchpad: '平台上线预告',
}
