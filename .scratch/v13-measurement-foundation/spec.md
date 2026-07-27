# V1.3 量尺與規則基礎版

Status: claimed

## 目標

在不改變 V1.2 正式 Top 10 選股結果的前提下，建立可版本化、可測試、可向前
累積的訊號決策與 TradePlan 快照。V1.3 shadow 資料將取代 `Status` 字串解析，
並為後續成交區間、episode、企業行動與 R 區間量測提供正式資料契約。

## 已確認問題

- V1.2 三腿互斥，但 CSV 未保存正式腿別；舊 `SetupType` 與正式選腿脫鉤。
- 超賣腿因通用 DistTag 進場器回傳 `(0, 0)`，無法累積有效 R。
- 守 MA20 的拉回腿仍以 MA60 產生進場區，交易概念與量測錯位。
- `compute_backfill` 不區分 buy-limit 與 buy-stop，且可能在成交前判停損。
- `auto_adjust=True` 重抓歷史資料時，除息／拆股可能改變舊 bar 的價格基準。
- 盤前跳空只寫入 Top 10 DataFrame 副本，永久 CSV 欄位保持空白。
- LLM enrichment 搜尋台股關鍵字與台灣限定來源，修正前應停用。

## V1.3 shadow 規則

### 訊號決策

- `candidate_leg`：基本面否決前命中的互斥策略腿。
- `selected_leg`：通過否決後可建立 TradePlan 的策略腿。
- `leg_score_raw`：季營收與主題加分前的腿別原始分。
- `veto_reason`：否決原因，不再從顯示文字解析。
- 舊 `determine_status()` tuple 介面保留為 compatibility adapter。

### TradePlan V1.3.0-shadow

- 盤整腿：
  - 價格低於 MA60：`buy_stop_reclaim`，等收復 MA60。
  - 價格位於 MA60 上方：`buy_limit_zone`，只在 MA60 附近成交。
- 超賣腿：`buy_stop_reclaim`，突破前一完整交易日高點才確認反彈。
- 守均線腿：`buy_limit_zone`，必須使用實際觸發的 MA20 或 MA60。
- 初始停損為盤中觸價；V1 只使用初始停損與 D+40 時間出場。
- 移動停損、停利、分批出場與均線出場不納入本 shadow 版本。

這些計畫只寫入 CSV，不取代現行 Notion `進場參考價` 或績效回填。

## 快照資料契約

每日全池 CSV 增加：

- 引擎、TradePlan、量尺、Git、config 與 universe 版本。
- `candidate_leg`、`selected_leg`、腿別原始分、錨點與否決原因。
- TradePlan order type、trigger、entry zone、stop、有效日與時間出場。
- 季營收／主題分數拆解、主題前後 Priority 與 Top 10 反事實旗標。
- SPY、QQQ、VIX、ES/NQ、廣度與小型股環境。
- `PreGapPct` 必須回寫全池 DataFrame 後再封存。
- 舊 `SetupType` 保留，另加 `DiagnosticSetupTypeV1` 明示其診斷性質。

## 後續量尺規則

- 未成交 episode 不計 R。
- 日線無法確定 entry/stop 先後時，保存 `r_lower`、`r_upper`。
- 成交／停損使用未復權 as-traded OHLC；指標可繼續使用復權資料。
- 拆股調整持股與價格水位；現金股息另計入持有期總損益。
- 舊 22 筆 R 標記為 `legacy-v0`，不得與 V1.3 量尺合併。

## 驗收條件

1. 三條正式腿均有結構化 decision，不再依賴 emoji 解析。
2. 超賣腿可產生非零 shadow TradePlan。
3. 守 MA20 腿的 shadow plan 必須錨定 MA20，而非 MA60。
4. YoY 否決保留 candidate leg，但 selected leg 為 none。
5. 不改變 V1.2 `Status`、Priority、Top 10 與 Notion 正式欄位。
6. CSV 保存版本、腿別、分數拆解、TradePlan、環境與 PreGapPct。
7. 新介面有單元測試，舊週報測試持續通過。

## 非目標

- 本切片不重寫 `track_performance.py`。
- 本切片不啟用 V1.3 R、不回填 Notion 新欄位。
- 本切片不調整任何 V1.2 選股權重。
- 本切片不建立歷史 point-in-time 回測。
