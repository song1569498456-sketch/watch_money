import type { Book, Instrument } from "./types";

/** Convert a buy-side quote budget using actual asks, without rounding market steps. */
export function bookBaseForNotional(instrument: Instrument, book: Book, budget: number,
                                    asOf: number, staleSeconds = 60) {
  if (!Number.isFinite(budget) || budget <= 0) throw new Error("请输入有效的买方报价币名义金额");
  if (book.instrument_id !== instrument.id || instrument.mechanism !== "orderbook")
    throw new Error("买方盘口身份不匹配或市场不提供订单簿，无法按金额核算");
  const timestamps = [book.received_at, ...(book.source_at == null ? [] : [book.source_at])];
  if (!Number.isFinite(asOf) || !Number.isFinite(staleSeconds) || staleSeconds <= 0 || timestamps.some((at) =>
    typeof at !== "number" || !Number.isFinite(at) || at <= 0 || at > asOf || asOf - at > staleSeconds * 1000))
    throw new Error("买方盘口时间未知、过期或位于未来，无法按金额核算；请刷新盘口");
  if (book.quantity_unit !== "base" && book.quantity_unit !== "contracts")
    throw new Error("买方盘口数量单位未知，无法按金额核算");
  if (book.quantity_unit === "contracts" && (instrument.kind !== "perpetual" ||
      !Number.isFinite(instrument.contract_size) || instrument.contract_size <= 0))
    throw new Error("合约盘口缺少有效的永续合约乘数");
  if (!instrument.linear && (instrument.settle !== instrument.base || instrument.quote === instrument.base))
    throw new Error("反向合约需要明确基础币结算与不同报价币，无法推断换算");
  if (!Array.isArray(book.asks) || !Array.isArray(book.bids) || [...book.asks, ...book.bids].some((level) =>
    !Array.isArray(level) || level.length !== 2 || level.some((value) => !Number.isFinite(value) || value <= 0)))
    throw new Error("买方盘口包含无效价格或数量");
  if (book.asks.some(([price], index) => index > 0 && price < book.asks[index - 1][0]) ||
      book.bids.some(([price], index) => index > 0 && price > book.bids[index - 1][0]) ||
      (book.bids.length && book.asks.length && book.bids[0][0] >= book.asks[0][0]))
    throw new Error("买方盘口排序错误或买卖盘交叉，无法按金额核算");
  let remaining = budget, sizeBase = 0, levelsUsed = 0;
  for (const [price, quantity] of book.asks) {
    const availableBase = book.quantity_unit === "base" ? quantity : instrument.linear ?
      quantity * instrument.contract_size : quantity * instrument.contract_size / price;
    const capacityQuote = availableBase * price;
    if (!Number.isFinite(availableBase) || !Number.isFinite(capacityQuote) || capacityQuote <= 0)
      throw new Error("买方盘口换算超出有效数值范围");
    const spent = Math.min(remaining, capacityQuote);
    sizeBase += spent / price;
    remaining -= spent;
    levelsUsed++;
    if (remaining === 0) break;
  }
  if (remaining > 0 || !Number.isFinite(sizeBase) || sizeBase <= 0)
    throw new Error("买方卖盘深度不足，无法按指定金额完整核算；不会外推未提供深度，可改用基础数量模式");
  return { sizeBase, spentQuote: budget, averagePrice: budget / sizeBase, levelsUsed,
    sourceTimeKnown: book.source_at != null };
}
