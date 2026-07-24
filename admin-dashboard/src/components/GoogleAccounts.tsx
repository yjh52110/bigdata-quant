import React, { useState, useEffect } from 'react';
import { RefreshCw, Plus } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';

const fmtBytes = (b: number) => {
  if (!b) return '0 GB';
  const gb = b / 1024 ** 3;
  if (gb > 1024) return `${(gb / 1024).toFixed(2)} TB`;
  return `${gb.toFixed(2)} GB`;
};

export default function GoogleAccounts() {
  const { t } = useI18n();
  const [accountData, setAccountData] = useState<any>({ poolStatus: {}, accounts: [], transferToday: null, oauthConfigured: false });
  const [adding, setAdding] = useState(false);
  const [newIndex, setNewIndex] = useState('');
  const [addError, setAddError] = useState<string | null>(null);

  const load = () => {
    apiFetch('/api/accounts')
      .then(r => r.json())
      .then(data => setAccountData(data))
      .catch(console.error);
  };

  const startOAuth = async () => {
    setAddError(null);
    try {
      const res = await apiFetch('/api/accounts/auth-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_index: newIndex.trim() }),
      });
      const data = await res.json();
      if (!res.ok) { setAddError(data.detail || 'Could not start authorization'); return; }
      window.open(data.auth_url, '_blank', 'noopener');
      setAdding(false);
      setNewIndex('');
    } catch (e) {
      setAddError(String(e));
    }
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const poolStatus = accountData.poolStatus || {};
  const accounts = accountData.accounts || [];
  const transfer = accountData.transferToday;

  const uploadPct = transfer ? Math.min(100, (transfer.upload_bytes / transfer.upload_limit_bytes) * 100) : 0;
  const downloadPct = transfer ? Math.min(100, (transfer.download_bytes / transfer.download_limit_bytes) * 100) : 0;

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header className="flex flex-col sm:flex-row sm:justify-between sm:items-end gap-3">
        <div>
          <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('acc.title')}</h2>
          <p className="text-slate-400 text-sm sm:text-base">{t('acc.summary', { n: poolStatus.total_accounts ?? 0, tb: (poolStatus.total_capacity_tb ?? 0).toFixed(2) })}</p>
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button onClick={() => setAdding(v => !v)} className="flex items-center justify-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors">
            <Plus size={16} />
            <span>{t('acc.add')}</span>
          </button>
          <button onClick={load} className="flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors shadow-lg shadow-blue-500/20">
            <RefreshCw size={16} />
            <span>{t('acc.sync')}</span>
          </button>
        </div>
      </header>

      {adding && (
        <div className="glass-panel p-5 border-l-4 border-l-emerald-500">
          <h3 className="text-white font-semibold mb-3">{t('acc.connectTitle')}</h3>
          {!accountData.oauthConfigured ? (
            <p className="text-amber-300 text-sm">{accountData.oauthHint}</p>
          ) : (
            <div className="flex flex-col sm:flex-row gap-3">
              <input
                value={newIndex}
                onChange={e => setNewIndex(e.target.value)}
                placeholder={t('acc.labelPlaceholder')}
                className="flex-1 px-4 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white placeholder-slate-500 outline-none focus:border-emerald-500"
              />
              <button
                onClick={startOAuth}
                disabled={!newIndex.trim()}
                className="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
              >
                {t('acc.authorize')}
              </button>
            </div>
          )}
          {addError && <p className="text-red-400 text-sm mt-3">{addError}</p>}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="glass-panel p-5 flex flex-col justify-between">
          <div>
            <h3 className="text-lg font-semibold text-white mb-1">{t('acc.transferTitle')}</h3>
            <p className="text-xs text-slate-500 mb-6">
              {t('acc.transferNote')}
            </p>

            <div className="space-y-6">
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-slate-300">{t('acc.upload')}</span>
                  <span className="text-blue-400 font-bold">{transfer ? fmtBytes(transfer.upload_bytes) : '—'} / 750 GB</span>
                </div>
                <div className="w-full bg-slate-700/50 rounded-full h-2.5">
                  <div className="bg-blue-500 h-2.5 rounded-full" style={{ width: `${uploadPct}%` }}></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-slate-300">{t('acc.download')}</span>
                  <span className="text-purple-400 font-bold">{transfer ? fmtBytes(transfer.download_bytes) : '—'} / 10 TB</span>
                </div>
                <div className="w-full bg-slate-700/50 rounded-full h-2.5">
                  <div className="bg-purple-500 h-2.5 rounded-full" style={{ width: `${downloadPct}%` }}></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="lg:col-span-2 glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4">{t('acc.healthTitle')}</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[620px]">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('acc.colId')}</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('acc.colEmail')}</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('acc.colStatus')}</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">{t('acc.colUsage')}</th>
                </tr>
              </thead>
              <tbody>
                {accounts.length === 0 ? (
                  <tr><td colSpan={4} className="py-6 text-center text-slate-500">{t('acc.empty')}</td></tr>
                ) : accounts.map((acc: any, i: number) => (
                  <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                    <td className="py-3 px-4 text-slate-200 font-medium">{acc.account_index}</td>
                    <td className="py-3 px-4 text-slate-400 text-sm">{acc.email || t('acc.unknown')}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 text-xs font-semibold rounded bg-opacity-20 border border-opacity-30 ${
                        acc.health === 'ok' ? 'bg-emerald-500 border-emerald-500 text-emerald-400' :
                        acc.health === 'expired' ? 'bg-amber-500 border-amber-500 text-amber-400' :
                        'bg-red-500 border-red-500 text-red-400'
                      }`}>
                        {acc.is_connected ? t('acc.active') : t(acc.health === 'expired' ? 'st.expired' : 'st.error')}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-300 text-sm">{fmtBytes(acc.used || 0)} / {fmtBytes(acc.limit || 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
