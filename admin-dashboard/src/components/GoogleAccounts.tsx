import React, { useState, useEffect } from 'react';
import { HardDrive, Cloud, AlertTriangle, Play, Pause, RefreshCw } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function GoogleAccounts() {
  const [accountData, setAccountData] = useState({ poolStatus: {}, accounts: [] });

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/accounts`)
      .then(r => r.json())
      .then(data => setAccountData(data))
      .catch(console.error);
  }, []);

  const poolStatus = accountData.poolStatus;
  const accounts = accountData.accounts;
  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header className="flex justify-between items-end">
        <div>
          <h2 className="text-3xl font-bold text-white mb-2">Google Account Pool</h2>
          <p className="text-slate-400">100x 3TB/5TB Quota & Rclone Union Controls</p>
        </div>
        <button className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors shadow-lg shadow-blue-500/20">
          <RefreshCw size={16} />
          <span>Sync Status</span>
        </button>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="glass-panel p-5 flex flex-col justify-between">
          <div>
            <h3 className="text-lg font-semibold text-white mb-1">Global Quota Progress</h3>
            <p className="text-sm text-slate-400 mb-6">750GB Upload / 10TB Download Limit</p>
            
            <div className="space-y-6">
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-slate-300">Upload (750GB/day limit)</span>
                  <span className="text-blue-400 font-bold">420 GB / 750 GB</span>
                </div>
                <div className="w-full bg-slate-700/50 rounded-full h-2.5">
                  <div className="bg-blue-500 h-2.5 rounded-full" style={{ width: '56%' }}></div>
                </div>
              </div>
              
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-slate-300">Download (10TB/day limit)</span>
                  <span className="text-purple-400 font-bold">6.2 TB / 10 TB</span>
                </div>
                <div className="w-full bg-slate-700/50 rounded-full h-2.5">
                  <div className="bg-purple-500 h-2.5 rounded-full" style={{ width: '62%' }}></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="lg:col-span-2 glass-panel p-5">
          <h3 className="text-lg font-semibold text-white mb-4">Rclone Union Hot Mount Control</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">Account ID</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">Type</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">Status</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400">Usage</th>
                  <th className="py-3 px-4 text-sm font-semibold text-slate-400 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.length === 0 ? (
                  <tr><td colSpan={5} className="py-4 text-center text-slate-400">Loading accounts...</td></tr>
                ) : accounts.map((acc: any, i: number) => (
                  <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                    <td className="py-3 px-4 text-slate-200 font-medium">{acc.account_index}</td>
                    <td className="py-3 px-4 text-slate-400 text-sm">{acc.email || "Unknown"}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 text-xs font-semibold rounded bg-opacity-20 border border-opacity-30 ${
                        acc.health === 'ok' ? 'bg-emerald-500 border-emerald-500 text-emerald-400' :
                        acc.health === 'warn' ? 'bg-amber-500 border-amber-500 text-amber-400' :
                        'bg-red-500 border-red-500 text-red-400'
                      }`}>
                        {acc.is_connected ? "Active" : "Error"}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-300 text-sm">{((acc.used || 0) / (1024**3)).toFixed(2)} GB</td>
                    <td className="py-3 px-4 text-right">
                      <button className="text-slate-400 hover:text-white p-1 hover:bg-slate-700 rounded transition-colors mr-2">
                        {acc.health === 'error' ? <Play size={16} /> : <Pause size={16} />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
