# Gemini 暫時性失敗重試

Type: task
Status: resolved
Blocked by: -

## Scope

`llm_enrichment`:退避重試 + 可重試/不可重試分流。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-11 claimed。證據:2026-08-10 掃描 log 顯示唯一缺漏為
  `503 UNAVAILABLE`(模型暫時過載),Tavily 該日零失敗。

## Answer

- 3 次嘗試、退避 2s/5s。配額用罄刻意**不**重試 —— 當日不會恢復,
  重試會排擠後面標的的時間預算。
- 5 項測試:兩類錯誤分流、重試後成功、達上限放棄、永久錯誤只試一次。
