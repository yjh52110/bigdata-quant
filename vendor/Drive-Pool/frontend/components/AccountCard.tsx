type Account = {
  account_index: number;
  email: string | null;
  is_connected: boolean;
  used: number;
  limit: number;
  free: number;
};

function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  return (bytes / 1e3).toFixed(0) + " KB";
}

export default function AccountCard({
  account,
  onDisconnect,
}: {
  account: Account;
  onDisconnect: () => void;
}) {
  const pct = account.limit > 0 ? Math.min(100, (account.used / account.limit) * 100) : 0;

  async function handleDisconnect() {
    await fetch(`/api/accounts/${account.account_index}`, {
      method: "DELETE",
      credentials: "include",
    });
    onDisconnect();
  }

  async function handleConnect() {
    const res = await fetch(`/api/auth/oauth/${account.account_index}`, {
      credentials: "include",
    });
    const data = await res.json();
    window.location.href = data.auth_url;
  }

  return (
    <div
      className={`rounded-xl border p-5 transition ${
        account.is_connected
          ? "border-[#21212b] bg-[#15151a]"
          : "border-[#21212b]/50 bg-[#121217] opacity-60"
      }`}
    >
      <div className="mb-4 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white">
            {account.email ?? `Account ${account.account_index}`}
          </p>
          <div className="mt-1 flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${account.is_connected ? "bg-emerald-400" : "bg-[#555568]"}`} />
            <span className={`text-xs ${account.is_connected ? "text-emerald-400" : "text-[#555568]"}`}>
              {account.is_connected ? "Connected" : "Disconnected"}
            </span>
          </div>
        </div>
        <span className="flex-shrink-0 rounded-md border border-[#21212b] px-2 py-0.5 text-[10px] text-[#555568]">
          #{account.account_index}
        </span>
      </div>

      {account.is_connected && (
        <div className="mb-4 space-y-1.5">
          <div className="flex justify-between text-xs text-[#555568]">
            <span>{formatBytes(account.used)} used</span>
            <span>{formatBytes(account.free)} free</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#21212b]">
            <div
              className={`h-full rounded-full transition-all ${pct > 80 ? "bg-red-500" : "bg-indigo-500"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {account.is_connected ? (
        <button
          onClick={handleDisconnect}
          className="w-full rounded-lg border border-[#21212b] py-2 text-xs text-[#8888a4] transition hover:border-red-500/40 hover:text-red-400"
        >
          Disconnect
        </button>
      ) : (
        <button
          onClick={handleConnect}
          className="w-full rounded-lg bg-indigo-500/10 py-2 text-xs font-medium text-indigo-400 transition hover:bg-indigo-500/20"
        >
          Connect
        </button>
      )}
    </div>
  );
}
