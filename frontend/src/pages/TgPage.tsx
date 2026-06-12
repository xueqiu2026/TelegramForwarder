import { useState } from 'react'
import useSWR from 'swr'
import * as Dialog from '@radix-ui/react-dialog'
import { Search, Plus, Edit2, Trash2, Check, X, Send, AlertTriangle, HelpCircle, Copy } from 'lucide-react'
import { fetcher, chatsApi } from '../api'
import type { ChatDetail, Chat } from '../types'

export default function TgPage() {
  const { data: chats, mutate: mutateChats, error: loadError } = useSWR<ChatDetail[]>('/chats', fetcher)
  
  // 搜索过滤
  const [search, setSearch] = useState('')
  
  // 行内名称编辑
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [savingName, setSavingName] = useState(false)

  // 解析弹窗状态
  const [dialogOpen, setDialogOpen] = useState(false)
  const [link, setLink] = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolveError, setResolveError] = useState<string | null>(null)
  const [resolvedChat, setResolvedChat] = useState<Chat | null>(null)

  // 删除确认行 ID
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [deleting, setDeleting] = useState(false)
  
  // 复制提示
  const [copiedId, setCopiedId] = useState<number | null>(null)

  // 触发复制 ID
  const handleCopy = (id: number, text: string) => {
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  // 开始行内编辑
  const startEdit = (chat: ChatDetail) => {
    setEditingId(chat.id)
    setEditingName(chat.name || '')
  }

  // 保存行内编辑
  const saveName = async (id: number) => {
    if (!editingName.trim()) return
    setSavingName(true)
    try {
      await chatsApi.update(id, editingName.trim())
      setEditingId(null)
      mutateChats()
    } catch (err: any) {
      alert(err?.response?.data?.detail || '修改名称失败')
    } finally {
      setSavingName(false)
    }
  }

  // 执行删除
  const handleDelete = async (id: number) => {
    setDeleting(true)
    try {
      await chatsApi.delete(id)
      setDeleteConfirmId(null)
      mutateChats()
    } catch (err: any) {
      alert(err?.response?.data?.detail || '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  // 解析链接
  const handleResolve = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!link.trim()) return
    setResolving(true)
    setResolveError(null)
    setResolvedChat(null)
    try {
      const res = await chatsApi.resolve(link.trim())
      if (res.status === 'success' || res.chat) {
        setResolvedChat(res.chat)
        setLink('')
        mutateChats()
        // 1.5秒后自动关闭弹窗
        setTimeout(() => {
          setDialogOpen(false)
          setResolvedChat(null)
        }, 1500)
      } else {
        setResolveError('解析失败，请检查链接或确保该账号已加入对应频道')
      }
    } catch (err: any) {
      setResolveError(err?.response?.data?.detail || err?.message || '解析超时或通信失败')
    } finally {
      setResolving(false)
    }
  }

  // 数据列表过滤
  const filteredChats = chats?.filter(chat => {
    const s = search.toLowerCase()
    return (
      (chat.name && chat.name.toLowerCase().includes(s)) ||
      chat.telegram_chat_id.includes(s) ||
      chat.id.toString() === s
    )
  })

  return (
    <div style={{
      position: 'absolute',
      top: 24,
      bottom: 24,
      left: 24,
      right: 24,
      display: 'flex',
      flexDirection: 'column',
      gap: 20
    }}>
      
      {/* ── 头部与工具栏 ────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)', fontWeight: 600 }}>TG 频道与群组管理</h2>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-muted)' }}>展示并维护系统已绑定的所有 Telegram 实体</p>
        </div>
        
        <button 
          onClick={() => {
            setResolveError(null)
            setResolvedChat(null)
            setLink('')
            setDialogOpen(true)
          }} 
          style={{ ...primaryBtn, display: 'flex', alignItems: 'center', gap: 6 }}
        >
          <Plus size={16} /> 解析绑定新频道
        </button>
      </div>

      {/* ── 搜索过滤栏 ─────────────────────────── */}
      <div className="card-glass" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ position: 'relative', flex: 1, maxWidth: 360 }}>
          <Search size={16} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input
            type="text"
            placeholder="搜索频道名称或 Telegram ID..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ ...inp, paddingLeft: 32 }}
          />
          {search && (
            <button 
              onClick={() => setSearch('')}
              style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0 }}
            >
              <X size={14} />
            </button>
          )}
        </div>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          已绑定 {chats?.length || 0} 个频道/群组
        </span>
      </div>

      {/* ── 频道列表表格 ───────────────────────── */}
      <div className="card-glass" style={{ padding: 0, flex: 1, overflowY: 'auto', minHeight: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-surface)', zIndex: 1, boxShadow: '0 1px 0 var(--border-light)' }}>
            <tr style={{ background: 'var(--bg-surface-hover)' }}>
              {['ID', 'Telegram Chat ID', '频道名称', '转发状态/角色', '操作'].map(h => (
                <th key={h} style={{ padding: '12px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500, fontSize: 12 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loadError && (
              <tr>
                <td colSpan={5} style={{ padding: 30, textAlign: 'center', color: 'var(--color-danger)' }}>
                  ⚠️ 加载频道列表失败: {loadError.message}
                </td>
              </tr>
            )}
            {!chats && !loadError ? (
              <tr>
                <td colSpan={5} style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
                  <span className="spinner" style={{ marginRight: 8 }} /> 加载中...
                </td>
              </tr>
            ) : filteredChats?.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
                  没有找到匹配的频道/群组
                </td>
              </tr>
            ) : filteredChats?.map(c => {
              const inUse = c.source_rule_count > 0 || c.target_rule_count > 0
              const isEditing = editingId === c.id

              return (
                <tr key={c.id} style={{ borderBottom: '1px solid var(--border-light)', transition: 'background-color var(--transition-fast)' }} className="table-row-hover">
                  {/* ID */}
                  <td style={tdStyle} width="80">
                    <span className="badge" style={{ background: 'var(--bg-surface-active)', color: 'var(--text-secondary)' }}>{c.id}</span>
                  </td>

                  {/* Telegram Chat ID */}
                  <td style={tdStyle} width="220">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <code style={{ fontSize: 12, color: 'var(--text-muted)', background: 'var(--bg-base)', padding: '2px 6px', borderRadius: 'var(--radius-sm)' }}>
                        {c.telegram_chat_id}
                      </code>
                      <button onClick={() => handleCopy(c.id, c.telegram_chat_id)} style={miniIconBtn} title="复制 ID">
                        {copiedId === c.id ? <Check size={11} color="var(--color-success)" /> : <Copy size={11} />}
                      </button>
                    </div>
                  </td>

                  {/* 频道名称 (行内编辑) */}
                  <td style={tdStyle}>
                    {isEditing ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <input
                          type="text"
                          value={editingName}
                          onChange={e => setEditingName(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') saveName(c.id)
                            if (e.key === 'Escape') setEditingId(null)
                          }}
                          disabled={savingName}
                          autoFocus
                          style={{ ...inp, width: '100%', maxWidth: 260, padding: '4px 8px' }}
                        />
                        <button onClick={() => saveName(c.id)} disabled={savingName} style={actionConfirmBtn}>
                          {savingName ? '...' : <Check size={14} />}
                        </button>
                        <button onClick={() => setEditingId(null)} disabled={savingName} style={actionCancelBtn}>
                          <X size={14} />
                        </button>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span 
                          onDoubleClick={() => startEdit(c)} 
                          style={{ color: 'var(--text-primary)', fontWeight: 500, cursor: 'pointer' }}
                          title="双击进行编辑"
                        >
                          {c.name || '—'}
                        </span>
                        <button onClick={() => startEdit(c)} style={editBtn} title="编辑名称">
                          <Edit2 size={12} />
                        </button>
                      </div>
                    )}
                  </td>

                  {/* 状态与引用统计 */}
                  <td style={tdStyle} width="250">
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {c.source_rule_count > 0 && (
                        <span style={badgeSource}>
                          🟢 源 ({c.source_rule_count} 条规则)
                        </span>
                      )}
                      {c.target_rule_count > 0 && (
                        <span style={badgeTarget}>
                          🔵 目标 ({c.target_rule_count} 群组)
                        </span>
                      )}
                      {!inUse && (
                        <span style={badgeIdle}>
                          🟡 闲置 (无转发关联)
                        </span>
                      )}
                    </div>
                  </td>

                  {/* 操作 */}
                  <td style={tdStyle} width="120">
                    {inUse ? (
                      <button 
                        style={{ ...iconBtn, opacity: 0.4, cursor: 'not-allowed' }} 
                        disabled 
                        title="该频道已被转发规则引用，无法删除"
                      >
                        <Trash2 size={14} />
                      </button>
                    ) : deleteConfirmId === c.id ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <button 
                          onClick={() => handleDelete(c.id)} 
                          disabled={deleting}
                          style={{ ...iconBtn, color: 'var(--color-danger)', borderColor: 'rgba(239,68,68,0.2)' }} 
                          title="确认删除"
                        >
                          {deleting ? '...' : <Check size={14} />}
                        </button>
                        <button 
                          onClick={() => setDeleteConfirmId(null)} 
                          disabled={deleting}
                          style={iconBtn} 
                          title="取消"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ) : (
                      <button 
                        onClick={() => setDeleteConfirmId(c.id)} 
                        style={iconBtn} 
                        title="删除"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── 解析绑定新频道 弹窗 ────────────────── */}
      <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
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
              解析并绑定新频道
            </Dialog.Title>
            <Dialog.Description style={{ margin: '0 0 20px', fontSize: 13, color: 'var(--text-secondary)' }}>
              解析 Telegram 链接或用户名，成功后会自动录入系统频道库。
            </Dialog.Description>

            <form onSubmit={handleResolve} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <label style={lbl}>频道链接、邀请链接或用户名 *</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input
                    type="text"
                    placeholder="例如 @durov 或 https://t.me/durov"
                    value={link}
                    onChange={e => setLink(e.target.value)}
                    disabled={resolving || !!resolvedChat}
                    autoFocus
                    style={{ ...inp, flex: 1, fontFamily: 'monospace' }}
                  />
                  <button 
                    type="submit" 
                    disabled={resolving || !link.trim() || !!resolvedChat} 
                    style={primaryBtn}
                  >
                    {resolving ? <span className="spinner" /> : '开始解析'}
                  </button>
                </div>
              </div>

              {/* 常见帮助小贴士 */}
              <div style={{ display: 'flex', gap: 8, padding: '8px 12px', background: 'var(--bg-surface-hover)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)' }}>
                <HelpCircle size={16} color="var(--text-muted)" style={{ marginTop: 2, flexShrink: 0 }} />
                <span style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: '1.4' }}>
                  提示：如果目标是私有频道/群组，需要确保转发账号（即 sessions 中的账号）已经加入或拥有访问权限。
                </span>
              </div>

              {/* 解析成功渲染 */}
              {resolvedChat && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: 12, borderRadius: 'var(--radius-sm)',
                  background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '1px solid rgba(16,185,129,0.3)',
                  fontSize: 13, animation: 'fadeIn var(--transition-fast) forwards'
                }}>
                  <Send size={16} />
                  <div>
                    <strong>解析绑定成功！</strong>
                    <div style={{ fontSize: 12, opacity: 0.8, marginTop: 2 }}>
                      已绑定：{resolvedChat.name} (ID: {resolvedChat.telegram_chat_id})
                    </div>
                  </div>
                </div>
              )}

              {/* 解析失败渲染 */}
              {resolveError && (
                <div style={{
                  display: 'flex', gap: 8, padding: 12, borderRadius: 'var(--radius-sm)',
                  background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', border: '1px solid rgba(239,68,68,0.3)',
                  fontSize: 13
                }}>
                  <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>解析失败</strong>
                    <div style={{ fontSize: 12, opacity: 0.8, marginTop: 2 }}>{resolveError}</div>
                  </div>
                </div>
              )}

              {/* 弹窗底部操作 */}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8, paddingTop: 16, borderTop: '1px solid var(--border-light)' }}>
                <Dialog.Close asChild>
                  <button type="button" disabled={resolving} style={secondaryBtn}>关闭</button>
                </Dialog.Close>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

    </div>
  )
}

// ── 样式与布局定义 ──────────────────
const tdStyle: React.CSSProperties = { padding: '12px 16px', color: 'var(--text-secondary)', verticalAlign: 'middle' }
const lbl: React.CSSProperties = { display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }
const inp: React.CSSProperties = { width: '100%', padding: '8px 12px', background: 'var(--bg-base)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 13, outline: 'none', boxSizing: 'border-box' }

const primaryBtn: React.CSSProperties = { padding: '8px 16px', background: 'var(--brand-gradient)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontSize: 13, fontWeight: 600, transition: 'opacity var(--transition-fast)' }
const secondaryBtn: React.CSSProperties = { padding: '8px 16px', background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 13, cursor: 'pointer' }

const iconBtn: React.CSSProperties = { background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', padding: 6, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', transition: 'all var(--transition-fast)' }
const miniIconBtn: React.CSSProperties = { background: 'transparent', border: 'none', padding: 2, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', transition: 'color var(--transition-fast)' }
const editBtn: React.CSSProperties = { background: 'transparent', border: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', padding: 2 }

const actionConfirmBtn: React.CSSProperties = { background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: 'var(--radius-sm)', padding: '5px 8px', cursor: 'pointer', color: '#10b981', display: 'inline-flex', alignItems: 'center' }
const actionCancelBtn: React.CSSProperties = { background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', padding: '5px 8px', cursor: 'pointer', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center' }

// ── Badges ──
const badgeBase: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', padding: '3px 8px', borderRadius: '4px', fontSize: 11, fontWeight: 500 }
const badgeSource: React.CSSProperties = { ...badgeBase, background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '1px solid rgba(16,185,129,0.2)' }
const badgeTarget: React.CSSProperties = { ...badgeBase, background: 'rgba(59,130,246,0.1)', color: '#3b82f6', border: '1px solid rgba(59,130,246,0.2)' }
const badgeIdle: React.CSSProperties = { ...badgeBase, background: 'rgba(97,97,106,0.1)', color: 'var(--text-secondary)', border: '1px solid var(--border-light)' }
