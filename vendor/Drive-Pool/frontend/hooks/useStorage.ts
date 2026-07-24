"use client";

import { useCallback, useEffect, useState } from "react";

type Account = {
  account_index: number;
  email: string | null;
  is_connected: boolean;
  used: number;
  limit: number;
  free: number;
};

export function useStorage() {
  const [accounts, setAccounts] = useState<Account[]>([]);

  const refreshStorage = useCallback(async () => {
    try {
      const res = await fetch("/api/accounts", { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setAccounts(data);
      }
    } catch {
    }
  }, []);

  useEffect(() => {
    refreshStorage();
  }, [refreshStorage]);

  return { accounts, refreshStorage };
}
