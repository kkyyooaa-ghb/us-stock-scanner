# V1.3 P0:LLM enrichment 美股化

Status: resolved

## Goal

`llm_enrichment.py` 自台股版原樣移植後從未改市場:query 硬加「台股」、
`include_domains` 全為台灣財經媒體、prompt 自稱「台股研究助手」。因此
`Config.LLM_ENRICHMENT_ENABLED` 自 V1.3 起被設為 `False`,整個 P7.5 階段
在生產中是死碼。本次把新聞源與 prompt 換成美股口徑後重新啟用。

## Non-goals(選股中性,必須成立)

- 不改 SignalEngine、TradePlan、ShadowMeasurement、SnapshotSchema。
- 不改 `strategy_config_hash()` 的任何輸入鍵,不改 `SCAN_POOL`
  (`universe_version()` 只 hash SCAN_POOL)。
- 不改分數、腿別、Top 10、主題加分、`MIN_PRIORITY_FOR_GO`。
- 不改每日 CSV 快照的欄位或內容;LLM 摘要只寫 Notion「LLM 摘要」欄。

## Scope

- Tavily query builder 改美股:移除「台股」與 `.TW/.TWO` 清理,改用
  公司英文名 + ticker + `stock news`。
- `include_domains` 換成美國財經媒體與公司公告來源(含新聞稿線與 SEC)。
- 新增 `Config.COMPANY_NAMES`(99 檔 ticker → 公司英文名),讓 `APP`、`EA`、
  `ARM`、`MU`、`STX` 這類高歧義代號能搜到正確公司。
- Prompt 改「美股研究助手」:輸入為英文新聞,輸出維持繁體中文
  (Notion 欄位與使用者判讀語言不變),風險/催化條目改美股語彙。
- 時區由 UTC+8 改 ET,與全 repo 一致(新聞時效 cutoff 與 prompt 日期)。
- 重新啟用 `LLM_ENRICHMENT_ENABLED = True`;env 快關能力保留。
- 新增 `tests/test_llm_enrichment.py`(全程 mock,不打網路)。

## Acceptance criteria

1. query 不含「台股」,含公司英文名與 ticker;`.TW/.TWO` 清理移除。
2. `include_domains` 不含任何 `.tw` / 台灣媒體網域。
3. `COMPANY_NAMES` 覆蓋 `SCAN_POOL` 全部 99 檔;查無名稱時 fallback 至
   純 ticker 而非拋錯。
4. Prompt 自稱美股研究助手,明示新聞為英文、輸出繁體中文。
5. 新聞時效 cutoff 與 prompt 日期以 ET 計算。
6. `strategy_config_hash()` 與 `universe_version()` 相對於本次修改前
   **完全不變**。
7. Gemini/Tavily/Notion 任一失敗仍優雅降級,主流程不受影響(既有行為不退化)。
8. 新測試與既有 135 項全部通過;`git diff --check` 通過。

## Answer

- Tavily query 改 `公司英文名 (TICKER) stock news`;「台股」與 `.TW/.TWO`
  清理一併移除。`include_domains` 換為 15 個美國來源,分三類:主流財經媒體、
  新聞稿線(businesswire／prnewswire／globenewswire)與 `sec.gov`。
- 新增 `Config.COMPANY_NAMES`,與 `SCAN_POOL` 99 檔一對一;測試雙向驗證
  (缺名與多餘代號都會失敗)。查無名稱 fallback 為純 ticker,不拋錯。
- Prompt 改美股研究助手:風險/催化語彙換成財測下修、SEC 調查、內部人賣股、
  分析師升降評等;明示輸入為英文、輸出繁體中文;新增標的比對規則以對抗
  美股 ticker 歧義。Notion 欄位的三段式 emoji 格式不變(有測試釘住)。
- 時區 UTC+8 → ET:新聞時效 cutoff、prompt 日期與執行時間 log 全部改 ET;
  無時區的 `published_date` 改視為 ET。
- `LLM_ENRICHMENT_TOTAL_TIMEOUT` 180 → 300s。df_go 上限為 `TOP_N_RECOMMENDED`
  = 10 檔,以每檔 ~21s 估算需 ~210s,舊值會讓末尾 1~2 檔靜默「整段超時」。
- `LLM_ENRICHMENT_ENABLED` 改 `True`;env `LLM_ENRICHMENT_ENABLED=false`
  快關能力保留並有測試覆蓋(Config 關閉優先於 env 開啟)。

### 驗收證據

- **選股中性**:`strategy_config_hash()` 改動前後同為 `8142e595d788ac06`,
  `universe_version()` 同為 `ndx-99-78834e47b659`。另有測試直接 patch 全部
  LLM 設定與 `COMPANY_NAMES`,斷言兩個 identity 皆不變。
- 測試 135 → **165 項全部通過**;新增 `tests/test_llm_enrichment.py` 30 項,
  全程 mock、不打網路。
- 無金鑰 smoke:兩檔各自降級為 Tavily/Gemini 失敗,回傳 0/2 且不拋例外,
  主流程不受影響。
- `git diff --check` 通過。

### 未驗證項(需正式跑才知道)

- 真實 Tavily 命中率與 `include_domains` 是否過窄,只能等第一次正式跑後看
  Notion「LLM 摘要」品質再調。
- Gemini 對英文新聞輸出繁體中文的穩定度亦需實跑觀察。
