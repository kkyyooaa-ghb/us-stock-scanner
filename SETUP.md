# us-stock-scanner V1.0.0-US 部署手冊

血統:台股 stock-scanner V13.13.8 → 架構2移植(決策紀錄見 config.py docstring D1–D8)

## 1. 新 repo 檔案清單

| 檔案 | 來源 |
|------|------|
| `config.py` `sources.py` `analyzers.py` `main.py` `outputs.py` | 本次產出(美股版) |
| `requirements.txt` | 本次產出 |
| `.github/workflows/scan.yml` `.github/workflows/seed_revenue.yml` | 本次產出 |
| `llm_enrichment.py` | **從台股 repo 原樣複製**(架構通用;Tavily query 用代號,美股代號直接可用) |
| `data/quarter_revenue_cache.json` | 你 2026-06-11 已 seed 的檔,先 commit 進去(之後週六自動更新) |

⚠️ 不要複製台股的:`seed_*.py`、其他 workflows、`backtest_picks.py`(P9 回測引擎之後另出美股版)。

## 2. GitHub Secrets(Settings → Secrets and variables → Actions)

| Secret | 值 |
|--------|-----|
| `NOTION_TOKEN` | 與台股同一個 integration token 即可(integration 須對「美股掃描」DB 有存取權:DB 頁面 → ⋯ → Connections 加入) |
| `NOTION_DB_ID` | `37c323c3fc0180d3a84acdea1a5ca2af` ← **美股掃描 DB,勿用台股的** |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | 沿用(同一個 bot/頻道,訊息標頭已區分「美股盤前監控」) |
| `GEMINI_API_KEY` / `TAVILY_API_KEY` | 沿用台股的 |

不需要:`FINMIND_TOKEN`、`TWELVEDATA_TOKEN`(US 版已移除)。

## 3. cron-job.org(主掃描觸發)

建一個 job:
- **URL**:`https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/scan.yml/dispatches`
- **Method**:POST
- **Headers**:`Authorization: Bearer <GITHUB_PAT>`、`Accept: application/vnd.github+json`
  (PAT:fine-grained 給該 repo `Actions: Read and write`;classic 給 `repo`+`workflow`)
- **Body**:`{"ref":"main"}`
- **時區**:`America/New_York` ← **關鍵,DST 自動處理**(台北 21:00 夏令 / 22:00 冬令)
- **排程**:週一~週五 09:00

季營收 seed 走 GitHub schedule(週六,週頻不受 DST 影響),不用另建 cron job。

## 4. 美股休市日(2026 下半年,cron-job.org 手動停一次或忽略該日訊息)

06/19 六月節、07/03 國慶(補)、09/07 勞動節、11/26 感恩節、12/25 聖誕節。
(待辦 P:加 `exchange-calendars` 護欄讓程式自動跳過,等系統穩定後再做)

## 5. 首次啟用順序

1. repo 建好、secrets 設好、`data/quarter_revenue_cache.json` commit 進去
2. GitHub UI 手動跑一次 `scan.yml`(非排程時段:**不勾** force → 驗 TG 推播;確認後可勾 force 補寫一筆 Notion 驗 DB)
3. 確認 Notion「美股掃描」出現 `YYYY-MM-DD_<Ticker>` 紀錄、欄位齊全
4. cron-job.org job 啟用 → 隔天美東 09:00 看自動推播
5. 之後進入觀察期:**只收集、不調參**(D8:任何權重調整等 n≥15)

## 6. 已知待辦(依優先序)

- **P1** `track_performance` 美股版:回填 掃描日收盤價 / D+1/D+3/D+5 報酬% / R值 / 是否觸發停損(DB 欄已建好)
- **P2** 休市日護欄(exchange-calendars)
- **P3** `backtest_picks` 美股版(P9 回測引擎;累積樣本後才有意義)
- **P4** 方向 C:開盤後 5–15 分補掃「真實開盤量結構」(等美股 P9 證明量結構有用再做)
- **P5** 成分異動維護:Nasdaq 每年 12 月重組 → 更新 config SCAN_POOL → 重跑 seed
