# 成交、R 區間與企業行動

Type: task
Status: resolved
Blocked by: 01, 02

## Scope

- 以 as-traded OHLC 重寫成交與停損 evaluator。
- 區分 buy-limit、buy-stop 與未成交。
- 日線同 bar 雙觸輸出 R 上下界。
- 支援 D+20/40/60、MFE、MAE、拆股與現金股息。
- 舊 R 保留為 legacy-v0。

## Comments

- 2026-07-27：開始實作。新量尺先以獨立 shadow 模組與 CSV adapter
  上線；既有 Notion `R值` 與 `compute_backfill()` 維持 legacy-v0。

## Answer

- 新增深模組 `execution_measurement.py`，單一 interface
  `evaluate_trade_plan()` 封裝 order semantics、時間順序、R、MFE/MAE、
  horizons 與企業行動。
- buy-limit 與 buy-stop 分開判定；stop entry bar 雙觸輸出 R 區間，
  未成交不產生 R，後續 gap-through 以 open 出場。
- 使用 raw OHLC + splits + dividends，拆股正規化回原始股基準，股息納入
  每一原始股總損益。
- 新增 `track_shadow_performance.py`，每週從永久 daily snapshots 產生
  `reports/shadow_performance.csv`。
- legacy Notion 回填與週報標記 `legacy-v0`；shadow plan 與結果標記
  `v1.3.0-shadow`，不混算。
- 全套 `python -m unittest discover -s tests -v`：28 項通過。
