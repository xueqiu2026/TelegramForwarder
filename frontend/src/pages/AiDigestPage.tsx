import { useState } from 'react'
import useSWR from 'swr'
import api, { fetcher } from '../api'

export default function AiDigestPage() {
  const { data, error, mutate } = useSWR<any>('/ai-digest/status', fetcher, { refreshInterval: 10000 })
  const { data: models } = useSWR<Record<string, string[]>>('/ai-models', fetcher)
  const [filter, setFilter] = useState('')
  const [message, setMessage] = useState('')
  const [source, setSource] = useState('')
  const [name, setName] = useState('')
  const [model, setModel] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const action = async (work: () => Promise<any>) => {
    setBusy(true)
    try { await work(); setMessage('操作完成'); await mutate() }
    catch (e: any) { setMessage(e?.response?.data?.detail || e.message) }
    finally { setBusy(false) }
  }
  if (error) return <p>AI数据读取失败</p>
  if (!data) return <p>加载中…</p>
  const config = (update: any) => api.put('/ai-digest/config', {
    enabled: data.enabled, publish_enabled: data.publish_enabled,
    target_id: data.target_id, model: model ?? data.model, ...update,
  })
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 16, overflow: 'auto' }}>
    <section className="card-glass" style={{ padding: 20 }}>
      <h3>AI内容</h3>
      <p>北京时间 {data.times.join(" / ")}；原文增量筛选，不设双来源和36小时门槛。仅最终发送阶段输出中文。</p>
      <p>每批只分析新增消息，历史失败和积压不自动重试。</p>
      <p>状态：{data.status}　目标：{data.target_id}　{data.error}</p>
      <select value={model ?? data.model} onChange={e => setModel(e.target.value)}>
        <option value="">选择模型</option>
        {Object.values(models || {}).flat().map(m => <option key={m} value={m}>{m}</option>)}
      </select>{' '}
      <button disabled={busy} onClick={() => action(() => config({}))}>保存模型</button>{' '}
      <button disabled={busy} onClick={() => action(() => config({ enabled: !data.enabled }))}>{data.enabled ? '停用定时' : '启用定时'}</button>{' '}
      <button disabled={busy} onClick={() => action(() => config({ publish_enabled: !data.publish_enabled }))}>{data.publish_enabled ? '关闭发送' : '启用发送'}</button>{' '}
      <button disabled={busy} onClick={() => action(() => api.post('/ai-digest/run'))}>执行一次</button>{' '}
      <a href="/tg/api/ai-digest/export" target="_blank" rel="noreferrer">下载核对数据</a>
      <p>{message}</p>
      <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(data.result, null, 2)}</pre>
    </section>
    <section className="card-glass" style={{ padding: 20 }}>
      <h3>AI独立来源订阅</h3>
      <input placeholder="TG数字频道ID" value={source} onChange={e => setSource(e.target.value)} />{' '}
      <input placeholder="名称" value={name} onChange={e => setName(e.target.value)} />{' '}
      <button disabled={busy || !source} onClick={() => action(() => api.put('/ai-digest/sources', { source_id: source, name, active: true }))}>添加订阅</button>
      {data.sources.map((s: any) => <div key={s.id} style={{ marginTop: 8 }}>
        {s.name} {s.id} {s.active ? '采集中' : '已停用'}{' '}
        <button disabled={busy} onClick={() => action(() => api.put('/ai-digest/sources', { source_id: s.id, name: s.name, active: !s.active }))}>{s.active ? '停用' : '启用'}</button>
      </div>)}
    </section>
    <section className="card-glass" style={{ padding: 20 }}>
      <h3>事项与最终中文内容</h3>
      <select value={filter} onChange={e => setFilter(e.target.value)}><option value="">全部类别</option>
        {['B1', 'B5', 'B6', 'T1', 'T5', 'T7', 'U'].map(c => <option key={c}>{c}</option>)}
      </select>
      {data.items.filter((i: any) => !filter || i.category === filter).map((i: any) => <article key={i.id} style={{ borderBottom: '1px solid var(--border-light)', padding: '12px 0' }}>
        <h4>#{i.id} {i.category} · {i.subject} · {i.status}</h4>
        <p>{i.goal} {i.conditions}</p><p>{i.error}</p>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{i.output_zh || '尚未生成最终中文内容'}</pre>
        <details><summary>原文依据</summary>{i.sources.map((r: any, n: number) => <pre key={n} style={{ whiteSpace: 'pre-wrap' }}>原文#{r.id} revision {r.revision}{'\n'}{r.excerpt}</pre>)}</details>
        {data.publications.filter((p: any) => p.item_id === i.id).map((p: any) => <p key={p.target_id}>发送：{p.status} {p.error}　消息ID：{p.parts.map((r: any) => r.message_id || '待确认').join(', ')}</p>)}
      </article>)}
    </section>
    <section className="card-glass" style={{ padding: 20 }}><h3>原文与处理结果</h3>
      {data.messages.map((m: any) => <details key={m.id}><summary>#{m.id} {m.source} {new Date(m.date + 'Z').toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} · {m.status} · {m.recall}</summary>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{m.body}</pre>
        <pre style={{ whiteSpace: 'pre-wrap' }}>链接实体：{JSON.stringify(m.entities, null, 2)}</pre><a href={m.url} target="_blank" rel="noreferrer">内部原帖</a>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(m.result, null, 2)}</pre><p>{m.error}　{m.version}</p>
      </details>)}
    </section>
  </div>
}
