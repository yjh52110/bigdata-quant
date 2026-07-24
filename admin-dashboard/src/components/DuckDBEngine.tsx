import React, { useState, useEffect } from 'react';
import { Cpu, Terminal, XOctagon } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function DuckDBEngine() {
  const [tables, setTables] = useState([]);
  const [query, setQuery] = useState("SELECT * FROM 'data/sample.parquet' LIMIT 10;");
  const [result, setResult] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/duckdb/tables`)
      .then(r => r.json())
      .then(data => setTables(data.tables || []))
      .catch(console.error);
  }, []);

  const handleExecute = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/duckdb/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      });
      const data = await res.json();
      setResult(data);
    } catch (e) {
      console.error(e);
    }
  };
  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">DuckDB Compute Engine</h2>
        <p className="text-slate-400">In-memory analytical queries & NVMe caching</p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4">Cache Hit Ratios</h3>
          <div className="space-y-6">
            <div className="relative pt-6">
              <div className="absolute top-0 left-0 text-xs font-bold text-slate-400 tracking-wider uppercase">Hot Cache (RAM)</div>
              <div className="flex items-end gap-2 mb-2">
                <span className="text-3xl font-bold text-emerald-400">94.2%</span>
                <span className="text-sm text-slate-400 mb-1">hit rate</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-2"><div className="bg-emerald-500 h-2 rounded-full w-[94.2%]"></div></div>
            </div>
            
            <div className="relative pt-6">
              <div className="absolute top-0 left-0 text-xs font-bold text-slate-400 tracking-wider uppercase">Cold Cache (NVMe)</div>
              <div className="flex items-end gap-2 mb-2">
                <span className="text-3xl font-bold text-blue-400">68.5%</span>
                <span className="text-sm text-slate-400 mb-1">hit rate</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-2"><div className="bg-blue-500 h-2 rounded-full w-[68.5%]"></div></div>
            </div>
          </div>
        </div>

        <div className="lg:col-span-2 glass-panel p-5 flex flex-col">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center justify-between">
            <span>Active SQL Queue</span>
            <span className="text-xs bg-slate-800 border border-slate-700 px-2 py-1 rounded text-slate-300">4 running</span>
          </h3>
          <div className="flex-1 overflow-auto">
            <div className="space-y-3">
              {[
                { id: "q_8892", time: "42s", query: "SELECT symbol, AVG(price) FROM dex_trades WHERE...", status: "running" },
                { id: "q_8893", time: "18s", query: "WITH moving_avg AS (SELECT * FROM eth_quotes...)", status: "running" },
                { id: "q_8894", time: "115s", query: "SELECT * FROM polymarket_events JOIN predictions...", status: "slow" },
              ].map((q, i) => (
                <div key={i} className="flex items-center justify-between bg-slate-900/60 p-3 rounded border border-slate-700/50 hover:border-slate-600 transition-colors">
                  <div className="flex-1 font-mono text-sm">
                    <span className="text-purple-400 mr-3">[{q.id}]</span>
                    <span className="text-slate-300 truncate inline-block max-w-[200px] sm:max-w-md">{q.query}</span>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className={`text-xs font-mono ${q.status === 'slow' ? 'text-red-400 font-bold' : 'text-slate-400'}`}>{q.time}</span>
                    <button className="text-slate-500 hover:text-red-400 transition-colors" title="Kill Query">
                      <XOctagon size={18} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 glass-panel p-0 flex flex-col overflow-hidden border-t-4 border-t-blue-500">
        <div className="bg-slate-800/80 px-4 py-2 border-b border-slate-700 flex items-center gap-2">
          <Terminal size={16} className="text-blue-400" />
          <span className="text-sm font-mono text-slate-300">Interactive SQL Sandbox</span>
        </div>
        <div className="flex-1 p-4 bg-[#0d1117] font-mono text-sm text-slate-300 focus-within:ring-1 focus-within:ring-blue-500/50 transition-shadow">
          <textarea 
            className="w-full h-full bg-transparent resize-none outline-none"
            placeholder="Type your DuckDB SQL query here... e.g. SELECT * FROM 's3://bucket/data.parquet' LIMIT 10;"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>
        <div className="bg-slate-800/80 px-4 py-3 border-t border-slate-700 flex justify-between">
          <span className="text-sm text-slate-400">Available Tables: {tables.join(', ')}</span>
          <button onClick={handleExecute} className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded transition-colors shadow-lg shadow-blue-500/20 text-sm">
            Execute Query (⌘+Enter)
          </button>
        </div>
      </div>
    </div>
  );
}
