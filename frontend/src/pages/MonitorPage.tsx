import { useState, useEffect, useRef, useCallback } from 'react'
import useSWR from 'swr'
import { Trash2, Activity, Shield, Radio } from 'lucide-react'
import { fetcher } from '../api'
import type { RuleListItem } from '../types'
import { formatTime, stateNames, type Business, type JobStatus } from '../consoleTypes'
import { RecentResult } from './BusinessPage'

function RecentJob({business}:{business:Business}) {
  const {data,error}=useSWR<JobStatus>(`/business/${business.key}/status`,fetcher,{refreshInterval:15000})
  return <section className="panel"><h3>{business.name} · 最近批次</h3>{error?<p role="alert">批次读取失败。</p>:!data?<p>加载批次…</p>:<><p>{stateNames[data.status]||data.status} · 上次开始 {formatTime(data.last_run)}</p>{data.error&&<p role="alert">{data.error}</p>}<RecentResult result={data.result||{}}/></>}</section>
}

export default function MonitorPage() {
  const { data: health, error:healthError } = useSWR<{status:string;database:{status:string};bridge:{status:string;user_client_connected:boolean;bot_client_connected:boolean}}>('/health',fetcher,{refreshInterval:15000})
  const { data: businesses, error:businessError }=useSWR<Business[]>('/businesses',fetcher)
  // ── 数据 ──
  const { data: rules } = useSWR<RuleListItem[]>('/rules', fetcher)
  // ── WebSocket 日志 ──
  const [logs, setLogs] = useState<string[]>([])
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected' | 'reconnecting'>('disconnected')
  const logEndRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)
  const stoppedRef = useRef(false)
  const timerRef = useRef<number | undefined>(undefined)

  const connectWs = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/tg/api/ws/logs`)
    wsRef.current = ws
    ws.onopen = () => { setWsStatus('connected'); retryRef.current = 0 }
    ws.onmessage = (e) => {
      setLogs(prev => {
        const next = [...prev, e.data]
        return next.length > 500 ? next.slice(-500) : next
      })
    }
    ws.onclose = (event) => {
      if(stoppedRef.current) return
      if(event.code===1008){setWsStatus('disconnected');return}
      setWsStatus('reconnecting')
      const delay = Math.min(3000 * Math.pow(2, retryRef.current), 30000)
      retryRef.current++
      timerRef.current = window.setTimeout(connectWs, delay)
    }
    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => {
    stoppedRef.current=false
    connectWs()
    return () => {
      stoppedRef.current=true;window.clearTimeout(timerRef.current)
      if(wsRef.current){wsRef.current.onclose=null;wsRef.current.onerror=null;wsRef.current.close()}
    }
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
  const activeRules = rules?.filter(r => r.enable_rule && r.enable_forward).length ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, height: '100%', overflow: 'auto' }}>
      {/* ── 统计卡片 ─────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        {[
          { label: '总规则数', value: totalRules, icon: <Shield size={18} />, color: '#3b82f6' },
          { label: '直转配置开启', value: activeRules, icon: <Activity size={18} />, color: '#10b981' },
          { label: '系统状态', value: health?.status==='healthy'?'连接正常':health?'连接异常':'待核对', icon: <Radio size={18} />, color: '#10b981', dot: health?.status==='healthy' },
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

      <section className="panel"><h3>连接检查</h3>{healthError?<p role="alert">健康状态读取失败。</p>:!health?<p>正在检查…</p>:<div className="toolbar"><span>数据库：{health.database.status==='healthy'?'正常':'异常'}</span><span>主进程桥接：{health.bridge.status==='healthy'?'正常':'未连接'}</span><span>TG用户：{health.bridge.user_client_connected?'已连接':'未连接'}</span><span>Bot：{health.bridge.bot_client_connected?'已连接':'未连接'}</span></div>}</section>
      {businessError&&<p role="alert">业务目录读取失败。</p>}
      {businesses?.map(business=><RecentJob key={business.key} business={business}/>)}

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
        <div className="log-viewer" style={{ flex: 1, overflow: 'auto', background: '#f8f8fa', borderRadius: 'var(--radius-sm)', padding: 8, fontFamily: 'monospace', fontSize: 12 }}>
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

const iconBtnStyle: React.CSSProperties = {
  background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)', padding: 5, cursor: 'pointer', display: 'flex', alignItems: 'center', color: 'var(--text-secondary)',
}
