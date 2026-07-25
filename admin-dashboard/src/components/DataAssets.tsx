import React, { useState, useEffect } from 'react';
import { Database, UploadCloud, Filter, Zap, Archive, Download, Boxes } from 'lucide-react';

import { apiFetch } from '../api';
import { useI18n } from '../i18n';
import ResponsiveTable from './ResponsiveTable';

type AssetFilter = 'all' | 'real' | 'synthetic';

export default function DataAssets() {
  const { t } = useI18n();
  const [assetsInfo, setAssetsInfo] = useState({ assets: [], total_files: 0, total_size: 0, synthetic_files: 0, real_files: 0 });
  const [syncStatus, setSyncStatus] = useState({
    rclone_union: { configured: false, upstream_count: 0, policy: null },
    compaction_watchdog: { running: false, last_compaction_at: null, files_compacted_total: 0, last_error: null },
  });
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<AssetFilter>('all');

  useEffect(() => {
    const load = () => {
      Promise.all([
        apiFetch('/api/data-assets').then(r => r.json()).then(setAssetsInfo),
        apiFetch('/api/sync/status').then(r => r.json()).then(setSyncStatus),
      ]).catch(console.error).finally(() => setLoading(false));
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const visibleAssets = (assetsInfo.assets as any[]).filter(a =>
    filter === 'all' ? true : filter === 'synthetic' ? a.is_synthetic : !a.is_synthetic
  );

  const [symbol, setSymbol] = useState('BTCUSDT');
  const [months, setMonths] = useState(1);
  const [ingesting, setIngesting] = useState(false);
  const [ingestMsg, setIngestMsg] = useState<string | null>(null);

  const runIngest = async () => {
    setIngesting(true);
    setIngestMsg(null);
    try {
      const res = await apiFetch('/api/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: 'binance', symbol, interval: '1m', months }),
      });
      const d = await res.json();
      if (!res.ok) {
        setIngestMsg(d.detail || t('da.ingestFailed'));
      } else {
        const w = d.months_written?.length || 0;
        const s = d.months_skipped?.length || 0;
        const f = d.months_failed?.length || 0;
        setIngestMsg(t('da.ingestDone', { rows: d.total_rows.toLocaleString(), w, s }) + (f ? ` (${f} failed)` : ''));
      }
    } catch (e) {
      setIngestMsg(String(e));
    } finally {
      setIngesting(false);
    }
  };

  const [aws, setAws] = useState<any>({ chains: {}, tables: {}, measured_daily_mb: {} });
  const [awsChain, setAwsChain] = useState('eth');
  const [awsTable, setAwsTable] = useState('blocks');
  const [awsFrom, setAwsFrom] = useState('');
  const [awsTo, setAwsTo] = useState('');
  const [awsBudget, setAwsBudget] = useState(2);
  const [awsMsg, setAwsMsg] = useState<string | null>(null);
  const [awsBusy, setAwsBusy] = useState(false);

  useEffect(() => {
    apiFetch('/api/aws/catalog').then(r => r.json()).then(d => {
      setAws(d);
      // Default to a two-day window ending three days back; recent days are
      // not always published yet.
      const end = new Date(Date.now() - 3 * 86400000);
      const start = new Date(Date.now() - 4 * 86400000);
      setAwsTo(end.toISOString().slice(0, 10));
      setAwsFrom(start.toISOString().slice(0, 10));
    }).catch(console.error);
  }, []);

  const awsPerDayMb = aws.measured_daily_mb?.[awsChain]?.[awsTable];

  const awsRun = async (previewOnly: boolean) => {
    setAwsBusy(true);
    setAwsMsg(null);
    try {
      const path = previewOnly ? '/api/aws/preview' : '/api/ingest';
      const body = previewOnly
        ? { chain: awsChain, table: awsTable, start_date: awsFrom, end_date: awsTo }
        : { source: 'aws', chain: awsChain, table: awsTable, start_date: awsFrom, end_date: awsTo, max_gb: awsBudget };
      const res = await apiFetch(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const d = await res.json();
      if (!res.ok) { setAwsMsg(d.detail || 'Failed'); return; }
      setAwsMsg(previewOnly
        ? t('da.awsPreviewResult', { gb: d.total_gb, days: d.days.length })
        : t('da.awsDone', { w: d.days_written.length, gb: d.total_gb, s: d.days_skipped.length }));
    } catch (e) { setAwsMsg(String(e)); }
    finally { setAwsBusy(false); }
  };

  const lastCompactionText = syncStatus.compaction_watchdog.last_compaction_at
    ? new Date(syncStatus.compaction_watchdog.last_compaction_at * 1000).toLocaleString()
    : t('da.never');

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in pb-4">
      <header className="flex flex-col sm:flex-row sm:justify-between sm:items-end gap-3">
        <div>
          <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('da.title')}</h2>
          <p className="text-slate-400 text-sm sm:text-base">
            {t('da.summary', { total: assetsInfo.total_files, real: assetsInfo.real_files, syn: assetsInfo.synthetic_files, size: (assetsInfo.total_size / (1024 * 1024)).toFixed(2) })}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <Filter size={16} className="text-slate-500" />
          {(['all', 'real', 'synthetic'] as AssetFilter[]).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 min-h-[44px] sm:min-h-0 sm:py-1.5 rounded-lg border text-sm capitalize transition-colors ${
                filter === f
                  ? 'bg-blue-500/20 border-blue-500/40 text-blue-300'
                  : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              {f === 'all' ? t('da.filterAll') : f === 'real' ? t('da.filterReal') : t('da.filterSynthetic')}
            </button>
          ))}
        </div>
      </header>

      <div className="glass-panel p-5 border-l-4 border-l-emerald-500">
        <h3 className="text-white font-semibold mb-1 flex items-center gap-2">
          <Download size={18} className="text-emerald-400" />
          {t('da.ingestTitle')}
        </h3>
        <p className="text-xs text-slate-500 mb-4">
          {t('da.ingestNote')}
        </p>
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            placeholder="BTCUSDT"
            className="flex-1 px-4 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white placeholder-slate-500 outline-none focus:border-emerald-500 font-mono"
          />
          <select
            value={months}
            onChange={e => setMonths(Number(e.target.value))}
            className="px-4 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-emerald-500"
          >
            {[1, 3, 6, 12, 24].map(m => <option key={m} value={m}>{t('da.months', { n: m })}</option>)}
          </select>
          <button
            onClick={runIngest}
            disabled={ingesting || !symbol.trim()}
            className="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors flex-shrink-0"
          >
            {ingesting ? t('da.ingesting') : t('da.ingest')}
          </button>
        </div>
        {ingestMsg && <p className="text-sm text-slate-300 mt-3">{ingestMsg}</p>}
      </div>

      <div className="glass-panel p-5 border-l-4 border-l-indigo-500">
        <h3 className="text-white font-semibold mb-1 flex items-center gap-2">
          <Boxes size={18} className="text-indigo-400" />
          {t('da.awsTitle')}
        </h3>
        <p className="text-xs text-slate-500 mb-4">{t('da.awsNote')}</p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{t('da.awsChain')}</span>
            <select value={awsChain}
              onChange={e => { setAwsChain(e.target.value); setAwsTable((aws.tables?.[e.target.value] || ['blocks'])[0]); }}
              className="px-3 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-indigo-500">
              {Object.keys(aws.chains || {}).map(c => <option key={c} value={c}>{aws.chains[c]}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{t('da.awsTable')}</span>
            <select value={awsTable} onChange={e => setAwsTable(e.target.value)}
              className="px-3 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-indigo-500">
              {(aws.tables?.[awsChain] || []).map((tb: string) => <option key={tb} value={tb}>{tb}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{t('da.awsFrom')}</span>
            <input type="date" value={awsFrom} onChange={e => setAwsFrom(e.target.value)}
              className="px-3 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-indigo-500" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{t('da.awsTo')}</span>
            <input type="date" value={awsTo} onChange={e => setAwsTo(e.target.value)}
              className="px-3 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-indigo-500" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{t('da.awsBudget')}</span>
            <select value={awsBudget} onChange={e => setAwsBudget(Number(e.target.value))}
              className="px-3 min-h-[44px] rounded-lg bg-slate-800 border border-slate-700 text-white outline-none focus:border-indigo-500">
              {[0.5, 2, 5, 20, 100].map(g => <option key={g} value={g}>{g} GB</option>)}
            </select>
          </label>
        </div>

        {awsPerDayMb != null && (
          <p className="text-xs text-slate-500 mt-2">
            {awsChain}/{awsTable} — {t('da.awsPerDay', { mb: awsPerDayMb })}
          </p>
        )}

        <div className="flex flex-col sm:flex-row gap-3 mt-3">
          <button onClick={() => awsRun(true)} disabled={awsBusy}
            className="px-5 min-h-[44px] sm:min-h-0 sm:py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 disabled:opacity-50 text-white rounded-lg transition-colors">
            {t('da.awsPreview')}
          </button>
          <button onClick={() => awsRun(false)} disabled={awsBusy}
            className="px-5 min-h-[44px] sm:min-h-0 sm:py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 text-white rounded-lg transition-colors">
            {awsBusy ? t('da.ingesting') : t('da.awsIngest')}
          </button>
        </div>
        {awsMsg && <p className="text-sm text-slate-300 mt-3 break-words">{awsMsg}</p>}
      </div>

      {assetsInfo.synthetic_files > 0 && (
        <div className="glass-panel p-4 border-l-4 border-l-amber-500 bg-amber-900/20 text-amber-200 text-sm">
          {t('da.syntheticWarn', { syn: assetsInfo.synthetic_files, total: assetsInfo.total_files })}
        </div>
      )}

      {/* Hypersync status */}
      <div className="glass-panel p-6 border-l-4 border-l-blue-500 bg-gradient-to-r from-blue-900/40 to-slate-900/40 relative overflow-hidden">
        <div className="absolute top-0 right-0 p-4 opacity-5 pointer-events-none">
          <Zap size={120} />
        </div>
        <div className="flex justify-between items-start relative z-10">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Zap className="text-blue-400 flex-shrink-0" size={24} />
              <h3 className="text-xl sm:text-2xl font-bold text-white">Hypersync <span className="text-blue-400">(by Envio)</span></h3>
            </div>
            <p className="text-blue-100/70 mb-4 max-w-2xl text-sm">
              {t('da.hypersyncNote')}
            </p>
            <div className="flex flex-wrap gap-4">
              <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                <div className="text-xs text-slate-400 mb-1">{t('da.realFiles')}</div>
                <div className="text-lg font-mono text-white">{assetsInfo.real_files}</div>
              </div>
              <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                <div className="text-xs text-slate-400 mb-1">{t('da.synFiles')}</div>
                <div className="text-lg font-mono text-amber-300">{assetsInfo.synthetic_files}</div>
              </div>
              <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                <div className="text-xs text-slate-400 mb-1">{t('da.totalSize')}</div>
                <div className="text-lg font-mono text-blue-300">{(assetsInfo.total_size / (1024 * 1024)).toFixed(2)} MB</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">{t('da.rcloneTitle')}</h3>
            <UploadCloud className={syncStatus.rclone_union.configured ? 'text-emerald-400' : 'text-slate-500'} />
          </div>
          {syncStatus.rclone_union.configured ? (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-slate-400">{t('da.upstreams')}</span><span className="text-emerald-300 font-mono">{syncStatus.rclone_union.upstream_count}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">{t('da.policy')}</span><span className="text-slate-200 font-mono">{syncStatus.rclone_union.policy || 'default'}</span></div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">{t('da.rcloneNone')}</p>
          )}
        </div>

        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">{t('da.watchdogTitle')}</h3>
            <Archive className={syncStatus.compaction_watchdog.running ? 'text-emerald-400' : 'text-slate-500'} />
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-slate-400">{t('da.status')}</span>
              <span className={syncStatus.compaction_watchdog.running ? 'text-emerald-400' : 'text-slate-400'}>
                {syncStatus.compaction_watchdog.running ? t('da.running') : t('da.notRunning')}
              </span>
            </div>
            <div className="flex justify-between"><span className="text-slate-400">{t('da.filesCompacted')}</span><span className="text-slate-200 font-mono">{syncStatus.compaction_watchdog.files_compacted_total}</span></div>
            <div className="flex justify-between"><span className="text-slate-400">{t('da.lastCompaction')}</span><span className="text-slate-200 font-mono text-xs">{lastCompactionText}</span></div>
            {syncStatus.compaction_watchdog.last_error && (
              <p className="text-red-400 text-xs mt-2">Last error: {syncStatus.compaction_watchdog.last_error}</p>
            )}
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-xl font-semibold text-white mb-4 mt-2 flex items-center gap-2">
          <Database size={18} className="text-slate-400" />
          {t('da.ingestedFiles')}
        </h3>
        <div className="glass-panel p-3 sm:p-0">
          <ResponsiveTable
            rows={visibleAssets}
            loading={loading ? t('da.loading') : undefined}
            empty={assetsInfo.total_files === 0 ? t('da.noFiles') : t('da.noneOfType', { filter: filter === 'real' ? t('da.filterReal') : filter === 'synthetic' ? t('da.filterSynthetic') : t('da.filterAll') })}
            columns={[
              { key: 'file', header: t('da.colFile'), cellClass: 'text-slate-200 font-mono text-xs sm:text-sm break-all', render: (a: any) => a.filename },
              { key: 'size', header: t('da.colSize'), cellClass: 'text-slate-400 text-sm', render: (a: any) => a.size_str },
              {
                key: 'source', header: t('da.colSource'), alignRight: true,
                render: (a: any) => (
                  <span className={`text-xs px-2 py-1 rounded ${a.is_synthetic ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'}`}>
                    {a.is_synthetic ? t('da.synthetic') : t('da.real')}
                  </span>
                ),
              },
            ]}
          />
        </div>
      </div>
    </div>
  );
}
