# 成交、R 區間與企業行動

Type: task
Status: ready-for-agent
Blocked by: 01, 02

## Scope

- 以 as-traded OHLC 重寫成交與停損 evaluator。
- 區分 buy-limit、buy-stop 與未成交。
- 日線同 bar 雙觸輸出 R 上下界。
- 支援 D+20/40/60、MFE、MAE、拆股與現金股息。
- 舊 R 保留為 legacy-v0。
