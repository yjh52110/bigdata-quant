import React, { useState, useEffect } from 'react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { Activity, Database, Clock, Zap } from 'lucide-react';
import { apiFetch } from '../api';

export default function Overview() {
  const [stats, setStats] = useState({
    activeAccounts: 0,
    totalDataSize: '0 GB',
    apiLatency: '-',
    geminiStatus: '-',
    syncStatus: '-'
  });
  const [traffic, setTraffic] = useState<{ requests_total: number; avg_latency_ms: number; recent: any[] }>({
    requests_total: 0, avg_latency_ms: 0, recent: [],
  });

  useEffect(() => {
    const load = () => {
      apiFetch('/api/overview').then(r => r.json()).then(setStats).catch(console.error);
      apiFetch('/api/overview/traffic').then(r => r.json()).then(setTraffic).catch(console.error);
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const chartData = traffic.recent.map((r, i) => ({ i, latency: r.latency_ms }));

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">System Overview</h2>
        <p className="text-slate-400 text-sm sm:text-base">Real-time metrics from the running backend process</p>
      </header>

      {/* Top Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {[
          { title: "Active Accounts", value: stats.activeAccounts, icon: Activity, color: "text-blue-400", bg: "bg-blue-500/20" },
          { title: "Data Size", value: stats.totalDataSize, icon: Database, color: "text-emerald-400", bg: "bg-emerald-500/20" },
          { title: "Avg API Latency", value: stats.apiLatency, icon: Zap, color: "text-amber-400", bg: "bg-amber-500/20" },
          { title: "Gemini Status", value: stats.geminiStatus, icon: Clock, color: "text-purple-400", bg: "bg-purple-500/20" }
        ].map((stat, i) => (
          <div key={i} className="glass-panel p-5 flex items-center justify-between group hover:scale-[1.02] transition-transform duration-300">
            <div>
              <p className="text-sm text-slate-400 font-medium mb-1">{stat.title}</p>
              <h3 className="text-2xl font-bold text-white">{stat.value}</h3>
            </div>
            <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${stat.bg}`}>
              <stat.icon className={stat.color} size={24} />
            </div>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1">
        <div className="lg:col-span-2 glass-panel p-5 flex flex-col">
          <h3 className="text-lg font-semibold text-white mb-1">Recent Request Latency</h3>
          <p className="text-xs text-slate-500 mb-4">Real per-request latency for this API process (last {traffic.recent.length} requests, {traffic.requests_total} total since startup)</p>
          <div className="flex-1 min-h-[300px]">
            {chartData.length === 0 ? (
              <div className="h-full flex items-center justify-center text-slate-500 text-sm">No requests recorded yet</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                  <XAxis dataKey="i" stroke="#94a3b8" tick={{ fontSize: 12 }} label={{ value: 'request #', position: 'insideBottom', fontSize: 10, fill: '#64748b' }} />
                  <YAxis stroke="#94a3b8" tick={{ fontSize: 12 }} label={{ value: 'ms', angle: -90, position: 'insideLeft', fontSize: 10, fill: '#64748b' }} />
                  <Tooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px' }} />
                  <Area type="monotone" dataKey="latency" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#colorLatency)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="glass-panel p-5 flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-white">Strategy Leaderboard</h3>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-700 text-slate-400 uppercase tracking-wide">Preview — not real</span>
          </div>
          <p className="text-xs text-slate-500 mb-3">
            Strategy mining isn't implemented yet. This is illustrative sample data, not output from a real backtest.
          </p>
          <div className="flex-1 overflow-y-auto space-y-3 pr-2 opacity-60">
            {[
              { name: "Alpha-Omega-01", roi: "+24.5%", sharp: "2.4" },
              { name: "Mean-Rev-BTC", roi: "+18.2%", sharp: "1.9" },
              { name: "Arb-Flash-Bot", roi: "+12.1%", sharp: "3.1" },
            ].map((strat, i) => (
              <div key={i} className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3 flex justify-between items-center">
                <div>
                  <h4 className="text-sm font-semibold text-slate-200">{strat.name}</h4>
                  <p className="text-xs text-slate-400">Sharpe: {strat.sharp}</p>
                </div>
                <div className="text-emerald-400 font-bold text-sm bg-emerald-500/10 px-2 py-1 rounded">
                  {strat.roi}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
