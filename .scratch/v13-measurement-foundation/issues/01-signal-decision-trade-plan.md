# 結構化訊號與 TradePlan

Type: task
Status: resolved

## Scope

- 建立 `SignalDecision`、正式腿別、錨點與否決原因。
- 保留 `determine_status()` 舊 tuple 介面。
- 建立 V1.3.0 shadow TradePlan。
- 確保超賣與守均線腿產生符合各自概念的計畫。

## Acceptance

- 超賣腿不再得到 `(0, 0)` shadow entry。
- 守 MA20 腿以 MA20 為 plan anchor。
- shadow plan 不影響正式 Priority 與 Notion。
- 單元測試覆蓋三腿與基本面否決。

## Comments

- 2026-07-27：開始實作。production selection 維持 V1.2.0。

## Answer

- `determine_status_details()` 回傳結構化 `SignalDecision`，舊
  `determine_status()` tuple 介面保留。
- 新增 `trade_plan.py`，三腿各自產生 V1.3.0 shadow TradePlan。
- 超賣腿使用前高 reclaim，不再產生零進場價。
- 守均線腿使用實際觸發的 MA20／MA60。
- TradePlan 只寫 CSV，不改正式 Priority、舊 EntryLow/StopLoss 或 Notion。
- `python -m unittest discover -s tests -v`：16 項通過。
