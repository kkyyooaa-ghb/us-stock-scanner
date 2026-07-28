# 母體資格改成成交金額 + 全池對帳(v1.2.1／v1.3.3)

Type: task
Status: resolved
Blocked by: 06

## Scope

- 移除台股沿用的股數門檻,改用 20 個完整交易日的 `median(Close × Volume)`。
- 金額門檻 $20M;回看期、統計方式、最低價與資料新鮮度規則納入 ConfigHash。
- 每檔恰好一筆母體紀錄:可評分者 `data`,其餘 `universe_audit` 附原因碼。
- 永久驗證 `expected = processed + excluded + missing`。
- 訊號棒非最後完整交易日者不得參與主題共振與排名。

## 為什麼舊門檻必須整個拿掉

`MIN_AVG_VOLUME_LOTS = 1000`(= 100 萬股/日)是台股「張」的概念。股數與
股價成反比,套到美股後與真實流動性**反向**:

- 靜默排除 AXON／IDXX／MELI／MPWR／REGN／ROP 六檔,成交金額 3.11 億～11.45
  億美元/日;其中 MPWR 高於全池中位數 7.5 億。
- 同時留下全池唯一低於 1 億的 FER(90M),只因它股價低、股數湊得到 137 萬。
- 被排除者完全不進快照,是靜默且有系統偏誤的資料缺口(6/99 = 6.1%)。

## 門檻怎麼定的

99 檔近 20 日每日成交金額中位數:P0 90M / P5 301M / P50 750M / P100 40,773M。
$5M～$50M 全部排除 0 檔,$100M 才開始排除 FER。因此 $20M 是**執行容量護欄**
而非報酬最佳化參數 —— 它今天誰也不篩,離全池最低值仍有 4.5 倍邊際,作用是
在成分股異動或池子擴大時才生效。中位數而非均值,避免財報日單日暴量讓一檔
矇混過關。

## Dry run(切換前,selection-neutral)

以 monkeypatch 在記憶體中切換門檻,兩次都跑真正的 `run_scanner()`:

- 既有 93 檔的 Priority／Score／ThemeScore／SelectedLeg **零變動**。
- 主題加分 15 → 16 檔,只有 REGN 新獲得,無人失去。
- Top 10 完全相同;達標檔數 20 → 21。
- shadow_ready TradePlan 23 → 25(新增 IDXX、REGN)。

⚠️ 這是單日結果,不是每日增量。當天 biotech 本來就已有 ≥2 檔觸發,REGN 只是
加入一個已點亮的主題;在 biotech 恰好只有 1 檔觸發的日子,REGN 會讓整池點亮
而改變其他股票的分數與 Top 10。**dry run 證明的是切換風險低,不是切換無影響**,
這正是要升 `SignalEngineVersion` 的理由。

## Acceptance

- `expected = processed + excluded + missing` 由 schema 驗證,不平衡即拒寫。
- 同一 ticker 不得出現兩筆母體紀錄。
- `universe_audit` 列不得帶 Priority／Score(零分偽裝會被下游當普通標的)。
- 六類原因碼皆可被接受,未登記的原因碼一律拒絕。
- `snapshot_data_rows()` 只回傳 `data`,週報與 shadow 量尺不受 audit 列影響。
- 母體資格規則改變時 ConfigHash 必須跟著變。

## Answer

- `SignalEngineVersion` v1.2.0 → **v1.2.1**、`SnapshotSchemaVersion` v1.3.2 →
  **v1.3.3**;TradePlan 與 ShadowMeasurement 維持 `v1.3.1-shadow`。
- 新 ConfigHash `8142e595d788ac06`;v1.2.0 舊 cohort 因此不會與新母體混算。
- 上線驗收:99 = processed 98 + excluded 1(ALNY / `stale_bar`)+ missing 0;
  Top 10 與 baseline 相同,新增 IDXX、REGN 兩筆 TradePlan,
  `DollarVolumeMedian20` 98/98 有值、最低 90M、低於門檻者 0 檔。
- 測試 94 → 105 項,新增 `tests/test_universe_audit.py`。

## 待辦(非阻擋)

- SPY／QQQ 盤前共用 helper、盤前報價 value object:重構債,沿用自 06。
- `demote_stale_bar_plans` 在 freshness 於選股層生效後成為第二道防線,
  保留作為 defense in depth。
