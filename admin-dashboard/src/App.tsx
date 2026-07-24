import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard,
  Cloud,
  Database,
  Cpu,
  Key,
  Shield,
  Activity,
  Menu,
  X,
  LogOut,
} from 'lucide-react';
import Overview from './components/Overview';
import GoogleAccounts from './components/GoogleAccounts';
import DataAssets from './components/DataAssets';
import DuckDBEngine from './components/DuckDBEngine';
import GeminiPool from './components/GeminiPool';
import MCPAudit from './components/MCPAudit';
import Infrastructure from './components/Infrastructure';
import Login from './components/Login';
import { API_BASE_URL, getApiKey, clearApiKey } from './api';

const menuItems = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard, component: Overview },
  { id: 'google-accounts', label: 'Google Accounts', icon: Cloud, component: GoogleAccounts },
  { id: 'data-assets', label: 'Data Assets', icon: Database, component: DataAssets },
  { id: 'duckdb', label: 'DuckDB Engine', icon: Cpu, component: DuckDBEngine },
  { id: 'gemini', label: 'Gemini AI Pool', icon: Key, component: GeminiPool },
  { id: 'mcp-audit', label: 'MCP & Audit', icon: Shield, component: MCPAudit },
  { id: 'infrastructure', label: 'Infrastructure', icon: Activity, component: Infrastructure },
];

function App() {
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
        Connecting to backend...
      </div>
    );
  }

  if (apiHealthy === false) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-slate-900 text-slate-400 text-sm px-4 text-center">
        Cannot reach the backend at {API_BASE_URL}. Start it with{' '}
        <code className="mx-1 text-slate-300">uvicorn backend.api_server:app --port 8000</code> and reload.
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
            <p className="text-xs text-slate-400 mt-1">System Control Center</p>
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
              {item.label}
            </button>
          ))}
        </nav>
        <div className="p-4 border-t border-slate-700/50 space-y-3">
          <div className="flex items-center gap-3 px-2">
            <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
              apiHealthy === null ? 'bg-slate-500' : apiHealthy ? 'bg-green-500 shadow-[0_0_8px_#22c55e]' : 'bg-red-500 shadow-[0_0_8px_#ef4444]'
            }`}></div>
            <span className="text-xs text-slate-400 font-medium">
              {apiHealthy === null ? 'Checking API...' : apiHealthy ? 'API Reachable' : 'API Unreachable'}
            </span>
          </div>
          {authRequired && (
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-slate-400 hover:text-red-400 transition-colors"
            >
              <LogOut size={14} /> Log out
            </button>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto p-2 sm:p-4 lg:pl-0 w-full min-w-0">
        <div className="h-full glass-panel overflow-y-auto">
          <div className="p-4 sm:p-6 lg:p-8 h-full">
            <button
              className="lg:hidden mb-4 p-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-300"
              onClick={() => setSidebarOpen(true)}
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
