# 美股掃描永久報告

`reports/` 是週報與回測資料的正式保存位置。Telegram 僅負責推播，不再是資料來源。

## 目錄

- `latest.md`:最新一份人類可讀週報。
- `latest.json`:最新一份機器可讀統計快照。
- `weekly/YYYY-MM-DD_YYYY-MM-DD.md`:歷史週報。
- `weekly/YYYY-MM-DD_YYYY-MM-DD.json`:歷史統計快照。
- `daily/YYYY-MM-DD.csv`:每日完整掃描結果，供後續 P9 回測與週報重算。

## 產生流程

`.github/workflows/weekly_report.yml` 每週先執行績效回填，再由
`weekly_report.py` 讀取當週 GitHub Actions artifacts 與 Notion 校準欄位。
同一份統計資料會依序產生 Markdown、JSON 與 Telegram HTML，最後由 workflow
自動提交 `reports/`。

週報檔案為自動產生，若數字有誤，應修正來源資料或 `weekly_report.py`，不要只改報告。
