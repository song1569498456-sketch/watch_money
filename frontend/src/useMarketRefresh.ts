import { useEffect } from "react";
import type { MutableRefObject, Dispatch, SetStateAction } from "react";

// Query cadence, not a claim about exchange publication or field freshness.
export const MARKET_QUERY_MS = 5000;
export function useMarketRefresh(active: boolean, enabled: boolean, pending: MutableRefObject<boolean>, reload: Dispatch<SetStateAction<number>>) {
  useEffect(() => {
    if (!active || !enabled) return;
    const timer = setInterval(() => {
      if (!pending.current && document.visibilityState !== "hidden") reload(v => v + 1);
    }, MARKET_QUERY_MS);
    return () => clearInterval(timer);
  }, [active, enabled, pending, reload]);
}
