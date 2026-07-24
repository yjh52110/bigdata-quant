import React, { useState, useEffect } from 'react';
import { Server, Bell, Cpu, MemoryStick } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function Infrastructure() {
  const [infra, setInfra] = useState({ cpu: { percent: 0 }, memory: { percent: 0 }, disk: { percent: 0 } });

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/infrastructure`)
      .then(r => r.json())
      .then(data => setInfra(data))
      .catch(console.error);
  }, []);
  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">Infrastructure & Alerts</h2>
        <p className="text-slate-400">VPS Monitoring and Alert Routing</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4">Colab Clusters</h3>
          <div className="space-y-4">
            <div className="bg-slate-800/40 p-3 rounded-lg border border-slate-700/50">
              <div className="flex justify-between items-center mb-2">
                <span className="font-medium text-slate-200">Colab-T4-Pool</span>
                <span className="text-xs px-2 py-1 bg-emerald-500/20 text-emerald-400 rounded">Online (8 nodes)</span>
              </div>
              <div className="grid grid-cols-2 gap-4 mt-3 text-sm">
                <div>
                  <div className="flex items-center gap-1 text-slate-400 mb-1"><Cpu size={14}/> CPU Util</div>
                  <div className="text-white font-bold">{infra.cpu?.percent || 0}%</div>
                </div>
                <div>
                  <div className="flex items-center gap-1 text-slate-400 mb-1"><MemoryStick size={14}/> RAM</div>
                  <div className="text-white font-bold">{infra.memory?.percent || 0}%</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4">Contabo VPS</h3>
          <div className="space-y-4">
            <div className="bg-slate-800/40 p-3 rounded-lg border border-slate-700/50">
              <div className="flex justify-between items-center mb-2">
                <span className="font-medium text-slate-200">EU-DB-Master</span>
                <span className="text-xs px-2 py-1 bg-emerald-500/20 text-emerald-400 rounded">Online</span>
              </div>
              <div className="grid grid-cols-2 gap-4 mt-3 text-sm">
                <div>
                  <div className="flex items-center gap-1 text-slate-400 mb-1"><Cpu size={14}/> CPU</div>
                  <div className="text-white font-bold">12%</div>
                </div>
                <div>
                  <div className="flex items-center gap-1 text-slate-400 mb-1"><MemoryStick size={14}/> RAM</div>
                  <div className="text-white font-bold text-amber-400">89%</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Bell className="text-blue-400" size={20} />
          Alert Routing Rules
        </h3>
        <div className="overflow-x-auto flex-1">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Trigger Condition</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Severity</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Channels</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400 text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {[
                { cond: "DuckDB slow query > 5s", sev: "Warning", ch: ["Telegram"], active: true },
                { cond: "VPS RAM > 95%", sev: "Critical", ch: ["Lark", "DingTalk"], active: true },
                { cond: "Google Drive Rate Limited", sev: "Error", ch: ["Telegram", "Lark"], active: true },
                { cond: "Gemini Key Exhausted", sev: "Info", ch: ["Telegram"], active: false },
              ].map((rule, i) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4 text-slate-200 text-sm">{rule.cond}</td>
                  <td className="py-3 px-4">
                    <span className={`text-xs px-2 py-1 rounded ${
                      rule.sev === 'Critical' ? 'bg-red-500/20 text-red-400 border border-red-500/30' :
                      rule.sev === 'Error' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' :
                      rule.sev === 'Warning' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' :
                      'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                    }`}>
                      {rule.sev}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-sm text-slate-400 flex gap-2">
                    {rule.ch.map((c, j) => (
                      <span key={j} className="bg-slate-700/50 px-2 py-0.5 rounded text-xs">{c}</span>
                    ))}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <div className={`inline-flex w-8 h-4 rounded-full p-0.5 cursor-pointer ${rule.active ? 'bg-blue-500' : 'bg-slate-700'}`}>
                      <div className={`w-3 h-3 rounded-full bg-white transform transition-transform ${rule.active ? 'translate-x-4' : 'translate-x-0'}`}></div>
                    </div>
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
