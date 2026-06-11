"""
美股掃描系統 V1.0.0-US 配置中心
血統:台股 stock-scanner V13.13.8 → 架構2移植(2026-06-09 拍板)

═══════════════════════════════════════════════════════════════
移植決策紀錄(V1.0.0-US,2026-06-09):
═══════════════════════════════════════════════════════════════
  D1. 股票池:Nasdaq-100(暫定),約 95 檔快照,見 SCAN_POOL 註解
  D2. 盤前量(原 P7 MIS):方向 A → C
      A(本版):直接停用。實測證實 yfinance 免費盤前量不可靠
        (5 天僅 1 天有真實量,5m 也救不了);P7 在台股版只是
        TG 註記、從不進評分,graceful empty 即可。
      C(Arch-3 增益):開盤後 5-15 分補一掃讀真實開盤量結構,
        等美股 P9 樣本證明有用再做。
  D3. 月營收 YoY(原 P4):美股無月營收揭露 → 改「季營收 YoY」
      (yfinance quarterly income stmt,cache 週更),級距沿用
      但標記未校準。
  D4. 大盤燈號:^TWII → ^GSPC(主)+^IXIC(輔);櫃買偏弱 → ^RUT
      (小型股相對弱勢,同 -2% 容忍區邏輯);新增 VIX 背景。
  D5. 亞洲匯率共振:整段停用(台股出口商專屬邏輯)。美股版宏觀
      背景改 ES=F/NQ=F 期貨 + VIX(get_futures_macro),
      只當背景、不投票,等實戰再定權重。
  D6. 三大法人/融資餘額/台指期/除息扣點:停用(台股專屬資料)。
      sources.py 提供同形狀 graceful stub,主流程不崩。
  D7. 新訊號:個股盤前跳空(preMarketPrice 實測可靠)。
  D8. 校準歸零:所有台股 P9 結論(+0.78R 等)不轉移;沿用參數
      僅作起點,任何權重調整須等美股樣本 n≥15(校準紀律不變)。

排程(對應台股 09:00-09:15 掃描窗):
  美東盤前 09:00-09:25 ET ≈ 台北 21:00-21:25(夏令 EDT)
                          ≈ 台北 22:00-22:25(冬令 EST)
  ⚠️ GitHub Actions cron 走 UTC 不跟 DST → 觸發端用 cron-job.org
     並把時區設 America/New_York,程式內再以 ET 視窗 gate。
═══════════════════════════════════════════════════════════════
"""
import os


class Config:
    # ========== API 金鑰 ==========
    NOTION_TOKEN     = os.environ.get("NOTION_TOKEN", "")
    NOTION_DB_ID     = os.environ.get("NOTION_DB_ID", "")     # ⚠️ 美股新 DB,勿沿用台股 DB
    TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    # --- 相容保留(台股遺留,US 版不使用;留空避免舊碼 AttributeError) ---
    FINMIND_TOKEN    = os.environ.get("FINMIND_TOKEN", "")    # deprecated(US)
    TWELVEDATA_TOKEN = os.environ.get("TWELVEDATA_TOKEN", "") # deprecated(US)
    FINMIND_URL      = ""                                      # deprecated(US)
    TWELVEDATA_URL   = ""                                      # deprecated(US)

    # ========== 掃描池:Nasdaq-100 快照(D1) ==========
    # ✅ 以使用者 2026-06-11 季營收 seed 名單為準(99 檔,即實際 NDX 成分)。
    #   Nasdaq 每年 12 月重組+不定期調整;成分異動時:更新本清單 → 重跑 seed。
    #   - 已剔除確定變動:ANSS(2025/07 被 SNPS 併購下市)、SMCI(2024/12 剔除)
    #   - 上線前請與官方成分核對一次;之後每季回顧。
    #   - 防呆:download_stock_history 的品質檢查會自動略過抓不到的代號
    #     (失效成分只會少一檔,不會讓掃描崩潰)。
    SCAN_POOL = [
        'AAPL', 'ABNB', 'ADBE', 'ADI', 'ADP', 'ADSK', 'AEP', 'ALNY',
        'AMAT', 'AMD', 'AMGN', 'AMZN', 'APP', 'ARM', 'ASML', 'AVGO',
        'AXON', 'BKNG', 'BKR', 'CDNS', 'CEG', 'CHTR', 'CMCSA', 'COST',
        'CPRT', 'CRWD', 'CSCO', 'CSX', 'CTAS', 'CTSH', 'DASH', 'DDOG',
        'DXCM', 'EA', 'EXC', 'FANG', 'FAST', 'FER', 'FTNT', 'GEHC',
        'GILD', 'GOOGL', 'HON', 'IDXX', 'INSM', 'INTC', 'INTU', 'ISRG',
        'KDP', 'KHC', 'KLAC', 'LIN', 'LITE', 'LRCX', 'MAR', 'MCHP',
        'MDLZ', 'MELI', 'META', 'MNST', 'MPWR', 'MRVL', 'MSFT', 'MSTR',
        'MU', 'NFLX', 'NVDA', 'NXPI', 'ODFL', 'ORLY', 'PANW', 'PAYX',
        'PCAR', 'PDD', 'PEP', 'PLTR', 'PYPL', 'QCOM', 'REGN', 'ROP',
        'ROST', 'SBUX', 'SHOP', 'SNDK', 'SNPS', 'STX', 'TMUS', 'TRI',
        'TSLA', 'TTWO', 'TXN', 'VRSK', 'VRTX', 'WBD', 'WDAY', 'WDC',
        'WMT', 'XEL', 'ZS',
    ]

    # ========== 大盤指數 / 宏觀(D4, D5) ==========
    INDEX_TICKER            = "^GSPC"   # 主燈號指數(原 ^TWII 位)
    INDEX_TICKER_SECONDARY  = "^IXIC"   # 輔助(科技權重,池子主軸)
    SMALLCAP_TICKER         = "^RUT"    # 小型股(原櫃買 TPEx 位)
    VIX_TICKER              = "^VIX"    # 風險背景
    ES_FUTURES_TICKER       = "ES=F"    # S&P 期貨(盤前宏觀方向)
    NQ_FUTURES_TICKER       = "NQ=F"    # Nasdaq 期貨(盤前宏觀方向)
    MARKET_PROXY_ETF        = "SPY"     # 大盤盤前報價代理(原 TWSE MIS 位)

    # 大盤跳空分級(供 analyzers 跳空判讀;單位:百分比形式,1.0 = 1%)
    # ⚠️ 註:台股版 analyzers.py:1223 引用此二常數但 config 未定義(潛在
    #   AttributeError,被外層容錯吃掉)。美股版補上 — 起點值未校準(D8)。
    MARKET_GAP_HUGE   = 1.0    # |gap| > 1% → 大跳空
    MARKET_GAP_NORMAL = 0.3    # |gap| ≤ 0.3% → 平開區

    # 大盤趨勢權重(沿用台股 V13.13.0 規則形狀;名稱保留 TWII_* 供 analyzers 相容)
    # ⚠️ 校準歸零(D8):3 天/3% 是台股值,美股波動結構不同,n≥15 後再校準
    TWII_TREND_BULL_DAYS      = 3
    TWII_TREND_BEAR_DAYS      = 3
    TWII_TREND_PCT_THRESHOLD  = 0.03
    TWII_TREND_LOOKBACK_DAYS  = 5

    # VIX 背景分級(新增;只顯示、不投票,等實戰校準)
    VIX_ELEVATED_LEVEL = 20.0   # ≥ 20 → 風險偏高註記
    VIX_EXTREME_LEVEL  = 30.0   # ≥ 30 → 高壓警示

    # ES/NQ 期貨盤前方向顯著門檻(只顯示、不投票)
    FUTURES_SIG_PCT    = 0.5    # 隔夜 ±0.5% 視為有方向

    # DXY 美元指數(沿用台股 PR2-A;美股版同樣只當背景副標)
    DXY_TICKER          = "DX-Y.NYB"
    DXY_SIGNIFICANT_PCT = 0.3

    # ========== 小型股偏弱(原 P2 櫃買;名稱保留 OTC_* 供 analyzers 相容) ==========
    # 邏輯不變:^RUT 收盤 < 其 MA20 超過 2% → L2 降級 + 個股加註「⚠️ 小型股偏弱」
    OTC_INDEX_ENABLED      = True
    OTC_MA_PERIOD          = 20
    OTC_HISTORY_DAYS       = 60
    OTC_WEAKNESS_THRESHOLD = -0.02

    # ========== 個股盤前跳空(D7,新訊號) ==========
    # 實測:yfinance preMarketPrice 可靠(盤前「價」可得,「量」不可得)
    # v1 只顯示、不進評分;美股 P9 樣本 n≥15 後再決定權重
    PREMARKET_GAP_ENABLED     = True
    PREMARKET_GAP_SIG_PCT     = 2.0    # |gap| ≥ 2% → 顯著跳空註記
    PREMARKET_GAP_EXTREME_PCT = 5.0    # |gap| ≥ 5% → 極端跳空(多為財報/事件日)

    # ========== 季營收 YoY(D3,原 P4 月營收位) ==========
    # 來源:yfinance quarterly income statement(Total Revenue)
    # YoY 公式:最新季營收 / 去年同季營收 - 1(需 ≥ 5 季資料)
    # cache:seed 週更(sources.py --seed-revenue),非台股的每日 06:00
    QUARTER_REVENUE_CACHE_PATH       = "data/quarter_revenue_cache.json"
    QUARTER_REVENUE_CACHE_STALE_DAYS = 21    # 季頻資料,3 週內視為新鮮
    QUARTER_REVENUE_YOY_TIERS = [
        # (門檻下限, 加分, 標籤)— 級距沿用台股形狀,⚠️ 未經美股校準(D8)
        ( 0.50,  3, "🚀 季營收爆量+50%"),
        ( 0.30,  2, "✨ 季營收強勢+30%"),
        ( 0.10,  1, "📈 季營收成長+10%"),
        ( 0.00,  0, None),
        (-0.15, -1, "📉 季營收衰退"),
        (-1.00, -2, "🚨 季營收雪崩"),
    ]
    # 產業豁免:美股版先不豁免(台股豁免金融 YoY;美股金融多不在 NDX 內)
    REVENUE_EXCLUDED_STOCKS = set()

    # --- 相容別名(舊碼引用 MONTH_REVENUE_* 不崩) ---
    MONTH_REVENUE_CACHE_PATH        = QUARTER_REVENUE_CACHE_PATH
    MONTH_REVENUE_CACHE_STALE_DAYS  = QUARTER_REVENUE_CACHE_STALE_DAYS
    MONTH_REVENUE_YOY_TIERS         = QUARTER_REVENUE_YOY_TIERS
    MONTH_REVENUE_EXCLUDED_STOCKS   = REVENUE_EXCLUDED_STOCKS
    MONTH_REVENUE_COMBO_BONUS         = 1      # 組合拳保留(法人腿停用→實質不觸發)
    MONTH_REVENUE_COMBO_YOY_MIN       = 0.30
    MONTH_REVENUE_COMBO_INST_DAYS_MIN = 3

    # ========== 個股技術參數(原樣移植;美元計價) ==========
    MIN_PRICE_FILTER    = 10.0      # 低價股過濾(USD;NDX 內幾乎不觸發,保留通用性)
    # 台股「張」概念不適用;名稱保留、值改「千股」單位:
    # 1000(千股)= 1,000,000 股/日均量門檻(NDX 成分全數通過,保留防呆)
    MIN_AVG_VOLUME_LOTS = 1000
    THRESHOLD_VOL_RATIO = 1.2       # 量比門檻(日線量比,美股資料可靠 ✅)
    PRICE_LOW_PCT         = -0.20
    PRICE_HIGH_PCT        = 0.20
    PRICE_CONSOLIDATE_PCT = 0.08
    CONSOLIDATE_BUY_DAYS  = 5
    LOW_BUY_DAYS          = 3
    HIGH_BUY_DAYS         = 3

    # dist_tag ATR 動態化(V13.7.0 原樣移植 — 純技術,跨市場通用)
    DIST_SWEET_PCT        = 0.05    # ATR=0 fallback 用
    DIST_CAUTION_PCT      = 0.08
    DIST_SWEET_ATR_MULT   = 1.0
    DIST_CAUTION_ATR_MULT = 2.0

    # ATR% 死魚盤 floor(V13.9.6 原樣移植)
    ATR_PCT_FLOOR         = 0.02
    ATR_PCT_FLOOR_REPLACE = 0.03

    # 均線(V13.3.4 原樣移植)
    MA_SHORT_PERIOD = 20
    MA_LONG_PERIOD  = 60

    # ATR 動態停損(V13.6.0 P1 原樣移植)
    ATR_PERIOD              = 14
    ATR_STOP_MULT_DEFAULT   = 1.5
    ATR_STOP_MULT_TIGHT     = 1.2
    ATR_ENTRY_BUFFER_MULT   = 0.5
    ATR_ENTRY_PULLBACK_MULT = 1.0

    # ========== 主題池(D1:美股版,全部為 SCAN_POOL 子集) ==========
    # 規則不變:同主題 ≥2 檔 + 各 Priority ≥5 → 主題共振 + 各 +3 分
    THEME_POOLS = {
        'ai_semi':    ['NVDA', 'AMD', 'AVGO', 'MRVL', 'ARM', 'NXPI',
                       'ADI', 'TXN', 'QCOM', 'INTC', 'MCHP', 'MPWR'],
        'memory_storage': ['MU', 'SNDK', 'STX', 'WDC', 'LITE'],   # 記憶體/儲存(本輪 YoY 最強族群)
        'semi_eq':    ['ASML', 'AMAT', 'LRCX', 'KLAC'],
        'megacap':    ['AAPL', 'MSFT', 'GOOGL', 'META', 'AMZN'],
        'software':   ['ADBE', 'INTU', 'SNPS', 'CDNS', 'WDAY',
                       'DDOG', 'APP', 'PLTR', 'ADSK'],
        'cybersec':   ['PANW', 'CRWD', 'FTNT', 'ZS'],
        'biotech':    ['AMGN', 'VRTX', 'GILD', 'REGN', 'ALNY', 'INSM'],
        'consumer':   ['COST', 'PEP', 'MDLZ', 'SBUX', 'MNST', 'KDP',
                       'ROST', 'ORLY', 'WMT', 'KHC'],
        'datacenter_power': ['CEG', 'AEP', 'XEL', 'EXC'],   # AI 用電題材
        'fintech_crypto':   ['PYPL', 'MSTR'],
        'net_consumer':     ['NFLX', 'BKNG', 'ABNB', 'MELI', 'DASH',
                             'SHOP', 'PDD'],
    }
    THEME_BOOST_SCORE   = 3      # ⚠️ 沿用台股值,未經美股校準(D8)
    THEME_MA_SWITCH_PCT = 0.08

    # ========== 三大法人(D6:停用;常數保留供舊碼引用不崩) ==========
    INVESTMENT_TRUST_BUY_DAYS = 3
    FOREIGN_BUY_DAYS          = 3
    INVESTMENT_TRUST_MIN_LOTS = 500
    FOREIGN_MIN_LOTS          = 1000
    DEALER_HEDGE_SURGE_RATIO  = 2.0
    DEALER_HEDGE_MIN_LOTS     = 500

    # ========== 其他 ==========
    BATCH_SIZE = 20

    # ========== 資料源 Fallback(V13.6.0 形狀保留;v1 僅 yfinance) ==========
    # FinMind fallback 已移除;DATA_FALLBACK_ENABLED=False → download_stock_history
    # 直接回 yfinance 結果。未來若接 Alpha Vantage / Finnhub 備援,把開關打開
    # 並在 sources.download_stock_history 補第二腿即可(結構已留)。
    DATA_FALLBACK_ENABLED      = False
    DATA_MIN_BARS_PER_TICKER   = 50
    DATA_FALLBACK_MIN_RATIO    = 0.7
    FINMIND_PRICE_HISTORY_DAYS = 130    # deprecated(US),保留避免引用崩潰

    # ========== 精選設定(原樣移植) ==========
    TOP_N_RECOMMENDED   = 10
    MIN_PRIORITY_FOR_GO = 7
    FINMIND_MAX_WORKERS = 5             # deprecated(US),保留避免引用崩潰

    # ========== LLM enrichment(P7.5 原樣移植 — 架構沿用,新聞源換美股) ==========
    LLM_ENRICHMENT_ENABLED       = True
    LLM_MODEL                    = "gemini-2.5-flash"
    LLM_NEWS_DAYS                = 3
    LLM_NEWS_MAX_RESULTS         = 5
    LLM_ENRICHMENT_TOTAL_TIMEOUT = 180
    LLM_SUMMARY_COLUMN           = "LLM 摘要"   # Notion 欄位名(新 DB 同名即可)

    # ========== 執行時段護欄(原 V13.8.7;改 ET 視窗) ==========
    # 正常排程窗:美東 08:30–09:30 ET(盤前掃描);窗外執行加註警示
    SCAN_NORMAL_ET_HOUR_START = 8
    SCAN_NORMAL_ET_MIN_START  = 30
    SCAN_NORMAL_ET_HOUR_END   = 9
    SCAN_NORMAL_ET_MIN_END    = 30

    # ════════════════════════════════════════════════════════════
    # 以下為台股遺留常數(D2/D5/D6 停用區)。
    # 保留定義 = 讓尚未移植完的 analyzers/main 在「降級模式」下可運行
    # (對應 stub 一律回 ok=False / 空,該等分支自然不觸發)。
    # 美股版 analyzers/main 完成後,本區可整段刪除。
    # ════════════════════════════════════════════════════════════

    # --- 匯率區(D5 停用) ---
    FOREX_SIGNIFICANT_PCT     = 0.15
    CENTRAL_BANK_DEFENSE_LINE = 32.0
    FOREX_ASIA4_CURRENCIES    = ["USD", "CNY", "KRW", "JPY"]
    FOREX_MAJOR_CURRENCIES    = ["USD", "CNY", "KRW"]
    TWD_SURGE_THRESHOLD      = 0.03
    TWD_PLUNGE_THRESHOLD     = 0.03
    TWD_GAP_THRESHOLD        = 0.05
    TWD_CUMULATIVE_THRESHOLD = 0.05
    FOREX_5M_OUTPUTSIZE      = 60
    SIGNAL_CUMULATIVE_THRESHOLD = {"USD": 0.05, "CNY": 0.005,
                                   "KRW": 1.0,  "JPY": 0.08}
    FOREX_L2_WINDOW_HM = ["09:00", "09:05", "09:10", "09:15"]
    FOREX_L2_PRE_HM    = set()
    FOREX_L2_OPEN_HM   = {"09:00", "09:05", "09:10", "09:15"}
    FOREX_L2_API_START = "08:50:00"
    FOREX_L2_API_END   = "09:20:00"
    FOREX_NORMAL_HOUR_START = 6
    FOREX_NORMAL_HOUR_END   = 9
    FOREX_NORMAL_MIN_END    = 30
    SIGNAL_MAJORITY_THRESHOLD = 3
    SIGNAL_NOISE_THRESHOLD    = {"USD": 0.0, "CNY": 0.0, "KRW": 0.0, "JPY": 0.0}
    SIGNAL_FALLBACK_TO_LAYER1 = True

    # --- 期現貨區(D6 停用) ---
    BASIS_THRESHOLD_PCT       = 0.005
    BASIS_ALARM_PCT           = 0.015
    FOREIGN_SHORT_WATCH_LEVEL = 30000
    FOREIGN_SHORT_RED_LEVEL   = 50000
    VOL_SURGE_RATIO           = 1.5

    # --- 除息區(D6 停用) ---
    EX_DIV_MIN_POINTS    = 5.0
    EX_DIV_CALENDAR_PATH = "data/ex_div_calendar.json"
    EX_DIV_COVERAGE_PCT  = 0.0
    MAJOR_TAIEX_WEIGHTS  = {}

    # --- 融資區(D6 停用) ---
    MARGIN_BALANCE_CACHE_PATH       = "data/margin_balance_cache.json"
    MARGIN_BALANCE_FETCH_DAYS       = 14
    MARGIN_BALANCE_CACHE_STALE_DAYS = 3
    MARGIN_BALANCE_TIERS = [
        ( 0.05, "🔴", "融資爆衝", "散戶激進加碼,主力恐出貨"),
        ( 0.03, "🟠", "融資快增", "高風險區,留意主力動向"),
        ( 0.01, "🟡", "融資溫增", "散戶轉熱,適度減量"),
        (-0.01, None, None,       None),
        (-0.03, "🟢", "融資減量", "散戶撤退,中性偏多"),
        (-1.00, "🟢", "融資崩跌", "散戶恐慌出清,可能築底"),
    ]

    # --- 大盤開盤量結構區(D2 方向A 停用;C 階段再啟用並改開盤後資料) ---
    MIS_OPEN_VOLUME_CACHE_PATH        = "data/mis_open_volume.json"
    MIS_OPEN_VOLUME_CACHE_STALE_HOURS = 4
    VOL_STRUCTURE_SHRINK_RATIO        = 0.50
    VOL_STRUCTURE_EXPAND_RATIO        = 0.15
    VOL_STRUCTURE_HIGH_VOLUME_LOTS    = 999_999_999_999
    VOL_STRUCTURE_NOT_FALLING_GAP_PCT = -0.1
