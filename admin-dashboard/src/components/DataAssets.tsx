import React, { useState, useEffect } from 'react';
import { Database, UploadCloud, Server, Filter, Zap, Activity, TrendingUp, BarChart3 } from 'lucide-react';

import { API_BASE_URL } from '../App';

export default function DataAssets() {
  const [assetsInfo, setAssetsInfo] = useState({ assets: [], total_files: 0, total_size: 0 });

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/data-assets`)
      .then(r => r.json())
      .then(data => setAssetsInfo(data))
      .catch(console.error);
  }, []);

  return (
    <div className="h-full flex flex-col gap-6 animate-fade-in overflow-y-auto pb-4">
      <header className="flex justify-between items-end">
        <div>
          <h2 className="text-3xl font-bold text-white mb-2">Data Assets & Sync Monitor</h2>
          <p className="text-slate-400">Multi-cloud data pipelines and market asset filtering. Total Files: {assetsInfo.total_files}, Size: {(assetsInfo.total_size / (1024*1024)).toFixed(2)} MB</p>
        </div>
        <div className="flex gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white rounded-lg transition-colors">
            <Filter size={16} />
            <span>Filter Assets</span>
          </button>
        </div>
      </header>

      {/* Hypersync Highlight */}
      <div className="glass-panel p-6 border-l-4 border-l-blue-500 bg-gradient-to-r from-blue-900/40 to-slate-900/40 relative overflow-hidden">
        <div className="absolute top-0 right-0 p-4 opacity-5 pointer-events-none">
          <Zap size={120} />
        </div>
        <div className="flex justify-between items-start relative z-10">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Zap className="text-blue-400" size={24} />
              <h3 className="text-2xl font-bold text-white">Hypersync <span className="text-blue-400">(by Envio)</span> 2026</h3>
            </div>
            <p className="text-blue-100/70 mb-4 max-w-2xl text-sm">
              Next-generation pipeline active. Streaming data to Parquet formats at <strong className="text-white">2000x faster</strong> speeds than traditional RPC polling.
            </p>
            <div className="flex flex-wrap gap-4">
               <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                  <div className="text-xs text-slate-400 mb-1">Extraction Rate</div>
                  <div className="text-lg font-mono text-white">4.2 TB/hr</div>
               </div>
               <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                  <div className="text-xs text-slate-400 mb-1">Latency</div>
                  <div className="text-lg font-mono text-emerald-400">12ms</div>
               </div>
               <div className="bg-slate-900/60 px-4 py-2 rounded-lg border border-slate-700/50">
                  <div className="text-xs text-slate-400 mb-1">Format</div>
                  <div className="text-lg font-mono text-blue-300">Optimized Parquet</div>
               </div>
            </div>
          </div>
          <div className="text-right flex-shrink-0">
             <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs font-medium">
               <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></div>
               Stream Active
             </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">BigQuery Sync</h3>
            <UploadCloud className="text-purple-400" />
          </div>
          <div className="space-y-4">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-slate-400">DEX Trades (Daily)</span>
                <span className="text-purple-300">89%</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-1.5"><div className="bg-purple-500 h-1.5 rounded-full w-[89%]"></div></div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-slate-400">Market Events</span>
                <span className="text-purple-300">100%</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-1.5"><div className="bg-purple-500 h-1.5 rounded-full w-full"></div></div>
            </div>
          </div>
        </div>

        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">AWS S3 Copy</h3>
            <Database className="text-orange-400" />
          </div>
          <div className="space-y-4">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-slate-400">Cold Storage Backup</span>
                <span className="text-orange-300">45%</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-1.5"><div className="bg-orange-500 h-1.5 rounded-full w-[45%]"></div></div>
              <p className="text-xs text-slate-500 mt-1">Est. time remaining: 2h 15m</p>
            </div>
          </div>
        </div>

        <div className="glass-panel p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold text-white">Cryo Node Pool</h3>
            <Server className="text-emerald-400" />
          </div>
          <div className="flex items-center gap-4">
            <div className="text-4xl font-bold text-white">12<span className="text-lg text-slate-400 font-normal">/12</span></div>
            <div className="flex-1">
              <div className="flex justify-between text-sm mb-1">
                <span className="text-slate-400">Health Score</span>
                <span className="text-emerald-300">98%</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-1.5"><div className="bg-emerald-500 h-1.5 rounded-full w-[98%]"></div></div>
            </div>
          </div>
          <p className="text-xs text-slate-400 mt-4">5-minute slice queue: <span className="text-amber-400">2 pending</span></p>
        </div>
      </div>
      
      <div>
         <h3 className="text-xl font-semibold text-white mb-4 mt-2">Real-Time Data Assets</h3>
         <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Polymarket Card */}
            <div className="glass-panel p-5 border border-slate-700/50 hover:border-slate-600 transition-colors cursor-pointer group">
               <div className="flex justify-between items-start mb-4">
                  <div className="flex items-center gap-3">
                     <div className="w-10 h-10 rounded-lg bg-pink-500/20 flex items-center justify-center border border-pink-500/30 group-hover:bg-pink-500/30 transition-colors">
                        <Activity className="text-pink-400" size={20} />
                     </div>
                     <div>
                        <h4 className="text-white font-medium">Polymarket</h4>
                        <p className="text-xs text-slate-400">Prediction Markets</p>
                     </div>
                  </div>
                  <div className="flex items-center gap-1 bg-slate-800 rounded px-2 py-1">
                     <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                     <span className="text-[10px] font-medium text-slate-300 uppercase">Live</span>
                  </div>
               </div>
               <div className="space-y-3">
                  <div className="flex justify-between items-center py-2 border-b border-slate-800">
                     <span className="text-sm text-slate-400">Active Markets</span>
                     <span className="text-sm text-white font-mono">1,248</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-slate-800">
                     <span className="text-sm text-slate-400">Daily Volume</span>
                     <span className="text-sm text-white font-mono">$42.5M</span>
                  </div>
                  <div className="flex justify-between items-center py-2">
                     <span className="text-sm text-slate-400">Events/sec</span>
                     <span className="text-sm text-emerald-400 font-mono">~350</span>
                  </div>
               </div>
            </div>

            {/* DEX Card */}
            <div className="glass-panel p-5 border border-slate-700/50 hover:border-slate-600 transition-colors cursor-pointer group">
               <div className="flex justify-between items-start mb-4">
                  <div className="flex items-center gap-3">
                     <div className="w-10 h-10 rounded-lg bg-indigo-500/20 flex items-center justify-center border border-indigo-500/30 group-hover:bg-indigo-500/30 transition-colors">
                        <TrendingUp className="text-indigo-400" size={20} />
                     </div>
                     <div>
                        <h4 className="text-white font-medium">DEX Aggregator</h4>
                        <p className="text-xs text-slate-400">Uniswap V4 / Curve</p>
                     </div>
                  </div>
                  <div className="flex items-center gap-1 bg-slate-800 rounded px-2 py-1">
                     <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                     <span className="text-[10px] font-medium text-slate-300 uppercase">Live</span>
                  </div>
               </div>
               <div className="space-y-3">
                  <div className="flex justify-between items-center py-2 border-b border-slate-800">
                     <span className="text-sm text-slate-400">Tracked Pools</span>
                     <span className="text-sm text-white font-mono">14,205</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-slate-800">
                     <span className="text-sm text-slate-400">Liquidity Depth</span>
                     <span className="text-sm text-white font-mono">$2.1B</span>
                  </div>
                  <div className="flex justify-between items-center py-2">
                     <span className="text-sm text-slate-400">Trades/sec</span>
                     <span className="text-sm text-emerald-400 font-mono">~1,840</span>
                  </div>
               </div>
            </div>

            {/* CEX Card */}
            <div className="glass-panel p-5 border border-slate-700/50 hover:border-slate-600 transition-colors cursor-pointer group">
               <div className="flex justify-between items-start mb-4">
                  <div className="flex items-center gap-3">
                     <div className="w-10 h-10 rounded-lg bg-amber-500/20 flex items-center justify-center border border-amber-500/30 group-hover:bg-amber-500/30 transition-colors">
                        <BarChart3 className="text-amber-400" size={20} />
                     </div>
                     <div>
                        <h4 className="text-white font-medium">CEX Orderbooks</h4>
                        <p className="text-xs text-slate-400">Binance / OKX</p>
                     </div>
                  </div>
                  <div className="flex items-center gap-1 bg-slate-800 rounded px-2 py-1">
                     <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                     <span className="text-[10px] font-medium text-slate-300 uppercase">Live</span>
                  </div>
               </div>
               <div className="space-y-3">
                  <div className="flex justify-between items-center py-2 border-b border-slate-800">
                     <span className="text-sm text-slate-400">Ticker Pairs</span>
                     <span className="text-sm text-white font-mono">3,892</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-slate-800">
                     <span className="text-sm text-slate-400">24h Volume</span>
                     <span className="text-sm text-white font-mono">$18.4B</span>
                  </div>
                  <div className="flex justify-between items-center py-2">
                     <span className="text-sm text-slate-400">Updates/sec</span>
                     <span className="text-sm text-emerald-400 font-mono">~45,000</span>
                  </div>
               </div>
            </div>
         </div>
      </div>
    </div>
  );
}
