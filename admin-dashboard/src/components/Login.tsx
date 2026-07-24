import React, { useState } from 'react';
import { Lock } from 'lucide-react';
import { API_BASE_URL, setApiKey } from '../api';

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecking(true);
    setError(null);
    try {
      // Any real /api/* route (other than /api/health) is protected by the
      // backend's api_key_guard middleware -- this is the same check the
      // backend itself enforces, not a separate/fake auth system.
      const res = await fetch(`${API_BASE_URL}/api/overview`, {
        headers: { 'X-API-Key': password },
      });
      if (res.ok) {
        setApiKey(password);
        onSuccess();
      } else {
        setError('Incorrect password.');
      }
    } catch {
      setError(`Could not reach the backend at ${API_BASE_URL}.`);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="h-screen w-screen flex items-center justify-center bg-slate-900 px-4">
      <form onSubmit={submit} className="glass-panel w-full max-w-sm p-8 flex flex-col gap-5">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="w-12 h-12 rounded-xl bg-blue-500/20 flex items-center justify-center">
            <Lock className="text-blue-400" size={22} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">ChainQuant Admin</h1>
            <p className="text-sm text-slate-400 mt-1">Enter the admin password to continue</p>
          </div>
        </div>

        <input
          type="password"
          autoFocus
          value={password}
          onChange={e => setPassword(e.target.value)}
          placeholder="Password"
          className="w-full px-4 py-3 rounded-lg bg-slate-800 border border-slate-700 text-white placeholder-slate-500 outline-none focus:border-blue-500 transition-colors"
        />

        {error && <p className="text-sm text-red-400 text-center">{error}</p>}

        <button
          type="submit"
          disabled={checking}
          className="w-full py-3 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 text-white font-medium rounded-lg transition-colors"
        >
          {checking ? 'Checking...' : 'Unlock'}
        </button>
      </form>
    </div>
  );
}
