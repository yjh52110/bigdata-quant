import React, { useState, useEffect } from 'react';
import { Terminal, Table2 } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';

export default function DuckDBEngine() {
  const { t } = useI18n();
  const [tables, setTables] = useState<string[]>([]);
  const [query, setQuery] = useState("SELECT 1 AS status, 'DuckDB engine ready' AS message;");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const loadTables = () => {
    apiFetch('/api/duckdb/tables')
      .then(r => r.json())
      .then(data => setTables(data.tables || []))
      .catch(console.error);
  };

  useEffect(() => {
    loadTables();
    const interval = setInterval(loadTables, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleExecute = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await apiFetch('/api/duckdb/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || t('db.queryFailed'));
        setResult(null);
      } else {
        setResult(data);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
      loadTables();
    }
  };

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('db.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base">{t('db.subtitle')}</p>
      </header>

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Table2 size={18} className="text-blue-400" />
          {t('db.mountedViews', { n: tables.length })}
        </h3>
        {tables.length === 0 ? (
          <p className="text-sm text-slate-500">{t('db.noViews')}</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {tables.map((tbl, i) => (
              <button
                key={i}
                onClick={() => setQuery(`SELECT * FROM ${tbl} LIMIT 20;`)}
                className="text-xs font-mono px-3 py-1.5 rounded bg-slate-800 border border-slate-700 text-slate-300 hover:border-blue-500 hover:text-blue-300 transition-colors"
              >
                {tbl}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 glass-panel p-0 flex flex-col overflow-hidden border-t-4 border-t-blue-500">
        <div className="bg-slate-800/80 px-4 py-2 border-b border-slate-700 flex items-center gap-2">
          <Terminal size={16} className="text-blue-400" />
          <span className="text-sm font-mono text-slate-300">{t('db.sandbox')}</span>
        </div>
        <div className="flex-1 p-4 bg-[#0d1117] font-mono text-sm text-slate-300 focus-within:ring-1 focus-within:ring-blue-500/50 transition-shadow min-h-[120px]">
          <textarea
            className="w-full h-full bg-transparent resize-none outline-none"
            placeholder={t('db.placeholder')}
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>
        <div className="bg-slate-800/80 px-4 py-3 border-t border-slate-700 flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2">
          <span className="text-sm text-slate-400 truncate">{t('db.available')} {tables.join(', ') || t('db.none')}</span>
          <button
            onClick={handleExecute}
            disabled={running}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 text-white font-medium rounded transition-colors shadow-lg shadow-blue-500/20 text-sm flex-shrink-0"
          >
            {running ? t('db.running') : t('db.execute')}
          </button>
        </div>

        {(result || error) && (
          <div className="border-t border-slate-700 p-4 max-h-64 overflow-auto bg-slate-900/60">
            {error ? (
              <p className="text-red-400 text-sm font-mono">{error}</p>
            ) : (
              <>
                <p className="text-xs text-emerald-400 mb-2 font-mono">
                  {t('db.rowsIn', { n: result.row_count, ms: result.duration_ms })}
                </p>
                <table className="w-full text-left text-xs font-mono min-w-[320px]">
                  <thead>
                    <tr>
                      {result.columns.map((c: string, i: number) => (
                        <th key={i} className="px-2 py-1 text-slate-400 border-b border-slate-700">{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.slice(0, 50).map((row: any, i: number) => (
                      <tr key={i} className="border-b border-slate-800">
                        {result.columns.map((c: string, j: number) => (
                          <td key={j} className="px-2 py-1 text-slate-300">{String(row[c])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
