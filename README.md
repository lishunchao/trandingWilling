# 青云交易研究与信号系统

这是我们此前整理的青云/EMA 交易研究代码。项目只读取公开行情，用于信号扫描、纸面跟踪和历史回测；**不包含下单、账户、划转或资金操作代码**。

> 风险提示：任何信号和回测结果都不构成投资建议。历史或模拟结果不代表未来表现。默认关闭 Telegram/Bark 推送。

## 主要文件

- `binance_qingyun_scanner.py`：Binance USDT 永续合约只读扫描器，4 小时判断方向、15 分钟确认机会。
- `qingyun_paper_tracker.py`：基准版与增强版的本地纸面跟踪，不接交易账户。
- `qingyun_parallel_backtest.py`：基于 Binance Vision 公共历史数据的回测与对比工具。
- `青云操作系统v2.1_独立复刻版.pine`：TradingView Pine 独立复刻版。
- `trading_scanner_config.json`：可公开的策略与运行参数。
- `config.example.json` / `bark_private_config.example.json`：通知配置模板。
- `test_paper_tracker_costs.py`：成本迁移逻辑的确定性测试。

## 环境要求

- Windows、macOS 或 Linux
- Python 3.10+
- 扫描器与纸面跟踪仅使用 Python 标准库
- 历史回测额外需要 NumPy

安装回测依赖：

```powershell
python -m pip install -r requirements.txt
```

## 配置

1. 复制 `config.example.json` 为 `config.json`。
2. 如需 Telegram，在本地 `config.json` 填写 Bot Token 和 Chat ID，并将 `trading_scanner_config.json` 的 `telegram_enabled` 改为 `true`。
3. 如需 Bark，复制 `bark_private_config.example.json` 为 `bark_private_config.json`，填写私有推送地址，并将 `bark_enabled` 改为 `true`。

`config.json`、`bark_private_config.json`、日志、状态、缓存和回测大文件已由 `.gitignore` 排除。不要把真实 Token、私钥或密码写进示例文件。

## 使用

先执行一次无推送扫描：

```powershell
python binance_qingyun_scanner.py --once --dry-run
```

持续扫描（是否推送由配置控制）：

```powershell
python binance_qingyun_scanner.py
```

执行一次纸面跟踪：

```powershell
python qingyun_paper_tracker.py --once
```

查看回测参数：

```powershell
python qingyun_parallel_backtest.py --help
```

运行测试：

```powershell
python test_paper_tracker_costs.py
```

## 数据与输出

- `runtime/`：状态、日志和行情缓存，仅保留在本机。
- `outputs/`：回测和纸面跟踪结果，默认不提交。
- 历史行情来自 Binance Vision 公共数据；实时扫描使用 Binance 公共市场接口。

纸面跟踪采用固定研究成本假设：单边 0.05% taker 手续费、单边 0.02% 滑点，并附加 0.01% 资金费代理值。完整假设与参数以代码和输出报告为准。

## 故障排查

- 提示缺少 `config.json`：从 `config.example.json` 复制一份；只做 dry-run 也需要该文件存在。
- Binance 返回 451/429：通常是地区访问限制或频率限制，请降低并发/频率并遵守当地规定和接口条款。
- 回测首次运行较慢：程序需要下载并缓存历史压缩数据，后续会复用 `runtime/binance_vision_cache/`。
- Windows 上 `python` 不可用：尝试已安装解释器的完整路径，或安装 Python 3.10 以上版本。
