import React, { useState, useEffect } from 'react';
import { KeyRound, Activity } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';
import ResponsiveTable from './ResponsiveTable';

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

  const [probe, setProbe] = useState<any>(null);
  const [probing, setProbing] = useState(false);

  const runProbe = async () => {
    setProbing(true);
    try {
      const res = await apiFetch('/api/gemini/probe', { method: 'POST' });
      setProbe(await res.json());
    } catch (e) {
      setProbe({ configured: true, results: [], error: String(e) });
    } finally {
      setProbing(false);
    }
  };

  useEffect(() => {
    apiFetch('/api/gemini/reference').then(r => r.json()).then(d => setProbe((p: any) => p || { reference: d })).catch(() => {});
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

      <div className="glass-panel p-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-2">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Activity size={18} className="text-emerald-400" />
            {t('gm.probeResult')}
          </h3>
          <button
            onClick={runProbe}
            disabled={probing || !status.configured}
            className="px-4 min-h-[44px] sm:min-h-0 sm:py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-sm rounded-lg transition-colors"
          >
            {probing ? t('gm.probing') : t('gm.probe')}
          </button>
        </div>
        <p className="text-xs text-slate-500 mb-4">{t('gm.probeNote')}</p>

        {probe?.results?.length > 0 && (
          <div className="space-y-2 mb-4">
            {probe.results.map((r: any, i: number) => (
              <div key={i} className="bg-slate-800/50 border border-slate-700/50 rounded-lg p-3">
                <div className="flex flex-wrap items-center gap-2 mb-1">
                  <code className="text-xs font-mono text-slate-300">{r.alias}</code>
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    r.status === 'working' ? 'bg-emerald-500/20 text-emerald-400' :
                    r.status === 'rate_limited' ? 'bg-amber-500/20 text-amber-400' :
                    'bg-red-500/20 text-red-400'
                  }`}>
                    {r.status === 'working' ? t('gm.working') : r.status === 'rate_limited' ? t('gm.rateLimited') :
                     r.status === 'invalid_key' ? t('gm.invalidKey') : t('gm.probeError')}
                  </span>
                  <span className="text-xs text-slate-500 font-mono">{r.latency_ms}ms</span>
                  {r.tokens?.total != null && <span className="text-xs text-slate-500 font-mono">{r.tokens.total} tokens</span>}
                </div>
                {r.detail && <p className="text-xs text-slate-400 font-mono break-words whitespace-pre-wrap">{r.detail}</p>}
              </div>
            ))}
          </div>
        )}
        {probe?.tier_hint && <p className="text-xs text-slate-400 mb-4">{probe.tier_hint}</p>}

        <h4 className="text-sm font-semibold text-slate-300 mb-1">{t('gm.tierTitle')}</h4>
        <p className="text-xs text-amber-300/80 mb-3">{t('gm.subscriptionNote')}</p>
        <ResponsiveTable
          rows={(probe?.tier_rules || probe?.reference?.tier_rules || [])}
          empty="—"
          columns={[
            { key: 'tier', header: t('gm.colTier'), cellClass: 'text-slate-200 font-medium', render: (r: any) => r.tier },
            { key: 'qual', header: t('gm.colQualification'), cellClass: 'text-slate-400 text-sm', render: (r: any) => r.qualification },
          ]}
        />
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">{t('gm.rotationTitle')}</h3>
        <ResponsiveTable
          rows={status.keys}
          empty={t('gm.empty')}
          columns={[
            {
              key: 'alias', header: t('gm.colKey'),
              render: (k: any) => (
                <span className="flex items-center gap-2 justify-end sm:justify-start">
                  <KeyRound size={14} className="text-slate-500 flex-shrink-0" />
                  <span className="text-slate-200 font-mono text-sm">{k.alias}</span>
                </span>
              ),
            },
            {
              key: 'status', header: t('gm.colStatus'),
              render: (k: any) => (
                <span className={`inline-flex items-center gap-1.5 text-sm ${k.status === 'Active' ? 'text-emerald-400' : 'text-amber-400'}`}>
                  <span className={`w-2 h-2 rounded-full ${k.status === 'Active' ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
                  {k.status === 'Active' ? t('gm.active') : t('gm.cooling')}
                </span>
              ),
            },
            { key: 'today', header: t('gm.colToday'), cellClass: 'font-mono text-sm text-slate-300', render: (k: any) => k.requests_today },
            { key: 'cool', header: t('gm.colCooldown'), cellClass: 'font-mono text-sm text-slate-400', render: (k: any) => k.cooldown_remaining_s > 0 ? `${k.cooldown_remaining_s}s` : '—' },
          ]}
        />
      </div>
    </div>
  );
}
