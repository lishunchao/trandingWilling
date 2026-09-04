# 交易系统

这是一个只读取 Binance USDT 永续合约公开行情的研究与纸面跟踪项目。它不会连接交易账户，不读取 API Key，不会下单、转账或提现；默认也不会向 Telegram、Bark 或其他外部渠道推送。

仓库还保留历史回测工具和 TradingView Pine 独立复刻版，便于复现研究与人工核对。

## Windows 本地安装

系统要求：Windows 10/11、PowerShell。项目优先复用 Codex 自带的 Python；若不存在，则使用系统 `python`。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\test.ps1
```

纸面跟踪仅使用 Python 标准库；运行 `work/qingyun_parallel_backtest.py` 时需要 `requirements.txt` 中的 NumPy。虚拟环境会创建在 `.venv` 中，以便隔离和复现。

## 运行

执行一次真实公开行情扫描：

```powershell
.\start.ps1 -Once
```

持续运行，每根 15 分钟 K 线收盘后约一分钟扫描：

```powershell
.\start.ps1
```

按 `Ctrl+C` 停止。运行状态写入 `outputs/paper_tracker_status.json`，日志写入 `work/paper_tracker.log`，本地纸面仓位与历史写入 `work/paper_tracker_state.json`。这些运行数据不会提交到 Git。

历史回测参数：

```powershell
.\.venv\Scripts\python.exe .\work\qingyun_parallel_backtest.py --help
```

TradingView 脚本位于 `tradingview/青云操作系统v2.1_独立复刻版.pine`。它是独立复刻与研究版本，上线前仍应在 TradingView 中完成编译、回放与原版信号对照。

## Web V1 本地交易控制台

Web V1 直接读取现有纸面跟踪器的 `outputs/paper_tracker_status.json`，不会复制策略状态，也不会连接交易账户。先启动纸面跟踪器，再另开一个 PowerShell 窗口运行：

```powershell
.\start_web.ps1
```

浏览器访问 `http://127.0.0.1:8765`。控制台包含 Dashboard、Binance 公开 K 线、A/B/C 信号等级、入场/止损/止盈与盈亏比、纸面仓位、Telegram 状态和策略运行状态。

页面与接口均在本仓库的 `web/` 目录。后端使用 Python 标准库，不增加常驻依赖；主要只读接口为：

- `GET /api/v1/dashboard`：现有跟踪器状态的统一视图模型。
- `GET /api/v1/candles?symbol=BTCUSDT&interval=15m`：公开 Binance K 线代理。
- `GET /api/v1/health`：本地服务健康状态。

策略适配集中在 `web/adapters.py`。EMA12/25 已标记为运行模块；箱体突破、宏观/国际事件风险、多策略 Alpha、回测分析及统一评分与风控采用稳定模块标识预留接口，后续接入时无需重写页面结构。

信号评分是 V1 的展示规则，不会反向修改原策略或触发推送。总分 100 分：策略确认 30 分、盈亏比 30 分、止损风险距离 30 分、成本模型完整性 10 分；85 分以上为 A，70–84 分为 B，其余为 C。鼠标停在评分条上可以查看分项。后续箱体与宏观模块验证完成后再扩展权重。正式推广前必须通过足量历史、样本外和稳定性验证。

## 配置

策略参数位于 `config/trading_scanner_config.json`。默认只使用公开市场数据，通知开关固定关闭。`.env.example` 只是安全边界说明，不需要填写任何密钥。

## 安全边界

- 只做研究和虚拟成交记录，不构成交易建议。
- 没有自动下单、账户、资金划转或提现代码。
- 不要把真实 API Key、Telegram Bot Token、密码、私钥或私密推送地址写入仓库。
- 历史与纸面结果不能代表未来收益；正式使用前仍需独立复核交易逻辑、手续费、滑点和风控。

## 常见问题

- 提示找不到 Python：安装 Python 3.11 或更新版本，并确保 `python` 可用，然后重新运行 `setup.ps1`。
- Binance 接口超时或受地区网络限制：稍后重试；程序会尝试公开备用域名，但不会绕过网络政策。
- 首次扫描较慢：需要为合约列表下载 1 小时与 15 分钟 K 线；后续会复用本地状态。
