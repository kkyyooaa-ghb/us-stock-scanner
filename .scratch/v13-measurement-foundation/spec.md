# V1.3 量尺與規則基礎版

Status: resolved

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

### TradePlan V1.3.1-shadow

- 盤整腿：
  - 價格低於 MA60：`buy_stop_reclaim`，等收復 MA60。
  - 價格位於 MA60 上方：`buy_limit_zone`，只在 MA60 附近成交。
- 超賣腿：`buy_stop_reclaim`，突破訊號棒（掃描時最後一根完整交易日）
  高點才確認反彈；不得誤用訊號棒前一日高點。
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

### Snapshot contract v1.3.1

- `snapshot_schema.py` 是正式、空結果與錯誤 `scan_result.csv` 的唯一 schema
  與 writer；workflow 不再自行保存 header 字串。
- 92 欄 canonical schema 以 `SnapshotSchemaVersion`、
  `SnapshotRecordType`、`SnapshotRunStatus` 區分資料與 control row。
- 未登記欄位、ready plan 腿別／anchor 不一致、非法 `PreGapStatus` 會
  fail-closed，不可靜默丟欄或寫入矛盾快照。
- `PreGapStatus` 值域為 `available`、`no_premarket_trade`、
  `fetch_error`、`outside_premarket_window`、`disabled`；只有
  `available` 可帶 `PreGapPct`。
- PreGap 抓取窗只由 ET 04:00–09:30 判定，不依賴 XNYS calendar；
  抓取若跨過開盤，整批數值作廢，避免同份快照混合盤前與 regular session。
- `TradePlanVersion` 與 shadow measurement 使用 `v1.3.1-shadow`；
  舊版本不得用新時間規則重新解讀。
- `shadow_ready` 只允許三條正式腿與對應 anchor，且必須帶完整可執行欄位；
  empty、休市跳過與錯誤均為 typed control row。

## V1.3.1-shadow 成交量尺

- 輸入必須是 `auto_adjust=False` 的 as-traded OHLC，並同時下載
  `Dividends` 與 `Stock Splits`。
- 每筆 plan 保存不可變的 `PlanEarliestEntryDate`：
  - 交易日開盤前掃描：當日可成交。
  - 開盤當下／盤中／盤後補跑：下一個 XNYS session 才可成交。
  - 休市日強制補跑：下一個 XNYS session 才可成交。
  - 行事曆失敗：plan 標記 `timing_unavailable`，不得猜測或退回掃描日。
- 最早成交日以整份快照完成不可回補欄位後的封存時點為準；若掃描跨過
  09:30 ET，整批 plan 統一順延，不允許部分股票保留 D+0。
- 當日日 K 只有在行情下載開始時已超過 XNYS close 15 分鐘才視為完整；
  shadow measurement 的 as-of 使用最後一個完整 session，而非牆鐘日期。
- Entry window 從 `PlanEarliestEntryDate` 開始，以完整交易日 bar 計算：
  - `buy_limit_zone`：開盤低於 limit 時以開盤成交；否則 low 觸及
    limit 時以 limit 成交。
  - `buy_stop_reclaim`：開盤高於 trigger 時以開盤成交；否則 high
    觸及 trigger 時以 trigger 成交。
- buy-limit 往下穿過 entry 再到 stop 的順序可確定；buy-stop 同一日
  同時觸 trigger 與 stop 則無法排序，保存 `r_lower`／`r_upper`。
- 未成交計畫在有效窗結束後標記 `unfilled`，不得產生 R。
- 成交後若後續開盤直接低於 stop，以該日 open 作為 gap-through 出場，
  因此 R 可以小於 -1。
- 拆股以累積持股倍數把後續 raw OHLC 正規化回計畫價格基準；現金股息換算為
  每一原始股的持有期現金流後納入總損益。
- D+20/40/60 是從最早可成交日收盤起算、包含拆股與股息的總報酬；MFE/MAE
  則以實際成交風險單位表示。
- 初始停損或成交後 D+40 收盤完成交易；一般未完成樣本只輸出 mark R。
  若 entry bar 本身雙觸，先保存悲觀下界 -1，樂觀上界待後續路徑完成。
- 每週由永久 `reports/daily/*.csv` 重算並覆寫
  `reports/shadow_performance.csv`，不寫入 Notion legacy 欄位。

## 量尺分代

- 未成交 episode 不計 R。
- 日線無法確定 entry/stop 先後時，保存 `r_lower`、`r_upper`。
- 成交／停損使用未復權 as-traded OHLC；指標可繼續使用復權資料。
- 拆股調整持股與價格水位；現金股息另計入持有期總損益。
- 舊 22 筆 R 標記為 `legacy-v0`，不得與 V1.3 量尺合併。

## Episode 與樣本成熟度

- 每日原始訊號永久保存在 `reports/daily/`；episode 去重不刪除來源。
- 一檔 ticker 同時只允許一個活躍 episode。首筆訊號凍結 selected leg、
  order type 與 TradePlan，後續同股訊號在生命週期內只增加 signal count。
- 即使後續 selected leg 改變，只記入 `EpisodeObservedLegs`，不在等待成交或
  持倉期間建立第二筆交易。
- episode 結束條件：
  - 未成交：entry window 最後一個交易日。
  - 已成交：停損、gap-through 或 D+40 時間出場日。
  - 日線雙路徑：等樂觀路徑也定案後才關閉。
  - `invalid_plan`／`no_data`：當日關閉，避免資料錯誤永久吞掉後續訊號。
- 下一筆訊號日期必須晚於 episode end 才能建立新 episode；同日補跑仍合併。
- KPI 報告 filled、unfilled、awaiting、open、completed、ambiguous，
  並按 canonical selected leg 與 order type 分層。
- 調參閘門：
  - 全體至少 60 筆 completed-R episodes 才允許評估參數，100 筆為目標。
  - 腿別／order type 至少各 20 筆 completed-R，且全體閘門已通過，
    才標記該 segment 為 tuning-ready。
  - 閘門只授權分析，不會自動修改任何權重。
- Episode 建構前必須同時符合目前 `TradePlanVersion`、
  `PlanMeasurementVersion` 與 `V13MeasurementVersion`；舊世代 R 不得解鎖閘門。
- 每週輸出 `shadow_episodes.csv`、`shadow_episode_summary.json` 與
  `shadow_episode_summary.md`。

## 驗收條件

1. 三條正式腿均有結構化 decision，不再依賴 emoji 解析。
2. 超賣腿可產生非零 shadow TradePlan。
3. 守 MA20 腿的 shadow plan 必須錨定 MA20，而非 MA60。
4. YoY 否決保留 candidate leg，但 selected leg 為 none。
5. 不改變 V1.2 `Status`、Priority、Top 10 與 Notion 正式欄位。
6. CSV 保存版本、腿別、分數拆解、TradePlan、環境與 PreGapPct。
7. 新介面有單元測試，舊週報測試持續通過。
8. 同一 ticker 的生命週期內重複訊號只形成一個 episode。
9. Episode 報告能分層顯示腿別、order type 與 R 上下界。
10. 未達 60 筆 completed-R 前，調參閘門保持關閉。
11. 超賣腿 trigger 使用訊號棒高點，不得使用 T-2 高點。
12. 盤中／盤後補跑的成交與 horizon 不得從計畫存在前的日線起算。
13. 正常、空結果與錯誤快照使用相同 canonical schema。
14. PreGap 缺值可區分無盤前成交、抓取失敗與非盤前補跑。
15. 掃描跨過開盤、行情下載跨過收盤與盤前立即量測均不得產生 look-ahead。
16. 舊版量尺、非法腿別或不完整 TradePlan 不得進入 completed-R 閘門。

## 非目標

- 本切片不重寫 `track_performance.py`。
- 本切片不啟用 V1.3 R、不回填 Notion 新欄位。
- 本切片不調整任何 V1.2 選股權重。
- 本切片不建立歷史 point-in-time 回測。
