import React, { useState, useEffect } from 'react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { Activity, Database, Clock, Zap } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';

// Rows across every derived dataset -- the "TB compressed into GB" figure, and
// the only number here that reflects work this platform actually did.
const rowsTotal = (d: any) =>
  (d?.drive?.catalog ?? []).reduce((n: number, e: any) => n + (e.rows ?? 0), 0);

export default function Overview() {
  const { t } = useI18n();
  const [sources, setSources] = useState<any>(null);
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
      apiFetch('/api/datasources').then(r => r.json()).then(setSources).catch(console.error);
    apiFetch('/api/overview').then(r => r.json()).then(setStats).catch(console.error);
      apiFetch('/api/overview/traffic').then(r => r.json()).then(setTraffic).catch(console.error);
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const chartData = traffic.recent.map((r, i) => ({ i, latency: r.latency_ms }));

  // The backend returns fixed English status strings; map the known ones so
  // they follow the selected language instead of leaking through untranslated.
  const backendStatus = (v: string) => {
    const map: Record<string, Parameters<typeof t>[0]> = {
      'Healthy': 'st.healthy',
      'Not configured': 'st.notConfigured',
      'Active': 'st.active',
      'No accounts connected': 'st.noAccounts',
    };
    return map[v] ? t(map[v]) : v;
  };

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('ov.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base">{t('ov.subtitle')}</p>
      </header>

      {/* Top Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {[
          { title: t('ov.activeAccounts'), value: stats.activeAccounts, icon: Activity, color: "text-blue-400", bg: "bg-blue-500/20" },
          { title: t('ov.dataSize'), value: stats.totalDataSize, icon: Database, color: "text-emerald-400", bg: "bg-emerald-500/20" },
          { title: t('ov.latency'), value: stats.apiLatency, icon: Zap, color: "text-amber-400", bg: "bg-amber-500/20" },
          { title: t('ov.gemini'), value: backendStatus(stats.geminiStatus), icon: Clock, color: "text-purple-400", bg: "bg-purple-500/20" }
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
          <h3 className="text-lg font-semibold text-white mb-1">{t('ov.latencyChart')}</h3>
          <p className="text-xs text-slate-500 mb-4">{t('ov.latencyNote', { n: traffic.recent.length, total: traffic.requests_total })}</p>
          <div className="flex-1 min-h-[300px]">
            {chartData.length === 0 ? (
              <div className="h-full flex items-center justify-center text-slate-500 text-sm">{t('ov.noRequests')}</div>
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
          <h3 className="text-lg font-semibold text-white mb-1">{t('ov.pipeline')}</h3>
          <p className="text-xs text-slate-500 mb-4">{t('ov.pipelineNote')}</p>
          <div className="flex-1 space-y-3">
            {[
              { label: t('ov.pipeS3'), v: sources ? `${(sources.s3.total_gb / 1024).toFixed(2)} TB` : '—',
                sub: sources ? t('ov.pipeS3Sub', { n: sources.s3.chains.length }) : '', tone: 'text-blue-400' },
              { label: t('ov.pipeDrive'), v: sources ? `${(sources.drive.summary.total_bytes / 1048576).toFixed(1)} MB` : '—',
                sub: sources ? t('ov.pipeDriveSub', { n: sources.drive.summary.total_datasets }) : '', tone: 'text-purple-400' },
              { label: t('ov.pipeRows'), v: sources ? rowsTotal(sources).toLocaleString() : '—',
                sub: t('ov.pipeRowsSub'), tone: 'text-emerald-400' },
            ].map((row, i) => (
              <div key={i} className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3 flex justify-between items-center gap-3">
                <div className="min-w-0">
                  <h4 className="text-sm font-semibold text-slate-200">{row.label}</h4>
                  <p className="text-xs text-slate-500 break-words">{row.sub}</p>
                </div>
                <div className={`font-bold text-sm shrink-0 ${row.tone}`}>{row.v}</div>
              </div>
            ))}
          </div>
          {sources?.drive.catalog?.length === 0 && (
            <p className="text-xs text-amber-300/80 mt-3">{t('ov.pipeEmpty')}</p>
          )}
        </div>
      </div>
    </div>
  );
}
