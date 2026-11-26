import { useState } from 'react'
import useSWR from 'swr'
import { fetcher } from '../api'
import { formatTime, stateNames, type Original, type Page } from '../consoleTypes'

export function OriginalBody({ row }: { row: Original }) {
  return <article className="original">
    <p><strong>{row.source_name || row.source_id}</strong> · 原文 #{row.id} · TG #{row.message_id} · 修订 {row.revision}</p>
    <p className="muted">发布 {formatTime(row.published_at)} · 收取 {formatTime(row.received_at)}</p>
    {row.edited_at && <p className="muted">编辑 {formatTime(row.edited_at)}</p>}
    <div className="copy-body">{row.body || '无文字消息'}</div>
    {row.entities?.filter(e=>e.url).map((e,n)=><p key={n}>{e.url && /^https?:\/\//i.test(e.url) ? <a href={e.url} target="_blank" rel="noreferrer">{e.url}</a> : e.url}</p>)}
    {!!Object.keys(row.media || {}).length && <p className="muted">包含媒体 · 本页展示媒体标记</p>}
    {row.business_states && <p>{Object.entries(row.business_states).map(([b, s]) =>
      <span key={b} className="badge">{b === 'alpha' ? 'Alpha' : 'AI'}：{s ? stateNames[s.analysis_status || s.status || ''] || s.status : '未建立处理态'}{s?.error && ` · ${s.error}`}</span>)}</p>}
    {row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer">内部原帖</a>}
  </article>
}

export default function Originals({ source }: { source: string }) {
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [business, setBusiness] = useState('')
  const [status, setStatus] = useState('')
  const [after, setAfter] = useState(''), [until, setUntil] = useState('')
  const params = new URLSearchParams({
    source, q, business, status, page: String(page), page_size: '10',
  })
  if(after) params.set('after',new Date(after+':00+08:00').toISOString())
  if(until) params.set('until',new Date(until+':00+08:00').toISOString())
  const { data, error } = useSWR<Page<Original>>('/collected-messages?' + params, fetcher)
  return <section className="originals">
    <div className="toolbar"><input aria-label="搜索原文" placeholder="搜索本来源原文" value={q} onChange={e => { setQ(e.target.value); setPage(1) }} />
      <select aria-label="原文业务" value={business} onChange={e => { setBusiness(e.target.value); setStatus(''); setPage(1) }}>
        <option value="">全部原文</option><option value="alpha">Alpha处理态</option><option value="ai">AI处理态</option></select>
      {business && <select aria-label="处理状态" value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}><option value="">全部状态</option>
        {(business === 'alpha' ? ['pending', 'done', 'uncertain', 'failed'] : ['pending', 'candidate', 'needs_info', 'excluded', 'unmatched', 'failed']).map(s => <option key={s} value={s}>{stateNames[s]}</option>)}</select>}
      <span className="muted">筛选结果 {data?.total ?? '—'} 条</span>
      <label className="muted">北京时间起 <input aria-label="原文起始时间" type="datetime-local" value={after} onChange={e=>{setAfter(e.target.value);setPage(1)}} /></label>
      <label className="muted">止（不含） <input aria-label="原文结束时间" type="datetime-local" value={until} onChange={e=>{setUntil(e.target.value);setPage(1)}} /></label>
    </div>
    {error ? <p role="alert">原文读取失败，请重试。</p> : !data ? <p>加载原文…</p> : data.items.length ? data.items.map(row => <OriginalBody key={row.id} row={row} />) : <p>没有已保存的匹配原文。</p>}
    <div className="pagination"><button disabled={page === 1} onClick={() => setPage(page-1)}>上一页</button><span>{page}</span><button disabled={!data || page*10 >= data.total} onClick={() => setPage(page+1)}>下一页</button></div>
  </section>
}
