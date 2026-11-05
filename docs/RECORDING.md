# 按需行情诊断录制

`watch_money.recording.RawMarketRecorder` 保存公共行情采集钩子提供的 payload，默认关闭。
当前 CEX 钩子提供 CCXT 已标准化的 ticker（含其 `info`），DEX 钩子提供其公共接口返回的 JSON。
这是**有限范围的 payload 诊断录制**，不等同于完整网络原始消息、所有 WebSocket 帧或可直接执行的历史回放。
没有持久化请求头、请求 URL 或账户凭证；请只向该接口传递公共行情。

```python
from watch_money.recording import RawMarketRecorder

recorder = RawMarketRecorder(data_dir)  # 不创建目录，默认 disabled
recorder.configure(enabled=True, max_record_bytes=262144,
                   max_total_bytes=104857600, retention_days=7)
written = await asyncio.to_thread(recorder.record, "okx", "rest:ccxt", ticker)
stats = recorder.status()
```

`configure` 接受上例中的四个可选关键字参数并返回状态。无效配置抛 `ValueError`，且整次变更不生效。
白名单、平台开关和开关持久化由 collector/配置层管理；录制器不自行订阅或请求行情。
每个数据目录使用一个实例，可以跨线程调用；不提供多个实例/多个进程共同写目录的锁。
异步调用方使用 `asyncio.to_thread`，模块没有后台线程、消息队列或残留文件句柄。

文件为 `data_dir/recordings/YYYY-MM-DD.jsonl`，日期取本地接收时间的 UTC 日期。
每一行为 `{received_at_ms, venue, transport, payload}`，时间为 UTC epoch 毫秒。
`venue` 与 `transport` 是简单来源标签，不是 URL；所有平台共享当天文件。
payload 接受字典、数组或能解析为这两种类型的 JSON 文本/字节。
JSON 数值保留原始意义，禁止 NaN/Infinity，不把缺失字段替换为零。

字典/列表中敏感键递归替换为 `[REDACTED]`，包括 token、apiKey、secret、password、
passphrase、authorization、cookie、privateKey、signature 等常见变体；
headers、url、uri、endpoint、credentials 等请求上下文字段递归移除。
原传入对象不被修改。该措施按字段名工作，不会识别任意普通字符串中隐藏的秘密；
不能把私有接口响应或包含个人数据的对象作为公共行情送入。

单条上限包含 JSON 外层、UTF-8 编码和末尾换行。
总额度统计本模块命名的每日 JSONL 文件，包含以前会话的文件；不计普通历史 Parquet 和其他文件。
达到额度后丢弃新记录，不截断已有样本；降低额度也不会删除尚在保留期内的样本，
此时在到期释放空间或上调额度前停止新增。
启用后的有效录制尝试清理过期日期文件；保留 N 天指今天及前 N−1 个 UTC 日期。
关闭录制和读取状态都不清理文件；不提供脱离采集运行的定时清理任务。

`record` 返回 bool，大小/额度不足及普通序列化/磁盘异常均返回 False，不中断采集；
异常文本与拒绝的 payload 不写日志或状态，避免将凭证带入诊断信息。
状态中的 `recorded`、`dropped`、`errors`、`bytes` 是本实例的累计计数：

- `recorded` / `bytes`：成功写入的条数/字节数（不随到期清理减少）。
- `dropped`：启用时失败的条数；关闭时调用不计丢弃。
- `errors`：畸形 payload、写盘或状态扫描异常；额度丢弃不计错误。
- `disk_bytes` / `files`：当前已有文件大小/数量；无法扫描时为 null。

写入在单实例锁中进行，空间检查与 append 为同一临界区。普通短写会回滚到之前文件长度；
无 fsync/断电事务保证，也不代替备份。只读列表/API 由应用层提供，不接受任意客户端文件路径。

验证：`PYTHONPATH=backend .venv/bin/python -m pytest tests/test_recording.py -q`。
测试覆盖递归脱敏、原对象不变、JSON 文本、畸形/循环输入、开关、UTF-8 大小、
跨实例重启额度、UTC 保留边界、并发完整性/额度竞争及文件系统失败后恢复。
