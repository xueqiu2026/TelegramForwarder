import { useEffect, useState } from 'react'
import useSWR from 'swr'
import api, { fetcher } from '../api'
import { OriginalBody } from '../components/Originals'
import TelegramText from '../components/TelegramText'
import { categories, errorText, formatTime, stateNames, type Business, type ContentItem, type JobStatus, type Page } from '../consoleTypes'

export function RecentResult({result}:{result:Record<string,unknown>}) {
  const publish=result.publish as {sent?:number;errors?:number;unknown?:number}|string|undefined
  const recovery=result.recovery_errors as Record<string,string>|undefined
  return <div className="muted"><p>分析失败 {typeof result.analysis_errors==='number'?result.analysis_errors:'—'} · 成稿失败 {typeof result.draft_errors==='number'?result.draft_errors:'—'}</p>
    {typeof publish==='object'&&<p>本轮已发送 {publish.sent??0} · 发送失败 {publish.errors??0} · 结果待核对 {publish.unknown??0}</p>}
    {typeof publish==='string'&&<p>发布结果：{publish==='disabled'?'发布关闭':publish==='none'?'未发送':publish}</p>}
    {Object.entries(recovery||{}).map(([peer,reason])=><p key={peer} role="alert">补采未完成 {peer}：{reason}</p>)}</div>
}

function Detail({ business, id }: { business: Business; id: number }) {
  const { data, error } = useSWR<ContentItem>(`/business/${business.key}/items/${id}`, fetcher)
  if(error) return <p role="alert">详情读取失败。</p>
  if(!data) return <p>加载详情…</p>
  return <div className="content-detail">
    <p className="muted">{business.key==='alpha'?`判型状态：${stateNames[data.type_status||'']||data.type_status||'未知'} · 信息密度：${stateNames[data.detail_level||'']||data.detail_level||'未知'}`:`参与条件：${data.conditions||'未记录'}`}</p>
    <p className="muted">截止时间 · 北京时间：{formatTime(data.deadline)}</p>
    {business.key==='ai'&&data.dedup&&<p className="muted">去重判定：{({new:'独立新事项',same:'同一事项',distinct:'不同资格或技术方法',update:'实质更新',review:'待核对'} as Record<string,string>)[data.dedup.relation||'']||'待核对'}{(data.dedup.blocked_by_item_id||data.dedup.related_item_id)?` · 关联事项 #${data.dedup.blocked_by_item_id||data.dedup.related_item_id}`:''} · {data.dedup.blocked_reason||data.dedup.reason}{data.dedup.change&&` · 新增事实：${data.dedup.change}`}</p>}
    <h3>当前中文内容</h3><div className="copy-body">{data.draft_zh || data.output_zh || '当前没有中文稿。已发正文见下方回执。'}</div>
    {data.error && <p role="alert">{data.error}</p>}
    <h3>发布回执</h3>{!data.publications?.length && <p className="muted">暂无发布记录。</p>}
    {data.publications?.map(p=><section key={p.id} className="original"><p><strong>{p.target_id}</strong> · {stateNames[p.status] || p.status} · {formatTime(p.created_at)}</p>{p.error && <p role="alert">{p.error}</p>}
      {p.parts.map((part,n)=><div key={n}><p className="muted">第 {n+1} 段 · {stateNames[part.status]||part.status} · 消息ID {part.message_id??'待确认'}{part.photo?' · 含配图':''}{!part.entities?' · 历史记录未保存格式实体':''}</p><div className="copy-body"><TelegramText text={part.text} entities={part.entities}/></div></div>)}</section>)}
    <details><summary>内部原文依据 · {data.originals?.length || 0} 条</summary>{data.originals?.map(r=><OriginalBody key={r.id} row={r}/>)}</details>
  </div>
}

export default function BusinessPage({ business }: { business: Business }) {
  const { data:job, error:jobError, mutate } = useSWR<JobStatus>(`/business/${business.key}/status`,fetcher,{refreshInterval:5000})
  const { data:models } = useSWR<Record<string,string[]>>('/ai-models',fetcher)
  const [model,setModel]=useState(''), [hours,setHours]=useState(''), [notice,setNotice]=useState('')
  const [publicationStatus,setPublicationStatus]=useState('')
  const [busy,setBusy]=useState(false), [page,setPage]=useState(1), [category,setCategory]=useState(''), [status,setStatus]=useState('')
  const [tier,setTier]=useState(business.key==='alpha'?'main':''), [expanded,setExpanded]=useState<number|null>(null)
  const params=new URLSearchParams({page:String(page),page_size:'20',category,status,tier,publication_status:publicationStatus})
  const {data,error}=useSWR<Page<ContentItem>>(`/business/${business.key}/items?${params}`,fetcher,{refreshInterval:10000})
  useEffect(()=>{if(job){setModel(job.model);setHours(job.schedule_hours.join(','))}},[job?.model,job?.schedule_hours.join(',')])
  async function config(change:Record<string,unknown>) {
    setBusy(true);setNotice('')
    try { await api.put(`/${business.path}/config`,change);await mutate();setNotice('配置已保存。') } catch(e){setNotice(errorText(e))} finally{setBusy(false)}
  }
  async function run() {
    if(!job)return
    setBusy(true);setNotice('')
    try { const r=await api.post<{status:string;message?:string}>(`/${business.path}/run`);setNotice(r.data.message||'已受理，请等待任务状态更新；受理不代表已完成。');await mutate() } catch(e){setNotice(errorText(e))} finally{setBusy(false)}
  }
  return <div className="workspace"><div className="page-heading"><div><p className="eyebrow">SCHEDULED CONTENT</p><h1>{business.name}</h1><p className="muted">{business.key==='alpha'?'最近36小时 · 至少两个来源 · 独立成稿':'本批新增原文 · 无双来源门槛 · 仅最终发送前输出中文'}</p></div></div>
    {jobError?<p role="alert">任务状态读取失败。</p>:!job?<p>加载任务…</p>:<section className="panel job-panel">
      <div className="toolbar"><span className="badge">{stateNames[job.status]||job.status}</span><span>{job.source_count} 个订阅来源</span><span className="muted">上次开始 {formatTime(job.last_run)}</span></div>
      <p>目标 {job.target_id || '未设置'} · 定时 {job.enabled?'开启':'关闭'} · 发布 {job.publish_enabled?'开启':'关闭'}</p>
      <div className="toolbar"><button disabled={busy} onClick={()=>void config({enabled:!job.enabled})}>{job.enabled?'停用定时':'启用定时'}</button><button disabled={busy} onClick={()=>void config({publish_enabled:!job.publish_enabled})}>{job.publish_enabled?'关闭发布':'开启发布'}</button>
        <button className="primary" disabled={busy||job.status==='running'} onClick={()=>void run()}>{job.publish_enabled?'执行一次 · 按当前配置发布':'执行一次 · 不发布'}</button></div>
      <div className="toolbar"><label>模型 <select value={model} onChange={e=>setModel(e.target.value)}><option value="">未选择</option>{job.model&&!Object.values(models||{}).flat().includes(job.model)&&<option value={job.model}>{job.model}</option>}{Object.values(models||{}).flat().map(m=><option key={m}>{m}</option>)}</select></label><button disabled={busy||model===job.model} onClick={()=>void config({model})}>保存模型</button></div>
      <div className="toolbar"><label>北京时间整点 <input aria-label="定时小时" value={hours} onChange={e=>setHours(e.target.value)} /></label><button disabled={busy} onClick={()=>{const values=hours.split(',').map(v=>Number(v.trim()));if(!hours.trim()||values.some(v=>!Number.isInteger(v)||v<0||v>23)||new Set(values).size!==values.length){setNotice('请填写不重复的0–23整点，以英文逗号分隔');return}void config({schedule_hours:values})}}>保存时间</button></div>
      <p className="muted">当前时刻 {job.schedule_hours.map(h=>String(h).padStart(2,'0')+':00').join(' / ')}。采集与定时开关独立。{business.key==='ai'?'旧失败和积压不自动重试。':''}</p>
      {job.analysis_until&&<p className="muted">增量边界 {formatTime(job.analysis_until)}</p>}
      {job.error&&<p role="alert">{job.error}</p>}
      <div className="toolbar"><a href={`/tg/api/${business.path}/export`}>{business.key==='alpha'?'下载公开草稿':'下载核对数据'}</a>{business.key==='alpha'&&<a href="/tg/api/alpha/export?internal=true">下载内部核对材料</a>}</div>
      <details><summary>最近批次结果</summary><RecentResult result={job.result||{}}/></details>
    </section>}
    {notice&&<p className="notice" role="status">{notice}</p>}
    <section className="panel"><div className="toolbar filters"><select aria-label="发布状态" value={publicationStatus} onChange={e=>{setPublicationStatus(e.target.value);setPage(1)}}><option value="">全部发布状态</option>{['queued','sending','sent','failed','unknown'].map(s=><option key={s} value={s}>{stateNames[s]}</option>)}</select>
      {business.key==='alpha'?<select aria-label="来源门槛" value={tier} onChange={e=>{setTier(e.target.value);setPage(1)}}>{['main','single','closed','history'].map(t=><option key={t} value={t}>{stateNames[t]}</option>)}<option value="">全部</option></select>:<select aria-label="内容分类" value={category} onChange={e=>{setCategory(e.target.value);setPage(1)}}><option value="">全部分类</option>{['B1','B5','B6','T1','T5','T7','U'].map(c=><option key={c} value={c}>{categories[c]}</option>)}</select>}
      <select aria-label="内容状态" value={status} onChange={e=>{setStatus(e.target.value);setPage(1)}}><option value="">全部{business.key==='alpha'?'草稿':'资格'}状态</option>{(business.key==='alpha'?['pending','ready','failed']:['candidate','needs_info','expired']).map(s=><option key={s} value={s}>{stateNames[s]}</option>)}</select></div>
      {error?<p role="alert">内容读取失败。</p>:!data?<p className="empty">加载内容…</p>:<>{!data.items.length&&<p className="empty">当前筛选暂无内容。</p>}{data.items.map(item=><article key={item.id} className="content-row"><button className="content-title" onClick={()=>setExpanded(expanded===item.id?null:item.id)}>{item.project||item.subject}｜{item.title||item.goal}</button><p className="muted">#{item.id} · {categories[item.content_type||item.category||'']||'类型待核'} · {stateNames[item.draft_status||item.status||'']||item.status}{item.source_count!==undefined?` · ${item.source_count}个窗口内来源`:''}</p>{item.publication_summaries?.map(p=><span className="badge" key={p.id}>{p.target_id} · {stateNames[p.status]||p.status}</span>)}{item.error&&<p role="alert">{item.error}</p>}{expanded===item.id&&<Detail business={business} id={item.id}/>}</article>)}
      <div className="pagination"><span>共 {data.total} 条</span><button disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>{page}</span><button disabled={page*20>=data.total} onClick={()=>setPage(page+1)}>下一页</button></div></>}
    </section>
  </div>
}
