import { useState } from 'react'
import { LogIn, AlertCircle } from 'lucide-react'
import { authApi } from '../api'

interface Props {
  onLogin: (username: string) => void
}

export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) return
    setError('')
    setLoading(true)
    try {
      const data = await authApi.login(username, password)
      onLogin(data.username)
    } catch (err: any) {
      setError(err?.response?.data?.detail || '登录失败，请检查用户名和密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100vh', background: 'var(--bg-base)',
    }}>
      <div className="card-glass animate-fade-in" style={{
        width: 400, padding: 40, textAlign: 'center',
      }}>
        {/* Logo */}
        <div style={{ marginBottom: 24 }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="url(#loginGrad)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <defs>
              <linearGradient id="loginGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#3b82f6" />
                <stop offset="100%" stopColor="#8b5cf6" />
              </linearGradient>
            </defs>
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
          <h1 style={{
            margin: '12px 0 4px', fontSize: 22, fontWeight: 700,
            background: 'var(--brand-gradient)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          }}>
            TG Forwarder Console
          </h1>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
            请登录以继续
          </p>
        </div>

        {/* 错误提示 */}
        {error && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px',
            background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
            borderRadius: 'var(--radius-sm)', marginBottom: 16, textAlign: 'left',
          }}>
            <AlertCircle size={16} color="#ef4444" />
            <span style={{ fontSize: 13, color: '#ef4444' }}>{error}</span>
          </div>
        )}

        {/* 登录表单 */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="用户名"
            autoFocus
            style={inputStyle}
          />
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="密码"
            style={inputStyle}
          />
          <button
            type="submit"
            disabled={loading || !username.trim() || !password.trim()}
            style={{
              marginTop: 4, padding: '10px 20px',
              background: loading ? 'var(--bg-surface-hover)' : 'var(--brand-gradient)',
              color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)',
              fontSize: 14, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            }}
          >
            {loading ? (
              <><span className="spinner" /> 登录中...</>
            ) : (
              <><LogIn size={16} /> 登录</>
            )}
          </button>
        </form>
      </div>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '10px 14px', background: 'var(--bg-base)',
  border: '1px solid var(--border-light)', borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)', fontSize: 14, outline: 'none', boxSizing: 'border-box',
}
