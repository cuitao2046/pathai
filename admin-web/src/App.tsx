import { useEffect, useState } from 'react';
import { api, setCsrfToken, setUnauthorizedHandler } from './api';
import LoginPage from './pages/LoginPage';
import ForceChangePage from './pages/ForceChangePage';
import Workspace from './pages/Workspace';

type Phase = 'boot' | 'login' | 'force' | 'ready';

export default function App() {
  const [phase, setPhase] = useState<Phase>('boot');
  const [username, setUsername] = useState('');

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setCsrfToken(null);
      setPhase('login');
    });
    api
      .me()
      .then((me) => {
        setCsrfToken(me.csrfToken);
        setUsername(me.username);
        setPhase(me.mustChangePassword ? 'force' : 'ready');
      })
      .catch(() => {
        setPhase('login');
      });
  }, []);

  if (phase === 'boot') {
    return (
      <div className="boot-screen">
        <p>正在加载…</p>
      </div>
    );
  }

  if (phase === 'login') {
    return (
      <LoginPage
        onLoggedIn={(me) => {
          setCsrfToken(me.csrfToken);
          setUsername(me.username);
          setPhase(me.mustChangePassword ? 'force' : 'ready');
        }}
      />
    );
  }

  if (phase === 'force') {
    return (
      <ForceChangePage
        username={username}
        onChanged={(me) => {
          setCsrfToken(me.csrfToken);
          setPhase('ready');
        }}
      />
    );
  }

  return (
    <Workspace
      username={username}
      onLogout={() => {
        setCsrfToken(null);
        setPhase('login');
      }}
    />
  );
}
