import React, { useState, useEffect } from 'react';
import { KeyRound, Zap, Settings } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function GeminiPool() {
  const [status, setStatus] = useState({
    status: 'Loading',
    activeKeys: 0,
    exhaustedKeys: 0,
    requestsToday: 0
  });

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/gemini/status`)
      .then(r => r.json())
      .then(data => setStatus(data))
      .catch(console.error);
  }, []);
  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">Gemini AI Key Pool</h2>
        <p className="text-slate-400">API quota tracking and prompt configuration</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="glass-panel p-5 flex items-center justify-between md:col-span-2">
          <div>
            <p className="text-sm text-slate-400 mb-1">Global 1,500 RPD Limit</p>
            <div className="flex items-end gap-2 text-white">
              <span className="text-4xl font-bold">{status.requestsToday}</span>
              <span className="text-slate-400 mb-1">/ 1,500</span>
            </div>
          </div>
          <div className="w-20 h-20 relative">
            <svg className="w-full h-full transform -rotate-90" viewBox="0 0 36 36">
              <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#334155" strokeWidth="3" />
              <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#f59e0b" strokeWidth="3" strokeDasharray="83, 100" />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center text-sm font-bold text-amber-400">83%</div>
          </div>
        </div>

        <div className="glass-panel p-5 flex flex-col justify-center">
          <p className="text-sm text-slate-400 mb-2">Current RPM</p>
          <div className="flex items-center gap-3">
            <Zap className="text-amber-400" size={24} />
            <span className="text-3xl font-bold text-white">42<span className="text-base text-slate-400 font-normal">/min</span></span>
          </div>
        </div>
        
        <div className="glass-panel p-5 flex flex-col justify-center border border-indigo-500/30 bg-indigo-500/10 hover:bg-indigo-500/20 transition-colors cursor-pointer group">
          <div className="flex items-center gap-3 justify-center text-indigo-300 group-hover:text-indigo-200">
            <Settings size={24} />
            <span className="font-semibold">Prompt Config</span>
          </div>
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">10+ Key Rotation Status</h3>
        <div className="flex-1 overflow-x-auto">
          <table className="w-full text-left border-collapse min-w-[600px]">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Key Alias</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Tier</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Status</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">RPD Used</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">RPM</th>
              </tr>
            </thead>
            <tbody>
              {[
                { alias: "gemini-free-01", tier: "Free", status: "Active", rpd: "1,490/1,500", rpm: "12", warn: true },
                { alias: "gemini-free-02", tier: "Free", status: "Active", rpd: "840/1,500", rpm: "15", warn: false },
                { alias: "gemini-paid-main", tier: "Pay-as-you-go", status: "Active", rpd: "4,200/∞", rpm: "45", warn: false },
                { alias: "gemini-free-03", tier: "Free", status: "Exhausted", rpd: "1,500/1,500", rpm: "0", warn: true },
                { alias: "gemini-free-04", tier: "Free", status: "Active", rpd: "120/1,500", rpm: "2", warn: false }
              ].map((k, i) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4 flex items-center gap-2">
                    <KeyRound size={14} className="text-slate-500" />
                    <span className="text-slate-200 font-mono text-sm">{k.alias}</span>
                  </td>
                  <td className="py-3 px-4">
                    <span className={`text-xs px-2 py-1 rounded ${k.tier === 'Free' ? 'bg-slate-700 text-slate-300' : 'bg-purple-500/20 text-purple-400 border border-purple-500/30'}`}>
                      {k.tier}
                    </span>
                  </td>
                  <td className="py-3 px-4">
                    <span className={`flex items-center gap-1.5 text-sm ${k.status === 'Active' ? 'text-emerald-400' : 'text-red-400'}`}>
                      <span className={`w-2 h-2 rounded-full ${k.status === 'Active' ? 'bg-emerald-500' : 'bg-red-500'}`}></span>
                      {k.status}
                    </span>
                  </td>
                  <td className={`py-3 px-4 text-sm font-mono ${k.warn ? 'text-amber-400' : 'text-slate-300'}`}>{k.rpd}</td>
                  <td className="py-3 px-4 text-sm font-mono text-slate-400">{k.rpm}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
