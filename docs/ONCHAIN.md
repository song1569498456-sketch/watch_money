# EVM 原生池状态与只读规模报价

2026-11-04。实现文件为 `backend/watch_money/adapters/onchain.py`。这是可由 API 集成层调用的独立服务，**实网尚未验证**；默认不请求任何 RPC、不扫描全链、不提供交易或订单簿。

## 范围与验证层次

| 已登记部署 | 原生池状态 | exact-input 单池估算 | 手续费依据 |
| --- | --- | --- | --- |
| Uniswap V3 Ethereum / Arbitrum / Base | factory、token、decimal、fee、tick spacing、slot0、active liquidity | 暂不提供，明确报错 | 池 `fee()/1e6`，并与 factory 的 fee/tick spacing 反查一致 |
| PancakeSwap V2 BNB Chain | factory、token、decimal、reserves | 官方 V2 SDK 的整数常数积公式 | 已登记工厂对应 SDK 的 `9975/10000`，fee fraction = `0.0025` |
| Aerodrome v1 Base | factory、token、decimal、stable flag、reserves、pause flag | 池 `getAmountOut(uint256,address)` 的 `eth_call` | factory `getFee(pool,stable)/10000`，包含 custom fee 和有效零费率 |

地址只读取现有 `deployments.json` 中这五条协议 × 链 × 版本，不生成跨链组合。Base 的 Uniswap factory 与 Ethereum/Arbitrum 的地址不同。Curve、Solana 协议及其他版本不在此模块支持范围。

每次请求先验证 `eth_chainId`（Ethereum=1、Arbitrum=42161、Base=8453、BSC=56），再读取已登记 factory、请求池和两个 token 的非空合约代码。检查池的 `factory()`、`token0()/token1()` 地址与排序；通过 factory 的 `getPair` 或带 fee/stable 的 `getPool` 反查请求池。Aerodrome 还检查 `isPool(pool)`。V3 额外核对 `feeAmountTickSpacing(fee)`。空地址、缺少代码、错误工厂/成员、非规范 ABI、错误整数位宽、bool、signed extension 均失败，不返回替代快照。

`factory_membership_verified=true` 只表示上述 RPC 检查成功。`code_sha256` 是实际读到代码的 SHA256 记录，**没有与特定编译产物或运行时代码哈希作匹配**。因此 `deployment_bytecode_verified=false`、`execution_verified=false` 一直保留；不能把这三个字段折叠成“已证明可成交”。代理/clone implementation 也未作编译产物认证。数据仍依赖配置 RPC 的真实性；未验证 state proof。

## 区块与时间

`block` 接受 `latest`、`safe`、`finalized` 或非负整数区块高度；拒绝 pending 与不明确的字符串。先解析 mined block 的 number/hash/timestamp，随后所有 `eth_getCode` 与 `eth_call` 固定使用 EIP-1898：

```json
{"blockHash":"0x...","requireCanonical":true}
```

结束时再读取该高度的 canonical block 并比较 number/hash/time，重组则整次请求失败。节点不支持 canonical block hash 参数时直接报错，**不会降级为 latest 或高度读取**。历史高度还需要节点保留相应状态；Arbitrum 的编号/时间是该链 RPC 返回的 L2 区块语义，不推断 L1 最终性。

`block_time` 是 UTC epoch **毫秒**，表示被观察区块时间；不是 last trade 或最后一次 reserve update。`received_at` 是本地接收时间。旧块仍返回旧的真实时间，应由 API/UI 按正常 freshness 规则标示历史或过期。V2 `getReserves()` 第三个字段是 modulo `2**32` 的秒值，原样保存在 `reserve_update_time_raw_seconds` 并设 `reserve_update_time_modulo_2_32=true`；不当作当前行情 epoch 时间。Aerodrome 同名字段为完整秒值，并明确标记非 modulo。

## 数量、价格和路线语义

资产身份使用 `chain:lowercase_contract_address`，不读取或猜测 ticker、不映射到其他平台同名资产。decimal 来自同一区块的 token `decimals()`，缺失即失败，不默认 18。

输入 `amount_in` 是 `token_in` 的**人类代币数量**，接受 decimal 字符串、`Decimal` 或整数，拒绝 float/bool、非有限值、非正值、超过 uint256 或无法精确表示的最小单位。不静默四舍五入。所有原始链上大整数使用字符串，Pydantic JSON 将 Decimal 也序列化为字符串，防止浏览器浮点丢失精度。

返回方向为 `token0-to-token1` 或 `token1-to-token0`；`amount_out` 使用输出 token 的 decimal。`average_out_per_in` 是每输入一单位代币得到的输出数量，不隐含买卖或美元。`route` 始终只包含一个已核对的底层池，不增加聚合器流动性，也没有多跳搜索。区块、factory、token、fee 及状态保留在 `quote.pool`。

Pancake V2 原始单位公式为：

```text
weighted_input = amount_in_raw * 9975
amount_out_raw = floor(weighted_input * reserve_out / (reserve_in * 10000 + weighted_input))
```

公式已包含手续费和该输入规模下的价格冲击；零输出、空池、超出 Solidity uint256 算术/uint112 reserve 容量均拒绝。手续费来自与登记 factory 匹配的官方 SDK，并非额外读取出的动态费率。历史 `pancake-swap-core` 仓库中另有 0.2% 实现，不能把该不同来源公式混用为此登记 V2 factory 的 0.25% 公式。

Aerodrome 直接调用池的 `getAmountOut`，由池自身处理 stable/volatile、内部 decimal、整数舍入及动态手续费；不使用 `quote(address,uint256,uint256)`，后者是观察窗口样本报价。暂停状态仍可读取，但 `isPaused=true` 时不提供规模报价。读取有效手续费后**不再次从池返回数量中扣费**。

V2/volatile 的 `spot_price_token1_per_token0` 是精度调整后的 reserve ratio；V3 为 `(sqrtPriceX96**2 / 2**192) * 10**(decimals0-decimals1)`。两者均是池状态价格，不能作为 `last` 或 executable bid/ask。Stable reserve ratio 不是正确的边际价，因此该价格字段留空。V3 active liquidity 也不是 token reserves 或 USD TVL，保持 `liquidity_raw` 单独字段；未初始化的池不虚构价格。V3 规模报价需要核验 quoter 部署或完整 tick traversal，此实现明确拒绝。

所有规模结果均设 `executable=false`：这是读取/公式估算，未执行用户余额、授权、transfer tax、token hook、gas、交易模拟、MEV、未来状态和滑点保护验证。`fee_included=true`、`price_impact_included=true`；策略不能重复扣同一池费或冲击，不能把此结果转换成可成交订单簿。Gas 与其他路径成本仍需单独输入。

## 集成接口

```python
from watch_money.adapters.onchain import NativePoolReader

reader = NativePoolReader(settings.rpc_urls)
try:
    state = await reader.read_pool(
        "pancakeswap", "bsc", "v2", pool_address, block="latest"
    )
    quote = await reader.quote_exact_input(
        "pancakeswap", "bsc", "v2", pool_address,
        token_in=token_address, amount_in="100.123456", block="safe"
    )
    payload = quote.model_dump(mode="json")
finally:
    await reader.close()
```

建议由 API 层持有一个 reader 并在 settings 更新时更新其生命周期；按显式地址按需读取，独立于原聚合池 adapter 的发现/目录逻辑。`OnchainError`/`UnsupportedPool` 可作为不可用状态；参数 `ValueError`/`TypeError` 可映射为请求校验失败。不要将 `factory_membership_verified` 当作人工资产映射认证，也不要覆盖聚合器既有来源证据。若需要写入共享 Pool，保留字段的具体认证范围；原有 `verified` 单一 bool 不足以表达编译字节码/成员/交易三种层次。

客户端延迟创建，20s timeout，继承系统代理与 CA，不跟随重定向。RPC 方法白名单仅为 `eth_chainId`、`eth_getBlockByNumber`、`eth_getCode`、`eth_call`；无 signer/private key/账户 API。传入测试 client 时 close 不关闭调用方的 client。RPC URL 可含凭证，输出只使用 `evm-rpc:chain:protocol:version:address` 来源身份；HTTP/RPC/JSON 错误不传播原 URL、远端 message/data。

## 官方来源与本轮验证

来源文本于 2026-11-04 通过允许的 GitHub raw 域名读取；下表 SHA256 对应读取的原始字节，不代表链上字节码已匹配。

| 官方来源 | 本轮读取 SHA256 |
| --- | --- |
| [Uniswap pool state ABI](https://raw.githubusercontent.com/Uniswap/v3-core/main/contracts/interfaces/pool/IUniswapV3PoolState.sol) | `d8d3937703d08d95f7f867a0f49c2aa73c3f3ef5508a54d431e829295f5175ea` |
| [Uniswap pool immutables ABI](https://raw.githubusercontent.com/Uniswap/v3-core/main/contracts/interfaces/pool/IUniswapV3PoolImmutables.sol) | `ac6332f7e174db7fa3852a6c5ae3da3bd013e72c2ff8fa1cf2ec37f6916e2951` |
| [Uniswap factory ABI](https://raw.githubusercontent.com/Uniswap/v3-core/main/contracts/interfaces/IUniswapV3Factory.sol) | `17c72e89a7d0eecca7929ca08d97f46e80930ef7024e4ccbc7b294c588477107` |
| [Uniswap TickMath bounds](https://raw.githubusercontent.com/Uniswap/v3-core/main/contracts/libraries/TickMath.sol) | `e68d3c6c8c847d16c287c1f17f684d11cf0f6267a8d8dc29e81656bccf68f062` |
| [Pancake V2 factory and fee constants](https://raw.githubusercontent.com/pancakeswap/pancake-swap-sdk/master/src/constants.ts) | `1d16cb07eb1e207af4a487830bda4b5342ed160d42739de86c5f531f5dcd2ae6` |
| [Pancake V2 exact-input SDK formula](https://raw.githubusercontent.com/pancakeswap/pancake-swap-sdk/master/src/entities/pair.ts) | `fbc942a64aec759033eb4af055fd3fb416e95325677eb5bda733dbdaaf88e993` |
| [Aerodrome pool implementation](https://raw.githubusercontent.com/aerodrome-finance/contracts/main/contracts/Pool.sol) | `348d3c313dbbb9a2d0598480075bf91e77ceb2d774aeddbe229c9f551186c8bf` |
| [Aerodrome factory implementation](https://raw.githubusercontent.com/aerodrome-finance/contracts/main/contracts/factories/PoolFactory.sol) | `9d0f6dd4ceb216e826d1e8ac263bd31fa2fbb53ffd546cc2ec21a8dc916f8b58` |
| [Aerodrome factory ABI](https://raw.githubusercontent.com/aerodrome-finance/contracts/main/contracts/interfaces/factories/IPoolFactory.sol) | `d7f942f469d559184397b3477dc30bd9510bb0d0bad84c6bbeff4a5596632086` |

同一区块读取语义来自 [EIP-1898](https://eips.ethereum.org/EIPS/eip-1898)。部署地址本身沿用登记表中已经记录的官方来源及 hash，不修改 `onchain_verified=false`。

运行 `.venv/bin/python -m pytest tests/test_onchain.py -q`：**66 passed**。独立 mock RPC fixture 验证所有请求的 hash pin、链/工厂/成员、ABI selector 与 signed integer、decimal 与 uint256、数量双向、零费/暂停、两种报价路径、重组整次失败、无 EIP-1898 降级、RPC 凭证脱敏以及注入客户端的重定向拒绝。该证据是确定性本地测试，**不是实网录制样本或真实池验收**。

云环境网络为 restricted/enforced，允许的来源域名可以读取 GitHub 官方源码，未提供 RPC URL/凭证且 RPC 主网域名未获网络放行。没有尝试关闭代理、修改网络策略或绕过访问限制。实网主网池状态、真实规模对照、编译字节码认证、V3 规模报价和全链发现均仍是后续工作。
