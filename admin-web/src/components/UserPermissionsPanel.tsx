import { useState } from 'react';
import { api, type ClientUser } from '../api';
export default function UserPermissionsPanel() {
  const [users, setUsers] = useState<ClientUser[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setMessage('');
    try { setUsers((await api.users()).users); }
    catch { setMessage('读取失败，请重试'); }
    finally { setBusy(false); }
  }
  async function save(user: ClientUser) {
    setBusy(true); setMessage('');
    try {
      const saved = await api.setUserPermissions(user);
      setUsers(current => current.map(item => item.userId === saved.userId ? saved : item));
      setMessage(`已保存 ${saved.userId}`);
    } catch { setMessage('保存失败，请刷新后重试'); }
    finally { setBusy(false); }
  }
  return <details className="user-permissions"><summary>小程序测试人员授权</summary>
    <p>普通用户可导航；测试人员可上传和计算。配置应用权限还允许指纹入库立即生效、参数应用和回滚。此授权作用于本服务实例管理的全部场所。</p>
    <button disabled={busy} onClick={() => void load()}>刷新用户（最近100位）</button>
    <p role="status">{message}</p>
    <table><thead><tr><th>用户编号</th><th>测试人员</th><th>允许应用配置</th></tr></thead><tbody>
      {users.map(user => <tr key={user.userId}><td>{user.userId}</td>
        <td><input type="checkbox" aria-label={`${user.userId} 测试人员`} checked={user.isTester} disabled={busy} onChange={e => void save({ ...user, isTester: e.target.checked, canApplyConfig: e.target.checked && user.canApplyConfig })}/></td>
        <td><input type="checkbox" aria-label={`${user.userId} 允许应用配置`} checked={user.canApplyConfig} disabled={busy || !user.isTester} onChange={e => void save({ ...user, canApplyConfig: e.target.checked })}/></td>
      </tr>)}
    </tbody></table>
  </details>;
}
