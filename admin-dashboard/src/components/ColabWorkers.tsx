import React, { useState, useEffect } from 'react';
import { Cpu, Send, RefreshCw } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';
import ResponsiveTable from './ResponsiveTable';

export default function ColabWorkers() {
  const { t } = useI18n();
  const [workers, setWorkers] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [stats, setStats] = useState<any>({ pending: 0, running: 0, done: 0, failed: 0 });
  const [jobType, setJobType] = useState<'sql' | 'ingest_binance'>('sql');
  const [sql, setSql] = useState('SELECT count(*) AS n FROM market_btcusdt_1m');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [months, setMonths] = useState(1);
  const [drivePath, setDrivePath] = useState('');
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => {
    apiFetch('/api/workers').then(r => r.json()).then(d => { setWorkers(d.workers || []); setStats(d.stats || {}); }).catch(console.error);
    apiFetch('/api/jobs?limit=20').then(r => r.json()).then(d => setJobs(d.jobs || [])).catch(console.error);
  };

  useEffect(() => {
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, []);

  const submit = async () => {
    setMsg(null);
    const body: any = { type: jobType, drive_path: drivePath.trim() };
    if (jobType === 'sql') body.sql = sql;
    else { body.symbol = symbol; body.months = months; body.interval = '1m'; }
    try {
      const res = await apiFetch('/api/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const d = await res.json();
      setMsg(res.ok ? t('cw.submitted', { id: d.id }) : (d.detail || t('cw.submitFailed')));
      load();
    } catch (e) { setMsg(String(e)); }
  };

  const online = workers.filter(w => w.online).length;

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('cw.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base">{t('cw.subtitle')}</p>
      </header>

      {workers.length === 0 && (
        <div className="glass-panel p-4 border-l-4 border-l-amber-500 bg-amber-900/20 text-amber-200 text-sm">
          {t('cw.noWorkersHint')}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: t('cw.online'), value: `${online}/${workers.length}`, color: online > 0 ? 'text-emerald-400' : 'text-slate-400' },
          { label: t('cw.pending'), value: stats.pending ?? 0, color: 'text-amber-400' },
          { label: t('cw.done'), value: stats.done ?? 0, color: 'text-emerald-400' },
          { label: t('cw.failed'), value: stats.failed ?? 0, color: (stats.failed ?? 0) > 0 ? 'text-red-400' : 'text-slate-400' },
        ].map((s, i) => (
          <div key={i} className="glass-panel p-4">
            <p className="text-xs text-slate-400 mb-1">{s.label}</p>
            <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
          </div>
        ))}
      </div>

      <div className="glass-panel p-5 border-l-4 border-l-emerald-500">
        <h3 className="text-white font-semibold mb-1 flex items-center gap-2">
          <Send size={18} className="text-emerald-400" />
          {t('cw.dispatchTitle')}
        </h3>
        <p className="text-xs text-slate-500 mb-4">{t('cw.dispatchNote')}</p>

        <div className="flex gap-2 mb-3">
          {(['sql', 'ingest_binance'] as const).map(ty => (
            <button
              key={ty}
              onClick={() => setJobType(ty)}
              className={`px-3 min-h-[44px] sm:min-h-0 sm:py-1.5 rounded-lg border text-sm transition-colors ${
                jobType === ty ? 'bg-blue-500/20 border-blue-500/40 text-blue-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              {ty === 'sql' ? t('cw.typeSql') : t('cw.typeIngest')}
            </button>
          ))}
        </div>

        {jobType === 'sql' ? (
          <textarea
            value={sql}
            onChange={e => setSql(e.target.value)}
            rows={3}
            className="w-full px-4 py-3 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 font-mono text-sm outline-none focus:border-emerald-500 resize-y"
          />
        ) : (
          <div className="flex flex-col sm:flex-row gap-3">
            <input
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              className="flex-1 px-4 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white font-mono outline-none focus:border-emerald-500"
            />
            <select
              value={months}
              onChange={e => setMonths(Number(e.target.value))}
              className="px-4 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-emerald-500"
            >
              {[1, 3, 6, 12, 24].map(m => <option key={m} value={m}>{t('da.months', { n: m })}</option>)}
            </select>
          </div>
        )}

        <input
          value={drivePath}
          onChange={e => setDrivePath(e.target.value)}
          placeholder={t('cw.drivePathPlaceholder')}
          className="w-full mt-3 px-4 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-slate-300 placeholder-slate-600 font-mono text-sm outline-none focus:border-emerald-500"
        />

        <button
          onClick={submit}
          className="mt-3 px-5 min-h-[44px] sm:min-h-0 sm:py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors"
        >
          {t('cw.dispatch')}
        </button>
        {msg && <p className="text-sm text-slate-300 mt-3">{msg}</p>}
      </div>

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Cpu size={18} className="text-blue-400" />
          {t('cw.workersTitle')}
        </h3>
        <ResponsiveTable
          rows={workers}
          empty={t('cw.noWorkers')}
          columns={[
            { key: 'label', header: t('cw.colWorker'), cellClass: 'text-slate-200', render: (w: any) => w.label },
            { key: 'runtime', header: t('cw.colRuntime'), cellClass: 'text-slate-400 text-sm', render: (w: any) => w.runtime || '—' },
            {
              key: 'status', header: t('cw.colStatus'),
              render: (w: any) => (
                <span className={`inline-flex items-center gap-1.5 text-sm ${w.online ? 'text-emerald-400' : 'text-slate-500'}`}>
                  <span className={`w-2 h-2 rounded-full ${w.online ? 'bg-emerald-500' : 'bg-slate-600'}`}></span>
                  {w.online ? t('cw.statusOnline') : t('cw.statusOffline', { s: w.seconds_since_seen })}
                </span>
              ),
            },
            { key: 'jobs', header: t('cw.colJobsDone'), cellClass: 'text-slate-300 font-mono text-sm', render: (w: any) => w.jobs_done },
          ]}
        />
      </div>

      <div className="flex-1 glass-panel p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white">{t('cw.jobsTitle')}</h3>
          <button onClick={load} className="text-slate-400 hover:text-white p-2" aria-label={t('acc.sync')}>
            <RefreshCw size={16} />
          </button>
        </div>
        <ResponsiveTable
          rows={jobs}
          empty={t('cw.noJobs')}
          columns={[
            { key: 'id', header: 'ID', cellClass: 'font-mono text-xs text-slate-400', render: (j: any) => j.id },
            { key: 'type', header: t('cw.colType'), cellClass: 'text-slate-300 text-sm', render: (j: any) => j.type },
            {
              key: 'status', header: t('cw.colStatus'),
              render: (j: any) => (
                <span className={`text-xs px-2 py-1 rounded ${
                  j.status === 'done' ? 'bg-emerald-500/20 text-emerald-400' :
                  j.status === 'failed' ? 'bg-red-500/20 text-red-400' :
                  j.status === 'running' ? 'bg-blue-500/20 text-blue-400' : 'bg-slate-700 text-slate-400'
                }`}>
                  {t(`cw.job.${j.status}` as any)}
                </span>
              ),
            },
            { key: 'dur', header: t('cw.colDuration'), cellClass: 'text-slate-400 font-mono text-sm', render: (j: any) => j.duration_s != null ? `${j.duration_s}s` : '—' },
            { key: 'out', header: t('cw.colResult'), cellClass: 'text-slate-400 font-mono text-xs break-all', render: (j: any) => (j.result_preview || j.error || '—').slice(0, 90) },
          ]}
        />
      </div>
    </div>
  );
}
