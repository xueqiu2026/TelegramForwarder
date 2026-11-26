import { Fragment, useEffect, useState } from 'react'
import useSWR from 'swr'
import api, { chatsApi, fetcher } from '../api'
import Originals from '../components/Originals'
import { errorText, formatTime, type SourcePage, type SourceRow, type SyncPreview } from '../consoleTypes'

export default function TgPage({ onRules }: { onRules: (chatId: number) => void }) {
  const [q, setQ] = useState(''), [folder, setFolder] = useState(''), [business, setBusiness] = useState('')
  const [collectionStatus, setCollectionStatus] = useState('')
  const [member, setMember] = useState(''), [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [notice, setNotice] = useState(''), [busy, setBusy] = useState('')
  const [preview, setPreview] = useState<SyncPreview | null>(null), [syncOpen, setSyncOpen] = useState(false)
  const [columns, setColumns] = useState<Record<string, boolean>>({ folders:true, collection:true, latest:true, raw:true, direct:true })
  const params = new URLSearchParams({ q, business, member_state:member, collection_status:collectionStatus, page:String(page), page_size:'30' })
  if (folder) params.set('folder',folder)
  const { data, error, mutate } = useSWR<SourcePage>('/sources?'+params,fetcher,{refreshInterval:30000})
  async function act(key:string, work:()=>Promise<unknown>) {
    setBusy(key); setNotice('')
    try { await work(); await mutate() } catch(e) { setNotice(errorText(e)) } finally { setBusy('') }
  }
  useEffect(()=> {
    if (preview?.status!=='running') return
    const timer=window.setInterval(()=> { api.get<SyncPreview>('/sources/sync/preview').then(r=>setPreview(r.data)).catch(e=>{setNotice(errorText(e));setPreview({status:'failed'})}) },1500)
    return ()=>window.clearInterval(timer)
  },[preview?.status])
  function filter(set:(v:string)=>void,value:string) { set(value);setPage(1);setExpanded(null) }
  async function rename(row:SourceRow) {
    const name=window.prompt('本地展示名（TG同步不会覆盖）',row.name)
    if(name?.trim()) await act('rename',()=>chatsApi.update(row.chat_id,name.trim()))
  }
  async function remove(row:SourceRow) {
    if(window.confirm(`从本地目录移除“${row.name}”？\n将删除关联的 ${row.direct.rules} 条直转规则。原文与业务结果保留，不退出TG。存在目标规则或活跃订阅时不能移除。`)) await act('remove',()=>chatsApi.delete(row.chat_id))
  }
  return <div className="workspace">
    <div className="page-heading"><div><p className="eyebrow">TELEGRAM</p><h1>TG 管理</h1><p className="muted">来源、业务订阅与原文，一处管理。新增分组不会自动开启业务。</p></div>
      <div className="toolbar"><button disabled={!!busy} onClick={()=>{const link=window.prompt('频道/群组链接、username或TG ID');if(link)void act('resolve',()=>chatsApi.resolve(link))}}>解析绑定</button>
      <button className="primary" disabled={!!busy} onClick={()=>{setSyncOpen(true);setPreview(null);void act('sync',async()=>{try{setPreview((await api.post<SyncPreview>('/sources/sync/preview')).data)}catch(e){setPreview({status:'failed',error:errorText(e)});throw e}})}}>同步 TG</button></div></div>
    {notice&&<p className="notice" role="status">{notice}</p>}
    <section className="panel"><div className="toolbar filters">
      <input aria-label="搜索来源" placeholder="搜索名称 / username / ID" value={q} onChange={e=>filter(setQ,e.target.value)}/>
      <select aria-label="TG分组" value={folder} onChange={e=>filter(setFolder,e.target.value)}><option value="">全部TG分组</option>{data?.folders.map(f=><option key={f.id} value={f.id}>{f.title}{!f.complete?' · 待核对':''}</option>)}</select>
      <select aria-label="业务订阅" value={business} onChange={e=>filter(setBusiness,e.target.value)}><option value="">全部业务</option>{data?.businesses.map(b=><option key={b.key} value={b.key}>{b.name}已订阅</option>)}</select>
      <select aria-label="成员状态" value={member} onChange={e=>filter(setMember,e.target.value)}><option value="">全部成员状态</option><option value="joined">用户账号已加入</option><option value="left">已退出</option><option value="unknown">待核对</option></select>
      <select aria-label="采集状态" value={collectionStatus} onChange={e=>filter(setCollectionStatus,e.target.value)}><option value="">全部采集状态</option><option value="enabled">已启用</option><option value="disabled">未启用</option><option value="received">已收到原文</option><option value="unknown">收取待核对</option><option value="error">补采有错误</option></select>
      <details className="column-picker"><summary>显示列</summary>{Object.entries({folders:'TG分组',collection:'采集状态',latest:'最近消息',raw:'原文',direct:'直转'}).map(([key,label])=><label key={key}><input type="checkbox" checked={columns[key]} onChange={e=>setColumns({...columns,[key]:e.target.checked})}/>{label}</label>)}</details>
    </div>
    {error?<p role="alert">来源读取失败，请检查服务后重试。</p>:!data?<p className="empty">加载来源…</p>:<>
    <div className="table-scroll"><table className="source-table"><thead><tr><th>频道 / 群组</th>{columns.folders&&<th>TG分组</th>}{data.businesses.map(b=><th key={b.key}>{b.name}</th>)}{columns.collection&&<th>采集状态</th>}{columns.latest&&<th>最近消息 · 北京时间</th>}{columns.raw&&<th>原文 · 全历史</th>}{columns.direct&&<th>直转</th>}<th>操作</th></tr></thead>
    <tbody>{data.items.map(row=><Fragment key={row.chat_id}><tr>
      <td><strong>{row.name}</strong><small>{row.username?'@'+row.username+' · ':''}{row.peer_id||row.telegram_chat_id}</small><small>{({channel:'频道/超级群',group:'普通群',user:'用户'} as Record<string,string>)[row.peer_type||'']||'类型待核对'} · {row.member_state==='joined'?'用户账号已加入':row.member_state==='left'?'用户账号已退出':'成员待核对'}{row.target_rule_count?' · 发送目标':''}</small></td>
      {columns.folders&&<td>{row.folders.map(f=><span className="badge" key={f.id}>{f.title}</span>)}{!row.folders.length&&<span className="muted">未分组 / 待同步</span>}</td>}
      {data.businesses.map(b=><td key={b.key}><button role="switch" aria-label={`${row.name} ${b.name}订阅`} aria-checked={!!row.subscriptions[b.key]?.active} className={`ios-switch ${row.subscriptions[b.key]?.active?'on':''}`} disabled={!!busy||!row.can_subscribe} onClick={()=>void act(`${row.chat_id}-${b.key}`,()=>api.put(`/sources/${row.chat_id}/subscriptions/${b.key}`,{active:!row.subscriptions[b.key]?.active}))}><span/></button></td>)}
      {columns.collection&&<td><span className={`badge ${row.collection.error?'bad':''}`}>{row.member_state==='left'?'成员不可用':row.collection.error?'补采有错误':!row.collection.enabled?'未启用业务采集':row.latest.received_at?'已收到原文':'已启用 · 收取待核对'}</span>{row.collection.error&&<small title={row.collection.error}>{row.collection.error}</small>}<small>补采检查 {formatTime(row.collection.scanned_at)}</small></td>}
      {columns.latest&&<td><small>发布 {formatTime(row.latest.published_at)}</small><small>收取 {formatTime(row.latest.received_at)}</small></td>}
      {columns.raw&&<td><button className="text-button" disabled={!row.peer_id} onClick={()=>setExpanded(expanded===row.chat_id?null:row.chat_id)}>{row.raw_count} 条 {expanded===row.chat_id?'收起':'展开'}</button></td>}
      {columns.direct&&<td><button className="text-button" onClick={()=>onRules(row.chat_id)}>{row.direct.blocked?'成员阻断':`${row.direct.enabled} 条配置开启`}</button><small>共 {row.direct.rules} 条规则</small></td>}
      <td><div className="row-actions"><button disabled={!!busy} onClick={()=>void rename(row)}>改名</button><button disabled={!!busy} onClick={()=>void remove(row)}>移除</button></div></td>
    </tr>{expanded===row.chat_id&&row.peer_id&&<tr className="expanded"><td colSpan={20}><Originals source={row.peer_id}/></td></tr>}</Fragment>)}</tbody></table></div>
    {!data.items.length&&<p className="empty">没有匹配来源。</p>}<div className="pagination"><span>共 {data.total} 个目录条目</span><button disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>{page}</span><button disabled={page*30>=data.total} onClick={()=>setPage(page+1)}>下一页</button></div></>}
    </section>
    {syncOpen&&<div className="modal-backdrop"><section className="panel sync-modal" role="dialog" aria-modal="true" aria-label="TG同步差异"><h2>TG同步核对</h2><p className="muted">只更新本地目录；确认退出的来源将停用业务并阻断自动重入。不会加入或退出TG。</p>
      {!preview||preview.status==='running'?<p>正在核对TG对话与分组…关闭窗口不会重新启动扫描。</p>:preview.status==='failed'?<p role="alert">{preview.error||'同步失败，请重新核对'}</p>:preview.result&&<><p>核对 {preview.result.sources.length} 个实体 · 分组 {preview.result.folders.length} 个 · {preview.result.complete?'对话扫描完整':'扫描不完整，不推断缺失来源退出'}</p>
      {!preview.result.folders_complete&&<p role="alert">TG分组读取不完整，本次保留已有分组关系。</p>}
      {preview.result.folders_complete&&<p className="muted">同步分组：{preview.result.folders.map(f=>f.title).join(' / ')||'无自定义分组'}</p>}
      {Object.entries(preview.result.errors).map(([key,value])=><p key={key} role="alert">{key}：{value}</p>)}
      <div className="sync-list">{preview.result.sources.filter(s=>s.change!=='unchanged').map(s=><p key={s.peer_id}><strong>{s.telegram_title}</strong> {s.peer_id} · {({added:'新增目录',updated:'更新资料',left:'确认退出'} as Record<string,string>)[s.change]||s.change} · {s.member_state==='left'?'将停用：'+(s.affected_businesses.join(' / ')||'无活跃业务'):s.member_state==='joined'?'核对名称/分组，保持订阅':'成员未知：保持成员状态'}</p>)}
      {preview.result.sources.flatMap(s=>s.changes.map(change=><p key={`${s.peer_id}-${change.field}`}><strong>{s.telegram_title}</strong> · {({telegram_title:'TG标题',username:'username',member_state:'成员状态',folders:'TG分组'} as Record<string,string>)[change.field]||change.field}：{change.before||'无'} → {change.after||'无'}</p>))}
      {preview.result.folder_changes.map(change=><p key={change.id}>分组 #{change.id}：{change.before?.title||'无'} → {change.after?.title||'已移除'} · 成员 {change.before?.peers.length??0} → {change.after?.peers.length??0}</p>)}</div>
      <button className="primary" disabled={!!busy} onClick={()=>void act('apply',async()=>{await api.post('/sources/sync/apply',{token:preview.token});setSyncOpen(false);setPreview(null);setNotice('同步已应用，原文与业务结果保留。')})}>确认应用核对结果</button></>}
      <button onClick={()=>setSyncOpen(false)}>关闭</button></section></div>}
  </div>
}
