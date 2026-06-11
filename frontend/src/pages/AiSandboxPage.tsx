import { useState, useEffect } from 'react'
import useSWR from 'swr'
import { Zap, Copy, Check, ChevronDown, ChevronUp, CheckSquare, Square, BarChart2, Clock, Layers } from 'lucide-react'
import { fetcher, sandboxApi } from '../api'

export default function AiSandboxPage() {
  const { data: modelsDict } = useSWR<Record<string, string[]>>('/ai-models', fetcher)
  const { data: defaultModelData } = useSWR<{ default_model: string }>('/ai-default-model', fetcher)
  const { data: historyData, error: historyError, mutate: mutateHistory } = useSWR<{ status: string; data?: any[]; message?: string }>('/sandbox/history-samples', fetcher)
  
  const [uploading, setUploading] = useState(false)
  const handleUploadJson = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const res = await sandboxApi.uploadJson(file)
      if (res.status === 'success') {
        alert(res.message || '本地 JSON 数据导入成功。')
        mutateHistory()
      } else {
        alert(res.message || '上传失败')
      }
    } catch (err: any) {
      console.error(err)
      alert(err.response?.data?.detail || err.message || '文件上传失败')
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const [model, setModel] = useState('gpt-5.5')
  
  // ── A/B Prompt ──
  const [promptA, setPromptA] = useState('请对以下转发的消息进行简明扼要的中文总结：')
  const [promptB, setPromptB] = useState('请作为加密货币分析师，过滤掉一切无用的广告和冗余，仅提取以下消息中关于【项目动态、Alpha机会、DeFi收益】的干货核心，按结构化排版汇总：')
  
  // ── 输入样本 ──
  const [testMsg, setTestMsg] = useState('请在此处输入测试文本，或者使用左侧的消息聚合器从 50 个真实频道中一键提取聚合。')
  
  // ── 运行状态 A ──
  const [resultA, setResultA] = useState<string | null>(null)
  const [errorA, setErrorA] = useState<string | null>(null)
  const [elapsedA, setElapsedA] = useState<number | null>(null)
  
  // ── 运行状态 B ──
  const [resultB, setResultB] = useState<string | null>(null)
  const [errorB, setErrorB] = useState<string | null>(null)
  const [elapsedB, setElapsedB] = useState<number | null>(null)
  
  const [loading, setLoading] = useState(false)
  const [copiedA, setCopiedA] = useState(false)
  const [copiedB, setCopiedB] = useState(false)

  // ── 50 频道内容大模型分析报告 ──
  const [showAnalysis, setShowAnalysis] = useState(false)
  const [analysisReport, setAnalysisReport] = useState<string | null>(null)
  const [analyzing, setAnalyzing] = useState(false)

  // ── 消息流聚合参数 ──
  const [selectedChannels, setSelectedChannels] = useState<string[]>([])
  const [selectedDays, setSelectedDays] = useState(1)
  const [aggregating, setAggregating] = useState(false)

  // 默认模型加载
  useEffect(() => {
    if (defaultModelData?.default_model) {
      setModel(defaultModelData.default_model)
    }
  }, [defaultModelData])

  // 执行 A/B 对照测试
  const runAbTest = async () => {
    if (!testMsg.trim()) return
    setLoading(true)
    setResultA(null)
    setErrorA(null)
    setResultB(null)
    setErrorB(null)

    const t0 = Date.now()

    // 并发测试 A 和 B
    const promiseA = sandboxApi.runTest({ prompt: promptA, model, test_message: testMsg })
      .then(data => {
        setElapsedA(Date.now() - t0)
        if (data.status === 'success') {
          setResultA(typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2))
        } else {
          setErrorA(data.detail || '测试失败')
        }
      })
      .catch((e: any) => {
        setElapsedA(Date.now() - t0)
        setErrorA(e?.response?.data?.detail || e.message || '请求失败')
      })

    const promiseB = sandboxApi.runTest({ prompt: promptB, model, test_message: testMsg })
      .then(data => {
        setElapsedB(Date.now() - t0)
        if (data.status === 'success') {
          setResultB(typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2))
        } else {
          setErrorB(data.detail || '测试失败')
        }
      })
      .catch((e: any) => {
        setElapsedB(Date.now() - t0)
        setErrorB(e?.response?.data?.detail || e.message || '请求失败')
      })

    await Promise.all([promiseA, promiseB])
    setLoading(false)
  }

  // 聚合历史消息流
  const handleAggregate = async () => {
    if (selectedChannels.length === 0) return
    setAggregating(true)
    try {
      const res = await sandboxApi.aggregateMessages({
        channels: selectedChannels,
        days: selectedDays
      })
      if (res.status === 'success') {
        setTestMsg(res.text)
      } else {
        alert(res.message || '聚合消息失败')
      }
    } catch (e) {
      console.error(e)
      alert('网络请求异常')
    } finally {
      setAggregating(false)
    }
  }

  // 分析 50 频道
  const handleAnalyze = async () => {
    setAnalyzing(true)
    setAnalysisReport(null)
    try {
      const res = await sandboxApi.analyzeChannels({ model })
      if (res.status === 'success') {
        setAnalysisReport(res.result)
      } else {
        alert(res.message || '分析失败')
      }
    } catch (e) {
      console.error(e)
      alert('请求大模型诊断超时或出错')
    } finally {
      setAnalyzing(false)
    }
  }

  const toggleChannelSelection = (link: string) => {
    setSelectedChannels(prev =>
      prev.includes(link) ? prev.filter(l => l !== link) : [...prev, link]
    )
  }

  const selectAllChannels = () => {
    if (!historyData?.data) return
    const all = historyData.data.map(ch => ch.channel_link)
    setSelectedChannels(selectedChannels.length === all.length ? [] : all)
  }

  // 复制 Prompt B 的推荐（常用于训练完成后的产出）
  const handleApplyPromptBToA = () => {
    setPromptA(promptB)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%', overflow: 'hidden' }}>
      
      {/* ── 50 频道内容大图全景诊断 ──────────────── */}
      <div className="card-glass" style={{ padding: 16, flexShrink: 0 }}>
        <button
          onClick={() => setShowAnalysis(!showAnalysis)}
          style={{
            background: 'none', border: 'none', width: '100%', display: 'flex',
            alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer',
            fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', outline: 'none'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <BarChart2 size={16} color="var(--color-primary)" />
            <span>📊 50 频道全局内容调性深度分析报告 (点击展开诊断大屏)</span>
          </div>
          {showAnalysis ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        {showAnalysis && (
          <div style={{ marginTop: 12, borderTop: '1px solid var(--border-light)', paddingTop: 12 }}>
            {!analysisReport && !analyzing && (
              <div style={{ textAlign: 'center', padding: '20px 0' }}>
                <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>
                  系统检测到本地抓取消息数据库有 50+ 频道抓取数据。点击下方按钮以调用 AI 进行板块聚类与总结 Prompt 自动生成。
                </p>
                <button onClick={handleAnalyze} style={primaryBtn}>
                  开始大模型内容调性分析
                </button>
              </div>
            )}

            {analyzing && (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: 30 }}>
                <span className="spinner" />
                <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>正在对 50 个频道的消息抽样进行主题聚类与 Prompt 自动训练 (约需 10-25s)...</span>
              </div>
            )}

            {analysisReport && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <pre style={{
                  padding: 16, background: '#0a0b0f', border: '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-sm)', maxHeight: 250, overflowY: 'auto',
                  fontSize: 13, color: '#f3f4f6', whiteSpace: 'pre-wrap', lineHeight: 1.6
                }}>
                  {analysisReport}
                </pre>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button
                    onClick={() => {
                      // 从 Markdown 提取 Prompt 尝试自动填充（模糊匹配 System Prompt 以后的内容，或者由用户自行复制）
                      alert('已完成分析！请在报告中复制 AI 专门生成的 System Prompt，并粘贴至下方 Prompt 编辑框进行测试。')
                    }}
                    style={secondaryBtn}
                  >
                    💡 提示
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 主操作区 ─────────────────────────────── */}
      <div style={{ display: 'flex', gap: 20, flex: 1, minHeight: 0 }}>
        
        {/* 左列: 数据流聚合器 */}
        <div className="card-glass" style={{ width: 320, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid var(--border-light)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Layers size={15} color="var(--color-primary)" />
              历史消息流聚合器
            </span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <label style={{ color: 'var(--color-primary)', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center' }} title="上传 50 个频道的 JSON 消息抓取备份数据以分析">
                {uploading ? '上传中...' : '导入JSON'}
                <input type="file" accept=".json" onChange={handleUploadJson} disabled={uploading} style={{ display: 'none' }} />
              </label>
              {historyData?.data && historyData.data.length > 0 && (
                <button
                  onClick={selectAllChannels}
                  style={{ background: 'none', border: 'none', color: 'var(--color-primary)', fontSize: 11, cursor: 'pointer' }}
                >
                  {selectedChannels.length === historyData.data.length ? '取消' : '全选'}
                </button>
              )}
            </div>
          </div>

          {/* 聚合天数选择 */}
          <div style={{ padding: '12px 16px', background: 'var(--bg-surface-hover)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border-light)' }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>聚合时间范围:</span>
            <select
              value={selectedDays}
              onChange={e => setSelectedDays(Number(e.target.value))}
              style={{ ...inputStyle, width: 'auto', height: 26, padding: '0 8px', fontSize: 12 }}
            >
              <option value={1}>过去 24 小时</option>
              <option value={3}>过去 3 天</option>
              <option value={7}>过去 7 天</option>
            </select>
          </div>

          {/* 频道选择列表 */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {historyError ? (
              <div style={{ padding: 12, color: '#ef4444', fontSize: 12 }}>
                加载失败: {historyError.response?.data?.detail || historyError.message || '接口请求异常'}
                <div style={{ marginTop: 8 }}>
                  <label style={uploadBtnStyle}>
                    重新选择 JSON 导入
                    <input type="file" accept=".json" onChange={handleUploadJson} disabled={uploading} style={{ display: 'none' }} />
                  </label>
                </div>
              </div>
            ) : !historyData ? (
              <div style={{ textAlign: 'center', padding: 20, color: 'var(--text-muted)', fontSize: 12 }}>加载本地频道列表中...</div>
            ) : historyData.status === 'error' ? (
              <div style={{ padding: 12, color: 'var(--text-secondary)', fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span>{historyData.message}</span>
                <div>
                  <label style={uploadBtnStyle}>
                    导入 JSON 消息数据
                    <input type="file" accept=".json" onChange={handleUploadJson} disabled={uploading} style={{ display: 'none' }} />
                  </label>
                </div>
              </div>
            ) : historyData.data?.map(ch => {
              const isSelected = selectedChannels.includes(ch.channel_link)
              return (
                <div
                  key={ch.channel_link}
                  onClick={() => toggleChannelSelection(ch.channel_link)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)', cursor: 'pointer',
                    background: isSelected ? 'var(--bg-surface-active)' : 'transparent',
                    transition: 'var(--transition-fast)'
                  }}
                >
                  {isSelected ? <CheckSquare size={14} color="var(--color-primary)" /> : <Square size={14} color="var(--text-muted)" />}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {ch.channel_name}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      抓取到 {ch.message_count} 条消息
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* 触发聚合按钮 */}
          <div style={{ padding: 12, borderTop: '1px solid var(--border-light)' }}>
            <button
              onClick={handleAggregate}
              disabled={selectedChannels.length === 0 || aggregating}
              style={{
                ...primaryBtn, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                background: selectedChannels.length === 0 ? 'var(--bg-surface-hover)' : 'var(--brand-gradient)',
                color: selectedChannels.length === 0 ? 'var(--text-muted)' : '#fff',
                cursor: selectedChannels.length === 0 || aggregating ? 'not-allowed' : 'pointer'
              }}
            >
              {aggregating ? <><span className="spinner" /> 聚合数据中...</> : <><Zap size={14} /> 聚合真实消息流</>}
            </button>
          </div>
        </div>

        {/* 右侧: A/B 训练与比对大屏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0, overflowY: 'auto' }}>
          
          {/* 测试输入框 */}
          <div className="card-glass" style={{ padding: 16, flexShrink: 0 }}>
            <h3 style={{ margin: '0 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>测试输入消息流</h3>
            <textarea
              value={testMsg}
              onChange={e => setTestMsg(e.target.value)}
              rows={4}
              placeholder="输入或聚合测试文本..."
              style={{ ...inputStyle, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
            />
          </div>

          {/* 提示词双栏 A/B 编辑区 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, minHeight: 280, flexShrink: 0 }}>
            
            {/* A 栏 */}
            <div className="card-glass" style={{ padding: 16, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexShrink: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: '#3b82f6' }}>🧪 总结 Prompt A (对比基准)</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}> rows: 8 </span>
              </div>
              <textarea
                value={promptA}
                onChange={e => setPromptA(e.target.value)}
                style={{ ...inputStyle, flex: 1, resize: 'none', fontSize: 12, lineHeight: 1.5 }}
                placeholder="输入 Prompt A..."
              />
            </div>

            {/* B 栏 */}
            <div className="card-glass" style={{ padding: 16, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexShrink: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: '#a855f7' }}>🔥 总结 Prompt B (调优目标)</span>
                <button
                  onClick={handleApplyPromptBToA}
                  style={{ background: 'none', border: 'none', color: 'var(--color-primary)', fontSize: 11, cursor: 'pointer' }}
                  title="把 Prompt B 的配置复制覆盖给 A，继续做下一轮的迭代对比"
                >
                  应用为基准
                </button>
              </div>
              <textarea
                value={promptB}
                onChange={e => setPromptB(e.target.value)}
                style={{ ...inputStyle, flex: 1, resize: 'none', fontSize: 12, lineHeight: 1.5 }}
                placeholder="输入 Prompt B..."
              />
            </div>

          </div>

          {/* 模型与运行测试按钮 */}
          <div className="card-glass" style={{ padding: 12, flexShrink: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>训练模型:</span>
              <select
                value={model}
                onChange={e => setModel(e.target.value)}
                style={{ ...inputStyle, width: 150, height: 30, padding: '0 8px', fontSize: 12 }}
              >
                {modelsDict && Object.entries(modelsDict).map(([provider, list]) => (
                  <optgroup key={provider} label={provider.toUpperCase()}>
                    {list.map(m => <option key={m} value={m}>{m}</option>)}
                  </optgroup>
                ))}
              </select>
            </div>

            <button
              onClick={runAbTest}
              disabled={loading || !testMsg.trim()}
              style={{
                ...primaryBtn, display: 'flex', alignItems: 'center', gap: 6,
                background: loading ? 'var(--bg-surface-hover)' : 'var(--brand-gradient)',
                cursor: loading ? 'not-allowed' : 'pointer'
              }}
            >
              {loading ? <><span className="spinner" /> 正在并行生成 A/B 总结...</> : <><Zap size={14} /> ⚡ 运行 A/B 对抗测试</>}
            </button>
          </div>

          {/* A/B 结果输出对照屏 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, height: 500, flexShrink: 0, minHeight: 0 }}>
            
            {/* 结果 A */}
            <div className="card-glass" style={{ padding: 16, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexShrink: 0 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Clock size={12} />
                  结果 A 指标: {elapsedA ? `${(elapsedA/1000).toFixed(1)}s` : '—'}
                </span>
                {resultA && (
                  <button
                    onClick={() => { navigator.clipboard.writeText(resultA); setCopiedA(true); setTimeout(() => setCopiedA(false), 2000) }}
                    style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
                  >
                    {copiedA ? <Check size={12} color="var(--color-success)" /> : <Copy size={12} />}
                  </button>
                )}
              </div>
              <div style={{ flex: 1, overflowY: 'auto', background: '#0a0b0f', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', padding: 10 }}>
                {errorA && <div style={{ fontSize: 12, color: '#ef4444' }}>{errorA}</div>}
                {resultA && <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', color: '#f3f4f6', lineHeight: 1.5 }}>{resultA}</pre>}
                {!resultA && !errorA && (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: 'var(--text-muted)' }}>
                    等待对比评测...
                  </div>
                )}
              </div>
            </div>

            {/* 结果 B */}
            <div className="card-glass" style={{ padding: 16, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexShrink: 0 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Clock size={12} />
                  结果 B 指标: {elapsedB ? `${(elapsedB/1000).toFixed(1)}s` : '—'}
                </span>
                {resultB && (
                  <button
                    onClick={() => { navigator.clipboard.writeText(resultB); setCopiedB(true); setTimeout(() => setCopiedB(false), 2000) }}
                    style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
                  >
                    {copiedB ? <Check size={12} color="var(--color-success)" /> : <Copy size={12} />}
                  </button>
                )}
              </div>
              <div style={{ flex: 1, overflowY: 'auto', background: '#0a0b0f', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)', padding: 10 }}>
                {errorB && <div style={{ fontSize: 12, color: '#ef4444' }}>{errorB}</div>}
                {resultB && <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', color: '#f3f4f6', lineHeight: 1.5 }}>{resultB}</pre>}
                {!resultB && !errorB && (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: 'var(--text-muted)' }}>
                    等待对比评测...
                  </div>
                )}
              </div>
            </div>

          </div>

        </div>

      </div>

    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '8px 12px', background: 'var(--bg-base)',
  border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)', fontSize: 13, outline: 'none',
  boxSizing: 'border-box',
}

const primaryBtn: React.CSSProperties = {
  padding: '8px 18px', background: 'var(--brand-gradient)', color: '#fff',
  border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer',
  fontSize: 13, fontWeight: 600, transition: 'var(--transition-fast)'
}

const secondaryBtn: React.CSSProperties = {
  padding: '6px 14px', background: 'var(--bg-surface-hover)', border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 12, cursor: 'pointer'
}

const uploadBtnStyle: React.CSSProperties = {
  display: 'inline-block',
  padding: '6px 16px',
  background: 'var(--bg-surface-hover)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--color-primary)',
  fontSize: 12,
  cursor: 'pointer',
  textAlign: 'center',
  fontWeight: 500,
  transition: 'var(--transition-fast)'
}
