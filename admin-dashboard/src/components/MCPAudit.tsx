import React, { useState, useEffect } from 'react';
import { Activity, Lock, Users, Plus, Copy } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';

export default function MCPAudit() {
  const { t } = useI18n();
  const [logs, setLogs] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [newUser, setNewUser] = useState('');
  const [issuedKey, setIssuedKey] = useState<{ user: string; key: string } | null>(null);
  const [userError, setUserError] = useState<string | null>(null);

  const loadUsers = () => {
    apiFetch('/api/mcp/users').then(r => r.json()).then(d => setUsers(d.users || [])).catch(console.error);
  };

  useEffect(() => {
    const load = () => {
      apiFetch('/api/mcp/logs').then(r => r.json()).then(data => setLogs(data.logs || [])).catch(console.error);
      loadUsers();
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const createUser = async () => {
    setUserError(null);
    try {
      const res = await apiFetch('/api/mcp/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: newUser.trim() }),
      });
      const d = await res.json();
      if (!res.ok) { setUserError(d.detail || 'Failed'); return; }
      setIssuedKey({ user: d.user_id, key: d.api_key });
      setNewUser('');
      loadUsers();
    } catch (e) { setUserError(String(e)); }
  };

  const toggleUser = async (user_id: string, disabled: boolean) => {
    await apiFetch('/api/mcp/users/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id, disabled }),
    });
    loadUsers();
  };

  const errorCount = logs.filter(l => l.status.includes('error') || l.status.includes('blocked')).length;

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('mcp.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base break-words">{t('mcp.subtitle')}</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Lock className="text-blue-400" size={20} />
            {t('mcp.invocationCount')}
          </h3>
          <div className="text-4xl font-bold text-white">{logs.length}</div>
          <p className="text-xs text-slate-500 mt-2">{t('mcp.loggedCalls')}</p>
        </div>

        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Activity className="text-amber-400" size={20} />
            {t('mcp.errors')}
          </h3>
          {errorCount === 0 ? (
            <p className="text-slate-400">{t('mcp.noErrors')}</p>
          ) : (
            <div className="text-4xl font-bold text-red-400">{errorCount}</div>
          )}
        </div>
      </div>

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
          <Users size={18} className="text-emerald-400" />
          {t('mcp.usersTitle', { n: users.length })}
        </h3>
        <p className="text-xs text-slate-500 mb-4">
          {t('mcp.usersNote')}
        </p>

        <div className="flex flex-col sm:flex-row gap-3 mb-4">
          <input
            value={newUser}
            onChange={e => setNewUser(e.target.value)}
            placeholder={t('mcp.newUserPlaceholder')}
            className="flex-1 px-4 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white placeholder-slate-500 outline-none focus:border-emerald-500"
          />
          <button
            onClick={createUser}
            disabled={!newUser.trim()}
            className="flex items-center justify-center gap-2 px-5 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
          >
            <Plus size={16} /> {t('mcp.issueKey')}
          </button>
        </div>
        {userError && <p className="text-red-400 text-sm mb-3">{userError}</p>}

        {issuedKey && (
          <div className="mb-4 p-3 rounded-lg bg-emerald-900/25 border border-emerald-600/40">
            <p className="text-xs text-emerald-300 mb-2">
              {t('mcp.keyFor', { user: issuedKey.user })}
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs font-mono text-emerald-200 break-all">{issuedKey.key}</code>
              <button
                onClick={() => navigator.clipboard?.writeText(issuedKey.key)}
                className="p-1.5 text-emerald-300 hover:text-white hover:bg-emerald-700/40 rounded flex-shrink-0"
                title={t('mcp.copy')}
              >
                <Copy size={14} />
              </button>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse min-w-[520px]">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-2 px-3 text-sm font-semibold text-slate-400">{t('mcp.colUser')}</th>
                <th className="py-2 px-3 text-sm font-semibold text-slate-400">{t('mcp.colKey')}</th>
                <th className="py-2 px-3 text-sm font-semibold text-slate-400">{t('mcp.colToday')}</th>
                <th className="py-2 px-3 text-sm font-semibold text-slate-400">{t('mcp.colRate')}</th>
                <th className="py-2 px-3 text-sm font-semibold text-slate-400 text-right">{t('mcp.colStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr><td colSpan={5} className="py-5 text-center text-slate-500">{t('mcp.noUsers')}</td></tr>
              ) : users.map((u, i) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-2 px-3 text-slate-200">{u.user_id}</td>
                  <td className="py-2 px-3 text-slate-500 font-mono text-xs">{u.api_key_masked}</td>
                  <td className="py-2 px-3 text-slate-300 font-mono text-sm">{u.used_today}/{u.daily_quota}</td>
                  <td className="py-2 px-3 text-slate-400 font-mono text-sm">{u.rate_per_min}/min</td>
                  <td className="py-2 px-3 text-right">
                    <button
                      onClick={() => toggleUser(u.user_id, !u.disabled)}
                      className={`text-xs px-2 py-1 rounded border transition-colors ${
                        u.disabled
                          ? 'bg-red-500/20 text-red-400 border-red-500/30 hover:bg-red-500/30'
                          : 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/30'
                      }`}
                    >
                      {u.disabled ? t('mcp.disabled') : t('mcp.enabled')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">{t('mcp.liveLogs')}</h3>
        <div className="flex-1 bg-slate-950 rounded-lg border border-slate-800 p-4 font-mono text-xs overflow-y-auto space-y-2">
          {logs.length === 0 ? (
            <div className="text-slate-600 italic">{t('mcp.noLogs')}</div>
          ) : logs.map((log, i) => (
            <div key={i} className="flex flex-wrap gap-x-3 gap-y-1 border-b border-slate-800/50 pb-1.5 sm:border-0 sm:pb-0">
              <span className="text-slate-500">{log.time}</span>
              <span className="text-blue-400 sm:w-20 flex-shrink-0">[{log.client}]</span>
              <span className="text-purple-400 sm:w-48 flex-shrink-0 break-all">{log.action}</span>
              <span className={log.status.includes('error') || log.status.includes('blocked') ? "text-red-400 font-bold" : "text-emerald-400"}>{log.status}</span>
              <span className="text-slate-400 sm:ml-auto flex-shrink-0">{log.duration_ms}ms</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
