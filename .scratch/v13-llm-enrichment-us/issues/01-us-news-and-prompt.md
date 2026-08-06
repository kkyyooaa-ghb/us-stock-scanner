# 新聞源與 prompt 美股化

Type: task
Status: resolved
Blocked by: -

## Scope

- `config.py`:新增 `COMPANY_NAMES`(99 檔),`LLM_ENRICHMENT_ENABLED` 改 `True`。
- `llm_enrichment.py`:query builder、`include_domains`、PROMPT_TEMPLATE、
  時區改 ET、公司名解析與 fallback。
- `tests/test_llm_enrichment.py`:新增回歸測試。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-06 claimed(Claude,經 owner 指派)。
- 前置驗證:`strategy_config_hash()` 是明確 allowlist(`trade_plan.py:217`),
  新增 Config 常數不進 hash;`universe_version()` 只 hash `SCAN_POOL`,本次不動。
- 基線:本機 135 項測試通過,`main...origin/main` 0 ahead / 0 behind。

## Answer

- 見 `../spec.md` 的 Answer 與驗收證據。
- 額外發現並一併修正:`LLM_ENRICHMENT_TOTAL_TIMEOUT` 180s 是台股版 5 檔的
  預算,美股 df_go 上限 10 檔會讓末尾靜默超時,已改 300s。
- `main.py` 未改動 —— 公司名解析放在 `llm_enrichment` 內部,生產掃描主路徑
  零變更。
