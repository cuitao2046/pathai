import { FormEvent, useState } from 'react';
import { api, ApiError } from '../api';
import type { MePayload } from '../types';

/** 新密码按 UTF-8 编码至少 8 字节（与后端 password_too_short 规则一致）。 */
function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

export default function ForceChangePage({
  username,
  onChanged,
}: {
  username: string;
  onChanged: (me: MePayload) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!currentPassword || !newPassword || !confirmPassword) {
      setError('请填写全部字段');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致');
      return;
    }
    if (utf8ByteLength(newPassword) < 8) {
      setError('新密码按 UTF-8 编码后至少 8 个字节');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const me = await api.changePassword(currentPassword, newPassword, confirmPassword);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      onChanged(me);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : '修改失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">修改初始密码</h1>
        <p className="auth-subtitle">
          账户 {username} 仍在使用初始密码，为保障安全请先设置新密码
        </p>
        <label className="field">
          <span className="field-label">当前密码</span>
          <input
            type="password"
            value={currentPassword}
            autoComplete="current-password"
            onChange={(e) => setCurrentPassword(e.target.value)}
            disabled={submitting}
          />
        </label>
        <label className="field">
          <span className="field-label">新密码（至少 8 个字节）</span>
          <input
            type="password"
            value={newPassword}
            autoComplete="new-password"
            onChange={(e) => setNewPassword(e.target.value)}
            disabled={submitting}
          />
        </label>
        <label className="field">
          <span className="field-label">确认新密码</span>
          <input
            type="password"
            value={confirmPassword}
            autoComplete="new-password"
            onChange={(e) => setConfirmPassword(e.target.value)}
            disabled={submitting}
          />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button type="submit" className="btn btn-primary btn-block" disabled={submitting}>
          {submitting ? '提交中…' : '确认修改'}
        </button>
      </form>
    </div>
  );
}
