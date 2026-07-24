import React, { useState, useEffect } from 'react';
import { KeyRound } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function GeminiPool() {
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
      fetch(`${API_BASE_URL}/api/gemini/status`)
        .then(r => r.json())
        .then(data => setStatus(data))
        .catch(console.error);
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">Gemini AI Key Pool</h2>
        <p className="text-slate-400">Real per-key rotation status (GEMINI_API_KEY / GEMINI_API_KEYS)</p>
      </header>

      {!status.configured && (
        <div className="glass-panel p-4 border-l-4 border-l-amber-500 bg-amber-900/20 text-amber-200 text-sm">
          No Gemini keys configured. Set <code>GEMINI_API_KEY</code> (single) or <code>GEMINI_API_KEYS</code>
          (comma-separated) as environment variables before starting the backend.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400 mb-1">Requests Today (all keys)</p>
          <div className="text-3xl font-bold text-white">{status.requests_today_total}</div>
        </div>
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400 mb-1">Active Keys</p>
          <div className="text-3xl font-bold text-emerald-400">{status.active_keys} <span className="text-base text-slate-500">/ {status.total_keys}</span></div>
        </div>
        <div className="glass-panel p-5">
          <p className="text-sm text-slate-400 mb-1">Cooldown / Exhausted</p>
          <div className="text-3xl font-bold text-amber-400">{status.exhausted_keys}</div>
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">Key Rotation Status</h3>
        <div className="flex-1 overflow-x-auto">
          <table className="w-full text-left border-collapse min-w-[500px]">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Key (masked)</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Status</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Requests Today</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Cooldown Remaining</th>
              </tr>
            </thead>
            <tbody>
              {status.keys.length === 0 ? (
                <tr><td colSpan={4} className="py-6 text-center text-slate-500">No keys configured.</td></tr>
              ) : status.keys.map((k: any, i: number) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4 flex items-center gap-2">
                    <KeyRound size={14} className="text-slate-500" />
                    <span className="text-slate-200 font-mono text-sm">{k.alias}</span>
                  </td>
                  <td className="py-3 px-4">
                    <span className={`flex items-center gap-1.5 text-sm ${k.status === 'Active' ? 'text-emerald-400' : 'text-amber-400'}`}>
                      <span className={`w-2 h-2 rounded-full ${k.status === 'Active' ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
                      {k.status}
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
