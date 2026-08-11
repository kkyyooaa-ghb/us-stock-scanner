# LLM 摘要品質與週報 Top5 修正

Status: resolved

## Goal

8/6~8/10 首週實跑後的三項修正。全部**不碰 ConfigHash** —— cohort 目前
8/60,預估 10-09 達標,期間任何選股或量尺改動都會歸零。

## Scope

- P1:Gemini 暫時性失敗加退避重試。
- P2:prompt 加非事件排除規則,擋掉純價格波動與平台上架類雜訊。
- P3:週報 Top5 只列實際觸發腿別者。

## Non-goals

- 不改 SignalEngine、TradePlan、ShadowMeasurement、SnapshotSchema。
- **不改計分邏輯** —— EA 的 Score 異常成因留待 10 月與調參一併處理。
- 不砍 `include_domains`。

## Answer

### P1 Gemini 重試

`_call_with_retry()` + `_is_retryable()`,3 次、退避 2s/5s。可重試:
503/500/429/逾時/連線中斷。不可重試:金鑰、權限、格式錯誤、**配額用罄**
(當日不會恢復,重試只會排擠後面標的的時間預算)。

### P2 非事件排除規則

prompt 新增四類排除:純價格波動、平台上架與衍生商品(代幣化股票、ETF
成分調整)、泛泛評論與內容農場標題、過期重述。並補「全被排除時寫
『無相關新聞』」。

改 prompt 而非砍網域:雜訊是內容類型問題,不是來源問題;砍網域會連帶
失去該來源的有效報導。

### P3 Top5 排除無腿別

`aggregate()` 只列 `SelectedLeg` 有實際腿別者。舊 schema 無此欄時不過濾
(無從判斷不臆測);全部無腿別時退回原行為避免空 Top5。

8/3~8/7 實測:EA(P1 S19.8 leg=none)被排除,遞補 AMAT
(P14 S14.6 consolidation_dip),其餘四檔不變。

### 驗收證據

- 測試 226 → **239 項全部通過**,新增 13 項。
- `strategy_config_hash()` = `8142e595d788ac06`、`universe_version()` =
  `ndx-99-78834e47b659`,與修正前完全相同。
- `git diff --check` 通過。

### 未驗證項

P1 與 P2 的實效要等下一次 09:00 ET 掃描才看得到:重試是否真的救回
503、排除規則是否過嚴而讓摘要變空。兩者都只影響 Notion 摘要欄,
不影響選股。
