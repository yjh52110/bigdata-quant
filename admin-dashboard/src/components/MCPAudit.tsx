import React, { useState, useEffect } from 'react';
import { Activity, Lock } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function MCPAudit() {
  const [logs, setLogs] = useState<any[]>([]);

  useEffect(() => {
    const load = () => {
      fetch(`${API_BASE_URL}/api/mcp/logs`)
        .then(r => r.json())
        .then(data => setLogs(data.logs || []))
        .catch(console.error);
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const errorCount = logs.filter(l => l.status.includes('error') || l.status.includes('blocked')).length;

  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">MCP Protocol & Audit Logs</h2>
        <p className="text-slate-400">Real tool-call log from mcp_server.py (backend/data/mcp_invocations.jsonl)</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Lock className="text-blue-400" size={20} />
            Invocation Count
          </h3>
          <div className="text-4xl font-bold text-white">{logs.length}</div>
          <p className="text-xs text-slate-500 mt-2">logged tool calls (most recent 100)</p>
        </div>

        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Activity className="text-amber-400" size={20} />
            Errors / Blocked
          </h3>
          {errorCount === 0 ? (
            <p className="text-slate-400">No errors or blocked queries in the recent log.</p>
          ) : (
            <div className="text-4xl font-bold text-red-400">{errorCount}</div>
          )}
        </div>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <h3 className="text-lg font-semibold text-white mb-4">Live Invocation Logs</h3>
        <div className="flex-1 bg-slate-950 rounded-lg border border-slate-800 p-4 font-mono text-xs overflow-y-auto space-y-2">
          {logs.length === 0 ? (
            <div className="text-slate-600 italic">No MCP tool calls logged yet. Call a tool via mcp_server.py to see entries here.</div>
          ) : logs.map((log, i) => (
            <div key={i} className="flex gap-4">
              <span className="text-slate-500">{log.time}</span>
              <span className="text-blue-400 w-20 flex-shrink-0">[{log.client}]</span>
              <span className="text-purple-400 w-48 flex-shrink-0">{log.action}</span>
              <span className={log.status.includes('error') || log.status.includes('blocked') ? "text-red-400 font-bold" : "text-emerald-400"}>{log.status}</span>
              <span className="text-slate-400 ml-auto flex-shrink-0">{log.duration_ms}ms</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
