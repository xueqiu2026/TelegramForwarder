// ── 枚举类型 ──────────────────────────────────────────────
export type ForwardMode = 'WHITELIST' | 'BLACKLIST' | 'BLACKLIST_THEN_WHITELIST' | 'WHITELIST_THEN_BLACKLIST'
export type PreviewMode = 'ON' | 'OFF' | 'FOLLOW'
export type MessageMode = 'MARKDOWN' | 'HTML'
export type AddMode = 'WHITELIST' | 'BLACKLIST'
export type HandleMode = 'FORWARD' | 'EDIT'

// ── 数据模型 ──────────────────────────────────────────────
export interface Chat {
  id: number
  telegram_chat_id: string
  name: string
}

export interface ChatDetail extends Chat {
  source_rule_count: number
  target_rule_count: number
}

export interface RuleListItem {
  id: number
  source_chat_id: number
  source_chat_name: string
  target_chat_id: number
  target_chat_name: string
  enable_rule: boolean
  enable_forward: boolean
  is_ai: boolean
  is_summary: boolean
  summary_time: string | null
}

export interface RuleDetail {
  id: number
  source_chat_id: number
  target_chat_id: number
  forward_mode: ForwardMode
  use_bot: boolean
  message_mode: MessageMode
  is_replace: boolean
  is_preview: PreviewMode
  is_original_link: boolean
  is_delete_original: boolean
  is_original_sender: boolean
  userinfo_template: string | null
  time_template: string | null
  original_link_template: string | null
  is_original_time: boolean
  add_mode: AddMode
  enable_rule: boolean
  enable_forward: boolean
  handle_mode: HandleMode
  enable_comment_button: boolean
  // AI
  is_ai: boolean
  ai_model: string | null
  ai_prompt: string | null
  enable_ai_upload_image: boolean
  is_summary: boolean
  summary_time: string | null
  summary_prompt: string | null
  summary_prompt_b?: string | null
  summary_prompt_d?: string | null
  is_keyword_after_ai: boolean
  is_top_summary: boolean
  // 其他
  enable_push: boolean
  enable_only_push: boolean
  only_rss: boolean
  enable_sync: boolean
}

export interface Keyword {
  id: number
  keyword: string
  is_regex: boolean
  is_blacklist: boolean
}

export interface ReplaceRule {
  id: number
  pattern: string
  content: string
}

export interface PushConfig {
  id: number
  enable_push_channel: boolean
  push_channel: string
  media_send_mode: string
}

export interface MediaTypes {
  photo: boolean
  document: boolean
  video: boolean
  audio: boolean
  voice: boolean
}

export interface RssConfig {
  id: number
  rule_id: number
  enable_rss: boolean
  rule_title: string
  rule_description: string
  language: string
  max_items: number
  is_auto_title: boolean
  is_auto_content: boolean
  is_ai_extract: boolean
  ai_extract_prompt: string
  is_auto_markdown_to_html: boolean
  enable_custom_title_pattern: boolean
  enable_custom_content_pattern: boolean
  source_chat_name?: string | null
  target_chat_name?: string | null
}

export interface RssPattern {
  id: number
  pattern: string
  pattern_type: 'title' | 'content'
  priority: number
}

export interface SummaryItem {
  id: number
  rule_id: number
  source_channel_name: string
  summary_text: string
  message_count: number
  time_range_start: string | null
  time_range_end: string | null
  ai_model: string | null
  created_at: string | null
}

export interface SummaryResponse {
  total: number
  data: SummaryItem[]
}
