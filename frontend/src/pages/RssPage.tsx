import { useState } from 'react'
import useSWR from 'swr'
import * as Dialog from '@radix-ui/react-dialog'
import { Plus, Edit, Trash2, Copy, Check, X, ToggleLeft, ToggleRight, ChevronDown, ChevronUp, Play, Beaker } from 'lucide-react'
import { fetcher, rssApi, patternsApi, regexApi } from '../api'
import type { RuleListItem, RssConfig, RssPattern } from '../types'

const DEFAULT_AI_PROMPT = '请按照以下要求从消息文本中提取标题和正文内容：\n1. 若文本中已有明确标题，则直接提取；否则根据正文生成合适标题；\n2. 若原文为 Markdown 格式，请先转换为 HTML 格式后再提取；\n3. 最终输出必须为标准 JSON 格式，如下：\n{ "title": "提取或生成的标题", "content": "提取后的正文内容（HTML 格式），要去掉标题" }\n请仅返回上述 JSON 格式，不得包含其他任何内容或代码块。以下是消息内容：'

interface FormState {
  rule_id: number | ''
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
}

const emptyForm: FormState = {
  rule_id: '', rule_title: '', rule_description: '', language: 'zh-CN', max_items: 50,
  is_auto_title: false, is_auto_content: false, is_ai_extract: false,
  ai_extract_prompt: DEFAULT_AI_PROMPT, is_auto_markdown_to_html: false,
  enable_custom_title_pattern: false, enable_custom_content_pattern: false,
}

export default function RssPage() {
  const { data: configs, mutate: mutateConfigs } = useSWR<RssConfig[]>('/rss-configs', fetcher)
  const { data: rules } = useSWR<RuleListItem[]>('/rules', fetcher)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [patterns, setPatterns] = useState<RssPattern[]>([])
  const [newPattern, setNewPattern] = useState({ pattern: '', pattern_type: 'title' as 'title' | 'content', priority: 0 })
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)

  // 正则测试
  const [showRegexTest, setShowRegexTest] = useState(false)
  const [regexTest, setRegexTest] = useState({ pattern: '', test_text: '', pattern_type: 'title' })
  const [regexResult, setRegexResult] = useState<any>(null)

  // 打开弹窗
  const openCreate = () => {
    setEditId(null)
    setForm(emptyForm)
    setPatterns([])
    setDialogOpen(true)
  }

  const openEdit = async (config: RssConfig) => {
    setEditId(config.id)
    setForm({
      rule_id: config.rule_id,
      rule_title: config.rule_title,
      rule_description: config.rule_description,
      language: config.language,
      max_items: config.max_items,
      is_auto_title: config.is_auto_title,
      is_auto_content: config.is_auto_content,
      is_ai_extract: config.is_ai_extract,
      ai_extract_prompt: config.ai_extract_prompt || DEFAULT_AI_PROMPT,
      is_auto_markdown_to_html: config.is_auto_markdown_to_html,
      enable_custom_title_pattern: config.enable_custom_title_pattern,
      enable_custom_content_pattern: config.enable_custom_content_pattern,
    })
    try {
      const p = await patternsApi.getByConfig(config.id)
      setPatterns(p)
    } catch { setPatterns([]) }
    setDialogOpen(true)
  }

  // 复制配置
  const copyFrom = (sourceId: number) => {
    const src = configs?.find(c => c.id === sourceId)
    if (!src) return
    setForm(prev => ({
      ...prev,
      rule_title: src.rule_title,
      rule_description: src.rule_description,
      language: src.language,
      max_items: src.max_items,
      is_auto_title: src.is_auto_title,
      is_auto_content: src.is_auto_content,
      is_ai_extract: src.is_ai_extract,
      ai_extract_prompt: src.ai_extract_prompt || DEFAULT_AI_PROMPT,
      is_auto_markdown_to_html: src.is_auto_markdown_to_html,
      enable_custom_title_pattern: src.enable_custom_title_pattern,
      enable_custom_content_pattern: src.enable_custom_content_pattern,
    }))
  }

  // 保存
  const saveConfig = async () => {
    if (!form.rule_id) return
    setSaving(true)
    try {
      let configId: number
      if (editId) {
        await rssApi.update(editId, form as any)
        configId = editId
      } else {
        const res = await rssApi.create(form as any)
        configId = res.data.id
      }
      // 保存 patterns: 先清空再重建
      if (patterns.length > 0) {
        try { await patternsApi.deleteAll(configId) } catch {}
        for (const p of patterns) {
          await patternsApi.create(configId, { pattern: p.pattern, pattern_type: p.pattern_type, priority: p.priority })
        }
      }
      mutateConfigs()
      setDialogOpen(false)
    } catch (e: any) {
      alert(e?.response?.data?.detail || '保存失败')
    } finally { setSaving(false) }
  }

  // 删除
  const doDelete = async (id: number) => {
    try { await rssApi.delete(id); mutateConfigs() } catch (e: any) { alert(e?.response?.data?.detail || '删除失败') }
    setDeleteConfirm(null)
  }

  // Toggle
  const doToggle = async (id: number) => {
    try { await rssApi.toggle(id); mutateConfigs() } catch (e: any) { alert('操作失败') }
  }

  // 复制 RSS 链接
  const copyLink = (ruleId: number) => {
    const link = `${window.location.origin}/rss/feed/${ruleId}`
    navigator.clipboard.writeText(link)
    setCopiedId(ruleId)
    setTimeout(() => setCopiedId(null), 2000)
  }

  // 添加 pattern (本地)
  const addPattern = () => {
    if (!newPattern.pattern.trim()) return
    setPatterns(prev => [...prev, { ...newPattern, id: -(Date.now()) }])
    setNewPattern({ pattern: '', pattern_type: 'title', priority: 0 })
  }

  // 正则测试
  const runRegexTest = async () => {
    if (!regexTest.pattern || !regexTest.test_text) return
    try {
      const res = await regexApi.test(regexTest)
      setRegexResult(res)
    } catch (e: any) { setRegexResult({ success: false, message: e.message }) }
  }

  const applyRegex = () => {
    if (regexResult?.success && regexResult?.matched) {
      setNewPattern(prev => ({ ...prev, pattern: regexTest.pattern, pattern_type: regexTest.pattern_type as any }))
      setShowRegexTest(false)
    }
  }

  // 自动填充标题
  const autoFillTitle = () => {
    const rule = rules?.find(r => r.id === form.rule_id)
    if (rule) setForm(prev => ({ ...prev, rule_title: `来自 ${rule.source_chat_name}` }))
  }

  const titlePatterns = patterns.filter(p => p.pattern_type === 'title')
  const contentPatterns = patterns.filter(p => p.pattern_type === 'content')
  const configuredRuleIds = configs?.map(c => c.rule_id) ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, height: '100%', overflow: 'auto' }}>
      {/* ── Header ─────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>RSS 配置管理</h2>
        <button onClick={openCreate} style={{ ...primaryBtn, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Plus size={16} /> 新建配置
        </button>
      </div>

      {/* ── 配置表格 ───────────────────────── */}
      <div className="card-glass" style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: 'var(--bg-surface-hover)' }}>
              {['ID', '规则', '标题', '描述', '状态', 'RSS 链接', '操作'].map(h => (
                <th key={h} style={{ padding: '10px 14px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500, fontSize: 12 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!configs || configs.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>暂无 RSS 配置，点击"新建配置"创建</td></tr>
            ) : configs.map(c => (
              <tr key={c.id} style={{ borderBottom: '1px solid var(--border-light)' }}>
                <td style={tdStyle}>{c.id}</td>
                <td style={tdStyle}>
                  <span className="badge">{c.rule_id}</span>
                  {c.source_chat_name && <span style={{ marginLeft: 6, fontSize: 12, color: 'var(--text-muted)' }}>{c.source_chat_name}</span>}
                </td>
                <td style={tdStyle}>{c.rule_title || '—'}</td>
                <td style={{ ...tdStyle, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.rule_description || '—'}</td>
                <td style={tdStyle}>
                  <span className={`badge ${c.enable_rss ? 'badge-success' : 'badge-danger'}`}>
                    {c.enable_rss ? '启用' : '禁用'}
                  </span>
                </td>
                <td style={tdStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <code style={{ fontSize: 11, color: 'var(--text-muted)' }}>/rss/feed/{c.rule_id}</code>
                    <button onClick={() => copyLink(c.rule_id)} style={iconBtn} title="复制链接">
                      {copiedId === c.rule_id ? <Check size={12} color="var(--color-success)" /> : <Copy size={12} />}
                    </button>
                  </div>
                </td>
                <td style={tdStyle}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button onClick={() => openEdit(c)} style={iconBtn} title="编辑"><Edit size={14} /></button>
                    <button onClick={() => doToggle(c.id)} style={iconBtn} title={c.enable_rss ? '禁用' : '启用'}>
                      {c.enable_rss ? <ToggleRight size={14} color="var(--color-success)" /> : <ToggleLeft size={14} />}
                    </button>
                    {deleteConfirm === c.id ? (
                      <>
                        <button onClick={() => doDelete(c.id)} style={{ ...iconBtn, color: '#ef4444' }} title="确认删除"><Check size={14} /></button>
                        <button onClick={() => setDeleteConfirm(null)} style={iconBtn} title="取消"><X size={14} /></button>
                      </>
                    ) : (
                      <button onClick={() => setDeleteConfirm(c.id)} style={iconBtn} title="删除"><Trash2 size={14} /></button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── 创建/编辑 弹窗 ────────────────── */}
      <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000 }} />
          <Dialog.Content style={{
            position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
            width: '90%', maxWidth: 720, maxHeight: '85vh', overflow: 'auto',
            background: 'var(--bg-surface)', borderRadius: 'var(--radius-lg)', padding: 24,
            border: '1px solid var(--border-light)', zIndex: 1001,
          }}>
            <Dialog.Title style={{ margin: '0 0 20px', fontSize: 17, fontWeight: 600, color: 'var(--text-primary)' }}>
              {editId ? '编辑 RSS 配置' : '创建 RSS 配置'}
            </Dialog.Title>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* 规则选择 + 复制 */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={lbl}>关联规则 *</label>
                  <select value={form.rule_id} onChange={e => setForm(prev => ({ ...prev, rule_id: Number(e.target.value) }))} disabled={!!editId} style={inp}>
                    <option value="">选择规则...</option>
                    {rules?.map(r => (
                      <option key={r.id} value={r.id} disabled={!editId && configuredRuleIds.includes(r.id)}>
                        {r.id} - {r.source_chat_name} → {r.target_chat_name} {configuredRuleIds.includes(r.id) ? '(已配置)' : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={lbl}>复制已有配置</label>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <select id="copySource" style={{ ...inp, flex: 1 }}>
                      <option value="">选择配置...</option>
                      {configs?.map(c => <option key={c.id} value={c.id}>{c.id} - {c.rule_title || `规则${c.rule_id}`}</option>)}
                    </select>
                    <button onClick={() => { const el = document.getElementById('copySource') as HTMLSelectElement; if (el.value) copyFrom(Number(el.value)) }} style={secondaryBtn}>复制</button>
                  </div>
                </div>
              </div>

              {/* 标题/描述 */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={lbl}>标题</label>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <input value={form.rule_title} onChange={e => setForm(prev => ({ ...prev, rule_title: e.target.value }))} style={{ ...inp, flex: 1 }} />
                    <button onClick={autoFillTitle} style={secondaryBtn}>自动</button>
                  </div>
                </div>
                <div>
                  <label style={lbl}>语言</label>
                  <input value={form.language} onChange={e => setForm(prev => ({ ...prev, language: e.target.value }))} style={inp} />
                </div>
              </div>
              <div>
                <label style={lbl}>描述</label>
                <textarea value={form.rule_description} onChange={e => setForm(prev => ({ ...prev, rule_description: e.target.value }))} rows={2} style={{ ...inp, resize: 'vertical' }} />
              </div>
              <div style={{ width: 120 }}>
                <label style={lbl}>最大条目数</label>
                <input type="number" value={form.max_items} onChange={e => setForm(prev => ({ ...prev, max_items: Number(e.target.value) }))} style={inp} />
              </div>

              {/* 内容处理 */}
              <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: 16 }}>
                <h4 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-primary)' }}>内容处理</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }}>
                  {[
                    { key: 'is_ai_extract', label: 'AI 提取标题和内容' },
                    { key: 'is_auto_title', label: '自动提取标题' },
                    { key: 'is_auto_content', label: '自动提取内容' },
                    { key: 'is_auto_markdown_to_html', label: 'Markdown → HTML' },
                  ].map(f => (
                    <label key={f.key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-secondary)', cursor: 'pointer' }}>
                      <input type="checkbox" checked={(form as any)[f.key]}
                        onChange={e => {
                          const val = e.target.checked
                          setForm(prev => {
                            const next = { ...prev, [f.key]: val }
                            // AI 提取互斥
                            if (f.key === 'is_ai_extract' && val) {
                              next.is_auto_title = false; next.is_auto_content = false
                            }
                            if ((f.key === 'is_auto_title' || f.key === 'is_auto_content') && val) {
                              next.is_ai_extract = false
                            }
                            return next
                          })
                        }}
                        style={{ accentColor: 'var(--color-primary)' }}
                      />
                      {f.label}
                    </label>
                  ))}
                </div>
              </div>

              {/* AI Prompt */}
              {form.is_ai_extract && (
                <div>
                  <label style={lbl}>AI 提取提示词</label>
                  <textarea value={form.ai_extract_prompt} onChange={e => setForm(prev => ({ ...prev, ai_extract_prompt: e.target.value }))} rows={4} style={{ ...inp, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }} />
                </div>
              )}

              {/* 正则配置 */}
              <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: 16 }}>
                <h4 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-primary)' }}>正则表达式配置</h4>
                <div style={{ display: 'flex', gap: 16, marginBottom: 12 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)', cursor: 'pointer' }}>
                    <input type="checkbox" checked={form.enable_custom_title_pattern} onChange={e => setForm(prev => ({ ...prev, enable_custom_title_pattern: e.target.checked }))} style={{ accentColor: 'var(--color-primary)' }} />
                    自定义标题模式
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)', cursor: 'pointer' }}>
                    <input type="checkbox" checked={form.enable_custom_content_pattern} onChange={e => setForm(prev => ({ ...prev, enable_custom_content_pattern: e.target.checked }))} style={{ accentColor: 'var(--color-primary)' }} />
                    自定义内容模式
                  </label>
                </div>

                {/* 添加模式 */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                  <input value={newPattern.pattern} onChange={e => setNewPattern(prev => ({ ...prev, pattern: e.target.value }))} placeholder="正则表达式" style={{ ...inp, flex: 1, fontFamily: 'monospace' }} />
                  <select value={newPattern.pattern_type} onChange={e => setNewPattern(prev => ({ ...prev, pattern_type: e.target.value as any }))} style={{ ...inp, width: 100 }}>
                    <option value="title">标题</option>
                    <option value="content">内容</option>
                  </select>
                  <input type="number" value={newPattern.priority} onChange={e => setNewPattern(prev => ({ ...prev, priority: Number(e.target.value) }))} style={{ ...inp, width: 60 }} title="优先级" />
                  <button onClick={addPattern} style={secondaryBtn}>添加</button>
                </div>

                {/* 模式列表 */}
                {[{ label: '标题模式', list: titlePatterns }, { label: '内容模式', list: contentPatterns }].map(group => (
                  group.list.length > 0 && (
                    <div key={group.label} style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>{group.label}</div>
                      {group.list.map((p, i) => (
                        <div key={p.id || i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', background: 'var(--bg-base)', borderRadius: 'var(--radius-sm)', marginBottom: 4, fontSize: 13 }}>
                          <code style={{ flex: 1, color: '#f59e0b' }}>{p.pattern}</code>
                          <span className="badge" style={{ fontSize: 11 }}>P{p.priority}</span>
                          <button onClick={() => setPatterns(prev => prev.filter(pp => pp !== p))} style={{ ...iconBtn, padding: 2 }}><X size={12} /></button>
                        </div>
                      ))}
                    </div>
                  )
                ))}

                {/* 正则测试 */}
                <button onClick={() => setShowRegexTest(!showRegexTest)} style={{ ...secondaryBtn, display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                  <Beaker size={12} /> 正则测试 {showRegexTest ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </button>
                {showRegexTest && (
                  <div style={{ marginTop: 10, padding: 12, background: 'var(--bg-base)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)' }}>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                      <input value={regexTest.pattern} onChange={e => setRegexTest(prev => ({ ...prev, pattern: e.target.value }))} placeholder="正则表达式" style={{ ...inp, flex: 1, fontFamily: 'monospace' }} />
                      <select value={regexTest.pattern_type} onChange={e => setRegexTest(prev => ({ ...prev, pattern_type: e.target.value }))} style={{ ...inp, width: 80 }}>
                        <option value="title">标题</option>
                        <option value="content">内容</option>
                      </select>
                    </div>
                    <textarea value={regexTest.test_text} onChange={e => setRegexTest(prev => ({ ...prev, test_text: e.target.value }))} placeholder="测试文本..." rows={3} style={{ ...inp, resize: 'vertical', marginBottom: 8 }} />
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button onClick={runRegexTest} style={{ ...primaryBtn, fontSize: 12, padding: '4px 14px', display: 'flex', alignItems: 'center', gap: 4 }}><Play size={12} /> 测试</button>
                      {regexResult?.success && regexResult?.matched && regexResult?.has_groups && (
                        <button onClick={applyRegex} style={{ ...secondaryBtn, fontSize: 12, padding: '4px 14px' }}>应用此正则</button>
                      )}
                    </div>
                    {regexResult && (
                      <div style={{
                        marginTop: 8, padding: 8, borderRadius: 'var(--radius-sm)', fontSize: 13,
                        background: regexResult.matched && regexResult.has_groups ? 'rgba(16,185,129,0.1)' : regexResult.matched ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                        color: regexResult.matched && regexResult.has_groups ? '#10b981' : regexResult.matched ? '#f59e0b' : '#ef4444',
                        border: `1px solid ${regexResult.matched && regexResult.has_groups ? 'rgba(16,185,129,0.3)' : regexResult.matched ? 'rgba(245,158,11,0.3)' : 'rgba(239,68,68,0.3)'}`,
                      }}>
                        {regexResult.message}
                        {regexResult.extracted && <><br /><strong>提取内容: </strong>{regexResult.extracted}</>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* 底部按钮 */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border-light)' }}>
              <Dialog.Close asChild>
                <button style={secondaryBtn}>取消</button>
              </Dialog.Close>
              <button onClick={saveConfig} disabled={saving || !form.rule_id} style={primaryBtn}>
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}

// ── 样式 ──────────────────────────────
const tdStyle: React.CSSProperties = { padding: '10px 14px', color: 'var(--text-secondary)' }
const lbl: React.CSSProperties = { display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }
const inp: React.CSSProperties = { width: '100%', padding: '7px 10px', background: 'var(--bg-base)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 13, outline: 'none', boxSizing: 'border-box' as const }
const primaryBtn: React.CSSProperties = { padding: '8px 20px', background: 'var(--brand-gradient)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontSize: 13, fontWeight: 600 }
const secondaryBtn: React.CSSProperties = { padding: '6px 14px', background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 13, cursor: 'pointer' }
const iconBtn: React.CSSProperties = { background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', padding: 5, cursor: 'pointer', display: 'flex', alignItems: 'center', color: 'var(--text-secondary)' }
