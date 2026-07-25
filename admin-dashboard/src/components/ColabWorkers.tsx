import React, { useState, useEffect } from 'react';
import { Cpu, Send, RefreshCw, Terminal, BookOpen, Info } from 'lucide-react';
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
  const [colab, setColab] = useState<any>(null);
  const [kaggle, setKaggle] = useState<any>(null);
  const [kgJobs, setKgJobs] = useState<any[]>([]);
  const [kgUser, setKgUser] = useState('');
  const [kgKind, setKgKind] = useState<'aws' | 'binance' | 'drivecheck'>('aws');
  const [kgChain, setKgChain] = useState('eth');
  const [kgTable, setKgTable] = useState('blocks');
  const [kgDays, setKgDays] = useState('2024-01-15');
  const [kgSymbol, setKgSymbol] = useState('BTCUSDT');
  const [kgInterval, setKgInterval] = useState('1m');
  const [kgMonths, setKgMonths] = useState('2024-01');
  const [kgBusy, setKgBusy] = useState(false);
  const [kgMsg, setKgMsg] = useState<string | null>(null);

  const loadKgJobs = (refresh = false) => {
    apiFetch(`/api/kaggle/jobs${refresh ? '?refresh=true' : ''}`)
      .then(r => r.json()).then(d => setKgJobs(d.jobs || [])).catch(console.error);
  };

  const dispatchKaggle = async () => {
    setKgBusy(true); setKgMsg(null);
    // Slug must be unique per push or Kaggle versions the same kernel; index by
    // job shape so repeat runs of the same target are recognisable in the list.
    const stamp = kgKind === 'aws' ? `${kgChain}-${kgTable}`
      : kgKind === 'binance' ? `${kgSymbol.toLowerCase()}-${kgInterval}`
      : 'probe';
    try {
      const res = await apiFetch('/api/kaggle/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: kgUser.trim(), slug: `cq-${kgKind}-${stamp}`, kind: kgKind,
          chain: kgChain, table: kgTable, days: kgDays.split(',').map(x => x.trim()).filter(Boolean),
          symbol: kgSymbol, interval: kgInterval,
          months: kgMonths.split(',').map(x => x.trim()).filter(Boolean),
        }),
      });
      const d = await res.json();
      setKgMsg(res.ok ? t('kg.dispatched', { ref: d.ref }) : (d.detail || t('kg.dispatchFailed')));
      if (res.ok) loadKgJobs();
    } catch (e) {
      setKgMsg(String(e));
    } finally { setKgBusy(false); }
  };

  const fetchKgOutput = async (ref: string) => {
    setKgBusy(true);
    try {
      const res = await apiFetch(`/api/kaggle/output/${ref}`, { method: 'POST' });
      const d = await res.json();
      setKgMsg(d.result ? t('kg.gotOutput', { n: d.files, mb: (d.bytes / 1048576).toFixed(1) })
                        : (d.raw || t('kg.noOutput')));
    } catch (e) { setKgMsg(String(e)); } finally { setKgBusy(false); }
  };
  const [probing, setProbing] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<Record<string, any>>({});

  const loadColab = () => {
    apiFetch('/api/colab/status').then(r => r.json()).then(setColab).catch(console.error);
    apiFetch('/api/kaggle/status').then(r => r.json()).then(setKaggle).catch(console.error);
    loadKgJobs();
  };

  const [measuring, setMeasuring] = useState(false);

  const remeasure = async () => {
    setMeasuring(true);
    try {
      await apiFetch('/api/colab/entitlements', { method: 'POST' });
      loadColab();
    } catch (e) {
      console.error(e);
    } finally { setMeasuring(false); }
  };

  const probe = async (name: string) => {
    setProbing(name);
    try {
      const res = await apiFetch(`/api/colab/probe/${encodeURIComponent(name)}`, { method: 'POST' });
      const d = await res.json();
      setProbeResult(p => ({ ...p, [name]: res.ok ? d.specs : { error: d.detail } }));
    } catch (e) {
      setProbeResult(p => ({ ...p, [name]: { error: String(e) } }));
    } finally { setProbing(null); }
  };

  const load = () => {
    apiFetch('/api/workers').then(r => r.json()).then(d => { setWorkers(d.workers || []); setStats(d.stats || {}); }).catch(console.error);
    apiFetch('/api/jobs?limit=20').then(r => r.json()).then(d => setJobs(d.jobs || [])).catch(console.error);
  };

  useEffect(() => {
    load();
    loadColab();
    const i = setInterval(load, 5000);
    // The CLI shells out and takes seconds, so poll it far less often.
    const c = setInterval(loadColab, 30000);
    return () => { clearInterval(i); clearInterval(c); };
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

      {colab && (
        <div className="glass-panel p-5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-3">
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Terminal size={18} className="text-blue-400" />
              {t('cw.cliTitle')}
            </h3>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-xs px-2 py-1 rounded ${colab.installed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                {colab.installed ? `${t('cw.cliInstalled')} v${colab.version}` : t('cw.cliMissing')}
              </span>
              <span className={`text-xs px-2 py-1 rounded ${colab.authenticated ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
                {colab.authenticated ? t('cw.cliAuthed') : t('cw.cliNotAuthed')}
              </span>
              <button onClick={loadColab} className="text-slate-400 hover:text-white p-2" aria-label={t('acc.sync')}>
                <RefreshCw size={14} />
              </button>
            </div>
          </div>
          {colab.auth_hint && <p className="text-xs text-amber-300 mb-3">{colab.auth_hint}</p>}
          {colab.reason && !colab.available && <p className="text-xs text-red-400 mb-3 font-mono break-words">{colab.reason}</p>}

          {colab.orphan_hint && (
            <p className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded p-3 mb-3">
              {colab.orphan_hint}
            </p>
          )}
          <h4 className="text-sm font-semibold text-slate-300 mb-2">{t('cw.liveSessions', { n: colab.sessions?.length ?? 0 })}</h4>
          <ResponsiveTable
            rows={colab.sessions || []}
            empty={t('cw.noLiveSessions')}
            columns={[
              { key: 'name', header: t('cw.colSession'), cellClass: 'text-slate-200 font-mono text-sm', render: (x: any) => x.name },
              { key: 'machine', header: t('cw.colMachine'), cellClass: 'text-slate-500 font-mono text-xs break-all', render: (x: any) => x.machine },
              { key: 'hw', header: t('cw.colHardware'), cellClass: 'text-slate-300 text-sm', render: (x: any) => `${x.hardware} / ${x.variant}` },
              {
                key: 'state', header: t('cw.colState'), cellClass: 'text-sm',
                render: (x: any) => (
                  <span className="inline-flex flex-col items-end sm:items-start">
                    {x.orphan
                      ? <span className="text-xs px-2 py-1 rounded bg-amber-500/20 text-amber-400">{t('cw.orphan')}</span>
                      : <span className={x.status === 'BUSY' ? 'text-amber-400' : 'text-emerald-400'}>{x.status || '—'}</span>}
                    {x.last_execution && <span className="text-xs text-slate-500 break-words">{x.last_execution}</span>}
                  </span>
                ),
              },
              {
                key: 'probe', header: t('cw.probeBtn'), alignRight: true,
                render: (x: any) => {
                  const r = probeResult[x.name];
                  return (
                    <span className="inline-flex flex-col items-end gap-1">
                      <button onClick={() => probe(x.name)} disabled={probing === x.name || x.orphan}
                        className="text-xs px-3 min-h-[44px] sm:min-h-0 sm:py-1.5 rounded border bg-blue-500/20 border-blue-500/40 text-blue-300 disabled:opacity-50">
                        {probing === x.name ? t('cw.probing') : t('cw.probeBtn')}
                      </button>
                      {r && !r.error && (
                        <span className="text-xs font-mono text-slate-400">
                          {r.cpu_count} vCPU · {r.ram_gb}GB · {r.disk_free_gb}GB free{r.gpu && r.gpu !== 'none' ? ` · ${r.gpu}` : ''}
                        </span>
                      )}
                      {r?.error && <span className="text-xs text-red-400 break-words max-w-[220px]">{r.error}</span>}
                    </span>
                  );
                },
              },
            ]}
          />

          <div className="mt-4 flex flex-wrap gap-2 items-center">
            <span className="text-xs text-slate-500">{t('cw.hardwareOptions')}:</span>
            {Object.entries(colab.hardware_options || {}).map(([kind, list]: any) => (
              <span key={kind} className="text-xs px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-300 font-mono">
                {kind.toUpperCase()}: {(list as string[]).join(', ')}
              </span>
            ))}
          </div>
        </div>
      )}

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
            {
              key: 'specs', header: t('cw.specs'), cellClass: 'text-slate-400 font-mono text-xs',
              render: (w: any) => {
                const s = w.specs || {};
                const bits = [];
                if (s.cpu_count) bits.push(`${s.cpu_count} vCPU`);
                if (s.ram_gb) bits.push(`${s.ram_gb}GB RAM`);
                if (s.disk_free_gb != null) bits.push(`${s.disk_free_gb}GB free`);
                if (s.gpu && s.gpu !== 'none') bits.push(s.gpu);
                return bits.length ? bits.join(' · ') : '—';
              },
            },
            {
              key: 'session', header: t('cw.session'), cellClass: 'font-mono text-xs',
              render: (w: any) => {
                const s = w.specs || {};
                if (s.elapsed_s == null) return '—';
                const h = Math.floor(s.elapsed_s / 3600), m = Math.floor((s.elapsed_s % 3600) / 60);
                const pct = s.max_session_s ? (s.elapsed_s / s.max_session_s) * 100 : 0;
                return (
                  <span className={pct > 80 ? 'text-amber-400' : 'text-slate-400'}>
                    {h}h{String(m).padStart(2, '0')}m / 12h
                  </span>
                );
              },
            },
            { key: 'jobs', header: t('cw.colJobsDone'), cellClass: 'text-slate-300 font-mono text-sm', render: (w: any) => w.jobs_done },
          ]}
        />
        <p className="text-xs text-slate-500 mt-3">{t('cw.sessionNote')}</p>
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

      {colab && (
        <div className="glass-panel p-5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-1">
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Cpu size={18} className="text-purple-400" />
              {t('cw.entTitle')}
            </h3>
            <button onClick={remeasure} disabled={measuring}
              className="text-xs px-3 min-h-[44px] sm:min-h-0 sm:py-2 rounded border bg-purple-500/20 border-purple-500/40 text-purple-300 disabled:opacity-50">
              {measuring ? t('cw.measuring') : t('cw.remeasure')}
            </button>
          </div>
          <p className="text-xs text-slate-500 mb-4">{t('cw.entNote')}</p>
          {colab.entitlements ? (
            <ResponsiveTable
              rows={colab.entitlements.attempts || []}
              empty="—"
              columns={[
                { key: 'hw', header: t('cw.colHardware'), cellClass: 'text-slate-200 font-mono text-sm', render: (x: any) => `${x.kind.toUpperCase()} ${x.variant}` },
                {
                  key: 'granted', header: t('cw.colGranted'),
                  render: (x: any) => (
                    <span className={`text-xs px-2 py-1 rounded ${x.granted ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
                      {x.granted ? t('cw.granted') : t('cw.denied')}
                    </span>
                  ),
                },
                {
                  key: 'specs', header: t('cw.colSpecs'), cellClass: 'text-slate-400 font-mono text-xs break-words',
                  render: (x: any) => x.specs
                    ? `${x.specs.cpu_count} vCPU · ${x.specs.ram_gb}GB · ${x.specs.disk_total_gb}GB${x.specs.gpu && x.specs.gpu !== 'none' ? ` · ${x.specs.gpu}` : ''}`
                    : (x.reason ? String(x.reason).slice(0, 90) : '—'),
                },
              ]}
            />
          ) : (
            <p className="text-sm text-slate-500">{t('cw.entNeverRun')}</p>
          )}

          <h4 className="text-sm font-semibold text-slate-300 mt-6 mb-2">{t('cw.plansTitle')}</h4>
          <p className="text-xs text-amber-300/80 mb-3">{colab.units_expiry_note}</p>
          <ResponsiveTable
            rows={colab.plans || []}
            empty="—"
            columns={[
              { key: 'plan', header: t('cw.colPlan'), cellClass: 'text-slate-200 text-sm', render: (x: any) => x.plan },
              { key: 'units', header: t('cw.colUnits'), cellClass: 'text-amber-300 text-sm', render: (x: any) => x.units },
              { key: 'extra', header: t('cw.colExtra'), cellClass: 'text-slate-500 text-xs break-words', render: (x: any) => x.extra },
            ]}
          />
        </div>
      )}

      {kaggle && (
        <div className="glass-panel p-5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-1">
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Terminal size={18} className="text-cyan-400" />
              {t('kg.title')}
            </h3>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-xs px-2 py-1 rounded ${kaggle.installed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                {kaggle.installed ? `${t('cw.cliInstalled')} v${kaggle.version}` : t('cw.cliMissing')}
              </span>
              <span className={`text-xs px-2 py-1 rounded ${kaggle.authenticated ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
                {kaggle.authenticated ? t('cw.cliAuthed') : t('cw.cliNotAuthed')}
              </span>
            </div>
          </div>
          <p className="text-xs text-slate-500 mb-3">{t('kg.contrast')}</p>
          {kaggle.auth_hint && (
            <p className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded p-3 mb-3">
              {kaggle.auth_hint}
            </p>
          )}

          {kaggle.available ? (
            <>
              <ResponsiveTable
                rows={[{ k: 'GPU', q: kaggle.gpu }, { k: 'TPU', q: kaggle.tpu }].filter(x => x.q && x.q.total_h != null)}
                empty={t('kg.noQuota')}
                columns={[
                  { key: 'acc', header: t('kg.colAcc'), cellClass: 'text-slate-200 font-mono text-sm', render: (x: any) => x.k },
                  { key: 'used', header: t('kg.colUsed'), cellClass: 'text-slate-300 text-sm', render: (x: any) => `${x.q.used_h} h` },
                  { key: 'total', header: t('kg.colTotal'), cellClass: 'text-slate-300 text-sm', render: (x: any) => `${x.q.total_h} h` },
                  {
                    key: 'left', header: t('kg.colRemaining'), cellClass: 'text-sm',
                    render: (x: any) => (
                      <span className={x.q.pct_used != null && x.q.pct_used > 80 ? 'text-amber-400' : 'text-emerald-400'}>
                        {x.q.remaining_h} h{x.q.pct_used != null ? ` (${t('kg.pctUsed', { p: x.q.pct_used })})` : ''}
                      </span>
                    ),
                  },
                ]}
              />
              {kaggle.refresh_time && (
                <p className="text-xs text-slate-500 mt-2">{t('kg.refresh')}: {String(kaggle.refresh_time)}</p>
              )}
            </>
          ) : (
            kaggle.reason && !kaggle.auth_hint && (
              <p className="text-xs text-red-400 font-mono break-words mb-3">{kaggle.reason}</p>
            )
          )}

          <div className="mt-5 pt-5 border-t border-slate-700/60">
            <h4 className="text-sm font-semibold text-slate-300 mb-1 flex items-center gap-2">
              <Send size={14} className="text-cyan-400" />
              {t('kg.dispatchTitle')}
            </h4>
            <p className="text-xs text-slate-500 mb-3">{t('kg.dispatchNote')}</p>

            <div className="flex flex-col sm:flex-row gap-2 mb-3">
              <input value={kgUser} onChange={e => setKgUser(e.target.value)}
                placeholder={t('kg.usernamePlaceholder')}
                className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
              <div className="flex gap-2">
                {(['aws', 'binance', 'drivecheck'] as const).map(k => (
                  <button key={k} onClick={() => setKgKind(k)}
                    className={`text-xs px-3 min-h-[44px] sm:min-h-0 sm:py-2 rounded border ${kgKind === k
                      ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-300'
                      : 'bg-slate-800 border-slate-700 text-slate-400'}`}>
                    {k === 'aws' ? t('kg.kindAws') : k === 'binance' ? t('kg.kindBinance') : t('kg.kindDriveCheck')}
                  </button>
                ))}
              </div>
            </div>

            {kgKind === 'drivecheck' ? (
              <p className="text-xs text-slate-500 mb-3">{t('kg.driveCheckNote')}</p>
            ) : kgKind === 'aws' ? (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
                <input value={kgChain} onChange={e => setKgChain(e.target.value)} placeholder="eth / btc"
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
                <input value={kgTable} onChange={e => setKgTable(e.target.value)} placeholder="blocks / transactions"
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
                <input value={kgDays} onChange={e => setKgDays(e.target.value)} placeholder="2024-01-15,2024-01-16"
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
                <input value={kgSymbol} onChange={e => setKgSymbol(e.target.value)} placeholder="BTCUSDT"
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
                <input value={kgInterval} onChange={e => setKgInterval(e.target.value)} placeholder="1m"
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
                <input value={kgMonths} onChange={e => setKgMonths(e.target.value)} placeholder="2024-01,2024-02"
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
              </div>
            )}

            <div className="flex flex-wrap gap-2 items-center">
              <button onClick={dispatchKaggle} disabled={kgBusy || !kgUser.trim() || !kaggle.authenticated}
                className="text-sm px-4 min-h-[44px] sm:min-h-0 sm:py-2 rounded bg-cyan-500/20 border border-cyan-500/40 text-cyan-300 disabled:opacity-40">
                {kgBusy ? t('kg.dispatching') : t('kg.dispatch')}
              </button>
              <button onClick={() => loadKgJobs(true)} disabled={kgBusy}
                className="text-sm px-3 min-h-[44px] sm:min-h-0 sm:py-2 rounded border border-slate-700 text-slate-400 disabled:opacity-40">
                {t('kg.refreshJobs')}
              </button>
              {!kaggle.authenticated && <span className="text-xs text-amber-400">{t('kg.needToken')}</span>}
            </div>
            {kgMsg && <p className="text-xs text-slate-300 mt-2 break-words font-mono">{kgMsg}</p>}

            <h5 className="text-xs font-semibold text-slate-400 mt-4 mb-2">{t('kg.jobsTitle')}</h5>
            <ResponsiveTable
              rows={kgJobs}
              empty={t('kg.noJobs')}
              columns={[
                { key: 'ref', header: t('kg.colRef'), cellClass: 'text-slate-200 font-mono text-xs break-all', render: (x: any) => x.ref },
                { key: 'kind', header: t('kg.colKind'), cellClass: 'text-slate-400 text-xs', render: (x: any) => x.params?.kind || '—' },
                {
                  key: 'state', header: t('cw.colState'),
                  render: (x: any) => {
                    const tone = x.status === 'COMPLETE' ? 'bg-emerald-500/20 text-emerald-400'
                      : x.status === 'ERROR' ? 'bg-red-500/20 text-red-400'
                      : 'bg-amber-500/20 text-amber-400';
                    return <span className={`text-xs px-2 py-1 rounded ${tone}`}>{x.status}</span>;
                  },
                },
                {
                  key: 'act', header: t('kg.colAction'), alignRight: true,
                  render: (x: any) => (
                    <button onClick={() => fetchKgOutput(x.ref)} disabled={kgBusy || x.status !== 'COMPLETE'}
                      className="text-xs px-3 min-h-[44px] sm:min-h-0 sm:py-1.5 rounded border border-slate-700 text-slate-300 disabled:opacity-40">
                      {t('kg.getOutput')}
                    </button>
                  ),
                },
              ]}
            />
          </div>

          <h4 className="text-sm font-semibold text-slate-300 mt-5 mb-2">{t('kg.freeTierTitle')}</h4>
          <p className="text-xs text-slate-500 mb-3">{kaggle.free_tier_note}</p>
          <ResponsiveTable
            rows={kaggle.free_tier || []}
            empty="—"
            columns={[
              { key: 'item', header: t('cw.colItem'), cellClass: 'text-slate-200 text-sm', render: (x: any) => x.item },
              { key: 'value', header: t('cw.colValue'), cellClass: 'text-cyan-300 text-sm', render: (x: any) => x.value },
              {
                key: 'src', header: t('kg.colSource'),
                render: (x: any) => {
                  const tone = x.source === 'measured' ? 'bg-blue-500/20 text-blue-300'
                    : x.source === 'official' ? 'bg-emerald-500/20 text-emerald-400'
                    : x.source === 'conflicting' ? 'bg-amber-500/20 text-amber-400'
                    : 'bg-slate-700 text-slate-400';
                  return <span className={`text-xs px-2 py-1 rounded ${tone}`}>{x.source}</span>;
                },
              },
              { key: 'note', header: t('cw.colNote'), cellClass: 'text-slate-500 text-xs break-words', render: (x: any) => x.note },
            ]}
          />

          <h4 className="text-sm font-semibold text-slate-300 mt-5 mb-2">{t('kg.driveTitle')}</h4>
          <ResponsiveTable
            rows={kaggle.drive_access || []}
            empty="—"
            columns={[
              { key: 'p', header: t('kg.colPlatform'), cellClass: 'text-slate-200 text-sm', render: (x: any) => x.platform },
              { key: 'm', header: t('kg.colMethod'), cellClass: 'text-slate-300 text-sm', render: (x: any) => x.method },
              {
                key: 'w', header: t('kg.colWorks'),
                render: (x: any) => (
                  <span className="inline-flex flex-col items-end sm:items-start gap-1">
                    <span className={`text-xs px-2 py-1 rounded ${x.works ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                      {x.works ? t('kg.worksYes') : t('kg.worksNo')}
                    </span>
                    <span className={`text-xs ${x.verified ? 'text-slate-500' : 'text-amber-400'}`}>
                      {x.verified ? t('kg.measured') : t('kg.unverified')}
                    </span>
                  </span>
                ),
              },
              { key: 'n', header: t('cw.colNote'), cellClass: 'text-slate-500 text-xs break-words', render: (x: any) => x.note },
            ]}
          />

          <h4 className="text-sm font-semibold text-slate-300 mt-5 mb-2">{t('kg.capTitle')}</h4>
          <ResponsiveTable
            rows={kaggle.capabilities || []}
            empty="—"
            columns={[
              { key: 'item', header: t('cw.colItem'), cellClass: 'text-slate-200 text-sm', render: (x: any) => x.item },
              { key: 'value', header: t('cw.colValue'), cellClass: 'text-cyan-300 text-sm', render: (x: any) => x.value },
              { key: 'note', header: t('cw.colNote'), cellClass: 'text-slate-500 text-xs break-words', render: (x: any) => x.note },
            ]}
          />

          <div className="flex flex-col gap-1.5 mt-4">
            {(kaggle.doc_links || []).map((d: any, i: number) => (
              <a key={i} href={d.url} target="_blank" rel="noopener noreferrer"
                 className="text-sm text-cyan-400 hover:text-cyan-300 hover:underline break-all">
                {d.title}
              </a>
            ))}
          </div>
        </div>
      )}

      {colab && (
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
            <Info size={18} className="text-amber-400" />
            {t('cw.limitsTitle')}
          </h3>
          <p className="text-xs text-amber-300/80 mb-4">{colab.quota_note}</p>
          <ResponsiveTable
            rows={colab.documented_limits || []}
            empty="—"
            columns={[
              { key: 'item', header: t('cw.colItem'), cellClass: 'text-slate-200 text-sm', render: (x: any) => x.item },
              { key: 'value', header: t('cw.colValue'), cellClass: 'text-amber-300 text-sm', render: (x: any) => x.value },
              { key: 'note', header: t('cw.colNote'), cellClass: 'text-slate-500 text-xs break-words', render: (x: any) => x.note },
            ]}
          />

          <h4 className="text-sm font-semibold text-slate-300 mt-5 mb-2 flex items-center gap-2">
            <BookOpen size={14} className="text-blue-400" />
            {t('cw.docsTitle')}
          </h4>
          <div className="flex flex-col gap-1.5">
            {(colab.doc_links || []).map((d: any, i: number) => (
              <a key={i} href={d.url} target="_blank" rel="noopener noreferrer"
                 className="text-sm text-blue-400 hover:text-blue-300 hover:underline break-all">
                {d.title}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
