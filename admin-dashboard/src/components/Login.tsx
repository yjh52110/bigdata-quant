import React, { useState } from 'react';
import { Lock } from 'lucide-react';
import { API_BASE_URL, setApiKey } from '../api';
import { useI18n } from '../i18n';

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const { t, lang, setLang } = useI18n();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecking(true);
    setError(null);
    try {
      // Any real /api/* route (other than /api/health) is protected by the
      // backend's api_key_guard middleware -- this is the same check the
      // backend itself enforces, not a separate/fake auth system.
      const res = await fetch(`${API_BASE_URL}/api/overview`, {
        headers: { 'X-API-Key': password },
      });
      if (res.ok) {
        setApiKey(password);
        onSuccess();
      } else {
        setError(t('login.wrong'));
      }
    } catch {
      setError(`${t('login.unreachable')} ${API_BASE_URL}`);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="h-screen w-screen flex items-center justify-center bg-slate-900 px-4">
      <form onSubmit={submit} className="glass-panel w-full max-w-sm p-8 flex flex-col gap-5">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="w-12 h-12 rounded-xl bg-blue-500/20 flex items-center justify-center">
            <Lock className="text-blue-400" size={22} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">{t('login.title')}</h1>
            <p className="text-sm text-slate-400 mt-1">{t('login.prompt')}</p>
          </div>
        </div>

        <input
          type="password"
          autoFocus
          value={password}
          onChange={e => setPassword(e.target.value)}
          placeholder={t('login.password')}
          className="w-full px-4 py-3 rounded-lg bg-slate-800 border border-slate-700 text-white placeholder-slate-500 outline-none focus:border-blue-500 transition-colors"
        />

        {error && <p className="text-sm text-red-400 text-center">{error}</p>}

        <button
          type="submit"
          disabled={checking}
          className="w-full py-3 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 text-white font-medium rounded-lg transition-colors"
        >
          {checking ? t('login.checking') : t('login.unlock')}
        </button>

        <div className="flex gap-1 bg-slate-800/60 rounded-lg p-1">
          {(['zh', 'en'] as const).map(l => (
            <button
              key={l}
              type="button"
              onClick={() => setLang(l)}
              className={`flex-1 text-xs py-1.5 rounded-md transition-colors ${
                lang === l ? 'bg-blue-500/25 text-blue-300' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {l === 'zh' ? '中文' : 'English'}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
