import { useEffect, useState } from 'react';
import { Database, HardDrive, Play, ChevronDown, ChevronRight, Layers } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';
import ResponsiveTable from './ResponsiveTable';

const gb = (v: number | null) =>
  v == null ? '—' : v >= 1024 ? `${(v / 1024).toFixed(2)} TB` : `${v.toFixed(1)} GB`;

export default function DataSources() {
  const { t } = useI18n();
  const [data, setData] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [chain, setChain] = useState('eth');
  const [table, setTable] = useState('blocks');
  const [datePrefix, setDatePrefix] = useState('2024-01-15');
  const [sql, setSql] = useState('SELECT count(*) AS n FROM s3_eth_blocks_2024_01_15');
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [frag, setFrag] = useState<any>(null);
  const [compacting, setCompacting] = useState(false);
  const [compactMsg, setCompactMsg] = useState<string | null>(null);

  const loadFrag = () =>
    apiFetch('/api/storage/fragmentation').then(r => r.json()).then(setFrag).catch(console.error);

  const runCompact = async (dryRun: boolean) => {
    setCompacting(true); setCompactMsg(null);
    try {
      const res = await apiFetch('/api/storage/compact', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: dryRun }),
      });
      const d = await res.json();
      if (!res.ok) { setCompactMsg(d.detail); return; }
      setCompactMsg(dryRun
        ? t('ds.compactDry', { g: d.plan.groups.length, n: d.plan.files_small })
        : t('ds.compactDone', {
            g: d.merged_groups, n: d.files_removed, rows: (d.rows ?? 0).toLocaleString(),
            before: (d.bytes_before / 1048576).toFixed(1), after: (d.bytes_after / 1048576).toFixed(1) }));
      loadFrag();
    } catch (e) { setCompactMsg(String(e)); } finally { setCompacting(false); }
  };

  useEffect(() => {
    apiFetch('/api/datasources').then(r => r.json()).then(setData).catch(console.error);
    loadFrag();
  }, []);

  // Keep the SQL in step with the picker: the view name is derived, so a stale
  // query silently targets a table the user is no longer looking at.
  const syncSql = (c: string, tb: string, d: string) => {
    const view = ['s3', c, tb.replace(/-/g, '_'), d.replace(/-/g, '_')]
      .filter(Boolean).join('_');
    setSql(`SELECT count(*) AS n FROM ${view}`);
  };

  const run = async () => {
    setBusy(true); setResult(null);
    try {
      const res = await apiFetch('/api/s3/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chain, table, date_prefix: datePrefix, sql }),
      });
      const d = await res.json();
      setResult(res.ok ? d : { error: d.detail });
    } catch (e) {
      setResult({ error: String(e) });
    } finally { setBusy(false); }
  };

  if (!data) return <div className="text-slate-400">{t('da.loading')}</div>;
  const tablesOf = (c: string) =>
    data.s3.chains.find((x: any) => x.chain === c)?.tables ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('ds.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base">{t('ds.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400">{t('ds.s3Total')}</p>
          <p className="text-2xl font-bold text-white mt-1">{gb(data.s3.total_gb)}</p>
          <p className="text-xs text-slate-500 mt-1">{data.s3.chains.length} {t('ds.chains')}</p>
        </div>
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400">{t('ds.driveTotal')}</p>
          <p className="text-2xl font-bold text-white mt-1">
            {(data.drive.summary.total_bytes / 1024 ** 3).toFixed(2)} GB
          </p>
          <p className="text-xs text-slate-500 mt-1">
            {data.drive.summary.total_datasets} {t('ds.datasets')}
          </p>
        </div>
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400">{t('ds.rules')}</p>
          <p className="text-lg font-bold text-white mt-1">
            {data.drive.rules.compression} · {data.drive.rules.file_bytes_min / 1048576}–
            {data.drive.rules.file_bytes_max / 1048576} MB
          </p>
        </div>
      </div>

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
          <Database size={18} className="text-blue-400" /> {t('ds.s3Title')}
        </h3>
        <p className="text-xs text-slate-500 mb-4">{data.s3.note}</p>
        <div className="space-y-2">
          {data.s3.chains.map((c: any) => (
            <div key={c.chain} className="border border-slate-700/60 rounded-lg overflow-hidden">
              <button
                onClick={() => setOpen(open === c.chain ? null : c.chain)}
                className="w-full flex items-center justify-between gap-3 px-4 py-3 min-h-[44px] text-left hover:bg-slate-800/40"
              >
                <span className="flex items-center gap-2 min-w-0">
                  {open === c.chain ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span className="text-slate-200 font-mono text-sm">{c.chain}</span>
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    c.maintainer === 'aws' ? 'bg-emerald-500/20 text-emerald-400'
                                           : 'bg-amber-500/20 text-amber-400'}`}>
                    {c.maintainer}
                  </span>
                </span>
                <span className="text-sm text-slate-300 shrink-0">{gb(c.total_gb)}</span>
              </button>
              {open === c.chain && (
                <div className="px-2 pb-2">
                  <ResponsiveTable
                    rows={c.tables}
                    empty="—"
                    columns={[
                      { key: 'table', header: t('ds.colTable'), cellClass: 'text-slate-200 font-mono text-xs', render: (x: any) => x.table },
                      { key: 'gb', header: t('ds.colSize'), cellClass: 'text-cyan-300 text-xs', render: (x: any) => gb(x.gb) },
                      { key: 'days', header: t('ds.colDays'), cellClass: 'text-slate-400 text-xs', render: (x: any) => x.days ?? '—' },
                      { key: 'span', header: t('ds.colSpan'), cellClass: 'text-slate-500 text-xs break-all', render: (x: any) => x.earliest ? `${x.earliest} → ${x.latest}` : '—' },
                    ]}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
          <Play size={18} className="text-emerald-400" /> {t('ds.queryTitle')}
        </h3>
        <p className="text-xs text-slate-500 mb-3">{t('ds.queryNote')}</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
          <select value={chain}
            onChange={e => { const c = e.target.value; const tb = tablesOf(c)[0]?.table ?? 'blocks';
                             setChain(c); setTable(tb); syncSql(c, tb, datePrefix); }}
            className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200">
            {data.s3.chains.map((c: any) => <option key={c.chain} value={c.chain}>{c.chain}</option>)}
          </select>
          <select value={table}
            onChange={e => { setTable(e.target.value); syncSql(chain, e.target.value, datePrefix); }}
            className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200">
            {tablesOf(chain).map((x: any) => <option key={x.table} value={x.table}>{x.table}</option>)}
          </select>
          <input value={datePrefix}
            onChange={e => { setDatePrefix(e.target.value); syncSql(chain, table, e.target.value); }}
            placeholder="2024-01-15 / 2024-01 / 2024"
            className="bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono" />
        </div>
        <textarea value={sql} onChange={e => setSql(e.target.value)} rows={3}
          className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2.5 text-sm text-slate-200 font-mono mb-3" />
        <button onClick={run} disabled={busy}
          className="text-sm px-4 min-h-[44px] sm:min-h-0 sm:py-2 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 disabled:opacity-40">
          {busy ? t('ds.running') : t('ds.run')}
        </button>

        {result?.error && <p className="text-xs text-red-400 mt-3 font-mono break-words">{result.error}</p>}
        {result?.columns && (
          <div className="mt-4">
            <p className="text-xs text-slate-500 mb-2">
              {t('ds.resultNote', { n: result.row_count, ms: result.elapsed_ms })}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="border-b border-slate-700">
                  {result.columns.map((c: string) => (
                    <th key={c} className="text-left py-2 px-3 text-slate-400 font-semibold">{c}</th>))}
                </tr></thead>
                <tbody>
                  {result.rows.slice(0, 50).map((row: any[], i: number) => (
                    <tr key={i} className="border-b border-slate-800">
                      {row.map((v, j) => (
                        <td key={j} className="py-2 px-3 text-slate-300 font-mono break-all">{String(v)}</td>))}
                    </tr>))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {frag && (
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
            <Layers size={18} className="text-amber-400" /> {t('ds.compactTitle')}
          </h3>
          <p className="text-xs text-slate-500 mb-4">{t('ds.compactNote')}</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {[
              { l: t('ds.fragTotal'), v: frag.files_total },
              { l: t('ds.fragSmall'), v: frag.files_small, warn: frag.files_small > 0 },
              { l: t('ds.fragGroups'), v: frag.groups.length },
              { l: t('ds.fragAfter'), v: frag.estimated_parts_after },
            ].map((x, i) => (
              <div key={i} className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3">
                <p className="text-xs text-slate-500">{x.l}</p>
                <p className={`text-xl font-bold ${x.warn ? 'text-amber-400' : 'text-slate-200'}`}>{x.v}</p>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => runCompact(true)} disabled={compacting}
              className="text-sm px-4 min-h-[44px] sm:min-h-0 sm:py-2 rounded border border-slate-700 text-slate-300 disabled:opacity-40">
              {t('ds.compactPreview')}
            </button>
            <button onClick={() => runCompact(false)} disabled={compacting || frag.groups.length === 0}
              className="text-sm px-4 min-h-[44px] sm:min-h-0 sm:py-2 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 disabled:opacity-40">
              {compacting ? t('ds.compacting') : t('ds.compactRun')}
            </button>
          </div>
          {compactMsg && <p className="text-xs text-slate-300 mt-3 break-words">{compactMsg}</p>}
        </div>
      )}

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
          <HardDrive size={18} className="text-purple-400" /> {t('ds.driveTitle')}
        </h3>
        <p className="text-xs text-slate-500 mb-1">{data.drive.summary.note}</p>
        <p className="text-xs text-amber-300/80 mb-4">{data.drive.rules.why}</p>
        <ResponsiveTable
          rows={data.drive.catalog}
          empty={t('ds.noDatasets')}
          columns={[
            { key: 'dataset', header: t('ds.colDataset'), cellClass: 'text-slate-200 font-mono text-xs break-all', render: (x: any) => x.dataset },
            { key: 'layer', header: t('ds.colLayer'), cellClass: 'text-slate-400 text-xs', render: (x: any) => x.layer },
            { key: 'rows', header: t('ds.colRows'), cellClass: 'text-slate-300 text-xs', render: (x: any) => (x.rows ?? 0).toLocaleString() },
            { key: 'bytes', header: t('ds.colSize'), cellClass: 'text-cyan-300 text-xs', render: (x: any) => `${((x.bytes ?? 0) / 1048576).toFixed(1)} MB` },
            { key: 'span', header: t('ds.colSpan'), cellClass: 'text-slate-500 text-xs break-all', render: (x: any) => x.time_min ? `${x.time_min} → ${x.time_max}` : '—' },
          ]}
        />
      </div>
    </div>
  );
}
