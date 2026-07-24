import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { Activity, DollarSign, Clock, Zap } from 'lucide-react';
import { API_BASE_URL } from '../App';

const mockPerformanceData = Array.from({ length: 24 }).map((_, i) => ({
  time: `${i}:00`,
  qps: Math.floor(Math.random() * 5000) + 2000,
  latency: Math.floor(Math.random() * 50) + 10,
}));

export default function Overview() {
  const [stats, setStats] = useState({
    activeAccounts: 0,
    totalDataSize: '0 GB',
    apiLatency: '-',
    geminiStatus: '-',
    syncStatus: '-'
  });

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/overview`)
      .then(r => r.json())
      .then(data => setStats(data))
      .catch(console.error);
  }, []);

  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header className="flex justify-between items-end">
        <div>
          <h2 className="text-3xl font-bold text-white mb-2">System Overview</h2>
          <p className="text-slate-400">Real-time metrics and financial summaries</p>
        </div>
        <div className="flex gap-2">
          <span className="px-3 py-1 bg-green-500/20 text-green-400 text-xs font-semibold rounded-full border border-green-500/30">Live Updates Active</span>
        </div>
      </header>

      {/* Top Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { title: "Active Accounts", value: stats.activeAccounts, icon: Activity, color: "text-blue-400", bg: "bg-blue-500/20" },
          { title: "Data Size", value: stats.totalDataSize, icon: DollarSign, color: "text-emerald-400", bg: "bg-emerald-500/20" },
          { title: "API Latency", value: stats.apiLatency, icon: Zap, color: "text-amber-400", bg: "bg-amber-500/20" },
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
          <h3 className="text-lg font-semibold text-white mb-4">Live QPS Traffic</h3>
          <div className="flex-1 min-h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={mockPerformanceData}>
                <defs>
                  <linearGradient id="colorQps" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <XAxis dataKey="time" stroke="#94a3b8" tick={{fontSize: 12}} />
                <YAxis stroke="#94a3b8" tick={{fontSize: 12}} />
                <Tooltip contentStyle={{backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '8px'}} />
                <Area type="monotone" dataKey="qps" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#colorQps)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="glass-panel p-5 flex flex-col">
          <h3 className="text-lg font-semibold text-white mb-4">Strategy Leaderboard</h3>
          <div className="flex-1 overflow-y-auto space-y-3 pr-2">
            {[
              { name: "Alpha-Omega-01", roi: "+24.5%", sharp: "2.4" },
              { name: "Mean-Rev-BTC", roi: "+18.2%", sharp: "1.9" },
              { name: "Arb-Flash-Bot", roi: "+12.1%", sharp: "3.1" },
              { name: "Grid-ETH-USDT", roi: "+8.4%", sharp: "1.5" },
              { name: "Stat-Arb-Sol", roi: "+6.9%", sharp: "1.2" }
            ].map((strat, i) => (
              <div key={i} className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3 flex justify-between items-center hover:bg-slate-800/80 transition-colors">
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
