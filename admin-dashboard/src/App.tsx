import React, { useState } from 'react';
import { 
  LayoutDashboard, 
  Cloud, 
  Database, 
  Cpu, 
  Key, 
  Shield, 
  Activity 
} from 'lucide-react';
import Overview from './components/Overview';
import GoogleAccounts from './components/GoogleAccounts';
import DataAssets from './components/DataAssets';
import DuckDBEngine from './components/DuckDBEngine';
import GeminiPool from './components/GeminiPool';
import MCPAudit from './components/MCPAudit';
import Infrastructure from './components/Infrastructure';

export const API_BASE_URL = 'http://localhost:8000';

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

  const ActiveComponent = menuItems.find(m => m.id === activeModule)?.component || Overview;

  return (
    <div className="flex h-screen bg-slate-900 text-slate-200 overflow-hidden font-sans">
      {/* Sidebar */}
      <aside className="w-64 glass-panel m-4 flex flex-col z-10 flex-shrink-0">
        <div className="p-6 border-b border-slate-700/50">
          <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
            OpenBrowser Admin
          </h1>
          <p className="text-xs text-slate-400 mt-1">System Control Center</p>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
          {menuItems.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveModule(item.id)}
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
        <div className="p-4 border-t border-slate-700/50">
          <div className="flex items-center gap-3 px-2">
            <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_#22c55e]"></div>
            <span className="text-xs text-slate-400 font-medium">All Systems Operational</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto p-4 pl-0">
        <div className="h-full glass-panel overflow-y-auto">
          <div className="p-8 h-full">
            <ActiveComponent />
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
