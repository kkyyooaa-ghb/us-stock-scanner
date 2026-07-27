# Shadow 快照與 PreGap

Type: task
Status: resolved
Blocked by: 01

## Scope

- 把 decision、TradePlan、版本與市場環境寫入每日全池 CSV。
- 拆解季營收、主題與主題前後 Priority。
- 保存主題加分造成的門檻／Top 10 反事實旗標。
- 修正 `PreGapPct` 只更新 Top 10 副本的問題。

## Acceptance

- 每日 CSV 可直接按腿別、order type、主題邊際組與市場環境分析。
- Top 10 的 PreGapPct 同時存在於 Notion row 與永久 CSV。
- 額外 shadow 欄位不改變正式 Top 10。

## Answer

- CSV 新增版本、腿別、TradePlan、分數拆解與市場環境欄。
- 保存 `PriorityPreTheme`、`PriorityPostTheme`、
  `CrossedThresholdDueToTheme` 與 `EnteredTop10DueToTheme`。
- 盤前跳空擴到完整掃描母體，並同步回寫 `df_all` 與 Top 10 copy。
- 舊 `SetupType` 保留，另存 `DiagnosticSetupTypeV1`。
- 錯市場 LLM enrichment 已預設停用，待 query builder 重寫。
