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

`.github/workflows/weekly_report.yml` 每週先執行績效回填，再由
`weekly_report.py` 讀取當週 GitHub Actions artifacts 與 Notion 校準欄位，
再由 `track_shadow_performance.py` 對永久 daily snapshots 重算 V1.3 量尺。
`build_shadow_episodes.py` 接著將重複日訊號合併為獨立交易 episode，
並輸出生命週期與樣本成熟度報告。
同一份統計資料會依序產生 Markdown、JSON 與 Telegram HTML，最後由 workflow
自動提交 `reports/`。

## 校準閘門

週報依 Notion `Status` 的第一個策略標記切分計分引擎世代：

- V1.2.0：`📐`、`📉`、`🌤️`
- 舊引擎 baseline：`🔥`
- 其他狀態列為未分類，不計入 D8

全期 R 統計保留作歷史比較，但只有 V1.2.0 已回填 R 值筆數達 15 筆時，
週報才會判定 D8 門檻通過並提示啟動權重或門檻校準。

排程或手動執行預設都只產生「最近完整週一～週日」。若需重建歷史週報，
在 `workflow_dispatch` 的 `report_week_end` 指定週日日期；非週日或未來日期
會直接拒絕，避免不完整資料覆蓋 `latest`。

週報檔案為自動產生，若數字有誤，應修正來源資料或 `weekly_report.py`，不要只改報告。
