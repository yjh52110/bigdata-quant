import React, { useState, useEffect } from 'react';
import { Server, Bell, Cpu, MemoryStick, HardDrive, Send } from 'lucide-react';
import { apiFetch } from '../api';
import { useI18n } from '../i18n';
import ResponsiveTable from './ResponsiveTable';

export default function Infrastructure() {
  const { t } = useI18n();
  const [infra, setInfra] = useState<any>({ host_label: '', cpu: { percent: 0 }, memory: { percent: 0 }, disk: { percent: 0 } });
  const [alerts, setAlerts] = useState<{ telegram_configured: boolean; rules: any[] }>({ telegram_configured: false, rules: [] });
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    const load = () => {
      apiFetch('/api/infrastructure').then(r => r.json()).then(setInfra).catch(console.error);
      apiFetch('/api/alerts').then(r => r.json()).then(setAlerts).catch(console.error);
    };
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  // Alert rules arrive with a stable id, so translate by id rather than by
  // matching on the backend's English condition text.
  const tRule = (rule: any) => {
    const key = `rule.${rule.id}` as Parameters<typeof t>[0];
    const translated = t(key);
    return translated === key ? rule.condition : translated;
  };
  const tSeverity = (sev: string) => {
    const key = `sev.${sev}` as Parameters<typeof t>[0];
    const translated = t(key);
    return translated === key ? sev : translated;
  };

  const sendTest = async () => {
    setTestResult(t('infra.sending'));
    try {
      const res = await apiFetch('/api/alerts/test', { method: 'POST' });
      const data = await res.json();
      setTestResult(res.ok ? t('infra.sent') : (data.detail || t('infra.failed')));
    } catch {
      setTestResult(t('infra.failed'));
    }
  };

  return (
    <div className="min-h-full flex flex-col gap-6 animate-fade-in">
      <header>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2">{t('infra.title')}</h2>
        <p className="text-slate-400 text-sm sm:text-base">{t('infra.subtitle')}</p>
      </header>

      <div className="glass-panel p-5">
        <h3 className="text-base sm:text-lg font-semibold text-white mb-4 flex items-start gap-2">
          <Server size={18} className="text-blue-400 flex-shrink-0 mt-1" />
          <span className="break-words">{t('infra.host')}</span>
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
          <div>
            <div className="flex items-center gap-1 text-slate-400 mb-1"><Cpu size={14} /> {t('infra.cpu')}</div>
            <div className="text-2xl text-white font-bold">{infra.cpu?.percent ?? 0}%</div>
          </div>
          <div>
            <div className="flex items-center gap-1 text-slate-400 mb-1"><MemoryStick size={14} /> {t('infra.ram')}</div>
            <div className={`text-2xl font-bold ${infra.memory?.percent > 90 ? 'text-red-400' : 'text-white'}`}>{infra.memory?.percent ?? 0}%</div>
          </div>
          <div>
            <div className="flex items-center gap-1 text-slate-400 mb-1"><HardDrive size={14} /> {t('infra.disk')}</div>
            <div className="text-2xl text-white font-bold">{infra.disk?.percent ?? 0}%</div>
          </div>
        </div>
        <p className="text-xs text-slate-500 mt-4">
          {t('infra.hostNote')}
        </p>
      </div>

      <div className="flex-1 glass-panel p-5 flex flex-col">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Bell className="text-blue-400" size={20} />
            {t('infra.alertRules')}
          </h3>
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`text-xs px-2 py-1 rounded ${alerts.telegram_configured ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
              {alerts.telegram_configured ? t('infra.telegramOn') : t('infra.telegramOff')}
            </span>
            <button
              onClick={sendTest}
              disabled={!alerts.telegram_configured}
              className="flex items-center justify-center gap-1.5 px-3 min-h-[44px] sm:min-h-0 sm:py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-xs rounded-lg transition-colors"
            >
              <Send size={12} /> {t('infra.sendTest')}
            </button>
          </div>
        </div>
        {testResult && <p className="text-xs text-slate-400 mb-3">{testResult}</p>}
        <ResponsiveTable
          rows={alerts.rules}
          empty="—"
          columns={[
            { key: 'cond', header: t('infra.colCondition'), cellClass: 'text-slate-200 text-sm', render: (r: any) => tRule(r) },
            {
              key: 'sev', header: t('infra.colSeverity'),
              render: (r: any) => (
                <span className={`text-xs px-2 py-1 rounded ${
                  r.severity === 'Critical' ? 'bg-red-500/20 text-red-400 border border-red-500/30' :
                  r.severity === 'Error' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' :
                  r.severity === 'Warning' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' :
                  'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                }`}>
                  {tSeverity(r.severity)}
                </span>
              ),
            },
            { key: 'ch', header: t('infra.colChannel'), render: (r: any) => <span className="bg-slate-700/50 px-2 py-0.5 rounded text-xs text-slate-400">{r.channel}</span> },
          ]}
        />
      </div>
    </div>
  );
}
