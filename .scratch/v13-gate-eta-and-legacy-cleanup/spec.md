# V1.3 閘門達標日預估 + config 台股遺留清理

Status: resolved

## Goal

兩件互不相干但同批處理的事:

1. 週報目前只顯示 `collecting 2/60`,每週都是同一句話,看不出還要多久,
   無從判斷該繼續等還是該重新檢視閘門設計。把「還要多久」量化出來。
2. `config.py` 尾端整段台股遺留常數(D2/D5/D6 停用區)已無人引用,
   卻仍讓人以為美股版還在跑匯率、期現貨、除息、融資邏輯。

## Non-goals

- 不改 SignalEngine、TradePlan、ShadowMeasurement、SnapshotSchema。
- 不改 `strategy_config_hash()` 輸入鍵,不改 `SCAN_POOL`。
- **預估不得影響任何調參授權** —— 授權只看實際 completed-R。
- 不動 `sources.py` 的 D2/D5/D6 stub(main.py 仍呼叫其中一支)。

## Scope

### 1. 達標日預估

- 新增 `gate_projection.py`,把預估拆成決定性與估計兩半。
- 接進 `episode_analysis` summary、episode Markdown 與週報。
- 週報 JSON schema 3 → 4。

### 2. config 清理

- 刪除無外部引用的台股遺留常數。
- `FINMIND_MAX_WORKERS` 有實際引用,更名為
  `INSTITUTIONAL_BATCH_MAX_WORKERS` 並同步 `main.py` 唯一呼叫點。

## Acceptance criteria

1. 預估分「已知管線(決定性)」與「未來到達(估計)」兩半,且 cohort 首日
   backlog 不得混入到達率。
2. 資料不足、無到達、超出視界一律明說原因,不猜日期。
3. 拿不到 XNYS 日曆時仍回交易日數,只是不給日期。
4. 預估失敗不得中斷週報,也不得改變 `parameter_tuning_allowed`。
5. config 刪除後 `strategy_config_hash()` 與 `universe_version()` 不變。
6. 全套測試通過;`git diff --check` 通過。

## Answer

### 達標日預估

- 新增 `gate_projection.py`。模型刻意分兩半,因為兩半可信度差很多:
  - **已知管線(決定性)**:已成交且仍 open 的 episode,其
    `V13BarsObserved` 與 `PlanTimeExitDays` 已知,time exit 到期日可直接算。
  - **未來到達(估計)**:每掃描日新增 episode 率 × 成交率外推。
- **cohort 首日必須排除**:2026-07-28 是 25 筆(一次性把既有訊號折成新
  episode),其後每日僅 3~4 筆。混入會把到達率高估 6 倍以上。有測試釘住。
- 區間改用 **Poisson 95%**(總事件數 n ± 2√n)而非樣本標準差。掃描日只有
  3 天時,樣本 sd 會給出假性精確的 ±2 天;實測改用 Poisson 後,60 筆的區間
  從「10-15 ~ 10-21」誠實擴大為「10-09 ~ 11-17」。
- 日曆是可注入接縫(`session_resolver`),測試不依賴 `exchange_calendars`;
  正式環境走 XNYS,拿不到日曆就只回交易日數。
- 預估在 `episode_analysis` 內由 `_safe_projection` 包住,任何例外降級為
  `projection_failed`,週報照常產出、閘門照常運作。

**目前實測結果**(基準日 2026-07-31,信心度 low):

| 里程碑 | 門檻 | 交易日 | 預估 | 區間 |
|---|---:|---:|---|---|
| 最低 | 60 | 55 | 2026-10-19 | 2026-10-09 ～ 2026-11-17 |
| 目標 | 100 | 70 | 2026-11-09 | 2026-10-23 ～ 2027-01-13 |

基準:管線 21 筆決定性 + 每掃描日新增 3.667 筆 × 成交率 71.9%
= 每日 2.635 筆完成;time exit 40 個交易日。

已標註的偏誤:只排 time exit(提早停損會讓實際更早)、`awaiting_fill`
未計入管線(同樣偏晚)、成交率的二項不確定性未計入。

### config 台股遺留清理

- 靜態掃描全 repo(已先確認**無任何** `getattr(Config, ...)` 動態取值,
  故靜態分析可信):159 個常數中 53 個在 config.py 外零引用。
- 刪除 49 個確為台股遺留者:匯率(D5)、期現貨、除息、融資、大盤開盤量
  結構(D2/D6)整段 40 個,加上 `FINMIND_*` / `TWELVEDATA_*` 金鑰與 URL、
  `MONTH_REVENUE_*` 相容別名。
- `FINMIND_MAX_WORKERS` 實際被 `main.py:245` 引用(餵 `get_institutional_batch`
  這支零成本 stub),更名為 `INSTITUTIONAL_BATCH_MAX_WORKERS`,行為不變。
- 保留 4 個無引用但**非**台股遺留者:`INDEX_TICKER_SECONDARY`（^IXIC)、
  `CONSOLIDATE_BUY_DAYS`、`LOW_BUY_DAYS`、`DATA_FALLBACK_MIN_RATIO`。
  這些是美股語意的未接線設定,不在本次範圍。
- config 常數 159 → 110,`FINMIND|TWELVEDATA|FOREX|TWD_|TAIEX|MARGIN_BALANCE|
  EX_DIV|BASIS_|FOREIGN_SHORT|MIS_OPEN|VOL_STRUCTURE` 全數歸零。

### 驗收證據

- `strategy_config_hash()` = `8142e595d788ac06`、`universe_version()` =
  `ndx-99-78834e47b659`,與改動前完全相同。
- 測試 165 → **186 項全部通過**;新增 `tests/test_gate_projection.py` 21 項。
- 端到端 smoke:summary → `load_v13_calibration_gate` → 週報顯示,
  gate 仍正確回報 `parameter_tuning_allowed=False`,projection 正常帶過。
- `git diff --check` 通過。
- 既有 `test_report_json_persists_the_validated_gate` 的 schema 斷言
  3 → 4,並加驗 `projection` 欄位存在。
