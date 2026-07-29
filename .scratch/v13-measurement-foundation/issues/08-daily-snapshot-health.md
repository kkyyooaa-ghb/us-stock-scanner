# 每日快照健康監控與盤前備援緩衝

Type: task
Status: resolved
Blocked by: 07

## Scope

- 新增獨立 `snapshot_health` 模組，從 canonical snapshot 產生可機器讀取的
  每日健康報告。
- 區分阻擋錯誤與可用但需注意的警告；stale／missing 不得直接讓有效快照
  失敗。
- 摘要母體對帳、版本、資料日期、PreGap 覆蓋、TradePlan、腿別、order type
  與 Top 10 報價狀態。
- `scan.yml` 在主程式後執行健康檢查，保存 JSON 並寫入 GitHub Step Summary。
- watchdog 第二次檢查由 09:25 ET 提前至 09:20 ET，降低跨 09:30 的風險。

## Non-goals

- 不改 SignalEngine、ConfigHash、TradePlan 或 shadow measurement 版本。
- 不調整分數、主題加分、Top 10 或任何成交／出場規則。
- 不因單日 stale quote／stale bar 自動更換資料源。
- 本切片不建立跨日三連續 stale 狀態儲存；先由每日與週報紀錄觀察。

## Health contract

- `blocked`：canonical 契約失敗、母體 ticker 集合不符、Git SHA 不符、
  快照時間不一致，或有效 PreGap 無法由保存的價與分母重算。
- `warning`：存在 excluded／missing ticker、PreGap 非 available，或 Top 10
  缺少有效盤前報價；資料仍可進 shadow。
- `ok`：無阻擋錯誤或警告。
- `skipped`：休市日 typed control row；workflow 成功但不產生 shadow 樣本。
- error／empty control row 為 `blocked`。

## Acceptance

- 健康報告為 JSON-safe dict，包含 `status`、`usable_for_shadow`、
  `errors`、`warnings` 與 `metrics`。
- CLI 可寫 JSON 與 Markdown，blocked 回傳非零；warning／skipped 回傳零。
- 正常 99 檔快照可驗證完整母體集合，不只驗證筆數。
- PreGap 重算容許兩位小數欄位的四捨五入誤差，不容許分母日期錯位。
- workflow artifact 同時保存 `scan_result.csv` 與 `snapshot_health.json`。
- watchdog 的 EDT／EST 兩組 cron 與 offset gate 都改為 09:15／09:20。
- 完整測試與 `git diff --check` 通過。

## Answer

- 新增 `snapshot_health.evaluate_snapshot_health()` 作為單一健康檢查介面，
  將 canonical 驗證、母體集合、Git SHA、快照時點、PreGap 公式與
  Top 10 報價狀態收斂成 `ok`／`warning`／`blocked`／`skipped` 報告。
- CLI 會輸出 `snapshot_health.json` 與 GitHub Step Summary；只有
  `blocked` 使 workflow 失敗，資料缺列或報價不足只警告且保留 shadow
  可用性。
- 正式掃描 workflow 已接線；artifact 同時保存原始 CSV 與健康 JSON。
- watchdog 的兩次檢查改為 09:15／09:20 ET。
- 以 2026-07-28 正式 artifact 實測，結果為 `warning` 且
  `usable_for_shadow=true`：ALNY 為母體排除警告，XEL／VRSK 為
  PreGap 警告，XEL 同時是 Top 10 報價警告；其餘對帳與 TradePlan 指標
  均吻合。
- 124 項測試通過，1 項 XNYS 整合測試因隨附 runtime 未安裝
  `exchange_calendars` 而條件式跳過；`git diff --check` 通過。
