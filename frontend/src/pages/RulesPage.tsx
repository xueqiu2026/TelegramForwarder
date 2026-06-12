import { useState, useEffect } from 'react'
import useSWR, { mutate } from 'swr'
import { Plus, X, ChevronRight, Trash2, AlertTriangle } from 'lucide-react'
import * as Switch from '@radix-ui/react-switch'
import * as Dialog from '@radix-ui/react-dialog'
import { fetcher, rulesApi, keywordsApi, replaceApi, pushApi } from '../api'
import type { RuleListItem, RuleDetail, Keyword, ReplaceRule as RRType, PushConfig, ChatDetail } from '../types'

// ── 枚举选项中文映射 ──────────────────────────────
const ENUM_OPTIONS = {
  forward_mode: {
    label: '消息过滤模式',
    description: '过滤新转发文本的黑白名单过滤机制',
    options: [
      { value: 'BLACKLIST', label: '黑名单 (仅过滤匹配的关键字)' },
      { value: 'WHITELIST', label: '白名单 (仅转发匹配的关键字)' },
      { value: 'BLACKLIST_THEN_WHITELIST', label: '先黑后白' },
      { value: 'WHITELIST_THEN_BLACKLIST', label: '先白后黑' },
    ]
  },
  handle_mode: {
    label: '转发处理方式',
    description: '定义新消息的输出行为',
    options: [
      { value: 'FORWARD', label: '直接转发 (标准模式)' },
      { value: 'EDIT', label: '以编辑原消息形式处理' },
    ]
  },
  message_mode: {
    label: '目标消息格式',
    description: '转发消息的排版标记渲染模式',
    options: [
      { value: 'MARKDOWN', label: 'Markdown 语法' },
      { value: 'HTML', label: 'HTML 格式' },
    ]
  },
  is_preview: {
    label: '链接卡片预览',
    description: '对消息内包含的超链接是否渲染富文本卡片预览',
    options: [
      { value: 'ON', label: '总是开启预览' },
      { value: 'OFF', label: '总是关闭预览' },
      { value: 'FOLLOW', label: '跟随原始消息' },
    ]
  },
  add_mode: {
    label: '新成员处理模式',
    description: '检测到新加入成员消息时的过滤模式',
    options: [
      { value: 'BLACKLIST', label: '新成员黑名单模式' },
      { value: 'WHITELIST', label: '新成员白名单模式' },
    ]
  }
}

// ── 现代化 Switch 封装组件 ────────────────────────
function FormSwitch({
  label,
  description,
  checked,
  onCheckedChange
}: {
  label: string
  description?: string
  checked: boolean
  onCheckedChange: (val: boolean) => void
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '12px 16px',
      background: 'var(--bg-surface-hover)',
      borderRadius: 'var(--radius-sm)',
      border: '1px solid var(--border-light)',
      transition: 'var(--transition-fast)'
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginRight: 16 }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>{label}</span>
        {description && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{description}</span>}
      </div>
      <Switch.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        style={{
          width: 38,
          height: 20,
          backgroundColor: checked ? 'var(--color-primary)' : 'var(--bg-base)',
          borderRadius: 9999,
          position: 'relative',
          border: '1px solid var(--border-light)',
          cursor: 'pointer',
          outline: 'none',
          transition: 'background-color 150ms',
        }}
      >
        <Switch.Thumb
          style={{
            display: 'block',
            width: 14,
            height: 14,
            backgroundColor: checked ? '#fff' : 'var(--text-secondary)',
            borderRadius: 9999,
            transition: 'transform 150ms',
            transform: checked ? 'translateX(20px)' : 'translateX(2px)',
          }}
        />
      </Switch.Root>
    </div>
  )
}

const TABS = [
  { id: 'overview', label: '概览' },
  { id: 'message', label: '消息处理' },
  { id: 'ai', label: 'AI 配置' },
  { id: 'filter', label: '过滤规则' },
  { id: 'push', label: '推送 & 总结' },
] as const

export default function RulesPage() {
  const { data: rules } = useSWR<RuleListItem[]>('/rules', fetcher)
  const { data: chats } = useSWR<ChatDetail[]>('/chats', fetcher)
  const { data: modelsDict } = useSWR<Record<string, string[]>>('/ai-models', fetcher)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selectedRule = rules?.find(r => r.id === selectedId)
  const [detail, setDetail] = useState<RuleDetail | null>(null)
  const [keywords, setKeywords] = useState<Keyword[]>([])
  const [replaceRules, setReplaceRules] = useState<RRType[]>([])
  const [pushConfigs, setPushConfigs] = useState<PushConfig[]>([])
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState<'overview' | 'message' | 'ai' | 'filter' | 'push'>('overview')

  // 新建规则状态
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [sourceChatId, setSourceChatId] = useState<number | ''>('')
  const [targetChatId, setTargetChatId] = useState<number | ''>('')
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  // 打开创建规则弹窗
  const openCreateDialog = () => {
    setCreateError(null)
    setSourceChatId('')
    const defaultTarget = chats?.find(c => c.name === '2026')
    setTargetChatId(defaultTarget ? defaultTarget.id : '')
    setCreateDialogOpen(true)
  }

  // 创建规则
  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!sourceChatId || !targetChatId) return
    setCreating(true)
    setCreateError(null)
    try {
      const res = await rulesApi.create({
        source_chat_id: Number(sourceChatId),
        target_chat_id: Number(targetChatId)
      })
      mutate('/rules')
      setCreateDialogOpen(false)
      setSelectedId(res.id) // 自动高亮选中新规则
      setSourceChatId('')
      setTargetChatId('')
    } catch (e: any) {
      setCreateError(e?.response?.data?.detail || '创建规则失败，可能是该源频道到目标群组的规则已存在')
    } finally {
      setCreating(false)
    }
  }

  // 删除规则
  const handleDeleteRule = async (id: number) => {
    try {
      await rulesApi.delete(id)
      mutate('/rules')
      if (selectedId === id) {
        setSelectedId(null)
        setDetail(null)
      }
    } catch (e: any) {
      alert(e?.response?.data?.detail || '删除规则失败')
    }
  }

  // 时间徽标管理器变量与逻辑
  const [newTimeInput, setNewTimeInput] = useState('00:00')
  const timeList = detail?.summary_time ? detail.summary_time.split(',').map(t => t.trim()).filter(Boolean) : []

  const handleRemoveTime = (timeToRemove: string) => {
    const nextList = timeList.filter(t => t !== timeToRemove)
    saveField('summary_time', nextList.join(','))
  }

  const handleAddTime = () => {
    if (!newTimeInput) return
    if (timeList.includes(newTimeInput)) return
    const nextList = [...timeList, newTimeInput].sort()
    saveField('summary_time', nextList.join(','))
  }

  // 加载详情
  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    rulesApi.getById(selectedId).then(setDetail).catch(console.error)
    keywordsApi.getByRule(selectedId).then(setKeywords).catch(() => setKeywords([]))
    replaceApi.getByRule(selectedId).then(setReplaceRules).catch(() => setReplaceRules([]))
    pushApi.getByRule(selectedId).then(setPushConfigs).catch(() => setPushConfigs([]))
    setActiveTab('overview') // 切换规则时默认重置到概览
  }, [selectedId])

  // 自动保存字段
  const saveField = async (field: string, value: any) => {
    if (!selectedId) return
    setSaving(true)
    try {
      await rulesApi.update(selectedId, { [field]: value })
      setDetail(prev => prev ? { ...prev, [field]: value } : prev)
      mutate('/rules')
    } catch (e) { console.error('保存失败', e) }
    finally { setSaving(false) }
  }

  // ── 一键预设配置 ──
  const applyPreset = async (presetType: 'direct' | 'ai' | 'rss') => {
    if (!selectedId || !detail) return
    setSaving(true)
    try {
      let preset: Partial<RuleDetail> = {}
      if (presetType === 'direct') {
        preset = {
          is_ai: false,
          is_summary: false,
          is_replace: false,
          enable_push: false,
        }
      } else if (presetType === 'ai') {
        preset = {
          is_ai: true,
          ai_model: 'gpt-5.5',
          ai_prompt: '请尊重原意，保持原有格式不变，用简体中文重写下面的内容：',
          is_summary: false,
        }
      } else if (presetType === 'rss') {
        preset = {
          only_rss: true,
          enable_rule: true,
          is_ai: false,
          is_summary: false,
        }
      }
      await rulesApi.update(selectedId, preset)
      setDetail(prev => prev ? { ...prev, ...preset } : prev)
      mutate('/rules')
    } catch (e) { console.error('应用预设失败', e) }
    finally { setSaving(false) }
  }

  // ── 一键同步配置给所有其它频道规则（支持选择性字段同步） ──
  const [showSyncModal, setShowSyncModal] = useState(false)
  const [syncFields, setSyncFields] = useState<Record<string, boolean>>({
    prompts: false,
    ai_settings: false,
    forward_settings: false,
    push_settings: false,
  })
  const syncFieldGroups: Record<string, { label: string; fields: string[] }> = {
    prompts: { label: '📡 所有总结 Prompt（简报 + 推文工厂 + 信息差快报）', fields: ['summary_prompt', 'summary_prompt_b', 'summary_prompt_d'] },
    ai_settings: { label: '🤖 AI 设置（模型、提示词、开关）', fields: ['is_ai', 'ai_model', 'ai_prompt', 'enable_ai_upload_image', 'is_keyword_after_ai'] },
    forward_settings: { label: '⚙️ 转发与总结设置（模式、时间、置顶等）', fields: ['forward_mode', 'handle_mode', 'message_mode', 'is_preview', 'is_replace', 'is_summary', 'summary_time', 'is_top_summary', 'use_bot', 'enable_forward', 'enable_comment_button'] },
    push_settings: { label: '📢 推送设置', fields: ['enable_push', 'enable_only_push'] },
  }
  const handleSyncToAll = () => {
    if (!selectedId) return
    setSyncFields({ prompts: false, ai_settings: false, forward_settings: false, push_settings: false })
    setShowSyncModal(true)
  }
  const executeSyncToAll = async () => {
    if (!selectedId) return
    const selected = Object.entries(syncFields).filter(([, v]) => v).map(([k]) => k)
    if (selected.length === 0) { alert('请至少选择一个同步项'); return }
    const fields = selected.flatMap(k => syncFieldGroups[k].fields)
    setShowSyncModal(false)
    setSaving(true)
    try {
      const res = await rulesApi.syncToAll(selectedId, fields)
      setSaving(false)
      alert(res.message || '配置已成功同步')
    } catch (e) {
      console.error(e)
      setSaving(false)
      alert('同步失败')
    }
  }

  // ── 关键字 CRUD ──
  const [newKw, setNewKw] = useState({ keyword: '', is_regex: false, is_blacklist: true })
  const addKeyword = async () => {
    if (!selectedId || !newKw.keyword.trim()) return
    try {
      await keywordsApi.create(selectedId, newKw)
      setKeywords(await keywordsApi.getByRule(selectedId))
      setNewKw({ keyword: '', is_regex: false, is_blacklist: true })
    } catch (e) { console.error(e) }
  }
  const delKeyword = async (kwId: number) => {
    if (!selectedId) return
    try { await keywordsApi.delete(selectedId, kwId); setKeywords(prev => prev.filter(k => k.id !== kwId)) } catch (e) { console.error(e) }
  }

  // ── 替换规则 CRUD ──
  const [newRR, setNewRR] = useState({ pattern: '', content: '' })
  const addReplace = async () => {
    if (!selectedId || !newRR.pattern.trim()) return
    try {
      await replaceApi.create(selectedId, newRR)
      setReplaceRules(await replaceApi.getByRule(selectedId))
      setNewRR({ pattern: '', content: '' })
    } catch (e) { console.error(e) }
  }
  const delReplace = async (rrId: number) => {
    if (!selectedId) return
    try { await replaceApi.delete(selectedId, rrId); setReplaceRules(prev => prev.filter(r => r.id !== rrId)) } catch (e) { console.error(e) }
  }

  // ── 推送通道 CRUD ──
  const [newPC, setNewPC] = useState({ push_channel: '', enable_push_channel: true, media_send_mode: 'Single' })
  const addPush = async () => {
    if (!selectedId || !newPC.push_channel.trim()) return
    try {
      await pushApi.create(selectedId, newPC)
      setPushConfigs(await pushApi.getByRule(selectedId))
      setNewPC({ push_channel: '', enable_push_channel: true, media_send_mode: 'Single' })
    } catch (e) { console.error(e) }
  }
  const delPush = async (pcId: number) => {
    if (!selectedId) return
    try { await pushApi.delete(selectedId, pcId); setPushConfigs(prev => prev.filter(p => p.id !== pcId)) } catch (e) { console.error(e) }
  }

  return (
    <div style={{ display: 'flex', gap: 20, height: '100%', minHeight: 0 }}>
      {/* ── 左侧: 规则列表 ─────────────────── */}
      <div className="card-glass" style={{ width: 300, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid var(--border-light)', fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>转发规则 <span className="badge badge-primary" style={{ marginLeft: 6 }}>{rules?.length ?? 0}</span></span>
          <button 
            onClick={openCreateDialog} 
            style={{ 
              background: 'none', border: 'none', color: 'var(--color-primary)', 
              display: 'flex', alignItems: 'center', gap: 4, padding: 0, 
              fontSize: 12, fontWeight: 600, cursor: 'pointer' 
            }}
          >
            <Plus size={14} /> 新建
          </button>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {!rules ? (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</div>
          ) : rules.map(r => (
            <div
              key={r.id}
              onClick={() => setSelectedId(r.id)}
              className="rule-list-item-container"
              style={{
                padding: '12px 16px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10,
                borderBottom: '1px solid var(--border-light)',
                background: selectedId === r.id ? 'var(--bg-surface-active)' : 'transparent',
                transition: 'var(--transition-fast)',
              }}
            >
              <div className={`health-dot ${r.enable_rule ? 'active' : 'danger'}`} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {r.source_chat_name}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  → {r.target_chat_name}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    if (window.confirm(`确定要删除规则 "${r.source_chat_name} → ${r.target_chat_name}" 吗？此操作将永久清空关联的过滤、替换、推送等配置！`)) {
                      handleDeleteRule(r.id)
                    }
                  }}
                  className="delete-rule-btn"
                  style={{
                    background: 'transparent', border: 'none', cursor: 'pointer',
                    padding: 2, display: 'flex', alignItems: 'center'
                  }}
                  title="删除规则"
                >
                  <Trash2 size={13} />
                </button>
                <ChevronRight size={14} color="var(--text-muted)" />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── 右侧: 规则详情 ─────────────────── */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {!detail ? (
          <div className="card-glass" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 14 }}>
            ← 选择左侧的一个规则开始配置
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* 保存状态浮层 */}
            {saving && <div style={{ position: 'fixed', top: 60, right: 20, padding: '6px 16px', background: 'var(--color-primary)', color: '#fff', borderRadius: 20, fontSize: 12, zIndex: 100, boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>保存中...</div>}

            {/* 规则头部标题与预设选择 */}
            <div className="card-glass" style={{ padding: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div className={`health-dot ${detail.enable_rule ? 'active' : 'danger'}`} />
                <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
                  {selectedRule?.source_chat_name} → {selectedRule?.target_chat_name}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <button
                  onClick={handleSyncToAll}
                  style={{
                    ...btnStyle,
                    background: 'var(--bg-surface-hover)',
                    border: '1px solid var(--border-light)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                    padding: '4px 10px',
                    height: 28,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4
                  }}
                  title="同步当前除了聊天 ID 之外的所有配置到其它所有转发规则中"
                >
                  🚀 同步设置到所有其它频道
                </button>
                <div style={{ width: '1px', height: '16px', background: 'var(--border-light)' }} />
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>一键配置预设:</span>
                <select
                  onChange={e => {
                    if (e.target.value) {
                      applyPreset(e.target.value as any)
                      e.target.value = ''
                    }
                  }}
                  style={{ ...inputStyle, width: 'auto', padding: '4px 8px', fontSize: 12, height: 28 }}
                >
                  <option value="">(选择快捷配置模板)</option>
                  <option value="direct">纯消息直转 (关闭 AI & 过滤 & 总结)</option>
                  <option value="ai">AI 翻译与摘要转发 (开启 AI 提示词)</option>
                  <option value="rss">仅生成 RSS 订阅源 (不转发至目标)</option>
                </select>
              </div>
            </div>

            {/* 子 Tab 导航 */}
            <div className="card-glass" style={{ padding: '0 16px', display: 'flex', gap: 12 }}>
              {TABS.map(t => {
                const isActive = activeTab === t.id
                return (
                  <button
                    key={t.id}
                    onClick={() => setActiveTab(t.id)}
                    style={{
                      background: 'none', border: 'none', padding: '14px 8px', fontSize: 13, fontWeight: 600,
                      color: isActive ? 'var(--color-primary)' : 'var(--text-secondary)',
                      borderBottom: isActive ? '3px solid var(--color-primary)' : '3px solid transparent',
                      cursor: 'pointer', transition: 'var(--transition-fast)',
                      outline: 'none', marginTop: 2
                    }}
                  >
                    {t.label}
                  </button>
                )
              })}
            </div>

            {/* Tab 1: 概览 */}
            {activeTab === 'overview' && (
              <div className="card-glass" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
                <h3 style={sectionTitle}>核心控制项</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
                  <FormSwitch
                    label="启用转发规则"
                    description="开启后当前映射转发规则正式生效；关闭则暂停转发"
                    checked={detail.enable_rule}
                    onCheckedChange={val => saveField('enable_rule', val)}
                  />
                  <FormSwitch
                    label="启用即时直发"
                    description="开启后源频道新消息会即时转发；关闭则跳过即时直发"
                    checked={detail.enable_forward}
                    onCheckedChange={val => saveField('enable_forward', val)}
                  />
                  <FormSwitch
                    label="使用 Bot 转发"
                    description="启用官方 Telegram Bot 进行推送；关闭则使用 Userbot 直接发送"
                    checked={detail.use_bot}
                    onCheckedChange={val => saveField('use_bot', val)}
                  />
                  <FormSwitch
                    label="仅 RSS 输出"
                    description="开启后此映射仅生成 RSS 订阅源，不再转发给 Telegram 目标聊天"
                    checked={detail.only_rss}
                    onCheckedChange={val => saveField('only_rss', val)}
                  />
                  <FormSwitch
                    label="同步删除与编辑"
                    description="源聊天的内容编辑与消息删除将自动同步至目标聊天"
                    checked={detail.enable_sync}
                    onCheckedChange={val => saveField('enable_sync', val)}
                  />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 8 }}>
                  <div>
                    <label style={labelStyle}>{ENUM_OPTIONS.forward_mode.label}</label>
                    <span style={descStyle}>{ENUM_OPTIONS.forward_mode.description}</span>
                    <select
                      value={detail.forward_mode}
                      onChange={e => saveField('forward_mode', e.target.value)}
                      style={{ ...inputStyle, marginTop: 6 }}
                    >
                      {ENUM_OPTIONS.forward_mode.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={labelStyle}>{ENUM_OPTIONS.handle_mode.label}</label>
                    <span style={descStyle}>{ENUM_OPTIONS.handle_mode.description}</span>
                    <select
                      value={detail.handle_mode}
                      onChange={e => saveField('handle_mode', e.target.value)}
                      style={{ ...inputStyle, marginTop: 6 }}
                    >
                      {ENUM_OPTIONS.handle_mode.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>
              </div>
            )}

            {/* Tab 2: 消息处理 */}
            {activeTab === 'message' && (
              <div className="card-glass" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
                <h3 style={sectionTitle}>消息格式排版与开关</h3>
                
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  <div>
                    <label style={labelStyle}>{ENUM_OPTIONS.message_mode.label}</label>
                    <span style={descStyle}>{ENUM_OPTIONS.message_mode.description}</span>
                    <select
                      value={detail.message_mode}
                      onChange={e => saveField('message_mode', e.target.value)}
                      style={{ ...inputStyle, marginTop: 6 }}
                    >
                      {ENUM_OPTIONS.message_mode.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={labelStyle}>{ENUM_OPTIONS.is_preview.label}</label>
                    <span style={descStyle}>{ENUM_OPTIONS.is_preview.description}</span>
                    <select
                      value={detail.is_preview}
                      onChange={e => saveField('is_preview', e.target.value)}
                      style={{ ...inputStyle, marginTop: 6 }}
                    >
                      {ENUM_OPTIONS.is_preview.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, marginTop: 8 }}>
                  <FormSwitch
                    label="启用文本替换规则"
                    description="开启后将在此频道的消息转发时应用下方定义的文本替换规则"
                    checked={detail.is_replace}
                    onCheckedChange={val => saveField('is_replace', val)}
                  />
                  <FormSwitch
                    label="自动删除原始消息"
                    description="消息成功转发至目标后，自动将来源聊天中的原消息物理删除"
                    checked={detail.is_delete_original}
                    onCheckedChange={val => saveField('is_delete_original', val)}
                  />
                  <FormSwitch
                    label="添加评论跳转按钮"
                    description="在消息底部追加一键快捷跳转去评论的交互按钮"
                    checked={detail.enable_comment_button}
                    onCheckedChange={val => saveField('enable_comment_button', val)}
                  />
                  <FormSwitch
                    label="保留并显示原始发送者"
                    description="在转发消息的顶端展示原作者或原频道的来源署名"
                    checked={detail.is_original_sender}
                    onCheckedChange={val => saveField('is_original_sender', val)}
                  />
                  <FormSwitch
                    label="保留并附加原始时间"
                    description="在转发消息的末尾附带消息在来源聊天中创建的原始时间"
                    checked={detail.is_original_time}
                    onCheckedChange={val => saveField('is_original_time', val)}
                  />
                  <FormSwitch
                    label="附加消息原始链接"
                    description="在转发消息的末尾附带指向来源消息的超链接地址"
                    checked={detail.is_original_link}
                    onCheckedChange={val => saveField('is_original_link', val)}
                  />
                </div>

                {/* 消息模板定义 (条件显示) */}
                {(detail.is_original_sender || detail.is_original_time || detail.is_original_link) && (
                  <div style={{
                    display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12, padding: 16,
                    background: 'var(--bg-base)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)'
                  }}>
                    <h4 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>自定义输出模板格式</h4>
                    
                    {detail.is_original_sender && (
                      <div>
                        <label style={labelStyle}>用户信息模板</label>
                        <input
                          value={detail.userinfo_template ?? ''}
                          onBlur={e => saveField('userinfo_template', e.target.value)}
                          onChange={e => setDetail(prev => prev ? { ...prev, userinfo_template: e.target.value } : prev)}
                          style={inputStyle}
                        />
                      </div>
                    )}
                    {detail.is_original_time && (
                      <div>
                        <label style={labelStyle}>时间模板</label>
                        <input
                          value={detail.time_template ?? ''}
                          onBlur={e => saveField('time_template', e.target.value)}
                          onChange={e => setDetail(prev => prev ? { ...prev, time_template: e.target.value } : prev)}
                          style={inputStyle}
                        />
                      </div>
                    )}
                    {detail.is_original_link && (
                      <div>
                        <label style={labelStyle}>原始链接模板</label>
                        <input
                          value={detail.original_link_template ?? ''}
                          onBlur={e => saveField('original_link_template', e.target.value)}
                          onChange={e => setDetail(prev => prev ? { ...prev, original_link_template: e.target.value } : prev)}
                          style={inputStyle}
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Tab 3: AI 配置 */}
            {activeTab === 'ai' && (
              <div className="card-glass" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
                <h3 style={sectionTitle}>AI 语言模型处理</h3>
                
                <FormSwitch
                  label="开启 AI 智能处理"
                  description="启用后每条消息在转发前均会提交给选定的 AI 大模型处理加工"
                  checked={detail.is_ai}
                  onCheckedChange={val => saveField('is_ai', val)}
                />

                {/* AI 参数联动条件显示 */}
                {detail.is_ai && (
                  <div style={{
                    display: 'flex', flexDirection: 'column', gap: 16, padding: '16px 0',
                    borderTop: '1px solid var(--border-light)'
                  }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                      <div>
                        <label style={labelStyle}>AI 语言模型</label>
                        <select
                          value={detail.ai_model ?? ''}
                          onChange={e => saveField('ai_model', e.target.value)}
                          style={inputStyle}
                        >
                          <option value="">(选择大模型模型)</option>
                          {modelsDict && Object.entries(modelsDict).map(([provider, list]) => (
                            <optgroup key={provider} label={provider.toUpperCase()}>
                              {list.map(m => <option key={m} value={m}>{m}</option>)}
                            </optgroup>
                          ))}
                        </select>
                      </div>
                      
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        <FormSwitch
                          label="AI 处理后再过滤关键字"
                          description="开启后消息将先经 AI 重写，再执行黑白名单关键字匹配"
                          checked={detail.is_keyword_after_ai}
                          onCheckedChange={val => saveField('is_keyword_after_ai', val)}
                        />
                        <FormSwitch
                          label="允许 AI 上传处理图片"
                          description="如果转发消息带图，开启此项将以 Multimodal 多模态向 AI 提交图片"
                          checked={detail.enable_ai_upload_image}
                          onCheckedChange={val => saveField('enable_ai_upload_image', val)}
                        />
                      </div>
                    </div>

                    <div>
                      <label style={labelStyle}>AI 系统提示词 (System Prompt)</label>
                      <textarea
                        value={detail.ai_prompt ?? ''}
                        onBlur={e => saveField('ai_prompt', e.target.value)}
                        onChange={e => setDetail(prev => prev ? { ...prev, ai_prompt: e.target.value } : prev)}
                        rows={8}
                        style={{ ...inputStyle, resize: 'vertical' }}
                        placeholder="请输入 AI 提示词规范，如：请把以下内容翻译成中文并总结要点..."
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Tab 4: 过滤规则 */}
            {activeTab === 'filter' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                
                {/* 关键字管理 */}
                <div className="card-glass" style={{ padding: 20 }}>
                  <h3 style={sectionTitle}>
                    过滤关键字 <span className="badge badge-primary" style={{ marginLeft: 6 }}>{keywords.length}</span>
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12, maxHeight: 250, overflowY: 'auto' }}>
                    {keywords.map(k => (
                      <div key={k.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', background: 'var(--bg-base)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: 13 }}>
                        <span style={{ flex: 1, color: 'var(--text-primary)', fontFamily: k.is_regex ? 'monospace' : 'inherit' }}>{k.keyword}</span>
                        {k.is_regex && <span className="badge badge-warn">正则表达式</span>}
                        <span className={`badge ${k.is_blacklist ? 'badge-danger' : 'badge-success'}`}>{k.is_blacklist ? '黑名单' : '白名单'}</span>
                        <button onClick={() => delKeyword(k.id)} style={{ ...iconBtnStyle, padding: 3 }}><X size={12} /></button>
                      </div>
                    ))}
                    {keywords.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '8px 0' }}>暂无过滤关键字</div>}
                  </div>
                  
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: 8, background: 'var(--bg-surface-hover)', borderRadius: 'var(--radius-sm)' }}>
                    <input
                      value={newKw.keyword}
                      onChange={e => setNewKw(prev => ({ ...prev, keyword: e.target.value }))}
                      onKeyDown={e => e.key === 'Enter' && addKeyword()}
                      placeholder="输入关键字进行过滤匹配"
                      style={{ ...inputStyle, flex: 1 }}
                    />
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                      <input type="checkbox" checked={newKw.is_regex} onChange={e => setNewKw(prev => ({ ...prev, is_regex: e.target.checked }))} /> 正则模式
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                      <input type="checkbox" checked={newKw.is_blacklist} onChange={e => setNewKw(prev => ({ ...prev, is_blacklist: e.target.checked }))} /> 黑名单
                    </label>
                    <button onClick={addKeyword} style={{ ...btnStyle, display: 'flex', alignItems: 'center', gap: 4 }}><Plus size={14} /> 添加</button>
                  </div>
                </div>

                {/* 替换规则 */}
                <div className="card-glass" style={{ padding: 20 }}>
                  <h3 style={sectionTitle}>
                    文本替换规则 <span className="badge badge-primary" style={{ marginLeft: 6 }}>{replaceRules.length}</span>
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12, maxHeight: 250, overflowY: 'auto' }}>
                    {replaceRules.map(r => (
                      <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', background: 'var(--bg-base)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: 13 }}>
                        <code style={{ color: '#f59e0b', fontFamily: 'monospace' }}>{r.pattern}</code>
                        <span style={{ color: 'var(--text-muted)' }}>→</span>
                        <span style={{ color: 'var(--text-primary)', flex: 1 }}>{r.content || '(直接删除匹配内容)'}</span>
                        <button onClick={() => delReplace(r.id)} style={{ ...iconBtnStyle, padding: 3 }}><X size={12} /></button>
                      </div>
                    ))}
                    {replaceRules.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '8px 0' }}>暂无替换规则</div>}
                  </div>
                  
                  <div style={{ display: 'flex', gap: 8, padding: 8, background: 'var(--bg-surface-hover)', borderRadius: 'var(--radius-sm)' }}>
                    <input value={newRR.pattern} onChange={e => setNewRR(prev => ({ ...prev, pattern: e.target.value }))} placeholder="匹配正则 (如 @[a-zA-Z0-9_]+)" style={{ ...inputStyle, flex: 1 }} />
                    <input value={newRR.content} onChange={e => setNewRR(prev => ({ ...prev, content: e.target.value }))} placeholder="替换为 (留空代表直接删除匹配内容)" style={{ ...inputStyle, flex: 1 }} />
                    <button onClick={addReplace} style={{ ...btnStyle, display: 'flex', alignItems: 'center', gap: 4 }}><Plus size={14} /> 添加</button>
                  </div>
                </div>
              </div>
            )}

            {/* Tab 5: 推送 & 总结 */}
            {activeTab === 'push' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                
                {/* 定时总结 */}
                <div className="card-glass" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <h3 style={sectionTitle}>AI 定时消息总结</h3>
                  <FormSwitch
                    label="启用 AI 定时消息总结"
                    description="定时将来源聊天室的消息汇聚，并生成 AI 智能摘要在目标频道发布"
                    checked={detail.is_summary}
                    onCheckedChange={val => saveField('is_summary', val)}
                  />

                  {detail.is_summary && (
                    <div style={{
                      display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 12,
                      borderTop: '1px solid var(--border-light)'
                    }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 3fr', gap: 16 }}>
                        <div>
                          <label style={labelStyle}>每日总结发送时间</label>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '6px 0' }}>
                            {timeList.map(t => (
                              <span
                                key={t}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: 4,
                                  padding: '3px 8px',
                                  background: 'var(--bg-surface-active)',
                                  border: '1px solid var(--border-light)',
                                  borderRadius: 12,
                                  fontSize: 12,
                                  color: 'var(--text-primary)',
                                }}
                              >
                                {t}
                                <button
                                  type="button"
                                  onClick={() => handleRemoveTime(t)}
                                  style={{
                                    background: 'none',
                                    border: 'none',
                                    cursor: 'pointer',
                                    color: 'var(--text-muted)',
                                    padding: 0,
                                    display: 'flex',
                                    alignItems: 'center',
                                  }}
                                  title="移除此时刻"
                                >
                                  <X size={10} />
                                </button>
                              </span>
                            ))}
                            {timeList.length === 0 && (
                              <span style={{ fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic' }}>
                                (未配置时间，请在下方添加)
                              </span>
                            )}
                          </div>
                          
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
                            <input
                              type="time"
                              value={newTimeInput}
                              onChange={e => setNewTimeInput(e.target.value)}
                              style={{ ...inputStyle, width: 'auto', padding: '4px 8px', height: 30 }}
                            />
                            <button
                              type="button"
                              onClick={handleAddTime}
                              style={{
                                ...btnStyle,
                                padding: '4px 10px',
                                height: 30,
                                fontSize: 12,
                                display: 'flex',
                                alignItems: 'center',
                                gap: 2,
                              }}
                            >
                              <Plus size={12} /> 添加时间
                            </button>
                          </div>
                          <span style={{ ...descStyle, marginTop: 4 }}>支持添加多个触发时刻</span>
                        </div>
                        <div>
                          <FormSwitch
                            label="置顶总结消息"
                            description="总结成功发布至目标群组/频道后，自动将其执行 Telegram 置顶操作"
                            checked={detail.is_top_summary}
                            onCheckedChange={val => saveField('is_top_summary', val)}
                          />
                        </div>
                      </div>

                      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                        <div>
                          <label style={labelStyle}>📡 跨市场情报简报 Prompt</label>
                          <textarea
                            value={detail.summary_prompt ?? ''}
                            onBlur={e => saveField('summary_prompt', e.target.value)}
                            onChange={e => setDetail(prev => prev ? { ...prev, summary_prompt: e.target.value } : prev)}
                            rows={6}
                            style={{ ...inputStyle, resize: 'vertical' }}
                            placeholder="配置每日简报总结的格式指南..."
                          />
                        </div>
                        <div>
                          <label style={labelStyle}>☀️ 早安老铁 / 推文工厂 Prompt</label>
                          <textarea
                            value={detail.summary_prompt_b ?? ''}
                            onBlur={e => saveField('summary_prompt_b', e.target.value)}
                            onChange={e => setDetail(prev => prev ? { ...prev, summary_prompt_b: e.target.value } : prev)}
                            rows={6}
                            style={{ ...inputStyle, resize: 'vertical' }}
                            placeholder="留空则使用系统默认的推文工厂 Prompt..."
                          />
                          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, display: 'block' }}>留空时将自动使用 constants.py 中的默认 WRITING_PROMPT_B</span>
                        </div>
                        <div>
                          <label style={labelStyle}>🏹 信息差快报 Prompt</label>
                          <textarea
                            value={detail.summary_prompt_d ?? ''}
                            onBlur={e => saveField('summary_prompt_d', e.target.value)}
                            onChange={e => setDetail(prev => prev ? { ...prev, summary_prompt_d: e.target.value } : prev)}
                            rows={6}
                            style={{ ...inputStyle, resize: 'vertical' }}
                            placeholder="留空则使用系统默认的信息差快报 Prompt..."
                          />
                          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, display: 'block' }}>留空时将自动使用 constants.py 中的默认 WRITING_PROMPT_D</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* 推送控制与通道管理 */}
                <div className="card-glass" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <h3 style={sectionTitle}>Apprise 第三方外部推送</h3>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <FormSwitch
                      label="启用第三方推送通道"
                      description="开启后消息将并行通过 Apprise 组件推送至您的外部通知系统"
                      checked={detail.enable_push}
                      onCheckedChange={val => saveField('enable_push', val)}
                    />
                    <FormSwitch
                      label="仅执行推送通道"
                      description="开启后消息只推送至配置的第三方通道，将不会转发至目标 Telegram 频道"
                      checked={detail.enable_only_push}
                      onCheckedChange={val => saveField('enable_only_push', val)}
                    />
                  </div>

                  {/* 通道添加与列表联动 */}
                  {detail.enable_push && (
                    <div style={{
                      display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 12,
                      borderTop: '1px solid var(--border-light)'
                    }}>
                      <h4 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                        外部推送配置通道列表 <span className="badge badge-primary" style={{ marginLeft: 6 }}>{pushConfigs.length}</span>
                      </h4>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 150, overflowY: 'auto' }}>
                        {pushConfigs.map(p => (
                          <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', background: 'var(--bg-base)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: 13 }}>
                            <div className={`health-dot ${p.enable_push_channel ? 'active' : 'danger'}`} />
                            <span style={{ flex: 1, color: 'var(--text-primary)', fontFamily: 'monospace' }}>{p.push_channel}</span>
                            <span className="badge">{p.media_send_mode}</span>
                            <button onClick={() => delPush(p.id)} style={{ ...iconBtnStyle, padding: 3 }}><X size={12} /></button>
                          </div>
                        ))}
                        {pushConfigs.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '4px 0' }}>暂无推送通道</div>}
                      </div>

                      <div style={{ display: 'flex', gap: 8, padding: 8, background: 'var(--bg-surface-hover)', borderRadius: 'var(--radius-sm)' }}>
                        <input
                          value={newPC.push_channel}
                          onChange={e => setNewPC(prev => ({ ...prev, push_channel: e.target.value }))}
                          placeholder="Apprise 格式 URL，如 ntfy://ntfy.sh/your-topic"
                          style={{ ...inputStyle, flex: 1 }}
                        />
                        <select
                          value={newPC.media_send_mode}
                          onChange={e => setNewPC(prev => ({ ...prev, media_send_mode: e.target.value }))}
                          style={{ ...inputStyle, width: 120 }}
                        >
                          <option value="Single">单条推送</option>
                          <option value="Group">合并推送</option>
                        </select>
                        <button onClick={addPush} style={{ ...btnStyle, display: 'flex', alignItems: 'center', gap: 4 }}><Plus size={14} /> 添加</button>
                      </div>
                    </div>
                  )}
                </div>

              </div>
            )}

          </div>
        )}
      </div>

      {/* ── 新建转发规则 弹窗 ────────────────── */}
      <Dialog.Root open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)', zIndex: 1000 }} />
          <Dialog.Content style={{
            position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
            width: '90%', maxWidth: 480,
            background: 'var(--bg-surface)', borderRadius: 'var(--radius-lg)', padding: 24,
            border: '1px solid var(--border-medium)', zIndex: 1001,
            boxShadow: '0 20px 25px -5px rgba(0,0,0,0.3), 0 10px 10px -5px rgba(0,0,0,0.2)'
          }}>
            <Dialog.Title style={{ margin: '0 0 8px', fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
              新建转发规则
            </Dialog.Title>
            <Dialog.Description style={{ margin: '0 0 20px', fontSize: 13, color: 'var(--text-secondary)' }}>
              创建一条从源频道自动转发/同步到目标群组的转发规则。
            </Dialog.Description>

            <form onSubmit={handleCreateRule} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <label style={labelStyle}>源频道 (消息来源) *</label>
                <select
                  value={sourceChatId}
                  onChange={e => setSourceChatId(e.target.value ? Number(e.target.value) : '')}
                  required
                  style={inputStyle}
                >
                  <option value="">(选择源频道/群组)</option>
                  {chats?.map(c => (
                    <option key={c.id} value={c.id}>
                      {c.name || '未命名'} ({c.telegram_chat_id})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={labelStyle}>目标群组 (接收群组) *</label>
                <select
                  value={targetChatId}
                  onChange={e => setTargetChatId(e.target.value ? Number(e.target.value) : '')}
                  required
                  style={inputStyle}
                >
                  <option value="">(选择目标频道/群组)</option>
                  {chats?.map(c => (
                    <option key={c.id} value={c.id}>
                      {c.name || '未命名'} ({c.telegram_chat_id})
                    </option>
                  ))}
                </select>
              </div>

              {createError && (
                <div style={{
                  display: 'flex', gap: 8, padding: 12, borderRadius: 'var(--radius-sm)',
                  background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', border: '1px solid rgba(239,68,68,0.3)',
                  fontSize: 13
                }}>
                  <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>创建失败</strong>
                    <div style={{ fontSize: 12, opacity: 0.8, marginTop: 2 }}>{createError}</div>
                  </div>
                </div>
              )}

              {/* 弹窗底部操作 */}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8, paddingTop: 16, borderTop: '1px solid var(--border-light)' }}>
                <Dialog.Close asChild>
                  <button type="button" disabled={creating} style={{ ...btnStyle, background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}>取消</button>
                </Dialog.Close>
                <button type="submit" disabled={creating || !sourceChatId || !targetChatId} style={btnStyle}>
                  {creating ? '创建中...' : '确认创建'}
                </button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

        {/* 同步选择弹窗 */}
        {showSyncModal && (
          <div style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)'
          }}>
            <div className="card-glass" style={{
              padding: 24, minWidth: 420, maxWidth: 500,
              display: 'flex', flexDirection: 'column', gap: 16
            }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
                🚀 选择要同步的配置项
              </h3>
              <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
                仅勾选的配置将被同步到所有其他频道规则，未勾选的项不会被覆盖。
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {Object.entries(syncFieldGroups).map(([key, { label }]) => (
                  <label key={key} style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                    background: syncFields[key] ? 'var(--bg-surface-hover)' : 'transparent',
                    borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)',
                    cursor: 'pointer', fontSize: 13, color: 'var(--text-primary)',
                    transition: 'background 0.15s'
                  }}>
                    <input
                      type="checkbox"
                      checked={syncFields[key]}
                      onChange={e => setSyncFields(prev => ({ ...prev, [key]: e.target.checked }))}
                      style={{ accentColor: 'var(--color-primary)' }}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
                <button
                  onClick={() => setShowSyncModal(false)}
                  style={{
                    padding: '6px 16px', borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)', background: 'var(--bg-surface)',
                    color: 'var(--text-primary)', cursor: 'pointer', fontSize: 13
                  }}
                >取消</button>
                <button
                  onClick={executeSyncToAll}
                  style={{
                    padding: '6px 16px', borderRadius: 'var(--radius-sm)',
                    border: 'none', background: 'var(--color-primary)',
                    color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 600
                  }}
                >确认同步</button>
              </div>
            </div>
          </div>
        )}
    </div>
  )
}

// ── 辅助 Style 定义 ──────────────────────────────
const sectionTitle: React.CSSProperties = {
  margin: '0 0 16px',
  fontSize: 14,
  fontWeight: 600,
  color: 'var(--text-primary)',
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 13,
  fontWeight: 600,
  color: 'var(--text-primary)',
  marginBottom: 2
}

const descStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 11,
  color: 'var(--text-muted)',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-base)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  fontSize: 13,
  outline: 'none',
  boxSizing: 'border-box',
}

const btnStyle: React.CSSProperties = {
  padding: '8px 16px',
  background: 'var(--color-primary)',
  color: '#fff',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  cursor: 'pointer',
  fontSize: 13,
  fontWeight: 500,
}

const iconBtnStyle: React.CSSProperties = {
  background: 'var(--bg-surface-hover)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)',
  padding: 6,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  color: 'var(--text-secondary)',
}
