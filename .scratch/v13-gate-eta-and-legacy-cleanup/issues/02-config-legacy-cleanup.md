# config 台股遺留清理

Type: task
Status: resolved
Blocked by: -

## Scope

- 刪除 `config.py` 中無外部引用的台股遺留常數。
- `FINMIND_MAX_WORKERS` 更名並同步 `main.py`。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-06 claimed(Claude,經 owner 指派)。
- 前置:先確認全 repo 無 `getattr(Config, ...)` 動態取值,靜態掃描才可信。
  唯二的動態存取是 `llm_enrichment` 對 `COMPANY_NAMES` 的字面量 getattr,
  與 `test_universe_audit` 斷言 `MIN_AVG_VOLUME_LOTS` 不存在。

## Answer

- 見 `../spec.md`。159 → 110 個常數,ConfigHash 不變。
- 刻意保留 4 個無引用但語意屬美股的設定,不在「台股遺留」範圍。
- `sources.py` 的 D2/D5/D6 stub 未動 —— 它們不讀這些常數,且 `main.py`
  仍呼叫 `get_institutional_batch` 以保留未來接源的介面。
