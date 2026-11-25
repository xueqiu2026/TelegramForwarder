import { useEffect, useState } from 'react'
import useSWR from 'swr'
import api, { fetcher } from '../api'

interface Opportunity {
  content_type: string | null
  type_status: string | null
  detail_level: string | null
  id: number
  project: string
  title: string
  tier: 'main' | 'single' | 'history' | 'closed'
  stage: string
  draft: string | null
  draft_status: string
  source_count: number
  first_seen: string
  error: string | null
}

interface Status {
  publish_enabled: boolean
  target_id: string
  enabled: boolean
  model: string
  status: string
  last_run: string | null
  counts: Record<string, number>
  error: string | null
  result: { recovery_errors?: Record<string, string>; analysis_errors?: number; draft_errors?: number; publish?: string | { sent: number; errors: number; unknown: number } }
  sources: { id: string; active: boolean; error: string | null }[]
  events: Opportunity[]
}

const contentTypes: Record<string, string> = { tutorial: '活动教程', sale: '发行申购', claim_check: '查询领取', allocation: '积分分配', update: '进展提醒', ipo: '全球IPO', launchpad: '平台上线预告' }
const tiers = { main: '主清单', single: '单源附录', closed: '已结束更新', history: '历史' }

export default function AlphaPage() {
  const { data, error, mutate } = useSWR<Status>('/alpha/status', fetcher, { refreshInterval: 5000 })
  const { data: models } = useSWR<Record<string, string[]>>('/ai-models', fetcher)
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [tier, setTier] = useState<keyof typeof tiers>('main')
  useEffect(() => { if (data) setModel(data.model) }, [data?.model])

  async function act(action: 'config' | 'run') {
    if (!data) return
    setBusy(true)
    setNotice('')
    try {
      if (action === 'config') {
        await api.put('/alpha/config', { enabled: !data.enabled, model })
        setNotice(data.enabled ? '定时任务已关闭，原文采集独立运行。' : '定时任务已开启，将在下一时刻生成草稿。')
      } else {
        // Save model without changing the current scheduling switch.
        await api.put('/alpha/config', { enabled: data.enabled, model })
        const result = await api.post('/alpha/run')
        setNotice(result.data.message)
      }
      await mutate()
    } catch (exc: unknown) {
      const err = exc as { response?: { data?: { detail?: string } }; message?: string }
      setNotice(err.response?.data?.detail || err.message || '操作失败')
    } finally { setBusy(false) }
  }

  if (error) return <div className="card-glass" style={{ padding: 24 }}>机会数据加载失败，请检查 API 和数据库迁移。</div>
  if (!data) return <div style={{ padding: 24 }}>正在加载机会草稿…</div>
  const events = data.events.filter(e => e.tier === tier)
  const stateNames: Record<string, string> = { idle: '未运行', running: '运行中', success: '完成', partial: '部分完成', failed: '失败' }
  return <div style={{ display: 'grid', gap: 18 }}>
    <div className="card-glass" style={{ padding: 24 }}>
      <h2 style={{ marginTop: 0 }}>机会草稿</h2>
      <p>持续采集，按北京时间 07:00、13:00、19:00、22:00 处理。主清单要求最近 36 小时内至少两个不同来源。{data.publish_enabled ? '符合要求的内容自动推送到 2026。' : '自动推送关闭。'}</p>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <label>分析模型：<select value={model} onChange={e => setModel(e.target.value)}>
          <option value="">请选择</option>
          {Object.values(models || {}).flat().map(m => <option key={m} value={m}>{m}</option>)}
        </select></label>
        <button disabled={busy || !model} onClick={() => act('run')}>{data.publish_enabled ? '立即筛选并推送' : '生成草稿'}</button>
        <button disabled={busy || (!data.enabled && !model)} onClick={() => act('config')}>
          {data.enabled ? '关闭定时任务' : '开启定时任务'}
        </button>
        <a href="/tg/api/alpha/export">下载公开草稿</a>
        <a href="/tg/api/alpha/export?internal=true">下载内部核对材料</a>
      </div>
      <p>定时：{data.enabled ? '开启' : '关闭'}　任务：{stateNames[data.status] || data.status}　采集来源：{data.sources.filter(s => s.active).length}</p>
      <p>待分析 {data.counts.pending || 0} · 分析失败 {data.counts.failed || 0} · 待补信息 {data.counts.uncertain || 0} · 已分析 {data.counts.done || 0}</p>
      {typeof data.result.publish === 'object' && <p>本轮推送：{data.result.publish.sent} 个事件 · 发送失败 {data.result.publish.errors} · 待核对 {data.result.publish.unknown}</p>}
      {data.last_run && <p>上次开始：{new Date(data.last_run + 'Z').toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</p>}
      {notice && <p role="status">{notice}</p>}
      {data.error && <p role="alert">任务失败：{data.error}</p>}
      {Object.entries(data.result.recovery_errors || {}).map(([source, reason]) => <p key={source} role="alert">补采未完成 {source}：{reason}</p>)}
      {data.sources.filter(s => s.error).map(s => <p key={s.id} role="alert">来源 {s.id}：{s.error}</p>)}
    </div>
    <div style={{ display: 'flex', gap: 12 }}>
      {(Object.keys(tiers) as (keyof typeof tiers)[]).map(t => <button key={t} onClick={() => setTier(t)} disabled={tier === t}>
        {tiers[t]}（{data.events.filter(e => e.tier === t).length}）
      </button>)}
    </div>
    {events.length === 0 && <p>暂无{tiers[tier]}内容。</p>}
    {events.map(event => <article className="card-glass" key={event.id} style={{ padding: 24 }}>
      <h3 style={{ marginTop: 0 }}>{event.project}｜{event.title}</h3>
      <p>{event.content_type ? contentTypes[event.content_type] || event.content_type : event.type_status === 'uncertain' ? '类型待核' : '尚未判型'} · {event.stage} · {event.source_count} 个窗口内来源 · {new Date(event.first_seen + 'Z').toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</p>
      {event.draft ? <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.85, overflowWrap: 'anywhere' }}>{event.draft}</div>
        : <p>{event.error || (tier === 'single' ? '等待第二个来源，原文保留在内部核对材料。' : '草稿待生成。')}</p>}
    </article>)}
  </div>
}
