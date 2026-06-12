import axios from 'axios'
import type {
  RuleListItem, RuleDetail, Keyword, ReplaceRule as ReplaceRuleType,
  PushConfig, MediaTypes, RssConfig, RssPattern, SummaryResponse, Chat, ChatDetail
} from './types'

// ── Axios 实例 (BUG-03 修复: withCredentials) ─────────────
const api = axios.create({
  baseURL: '/api',         // Vite proxy 转发到 8000
  withCredentials: true,   // 携带 Cookie
  headers: { 'Content-Type': 'application/json' },
})

export default api

// ── SWR fetcher ───────────────────────────────────────────
export const fetcher = (url: string) => api.get(url).then(r => r.data)

// ── Auth ──────────────────────────────────────────────────
export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }).then(r => r.data),
  logout: () => api.post('/auth/logout').then(r => r.data),
  me: () => api.get('/auth/me').then(r => r.data),
  changePassword: (data: FormData) =>
    axios.post('/rss/change_password', data, { withCredentials: true }).then(r => r.data),
}

// ── Chat ──────────────────────────────────────────────────
export const chatsApi = {
  getAll: () => api.get<ChatDetail[]>('/chats').then(r => r.data),
  update: (id: number, name: string) => api.put(`/chats/${id}`, { name }).then(r => r.data),
  delete: (id: number) => api.delete(`/chats/${id}`).then(r => r.data),
  resolve: (link: string) => api.post<{ status: string; chat: Chat }>('/chats/resolve', { link }).then(r => r.data),
}

// ── Rules ─────────────────────────────────────────────────
export const rulesApi = {
  getAll: () => api.get<RuleListItem[]>('/rules').then(r => r.data),
  getById: (id: number) => api.get<RuleDetail>(`/rules/${id}`).then(r => r.data),
  create: (data: { source_chat_id: number; target_chat_id: number }) => api.post<{ id: number }>('/rules', data).then(r => r.data),
  update: (id: number, data: Partial<RuleDetail>) => api.put(`/rules/${id}`, data),
  delete: (id: number) => api.delete(`/rules/${id}`),
  syncToAll: (id: number, fields?: string[]) => api.post(`/rules/${id}/sync-to-all`, fields ? { fields } : undefined).then(r => r.data),
}

// ── Keywords ──────────────────────────────────────────────
export const keywordsApi = {
  getByRule: (ruleId: number) => api.get<Keyword[]>(`/rules/${ruleId}/keywords`).then(r => r.data),
  create: (ruleId: number, data: Partial<Keyword>) => api.post(`/rules/${ruleId}/keywords`, data),
  delete: (ruleId: number, kwId: number) => api.delete(`/rules/${ruleId}/keywords/${kwId}`),
}

// ── Replace Rules ─────────────────────────────────────────
export const replaceApi = {
  getByRule: (ruleId: number) => api.get<ReplaceRuleType[]>(`/rules/${ruleId}/replace`).then(r => r.data),
  create: (ruleId: number, data: Partial<ReplaceRuleType>) => api.post(`/rules/${ruleId}/replace`, data),
  delete: (ruleId: number, rrId: number) => api.delete(`/rules/${ruleId}/replace/${rrId}`),
}

// ── Push Config ───────────────────────────────────────────
export const pushApi = {
  getByRule: (ruleId: number) => api.get<PushConfig[]>(`/rules/${ruleId}/push-config`).then(r => r.data),
  create: (ruleId: number, data: Partial<PushConfig>) => api.post(`/rules/${ruleId}/push-config`, data),
  delete: (ruleId: number, pcId: number) => api.delete(`/rules/${ruleId}/push-config/${pcId}`),
}

// ── Media Types ───────────────────────────────────────────
export const mediaApi = {
  getByRule: (ruleId: number) => api.get<MediaTypes>(`/rules/${ruleId}/media-types`).then(r => r.data),
  update: (ruleId: number, data: MediaTypes) => api.put(`/rules/${ruleId}/media-types`, data),
}

// ── RSS Config ────────────────────────────────────────────
export const rssApi = {
  getAll: () => api.get<RssConfig[]>('/rss-configs').then(r => r.data),
  create: (data: Partial<RssConfig>) => api.post('/rss-configs', data),
  update: (id: number, data: Partial<RssConfig>) => api.put(`/rss-configs/${id}`, data),
  delete: (id: number) => api.delete(`/rss-configs/${id}`),
  toggle: (id: number) => api.post(`/rss-configs/${id}/toggle`),
}

// ── RSS Patterns ──────────────────────────────────────────
export const patternsApi = {
  getByConfig: (configId: number) => api.get<RssPattern[]>(`/rss-configs/${configId}/patterns`).then(r => r.data),
  create: (configId: number, data: Partial<RssPattern>) => api.post(`/rss-configs/${configId}/patterns`, data),
  deleteAll: (configId: number) => api.delete(`/rss-configs/${configId}/patterns`),
  deleteOne: (patternId: number) => api.delete(`/rss-configs/patterns/${patternId}`),
}

// ── Regex Test ────────────────────────────────────────────
export const regexApi = {
  test: (data: { pattern: string; test_text: string; pattern_type: string }) =>
    api.post('/test-regex', data).then(r => r.data),
}

// ── Summaries ─────────────────────────────────────────────
export const summariesApi = {
  getAll: (page = 1, limit = 10, search?: string) => {
    const params = new URLSearchParams({ page: String(page), limit: String(limit) })
    if (search) params.set('search', search)
    return api.get<SummaryResponse>(`/summaries?${params}`).then(r => r.data)
  },
}

// ── AI Sandbox ────────────────────────────────────────────
export const sandboxApi = {
  runTest: (data: { prompt: string; model: string; test_message: string }) =>
    api.post('/sandbox/run-test', data).then(r => r.data),
  getHistorySamples: () => api.get<any>('/sandbox/history-samples').then(r => r.data),
  aggregateMessages: (data: { channels: string[]; days: number }) =>
    api.post('/sandbox/aggregate-messages', data).then(r => r.data),
  analyzeChannels: (data: { model: string }) =>
    api.post('/sandbox/analyze-channels', data).then(r => r.data),
  uploadJson: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/sandbox/upload-json', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    }).then(r => r.data)
  },
}

// ── AI Models & Config ────────────────────────────────────
export const aiApi = {
  getModels: () => api.get<Record<string, string[]>>('/ai-models').then(r => r.data),
  getDefaultModel: () => api.get<{ default_model: string }>('/ai-default-model').then(r => r.data),
}
