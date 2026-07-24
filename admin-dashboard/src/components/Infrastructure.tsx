import React, { useState, useEffect } from 'react';
import { Server, Bell, Cpu, MemoryStick, HardDrive, Send } from 'lucide-react';
import { API_BASE_URL } from '../App';

export default function Infrastructure() {
  const [infra, setInfra] = useState<any>({ host_label: '', cpu: { percent: 0 }, memory: { percent: 0 }, disk: { percent: 0 } });
  const [alerts, setAlerts] = useState<{ telegram_configured: boolean; rules: any[] }>({ telegram_configured: false, rules: [] });
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    const load = () => {
      fetch(`${API_BASE_URL}/api/infrastructure`).then(r => r.json()).then(setInfra).catch(console.error);
      fetch(`${API_BASE_URL}/api/alerts`).then(r => r.json()).then(setAlerts).catch(console.error);
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const sendTest = async () => {
    setTestResult('Sending...');
    try {
      const res = await fetch(`${API_BASE_URL}/api/alerts/test`, { method: 'POST' });
      const data = await res.json();
      setTestResult(res.ok ? 'Sent!' : (data.detail || 'Failed'));
    } catch {
      setTestResult('Request failed');
    }
  };

  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-3xl font-bold text-white mb-2">Infrastructure & Alerts</h2>
        <p className="text-slate-400">Host Monitoring and Alert Routing</p>
      </header>

      <div className="glass-panel p-5">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Server size={18} className="text-blue-400" />
          {infra.host_label || 'Compute Host'}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <div className="flex items-center gap-1 text-slate-400 mb-1"><Cpu size={14} /> CPU Util</div>
            <div className="text-2xl text-white font-bold">{infra.cpu?.percent ?? 0}%</div>
          </div>
          <div>
            <div className="flex items-center gap-1 text-slate-400 mb-1"><MemoryStick size={14} /> RAM</div>
            <div className={`text-2xl font-bold ${infra.memory?.percent > 90 ? 'text-red-400' : 'text-white'}`}>{infra.memory?.percent ?? 0}%</div>
          </div>
          <div>
            <div className="flex items-center gap-1 text-slate-400 mb-1"><HardDrive size={14} /> Disk</div>
            <div className="text-2xl text-white font-bold">{infra.disk?.percent ?? 0}%</div>
          </div>
        </div>
        <p className="text-xs text-slate-500 mt-4">
          These are real stats for whatever machine is running the FastAPI backend right now. There is no separate
          Colab/Contabo deployment wired up — deploy the backend there and this card will reflect that host instead.
        </p>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Bell className="text-blue-400" size={20} />
            Alert Routing Rules
          </h3>
          <div className="flex items-center gap-3">
            <span className={`text-xs px-2 py-1 rounded ${alerts.telegram_configured ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
              Telegram {alerts.telegram_configured ? 'configured' : 'not configured'}
            </span>
            <button
              onClick={sendTest}
              disabled={!alerts.telegram_configured}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-xs rounded-lg transition-colors"
            >
              <Send size={12} /> Send test alert
            </button>
          </div>
        </div>
        {testResult && <p className="text-xs text-slate-400 mb-3">{testResult}</p>}
        <div className="overflow-x-auto flex-1">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Trigger Condition</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Severity</th>
                <th className="py-3 px-4 text-sm font-semibold text-slate-400">Channel</th>
              </tr>
            </thead>
            <tbody>
              {alerts.rules.map((rule, i) => (
                <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4 text-slate-200 text-sm">{rule.condition}</td>
                  <td className="py-3 px-4">
                    <span className={`text-xs px-2 py-1 rounded ${
                      rule.severity === 'Critical' ? 'bg-red-500/20 text-red-400 border border-red-500/30' :
                      rule.severity === 'Error' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' :
                      rule.severity === 'Warning' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' :
                      'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                    }`}>
                      {rule.severity}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-sm text-slate-400">
                    <span className="bg-slate-700/50 px-2 py-0.5 rounded text-xs">{rule.channel}</span>
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
