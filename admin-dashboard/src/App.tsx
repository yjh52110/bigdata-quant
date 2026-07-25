import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard,
  Cloud,
  Database,
  Cpu,
  Key,
  Shield,
  Activity,
  Cloudy,
  Menu,
  X,
  LogOut,
} from 'lucide-react';
import Overview from './components/Overview';
import DataSources from './components/DataSources';
import GoogleAccounts from './components/GoogleAccounts';
import DataAssets from './components/DataAssets';
import DuckDBEngine from './components/DuckDBEngine';
import GeminiPool from './components/GeminiPool';
import MCPAudit from './components/MCPAudit';
import Infrastructure from './components/Infrastructure';
import ColabWorkers from './components/ColabWorkers';
import Login from './components/Login';
import { API_BASE_URL, getApiKey, clearApiKey } from './api';
import { useI18n } from './i18n';
import type { Key as I18nKey } from './i18n';

const menuItems: { id: string; labelKey: I18nKey; icon: typeof LayoutDashboard; component: React.ComponentType }[] = [
  { id: 'overview', labelKey: 'nav.overview', icon: LayoutDashboard, component: Overview },
  { id: 'google-accounts', labelKey: 'nav.accounts', icon: Cloud, component: GoogleAccounts },
  { id: 'data-sources', labelKey: 'nav.sources', icon: Database, component: DataSources },
  { id: 'data-assets', labelKey: 'nav.assets', icon: Database, component: DataAssets },
  { id: 'duckdb', labelKey: 'nav.duckdb', icon: Cpu, component: DuckDBEngine },
  { id: 'gemini', labelKey: 'nav.gemini', icon: Key, component: GeminiPool },
  { id: 'colab', labelKey: 'nav.colab', icon: Cloudy, component: ColabWorkers },
  { id: 'mcp-audit', labelKey: 'nav.mcp', icon: Shield, component: MCPAudit },
  { id: 'infrastructure', labelKey: 'nav.infra', icon: Activity, component: Infrastructure },
];

function App() {
  const { t, lang, setLang } = useI18n();
  const [activeModule, setActiveModule] = useState(menuItems[0].id);
  const [apiHealthy, setApiHealthy] = useState<boolean | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // null = still checking backend; true/false once we know whether a login is required
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState(false);

  const ActiveComponent = menuItems.find(m => m.id === activeModule)?.component || Overview;

  const checkAuthGate = () => {
    fetch(`${API_BASE_URL}/api/health`)
      .then(r => r.json())
      .then(data => {
        setApiHealthy(true);
        setAuthRequired(!!data.auth_enabled);
        if (!data.auth_enabled) {
          setAuthenticated(true);
        } else if (getApiKey()) {
          // We have a previously-entered password this session; trust it
          // until a real request comes back 401 (components handle that).
          setAuthenticated(true);
        }
      })
      .catch(() => {
        setApiHealthy(false);
        setAuthRequired(null);
      });
  };

  useEffect(() => {
    checkAuthGate();
    const interval = setInterval(() => {
      fetch(`${API_BASE_URL}/api/health`).then(r => setApiHealthy(r.ok)).catch(() => setApiHealthy(false));
    }, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleLogout = () => {
    clearApiKey();
    setAuthenticated(false);
  };

  if (authRequired === null && apiHealthy !== false) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-slate-900 text-slate-400 text-sm">
        {t('app.connecting')}
      </div>
    );
  }

  if (apiHealthy === false) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-slate-900 text-slate-400 text-sm px-4 text-center gap-2">
        <p>{t('app.cannotReach')} {API_BASE_URL}</p>
        <code className="text-slate-300">./start.sh</code>
        <p>{t('app.startHint')}</p>
      </div>
    );
  }

  if (authRequired && !authenticated) {
    return <Login onSuccess={() => { setAuthenticated(true); checkAuthGate(); }} />;
  }

  return (
    <div className="flex h-screen bg-slate-900 text-slate-200 overflow-hidden font-sans relative">
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-20 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`w-64 glass-panel m-4 flex flex-col z-30 flex-shrink-0 fixed inset-y-0 left-0 transition-transform duration-300 ease-out
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-[120%]'} lg:static lg:translate-x-0`}
      >
        <div className="p-6 border-b border-slate-700/50 flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
              ChainQuant Admin
            </h1>
            <p className="text-xs text-slate-400 mt-1">{t('app.subtitle')}</p>
          </div>
          <button className="lg:hidden text-slate-400 hover:text-white" onClick={() => setSidebarOpen(false)}>
            <X size={20} />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
          {menuItems.map((item) => (
            <button
              key={item.id}
              onClick={() => { setActiveModule(item.id); setSidebarOpen(false); }}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 text-sm font-medium ${
                activeModule === item.id
                  ? 'bg-blue-500/20 text-blue-400 shadow-[0_0_15px_rgba(59,130,246,0.15)] border border-blue-500/30'
                  : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
              }`}
            >
              <item.icon size={18} className={activeModule === item.id ? 'animate-pulse' : ''} />
              {t(item.labelKey)}
            </button>
          ))}
        </nav>
        <div className="p-4 border-t border-slate-700/50 space-y-3">
          <div className="flex gap-1 bg-slate-800/60 rounded-lg p-1">
            {(['zh', 'en'] as const).map(l => (
              <button
                key={l}
                onClick={() => setLang(l)}
                className={`flex-1 text-xs py-1.5 rounded-md transition-colors ${
                  lang === l ? 'bg-blue-500/25 text-blue-300' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {l === 'zh' ? '中文' : 'English'}
              </button>
            ))}
          </div>
          {authRequired && (
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-slate-400 hover:text-red-400 transition-colors"
            >
              <LogOut size={14} /> {t('app.logout')}
            </button>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto p-2 sm:p-4 lg:pl-0 w-full min-w-0">
        <div className="h-full glass-panel overflow-y-auto">
          <div className="p-4 sm:p-6 lg:p-8 h-full">
            {/* Floated rather than stacked so the page's own <h2> flows onto the
                same line -- each module renders its own title, so this is the only
                way to share a row with all of them without touching all eight. */}
            <button
              className="lg:hidden float-left mr-3 w-11 h-11 flex items-center justify-center rounded-lg bg-slate-800 border border-slate-700 text-slate-300"
              onClick={() => setSidebarOpen(true)}
              aria-label={t('app.openMenu')}
            >
              <Menu size={20} />
            </button>
            <ActiveComponent />
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
