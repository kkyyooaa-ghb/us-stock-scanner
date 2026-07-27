# Forward snapshot 盤前硬化

Type: task
Status: resolved
Blocked by: 04

## Scope

- 超賣反彈以訊號棒（掃描時最後一根完整日 K）高點作為突破錨。
- 每筆 TradePlan 保存不可變的最早可成交交易日，避免盤中／盤後補跑 look-ahead。
- 由單一 snapshot contract 管理正式、空白與錯誤 CSV schema。
- 明確記錄盤前跳空資料狀態，並統一 Leg／Plan anchor price 的來源。
- 不改變 V1.2 正式選股、Priority、Top 10 或 Notion 欄位。

## Comments

- 2026-07-27：開始實作。尚無 V1.3 forward snapshot，可在不切割樣本世代的
  前提下修正第一份快照規則。

## Answer

- 超賣腿已固定使用最後一根完整訊號棒高點；腿別與 anchor 契約在 writer
  與 evaluator 兩層 fail-closed。
- `PlanEarliestEntryDate` 以整份快照封存時點決定；掃描若跨過 09:30 ET，
  全批 plan 一律順延下一個 XNYS session。
- 日 K 是否完整凍結在行情下載開始時，並要求 session close 後 15 分鐘，
  避免跨收盤下載誤收 partial bar。
- Shadow 量測以 `last_complete_session_date` 為 as-of；尚未形成可成交 bar
  的計畫維持 `awaiting_fill`，不誤報 `no_data`。
- 正式、empty、skipped、error 與 workflow fallback 共用 92 欄 canonical
  schema；control artifact 會永久歸檔，但不進股票彙總。
- PreGap 由 04:00–09:30 ET 時鐘獨立判斷；若抓取跨過開盤，整批作廢，
  不保存混合時點。
- Episode gate 只接受目前 TradePlan 與 measurement 三重版本一致的資料。
- 本地完整測試 72 項通過；真實 XNYS 休市日整合測試因隨附 runtime 未安裝
  `exchange_calendars` 而條件式跳過；在安裝正式 requirements 的環境執行
  測試時會啟用。
