# PreGap v1.3.2 基準與 fail-closed 護欄

Type: task
Status: resolved
Blocked by: 05

## Scope

- 個股、SPY 與 QQQ 的盤前跳空分母改為日期驗證過的訊號棒收盤。
- 保存盤前報價、報價時間、參考價／日期／基準與定義版本。
- 拒絕過期、未來或非本日盤前窗的 `preMarketTime`。
- SPY 參考無效時，L2 與 FINAL 都不得輸出可執行的進場建議。
- 過期日 K 的 TradePlan 降為 `data_stale`，但不改變 V1.2 排名。
- 歷史快照依原 schema 歸檔；未版本化 PreGap 不進統計。
- 調參閘門以精確 `(SignalEngineVersion, ConfigHash)` cohort 隔離。

## Acceptance

- 同一訊號棒重跑時，正式 Priority、腿別、Top 10 與 TradePlan 價格不變。
- `stale_quote`、`stale_reference` 與 `data_stale` 都不產生可信 gap 或 R。
- 缺少或不符合現行 `ConfigHash` 的資料不得解鎖調參閘門。
- v1.3.1 與 v1.3.2 artifact 可在同一週各自原樣歸檔。

## Comments

- 2026-07-28：上線前審查發現只驗報價「同日」仍會接受數小時前的成交，
  且 L2 的無建議狀態會被一般 FINAL 黃燈文案覆寫；已在推送前補強。

## Answer

- `PreGapDefinitionVersion` 固定為 `v1.3.2-signal-bar-close`，分母來源明示為
  `signal_bar_close_auto_adjusted`。
- `preMarketTime` 必須不晚於掃描時點，且年齡不超過 60 分鐘。
- L2 保存 `reference_available`；缺基準時 FINAL 明確輸出「不提供進場建議」。
- Episode 分析直接比對 `strategy_config_hash()`，不再猜測最新或多數 hash，
  並在 JSON／Markdown 報告輸出 cohort identity。
- 本輪只升 Snapshot schema，不升正式選股、TradePlan 或 shadow 量尺版本。
