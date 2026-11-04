import { useState } from "react";
import { Database, Loader2 } from "lucide-react";
import { api } from "./api";
import type { Settings } from "./types";

const deployments = [
  { protocol: "uniswap", chain: "ethereum", version: "v3", quote: false },
  { protocol: "uniswap", chain: "arbitrum", version: "v3", quote: false },
  { protocol: "uniswap", chain: "base", version: "v3", quote: false },
  { protocol: "pancakeswap", chain: "bsc", version: "v2", quote: true },
  { protocol: "aerodrome", chain: "base", version: "v1", quote: true },
];
const addressPattern = /^0x[0-9a-fA-F]{40}$/;

export function NativePoolPanel({ settings }: { settings: Settings | null }) {
  const [selection, setSelection] = useState(0);
  const [poolAddress, setPoolAddress] = useState("");
  const [tokenIn, setTokenIn] = useState("");
  const [amountIn, setAmountIn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ mode: string; data: unknown } | null>(null);
  const deployment = deployments[selection];
  const hasRpc = !!settings?.rpc_urls[deployment.chain]?.trim();
  const clear = () => { setResult(null); setError(""); };
  const request = async (mode: "state" | "quote") => {
    clear();
    if (!hasRpc) {
      setError(`请先在上方 RPC 配置中填写并保存 ${deployment.chain} 的 RPC URL。`);
      return;
    }
    if (!addressPattern.test(poolAddress.trim())) {
      setError("请输入完整的 EVM 池合约地址（0x 开头的 40 位十六进制地址）。");
      return;
    }
    if (mode === "quote" && (!addressPattern.test(tokenIn.trim()) ||
      !/^(?:\d+\.?\d*|\.\d+)$/.test(amountIn.trim()) || !/[1-9]/.test(amountIn))) {
      setError("估算需要完整的输入代币地址，以及大于零的十进制代币数量。");
      return;
    }
    setBusy(true);
    try {
      const data = await api<unknown>(`/api/pools/native/${mode}`, "POST", {
        protocol: deployment.protocol,
        chain: deployment.chain,
        version: deployment.version,
        pool_address: poolAddress.trim(),
        block: "latest",
        ...(mode === "quote" ? { token_in: tokenIn.trim(), amount_in: amountIn.trim() } : {}),
      });
      setResult({ mode, data });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" aria-labelledby="native-pool-heading">
      <div className="panel-heading">
        <div>
          <h2 id="native-pool-heading">原生池只读检查 <span className="badge amber">非执行报价</span></h2>
          <p>按链与池合约读取区块状态；单池估算不含 gas，不作为收益计算器的可执行报价。</p>
        </div>
        <Database size={18} />
      </div>
      <div className="form-grid">
        <label className="field">
          <span>协议 / 链 / 版本</span>
          <select aria-label="原生池部署" value={selection} disabled={busy}
            onChange={(e) => { setSelection(Number(e.target.value)); setPoolAddress(""); setTokenIn(""); clear(); }}>
            {deployments.map((item, index) => (
              <option key={`${item.protocol}:${item.chain}`} value={index}>
                {item.protocol} · {item.chain} · {item.version}
              </option>
            ))}
          </select>
          <small>{hasRpc ? `${deployment.chain} RPC 已保存` : `${deployment.chain} RPC 尚未配置或保存`}
            {deployment.quote ? " · 支持单池只读估算" : " · 当前仅读状态"}</small>
        </label>
        <label className="field">
          <span>池合约地址</span>
          <input aria-label="原生池合约地址" className="mono" value={poolAddress} disabled={busy}
            onChange={(e) => { setPoolAddress(e.target.value); clear(); }} placeholder="0x…" spellCheck={false} />
          <small>读取 latest 区块并固定区块身份；返回工厂归属与代币原生身份。</small>
        </label>
        {deployment.quote && <>
          <label className="field">
            <span>输入代币合约地址</span>
            <input aria-label="原生池输入代币" className="mono" value={tokenIn} disabled={busy}
              onChange={(e) => { setTokenIn(e.target.value); clear(); }} placeholder="池内 token0 或 token1 地址" spellCheck={false} />
          </label>
          <label className="field">
            <span>输入数量（代币单位）</span>
            <input aria-label="原生池输入数量" inputMode="decimal" value={amountIn} disabled={busy}
              onChange={(e) => { setAmountIn(e.target.value); clear(); }} placeholder="例如 0.000001" />
            <small>按十进制字符串提交，保留完整精度。手续费与价格冲击按返回结果核对。</small>
          </label>
        </>}
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="panel-foot">
        <span>仅使用已保存的链 RPC；无需交易账户或签名。</span>
        <div className="platform-stack">
          <button type="button" className="button secondary" disabled={busy || !hasRpc}
            onClick={() => void request("state")}>
            {busy && <Loader2 size={15} className="spin" />}读取池状态
          </button>
          {deployment.quote && <button type="button" className="button secondary" disabled={busy || !hasRpc}
            onClick={() => void request("quote")}>读取单池估算</button>}
        </div>
      </div>
      {result && <details className="raw-details" open>
        <summary>{result.mode === "quote" ? "只读估算结果 · 不可执行报价" : "只读池状态结果"}（JSON）</summary>
        <pre aria-label="原生池 JSON 结果">{JSON.stringify(result.data, null, 2)}</pre>
      </details>}
    </section>
  );
}
