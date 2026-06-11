import { useState, useEffect, useRef, useCallback } from 'react'
import useSWR from 'swr'
import { Search, ChevronLeft, ChevronRight, Copy, Check, Eye, EyeOff, Trash2, Activity, BookOpen, Shield, Radio } from 'lucide-react'
import { fetcher, summariesApi } from '../api'
import type { RuleListItem, SummaryItem } from '../types'

export default function MonitorPage() {
  // ── 数据 ──
  const { data: rules } = useSWR<RuleListItem[]>('/rules', fetcher)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [summaryData, setSummaryData] = useState<{ total: number; data: SummaryItem[] }>({ total: 0, data: [] })
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const limit = 10

  // 加载总结数据
  useEffect(() => {
    summariesApi.getAll(page, limit, search || undefined).then(setSummaryData).catch(console.error)
  }, [page, search])

  const doSearch = () => { setPage(1); setSearch(searchInput) }
  const totalPages = Math.ceil(summaryData.total / limit) || 1

  const copySummary = (text: string, id: number) => {
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  // ── WebSocket 日志 ──
  const [logs, setLogs] = useState<string[]>([])
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected' | 'reconnecting'>('disconnected')
  const logEndRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)

  const connectWs = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/ws/logs`)
    wsRef.current = ws
    ws.onopen = () => { setWsStatus('connected'); retryRef.current = 0 }
    ws.onmessage = (e) => {
      setLogs(prev => {
        const next = [...prev, e.data]
        return next.length > 500 ? next.slice(-500) : next
      })
    }
    ws.onclose = () => {
      setWsStatus('reconnecting')
      const delay = Math.min(3000 * Math.pow(2, retryRef.current), 30000)
      retryRef.current++
      setTimeout(connectWs, delay)
    }
    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => {
    connectWs()
    return () => { wsRef.current?.close() }
  }, [connectWs])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  const getLogClass = (line: string) => {
    if (line.includes('ERROR')) return 'log-entry error'
    if (line.includes('WARNING') || line.includes('WARN')) return 'log-entry warn'
    if (line.includes('SUCCESS')) return 'log-entry success'
    return 'log-entry info'
  }

  // ── 统计 ──
  const totalRules = rules?.length ?? 0
  const activeRules = rules?.filter(r => r.enable_rule).length ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, height: '100%', overflow: 'auto' }}>
      {/* ── 统计卡片 ─────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        {[
          { label: '总规则数', value: totalRules, icon: <Shield size={18} />, color: '#3b82f6' },
          { label: '活跃规则', value: activeRules, icon: <Activity size={18} />, color: '#10b981' },
          { label: '总结记录', value: summaryData.total, icon: <BookOpen size={18} />, color: '#8b5cf6' },
          { label: '系统状态', value: '运行中', icon: <Radio size={18} />, color: '#10b981', dot: true },
        ].map((stat, i) => (
          <div key={i} className="card-glass" style={{ padding: 20, display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 40, height: 40, borderRadius: 10, background: `${stat.color}20`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: stat.color }}>
              {stat.icon}
            </div>
            <div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 2 }}>{stat.label}</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                {stat.value}
                {stat.dot && <div className="health-dot active" />}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ── 总结历史 ─────────────────────────── */}
      <div className="card-glass" style={{ padding: 20, flex: 1, minHeight: 300, display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 15, color: 'var(--text-primary)' }}>总结历史归档</h3>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
              <input
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doSearch()}
                placeholder="搜索频道或内容..."
                style={{ ...inputStyle, paddingLeft: 32, width: 220 }}
              />
            </div>
            <button onClick={doSearch} style={btnStyle}>搜索</button>
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-light)' }}>
                {['频道', '消息数', '时间范围', 'AI 模型', '创建时间', '操作'].map(h => (
                  <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500, fontSize: 12, whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {summaryData.data.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>暂无总结记录</td></tr>
              ) : summaryData.data.map(s => (
                <>
                  <tr key={s.id} style={{ borderBottom: '1px solid var(--border-light)' }}>
                    <td style={tdStyle}>{s.source_channel_name || '—'}</td>
                    <td style={tdStyle}><span className="badge badge-primary">{s.message_count}</span></td>
                    <td style={{ ...tdStyle, fontSize: 12 }}>
                      {s.time_range_start && s.time_range_end ? `${s.time_range_start.slice(5, 16)} ~ ${s.time_range_end.slice(5, 16)}` : '—'}
                    </td>
                    <td style={tdStyle}><span className="badge">{s.ai_model || '—'}</span></td>
                    <td style={{ ...tdStyle, fontSize: 12 }}>{s.created_at?.slice(0, 16) || '—'}</td>
                    <td style={tdStyle}>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button onClick={() => setExpandedId(expandedId === s.id ? null : s.id)} style={iconBtnStyle} title="展开">
                          {expandedId === s.id ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                        <button onClick={() => copySummary(s.summary_text, s.id)} style={iconBtnStyle} title="复制">
                          {copiedId === s.id ? <Check size={14} color="var(--color-success)" /> : <Copy size={14} />}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === s.id && (
                    <tr key={`${s.id}-exp`}>
                      <td colSpan={6} style={{ padding: '12px 16px', background: 'var(--bg-base)', borderBottom: '1px solid var(--border-light)' }}>
                        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>
                          {s.summary_text}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>

        {/* 分页 */}
        {summaryData.total > limit && (
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-light)' }}>
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} style={pageBtnStyle}>
              <ChevronLeft size={14} />
            </button>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: '28px' }}>
              {page} / {totalPages}
            </span>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} style={pageBtnStyle}>
              <ChevronRight size={14} />
            </button>
          </div>
        )}
      </div>

      {/* ── 日志流 ──────────────────────────── */}
      <div className="card-glass" style={{ padding: 16, height: 220, display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 14, color: 'var(--text-primary)' }}>实时日志</h3>
            <div className={`health-dot ${wsStatus === 'connected' ? 'active' : wsStatus === 'reconnecting' ? 'warning' : 'danger'}`} />
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {wsStatus === 'connected' ? '已连接' : wsStatus === 'reconnecting' ? '重连中...' : '断开'}
            </span>
          </div>
          <button onClick={() => setLogs([])} style={iconBtnStyle} title="清空"><Trash2 size={14} /></button>
        </div>
        <div className="log-viewer" style={{ flex: 1, overflow: 'auto', background: '#06070a', borderRadius: 'var(--radius-sm)', padding: 8, fontFamily: 'monospace', fontSize: 12 }}>
          {logs.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', padding: 8 }}>等待日志...</div>
          ) : logs.map((line, i) => (
            <div key={i} className={getLogClass(line)} style={{ padding: '1px 0', lineHeight: 1.5 }}>{line}</div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  padding: '6px 12px', background: 'var(--bg-base)', border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 13, outline: 'none',
}
const btnStyle: React.CSSProperties = {
  padding: '6px 16px', background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 13, cursor: 'pointer',
}
const iconBtnStyle: React.CSSProperties = {
  background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)', padding: 5, cursor: 'pointer', display: 'flex', alignItems: 'center', color: 'var(--text-secondary)',
}
const tdStyle: React.CSSProperties = { padding: '10px 12px', color: 'var(--text-secondary)' }
const pageBtnStyle: React.CSSProperties = {
  ...iconBtnStyle, width: 28, height: 28, justifyContent: 'center',
}
