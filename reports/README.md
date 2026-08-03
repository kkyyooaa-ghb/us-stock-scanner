# 美股掃描永久報告

`reports/` 是週報與回測資料的正式保存位置。Telegram 僅負責推播，不再是資料來源。

## 目錄

- `latest.md`:最新一份人類可讀週報。
- `latest.json`:最新一份機器可讀統計快照。
- `weekly/YYYY-MM-DD_YYYY-MM-DD.md`:歷史週報。
- `weekly/YYYY-MM-DD_YYYY-MM-DD.json`:歷史統計快照。
- `daily/YYYY-MM-DD.csv`:每日完整掃描結果，供後續 P9 回測與週報重算。
  V1.3.1 起由 canonical 92 欄 schema 寫入，正常、空結果與錯誤 artifact
  共用同一欄位契約；休市與錯誤 control 也會永久歸檔但不進股票彙總；
  `PreGapStatus` 可區分無盤前成交與抓取失敗。
- `shadow_performance.csv`:V1.3 TradePlan 的獨立成交、R 區間、
  D+20/40/60、MFE/MAE 與企業行動量尺；成交與報酬由不可變的
  `PlanEarliestEntryDate` 起算，as-of 為最後完整交易日，不與 Notion
  legacy R 或舊 shadow 版本混算。
- `shadow_episodes.csv`:依 ticker 交易生命週期去重後的獨立 episodes。
- `shadow_episode_summary.json`:filled/unfilled/open/completed、腿別與
  order type KPI，以及 60～100 筆成熟度閘門。
- `shadow_episode_summary.md`:episode KPI 的人類可讀版本。

## 產生流程

`.github/workflows/weekly_report.yml` 每週先執行 legacy 績效回填，再由
`weekly_report.py` 讀取當週 GitHub Actions artifacts 並封存 daily snapshots。
同一程序接著呼叫 `track_shadow_performance.py` 重算 V1.3 量尺，再由
`build_shadow_episodes.py` 將重複日訊號合併為獨立交易 episode；完成版本與
cohort 驗證後，才產出週報與 Telegram 推播，避免成熟度落後一週。
同一份統計資料會依序產生 Markdown、JSON 與 Telegram HTML，最後由 workflow
自動提交 `reports/`。

## 校準閘門

V1.3 調參閘門只採用現行 Snapshot schema、SignalEngine、TradePlan、
ShadowMeasurement 與 ConfigHash 完全相符的 completed-R episodes：

- 全體 completed-R 至少 60 筆才允許開始參數分析，100 筆為目標。
- 策略腿與 order type 各至少 20 筆，且全體 60 筆閘門已通過，才可個別判讀。
- 缺檔、JSON 損壞、版本／cohort 不符或成熟度欄位矛盾時一律 fail-closed，
  禁止調參且不沿用舊週 readiness。

Notion legacy-v0 統計仍依 `Status` 的第一個策略標記切分世代：

- V1.2.x：`📐`、`📉`、`🌤️`
- 舊引擎 baseline：`🔥`
- 其他狀態列為未分類，不計入 D8

全期與舊 D8 15 筆進度只保留作歷史比較，不再授權調整權重、量縮門檻或
`MIN_PRIORITY_FOR_GO`。

排程或手動執行預設都只產生「最近完整週一～週日」。若需重建歷史週報，
在 `workflow_dispatch` 的 `report_week_end` 指定週日日期；非週日或未來日期
會直接拒絕，避免不完整資料覆蓋 `latest`。

週報檔案為自動產生，若數字有誤，應修正來源資料或 `weekly_report.py`，不要只改報告。
