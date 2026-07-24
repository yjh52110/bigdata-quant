import React, { useState, useEffect } from 'react';
import { Database, UploadCloud, Filter, Zap, Archive } from 'lucide-react';

import { API_BASE_URL } from '../App';

export default function DataAssets() {
  const [assetsInfo, setAssetsInfo] = useState({ assets: [], total_files: 0, total_size: 0, synthetic_files: 0, real_files: 0 });
  const [syncStatus, setSyncStatus] = useState({
    rclone_union: { configured: false, upstream_count: 0, policy: null },
    compaction_watchdog: { running: false, last_compaction_at: null, files_compacted_total: 0, last_error: null },
  });

  useEffect(() => {
    const load = () => {
      fetch(`${API_BASE_URL}/api/data-assets`).then(r => r.json()).then(setAssetsInfo).catch(console.error);
      fetch(`${API_BASE_URL}/api/sync/status`).then(r => r.json()).then(setSyncStatus).catch(console.error);
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const lastCompactionText = syncStatus.compaction_watchdog.last_compaction_at
    ? new Date(syncStatus.compaction_watchdog.last_compaction_at * 1000).toLocaleString()
    : 'Never run';

  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in overflow-y-auto pb-4">
      <header className="flex justify-between items-end">
        <div>
          <h2 className="text-3xl font-bold text-white mb-2">Data Assets & Sync Monitor</h2>
          <p className="text-slate-400">
            Total Files: {assetsInfo.total_files} ({assetsInfo.real_files} real / {assetsInfo.synthetic_files} synthetic), Size: {(assetsInfo.total_size / (1024 * 1024)).toFixed(2)} MB
          </p>
        </div>
        <div className="flex gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white rounded-lg transition-colors">
            <Filter size={16} />
            <span>Filter Assets</span>
          </button>
        </div>
      </header>

      {assetsInfo.synthetic_files > 0 && (
        <div className="glass-panel p-4 border-l-4 border-l-amber-500 bg-amber-900/20 text-amber-200 text-sm">
          {assetsInfo.synthetic_files} of {assetsInfo.total_files} file(s) are synthetic test fixtures (filename starts with <code>synthetic_</code>), not real chain data.
          Set <code>HYPERSYNC_BEARER_TOKEN</code> and run ingestion to replace them.
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
              <Zap className="text-blue-400" size={24} />
              <h3 className="text-2xl font-bold text-white">Hypersync <span className="text-blue-400">(by Envio)</span></h3>
            </div>
            <p className="text-blue-100/70 mb-4 max-w-2xl text-sm">
              Streams real on-chain data straight to Parquet via <code>collect_parquet()</code>. Requires a free token from app.envio.dev.
            </p>
            <div className="flex flex-wrap gap-4">
              <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                <div className="text-xs text-slate-400 mb-1">Real Parquet Files</div>
                <div className="text-lg font-mono text-white">{assetsInfo.real_files}</div>
              </div>
              <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                <div className="text-xs text-slate-400 mb-1">Synthetic Test Files</div>
                <div className="text-lg font-mono text-amber-300">{assetsInfo.synthetic_files}</div>
              </div>
              <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                <div className="text-xs text-slate-400 mb-1">Total Size</div>
                <div className="text-lg font-mono text-blue-300">{(assetsInfo.total_size / (1024 * 1024)).toFixed(2)} MB</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">Rclone Union Sync</h3>
            <UploadCloud className={syncStatus.rclone_union.configured ? 'text-emerald-400' : 'text-slate-500'} />
          </div>
          {syncStatus.rclone_union.configured ? (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-slate-400">Healthy upstream accounts</span><span className="text-emerald-300 font-mono">{syncStatus.rclone_union.upstream_count}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">Routing policy</span><span className="text-slate-200 font-mono">{syncStatus.rclone_union.policy || 'default'}</span></div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Not configured yet — no rclone union remote found. Connect Google accounts and run the union manager.</p>
          )}
        </div>

        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">Compaction Watchdog</h3>
            <Archive className={syncStatus.compaction_watchdog.running ? 'text-emerald-400' : 'text-slate-500'} />
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-slate-400">Status</span>
              <span className={syncStatus.compaction_watchdog.running ? 'text-emerald-400' : 'text-slate-400'}>
                {syncStatus.compaction_watchdog.running ? 'Running' : 'Not running'}
              </span>
            </div>
            <div className="flex justify-between"><span className="text-slate-400">Files compacted (total)</span><span className="text-slate-200 font-mono">{syncStatus.compaction_watchdog.files_compacted_total}</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Last compaction</span><span className="text-slate-200 font-mono text-xs">{lastCompactionText}</span></div>
            {syncStatus.compaction_watchdog.last_error && (
              <p className="text-red-400 text-xs mt-2">Last error: {syncStatus.compaction_watchdog.last_error}</p>
            )}
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-xl font-semibold text-white mb-4 mt-2 flex items-center gap-2">
          <Database size={18} className="text-slate-400" />
          Ingested Files
        </h3>
        <div className="glass-panel p-0 overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">File</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Size</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400 text-right">Source</th>
              </tr>
            </thead>
            <tbody>
              {assetsInfo.assets.length === 0 ? (
                <tr><td colSpan={3} className="py-6 text-center text-slate-500">No parquet files ingested yet.</td></tr>
              ) : assetsInfo.assets.map((a: any, i: number) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4 text-slate-200 font-mono text-sm">{a.filename}</td>
                  <td className="py-3 px-4 text-slate-400 text-sm">{a.size_str}</td>
                  <td className="py-3 px-4 text-right">
                    <span className={`text-xs px-2 py-1 rounded ${a.is_synthetic ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'}`}>
                      {a.is_synthetic ? 'Synthetic' : 'Real'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
