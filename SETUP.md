# us-stock-scanner V1.2.1／V1.3 shadow 部署與排程手冊

血統：台股 stock-scanner V13.13.8 → 架構 2 美股移植；目前正式版本與進度請見
`PROGRESS_2026-07-27.md`。

## 1. 核心檔案

| 類別 | 檔案 |
|------|------|
| 掃描與分析 | `config.py`、`sources.py`、`analyzers.py`、`main.py`、`outputs.py` |
| 校準與週報 | `track_performance.py`、`weekly_report.py`、`reports/` |
| 排程 | `.github/workflows/scan.yml`、`scan_watchdog.yml`、`seed_revenue.yml`、`weekly_report.yml` |
| 資料 | `data/quarter_revenue_cache.json` |

## 2. GitHub Secrets

設定位置：Settings → Secrets and variables → Actions。

| Secret | 用途 |
|--------|------|
| `NOTION_TOKEN` | Notion integration token；須授權「美股掃描」DB |
| `NOTION_DB_ID` | `37c323c3fc0180d3a84acdea1a5ca2af` |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | 掃描與週報推播 |
| `GEMINI_API_KEY` / `TAVILY_API_KEY` | LLM enrichment |

不需要 `FINMIND_TOKEN`、`TWELVEDATA_TOKEN`。

## 3. 正式排程

### 盤前掃描

- 主觸發：cron-job.org，週一至週五 09:00 ET，呼叫 `scan.yml` 的
  `workflow_dispatch`。
- 備援：`scan_watchdog.yml` 在 09:15、09:20 ET 檢查當日執行紀錄。
  找到成功、排隊中或執行中的掃描就不動作；找不到或只有失敗紀錄才補觸發。
- watchdog 同時保留 EDT/EST 兩組 UTC cron，並由 `America/New_York`
  offset gate 只放行當季正確的一組。
- `scan.yml` 使用 concurrency，同一時間只允許一個掃描執行，避免主觸發與
  備援競態造成重複寫入。
- 程式仍有交易日、半日市與 08:30～09:30 ET 寫入護欄。

cron-job.org 設定：

- URL：`https://api.github.com/repos/kkyyooaa-ghb/us-stock-scanner/actions/workflows/scan.yml/dispatches`
- Method：`POST`
- Headers：`Authorization: Bearer <PAT>`、`Accept: application/vnd.github+json`
- Body：`{"ref":"main"}`
- 時區：`America/New_York`
- 排程：週一至週五 09:00

PAT 必須能執行 Actions workflow。

### 資料與報告

- 季營收 cache：每週六 13:00 UTC 執行 `seed_revenue.yml`。
- 校準週報：每週日 13:00 UTC 執行 `weekly_report.yml`。
- seed 與 weekly report 共用 repo-write concurrency，避免同時提交產生競態。
- 週報預設只產生最近完整的週一～週日。歷史重跑須手動提供週日
  `report_week_end`，非週日或未來日期會拒絕。

## 4. 手動執行

`scan.yml` 提供兩個獨立選項：

- `force_notion_sync`：允許在正常寫入時窗外補寫 Notion。
- `force_run`：允許休市日執行掃描；僅限測試，平常不要勾。

一般手動診斷兩者都不勾。若只想確認 watchdog 判定，可手動執行
`scan_watchdog.yml` 並維持 `force_dispatch=false`，此模式只檢查、不補跑。

## 5. 首次或變更後驗證

1. 確認 Secrets 與 cron-job.org PAT 有效。
2. 交易日 09:00 ET 確認 `scan.yml` 被主排程觸發。
3. 09:15/09:20 ET 確認 watchdog 找到健康 run 並跳過補跑。
4. 確認 Telegram 推播、Notion `YYYY-MM-DD_<Ticker>` 紀錄與
   `scan-result-*` artifact。
5. 週日確認 `reports/latest.md`、`reports/latest.json` 與週別封存已提交。

## 6. 待辦

- P3：累積足夠樣本後建立美股版 `backtest_picks`。
- P4：評估開盤後 5～15 分鐘的真實量價補掃。
- P5：維護 Nasdaq 年度成分異動並重跑 seed。
- 補齊 2026-07-06～2026-07-12 的歷史校準資料；來源不足時不得推估補造。
