# V1.3 調參分析工具

Status: resolved

## Goal

閘門預估 2026-10 中前後開啟。屆時要回答「哪條腿、哪種 order type、哪個
市場環境有 edge」,但目前沒有任何工具。現在建好,10 月直接跑;同時現在
就能用手上樣本驗證工具本身。

用未達標樣本**測試工具**與用它們**調參**是兩回事,前者一直被允許。

## Non-goals

- 不改 SignalEngine、TradePlan、ShadowMeasurement、SnapshotSchema。
- 不進 `strategy_config_hash()` / `universe_version()`。
- **不授權任何事**,也不修改任何參數。授權只由
  `weekly_report.load_v13_calibration_gate` 判定。

## 設計重點

### 為什麼不是「分組算平均 R」

策略調參最典型的死法是切十幾個維度、挑最好看的那格宣稱找到 edge。切 15 格、
每格 95% 信賴水準,純噪音也有約 54% 機率至少冒出一格「顯著」。

因此結論分兩層且都印出來:

- **探索層(未校正)**:各格自己的 95% 區間 → 只能產生假說。
- **確認層(Bonferroni 校正)**:以 eligible 格數校正後仍排除 0 才算結論。

多數情況確認層會是空的。那不是工具壞了,那就是答案。模組因此一併算出
「要多少筆才解析得動觀察到的效果」(`required_n_for_quarter_r`),讓「再等」
成為有依據的決定。

### 統計方法

- R 的分布非常態(停損在 -1 有質量點,獲利側是連續尾巴)→ 用 bootstrap
  百分位區間,不用 t 分布。
- 固定隨機種子,同一份資料每次給同一個答案(有測試釘住)。
- 樣本不足的格子**不算進**檢定數 —— 它們本來就不下結論,不該稀釋校正。

### 同日雙觸

Episode 保存 R 上下界(日線無法判斷盤中順序)。對上下界**各算一次**,
只有兩邊都通過確認才標 `robust_across_r_bounds`。不用單一數字掩蓋那個
已知的不確定性。

### 授權

閘門未開時統計照常輸出(工具驗證用),但 `authorized=False`、`findings`
清空、`findings_withheld=True`,Markdown 頂端掛未授權橫幅。

## Answer

- 新增 `tuning_analysis.py`:`analyze_tuning()`、`derive_dimensions()`、
  `render_markdown()`,以及 CLI
  `python -m tuning_analysis reports/shadow_episodes.csv`。
- 分層維度 9 項:`PlanSelectedLeg`、`OrderType`、`CandidateLeg`、
  `MarketBias`、`VixBucket`、`PriorityBucket`、`theme_crossed_threshold`、
  `theme_entered_top10`、`PreGapStatus`。缺欄者列入 `dimensions_missing`
  而非靜默略過。
- CLI 預設從 `reports/shadow_episode_summary.json` 讀閘門狀態,不自行判定。

### 驗收證據

- **假陽性防護**:6 格純噪音(每格 40 筆)跑完 `findings` 為空。
- **真訊號仍可偵測**:合成 leg_A 真 edge +1.0R / leg_B 0R,各 80 筆 →
  只抓出 leg_A,leg_B 未入選。
- **實際資料**:2 筆 completed → 檢定格數 0、無任何結論,並掛未授權橫幅。
- 測試 206 → **226 項全部通過**;新增 `tests/test_tuning_analysis.py` 20 項。
- `strategy_config_hash()` 與 `universe_version()` 不變。
- `git diff --check` 通過。

### 待閘門開啟後才能驗的事

- 真實 R 分布下 bootstrap 區間的實際寬度,以及每格 20 筆是否真的夠。
  `required_n_for_quarter_r` 屆時會直接給出答案 —— 若普遍遠大於 20,
  代表 `EPISODE_SEGMENT_MIN_COMPLETED` 本身需要重新檢視。
