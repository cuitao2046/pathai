import { FormEvent, useState } from 'react';
import { api, ApiError } from '../api';
import type { MePayload } from '../types';

export default function LoginPage({ onLoggedIn }: { onLoggedIn: (me: MePayload) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError('请输入用户名和密码');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const me = await api.login(username.trim(), password);
      setPassword('');
      onLoggedIn(me);
    } catch (exc) {
      setError(
        exc instanceof ApiError ? exc.message : '登录失败，请稍后重试',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">PathAI 地图管理后台</h1>
        <p className="auth-subtitle">请使用管理员账户登录</p>
        <label className="field">
          <span className="field-label">用户名</span>
          <input
            type="text"
            value={username}
            autoComplete="username"
            onChange={(e) => setUsername(e.target.value)}
            disabled={submitting}
          />
        </label>
        <label className="field">
          <span className="field-label">密码</span>
          <input
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
          />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button type="submit" className="btn btn-primary btn-block" disabled={submitting}>
          {submitting ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  );
}
