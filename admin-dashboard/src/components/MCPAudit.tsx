import React from 'react';
import { Shield, Activity, Lock, ExternalLink } from 'lucide-react';

export default function MCPAudit() {
  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">MCP Protocol & Audit Logs</h2>
        <p className="text-slate-400">Claude / OpenWebUI Integration & Security Auditing</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Lock className="text-blue-400" size={20} />
            Mount Status
          </h3>
          <div className="space-y-4">
            <div className="bg-slate-900/50 p-4 rounded-lg border border-slate-700/50 flex justify-between items-center">
              <div>
                <h4 className="font-medium text-slate-200">Claude Desktop MCP</h4>
                <p className="text-xs text-slate-400 mt-1">Local pipe connection</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                </span>
                <span className="text-sm text-emerald-400 font-medium">Connected</span>
              </div>
            </div>
            
            <div className="bg-slate-900/50 p-4 rounded-lg border border-slate-700/50 flex justify-between items-center">
              <div>
                <h4 className="font-medium text-slate-200">OpenWebUI Remote</h4>
                <p className="text-xs text-slate-400 mt-1">wss://ai.internal...</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                </span>
                <span className="text-sm text-emerald-400 font-medium">Connected</span>
              </div>
            </div>
          </div>
        </div>

        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Activity className="text-amber-400" size={20} />
            Rate Limit Audit
          </h3>
          <div className="h-40 flex items-center justify-center border border-dashed border-slate-700 rounded-lg">
             <div className="text-center">
                <p className="text-slate-400 mb-2">No active rate limit violations in past 24h.</p>
                <button className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1 mx-auto">
                  View historical graph <ExternalLink size={14} />
                </button>
             </div>
          </div>
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">Live Invocation Logs</h3>
        <div className="flex-1 bg-slate-950 rounded-lg border border-slate-800 p-4 font-mono text-xs overflow-y-auto space-y-2">
          {[
            { time: "14:23:01.002", client: "Claude", action: "duckdb_query", status: "200 OK", ms: "124ms" },
            { time: "14:22:58.412", client: "OpenWebUI", action: "fetch_market_data", status: "200 OK", ms: "89ms" },
            { time: "14:22:50.111", client: "Claude", action: "fetch_market_data", status: "429 Too Many", ms: "12ms", err: true },
            { time: "14:21:05.882", client: "Claude", action: "duckdb_query", status: "200 OK", ms: "402ms" },
          ].map((log, i) => (
            <div key={i} className="flex gap-4">
              <span className="text-slate-500">{log.time}</span>
              <span className="text-blue-400 w-20">[{log.client}]</span>
              <span className="text-purple-400 w-32">{log.action}</span>
              <span className={log.err ? "text-red-400 font-bold" : "text-emerald-400"}>{log.status}</span>
              <span className="text-slate-400 ml-auto">{log.ms}</span>
            </div>
          ))}
          <div className="text-slate-600 italic mt-4">Waiting for new events...</div>
        </div>
      </div>
    </div>
  );
}
