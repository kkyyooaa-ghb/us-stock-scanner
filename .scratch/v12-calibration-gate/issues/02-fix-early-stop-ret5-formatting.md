# 修正提前停損時 D+5 空值造成 P1 回填中止

Type: task
Status: resolved
Blocked by: 01

## Scope

- 重現 `r_final=True`、`stop_hit=True`、但 D+5 尚未成熟時的主流程輸出。
- 避免把空的 `ret5` 以浮點格式輸出，讓後續 Notion 樣本可以繼續回填。
- 保持 legacy-v0 的計算、Notion 欄位與 V1.3 shadow 量尺不變。
- 補上無網路、可重複的主流程回歸測試。

## Acceptance

- 提前停損且 D+5 尚未到時，`main()` 不拋出 `TypeError`。
- 輸出明確標示 D+5 尚未到，並保留 `R-1.00` 與停損標記。
- Notion properties 仍寫入停損與 R，但不寫尚不存在的 D+5 報酬。
- P1 相關測試、完整測試與 `git diff --check` 通過。

## Comments

- 2026-08-02 的正式週報 run `30749018452` 在
  `track_performance.py:324` 發生
  `TypeError: unsupported format string passed to NoneType.__format__`。
- workflow 使用 `continue-on-error`，所以整體 run 顯示成功，但 legacy 回填只完成部分資料。

## Answer

- 新增主流程回歸測試，使用真實 `compute_backfill()` 重現「提前停損使
  `r_final=True`，但 `ret5=None`」的正式故障形狀；修正前固定拋出相同
  `TypeError`。
- 定案訊息改為先建立 D+5 顯示字串：已成熟時維持原本百分比，未成熟時
  顯示「未到」，不改計算或 Notion properties。
- 提前停損仍會立即寫入 `是否觸發停損=true` 與 `R值=-1`，並保留空的
  D+5 報酬等待後續成熟。
- P1／週報／workflow 相關 14 項測試通過；完整套件 125 項通過、1 項因
  隨附 runtime 未安裝 `exchange_calendars` 而條件式跳過；
  `git diff --check` 通過。
