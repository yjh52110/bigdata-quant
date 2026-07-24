import React, { useState, useEffect } from 'react';
import { KeyRound } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';

export default function GeminiPool() {
  const { t } = useI18n();
  const [status, setStatus] = useState({
    configured: false,
    total_keys: 0,
    active_keys: 0,
    exhausted_keys: 0,
    requests_today_total: 0,
    keys: [] as any[],
  });

  useEffect(() => {
    const load = () => {
      apiFetch('/api/gemini/status')
        .then(r => r.json())
        .then(data => setStatus(data))
        .catch(console.error);
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('gm.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base">{t('gm.subtitle')}</p>
      </header>

      {!status.configured && (
        <div className="glass-panel p-4 border-l-4 border-l-amber-500 bg-amber-900/20 text-amber-200 text-sm">
          {t('gm.notConfigured')}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400 mb-1">{t('gm.requestsToday')}</p>
          <div className="text-3xl font-bold text-white">{status.requests_today_total}</div>
        </div>
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400 mb-1">{t('gm.activeKeys')}</p>
          <div className="text-3xl font-bold text-emerald-400">{status.active_keys} <span className="text-base text-slate-500">/ {status.total_keys}</span></div>
        </div>
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400 mb-1">{t('gm.cooldown')}</p>
          <div className="text-3xl font-bold text-amber-400">{status.exhausted_keys}</div>
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">{t('gm.rotationTitle')}</h3>
        <div className="flex-1 overflow-x-auto">
          <table className="w-full text-left border-collapse min-w-[500px]">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('gm.colKey')}</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('gm.colStatus')}</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('gm.colToday')}</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('gm.colCooldown')}</th>
              </tr>
            </thead>
            <tbody>
              {status.keys.length === 0 ? (
                <tr><td colSpan={4} className="py-6 text-center text-slate-500">{t('gm.empty')}</td></tr>
              ) : status.keys.map((k: any, i: number) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4 flex items-center gap-2">
                    <KeyRound size={14} className="text-slate-500" />
                    <span className="text-slate-200 font-mono text-sm">{k.alias}</span>
                  </td>
                  <td className="py-3 px-4">
                    <span className={`flex items-center gap-1.5 text-sm ${k.status === 'Active' ? 'text-emerald-400' : 'text-amber-400'}`}>
                      <span className={`w-2 h-2 rounded-full ${k.status === 'Active' ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
                      {k.status === 'Active' ? t('gm.active') : t('gm.cooling')}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-sm font-mono text-slate-300">{k.requests_today}</td>
                  <td className="py-3 px-4 text-sm font-mono text-slate-400">{k.cooldown_remaining_s > 0 ? `${k.cooldown_remaining_s}s` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
