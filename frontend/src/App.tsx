import { useState, useEffect } from 'react'
import { Rss, Settings, Cpu, Activity, LogOut, Key, X } from 'lucide-react'
import './App.css'

import { authApi } from './api'
import LoginPage from './pages/LoginPage'
import RulesPage from './pages/RulesPage'
import RssPage from './pages/RssPage'
import AiSandboxPage from './pages/AiSandboxPage'
import MonitorPage from './pages/MonitorPage'

type TabId = 'rss' | 'rules' | 'ai' | 'monitor'

interface TabDef {
  id: TabId
  label: string
  icon: React.ReactNode
}

const TABS: TabDef[] = [
  { id: 'rules', label: '规则设置', icon: <Settings size={16} /> },
  { id: 'rss', label: 'RSS 管理', icon: <Rss size={16} /> },
  { id: 'ai', label: 'AI 沙盒', icon: <Cpu size={16} /> },
  { id: 'monitor', label: '运维监控', icon: <Activity size={16} /> },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>('rules')
  const [user, setUser] = useState<string | null>(null)
  const [checking, setChecking] = useState(true)

  // 修改密码弹窗状态
  const [showPwdModal, setShowPwdModal] = useState(false)
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [pwdError, setPwdError] = useState<string | null>(null)
  const [pwdSuccess, setPwdSuccess] = useState<string | null>(null)
  const [pwdLoading, setPwdLoading] = useState(false)

  // 启动时检查会话
  useEffect(() => {
    authApi.me()
      .then(data => setUser(data.username))
      .catch(() => setUser(null))
      .finally(() => setChecking(false))
  }, [])

  const handleLogout = async () => {
    await authApi.logout()
    setUser(null)
  }

  const handlePwdChange = async (e: React.FormEvent) => {
    e.preventDefault()
    if (newPwd !== confirmPwd) {
      setPwdError('两次输入的新密码不一致')
      return
    }
    setPwdLoading(true)
    setPwdError(null)
    setPwdSuccess(null)
    try {
      const fd = new FormData()
      fd.append('current_password', oldPwd)
      fd.append('new_password', newPwd)
      fd.append('confirm_password', confirmPwd)
      const res = await authApi.changePassword(fd)
      if (res.success) {
        setPwdSuccess('密码修改成功')
        setOldPwd('')
        setNewPwd('')
        setConfirmPwd('')
        setTimeout(() => {
          setShowPwdModal(false)
          setPwdSuccess(null)
        }, 1500)
      } else {
        setPwdError(res.message || '密码修改失败')
      }
    } catch (err: any) {
      setPwdError(err?.response?.data?.message || err.message || '请求异常')
    } finally {
      setPwdLoading(false)
    }
  }

  // 加载中
  if (checking) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg-base)', color: 'var(--text-muted)', gap: 8 }}>
        <span className="spinner" /> 检查登录状态...
      </div>
    )
  }

  // 未登录 → 显示登录页
  if (!user) {
    return <LoginPage onLogin={(u) => setUser(u)} />
  }

  return (
    <div className="app-container">
      {/* ── 顶部导航栏 ─────────────────────────────── */}
      <header className="top-navbar">
        <div className="logo-section">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="url(#grad)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <defs>
              <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#3b82f6" />
                <stop offset="100%" stopColor="#8b5cf6" />
              </linearGradient>
            </defs>
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
          TG Forwarder Console
        </div>

        <nav className="nav-links">
          {TABS.map(tab => (
            <button
              key={tab.id}
              className={`nav-item ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </nav>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div className="health-dot active" />
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{user}</span>
          <button
            onClick={() => setShowPwdModal(true)}
            style={{
              display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px',
              background: 'transparent', border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
              fontSize: 12, cursor: 'pointer',
            }}
            title="修改密码"
          >
            <Key size={12} /> 修改密码
          </button>
          <button
            onClick={handleLogout}
            style={{
              display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px',
              background: 'transparent', border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)',
              fontSize: 12, cursor: 'pointer',
            }}
            title="登出"
          >
            <LogOut size={14} /> 登出
          </button>
        </div>
      </header>

      {/* ── 主内容区 ───────────────────────────────── */}
      <main className="main-content">
        {activeTab === 'rules' && <RulesPage />}
        {activeTab === 'rss' && <RssPage />}
        {activeTab === 'ai' && <AiSandboxPage />}
        {activeTab === 'monitor' && <MonitorPage />}
      </main>

      {/* ── 修改密码弹窗 ───────────────────────────── */}
      {showPwdModal && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div className="card-glass" style={{ width: 360, padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>安全设置 - 修改密码</h3>
              <button onClick={() => setShowPwdModal(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}><X size={16} /></button>
            </div>
            <form onSubmit={handlePwdChange} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {pwdError && <div style={{ fontSize: 12, color: '#ef4444', padding: '6px 10px', background: 'rgba(239,68,68,0.1)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(239,68,68,0.3)' }}>⚠️ {pwdError}</div>}
              {pwdSuccess && <div style={{ fontSize: 12, color: '#10b981', padding: '6px 10px', background: 'rgba(16,185,129,0.1)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(16,185,129,0.3)' }}>✅ {pwdSuccess}</div>}
              <div>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>当前密码</label>
                <input type="password" required value={oldPwd} onChange={e => setOldPwd(e.target.value)} style={modalInputStyle} />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>新密码</label>
                <input type="password" required value={newPwd} onChange={e => setNewPwd(e.target.value)} style={modalInputStyle} />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>确认新密码</label>
                <input type="password" required value={confirmPwd} onChange={e => setConfirmPwd(e.target.value)} style={modalInputStyle} />
              </div>
              <button
                type="submit"
                disabled={pwdLoading}
                style={{
                  marginTop: 8, padding: '10px 16px', background: 'var(--color-primary)', color: '#fff',
                  border: 'none', borderRadius: 'var(--radius-sm)', fontSize: 13, fontWeight: 600,
                  cursor: pwdLoading ? 'not-allowed' : 'pointer'
                }}
              >
                {pwdLoading ? '提交中...' : '提交修改'}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

const modalInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-base)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  fontSize: 13,
  outline: 'none',
  boxSizing: 'border-box',
}
