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
  D9. 計分核心重寫(V1.2.0,2026-07-13 依第一份校準報告拍板):
      Baseline(n=20 定案):舊「爆量吸籌」主導精選 R期望值 −0.26/
      勝率 20%/停損率 25%;且 40 筆窗完樣本中 20 筆為已偏離無進場價。
      拍板:①吸籌 +3→0(純標記) ②新②腿 RSI<35+止穩(原「精準上穿+
      量縮同日」四週零命中) ③逢低三腿(③②守均線)加 YoY<0 基本面否決。
      常數見「V1.2.0 計分核心(D9)」區。

排程(對應台股 09:00-09:15 掃描窗):
  美東盤前 09:00-09:25 ET ≈ 台北 21:00-21:25(夏令 EDT)
                          ≈ 台北 22:00-22:25(冬令 EST)
  ⚠️ GitHub Actions cron 走 UTC 不跟 DST → 觸發端用 cron-job.org
     並把時區設 America/New_York,程式內再以 ET 視窗 gate。
═══════════════════════════════════════════════════════════════
"""
import os


class Config:
    # ========== 策略／量尺版本(V1.3 shadow) ==========
    SIGNAL_ENGINE_VERSION        = "v1.2.1"
    TRADE_PLAN_VERSION           = "v1.3.1-shadow"
    MEASUREMENT_VERSION          = "legacy-v0"
    SHADOW_MEASUREMENT_VERSION   = "v1.3.1-shadow"
    SNAPSHOT_SCHEMA_VERSION      = "v1.3.3"
    # 盤前跳空定義版本。v0(未版本化)= 分母誤用 Yahoo regularMarketPreviousClose
    # (實為 Close[-2]),等於多算一個交易日,已知無效、不得進統計。
    PREGAP_DEFINITION_VERSION    = "v1.3.2-signal-bar-close"
    TRADE_PLAN_TIME_EXIT_DAYS    = 40
    EPISODE_TUNING_MIN_COMPLETED = 60
    EPISODE_TUNING_TARGET        = 100
    EPISODE_SEGMENT_MIN_COMPLETED = 20

    # ========== API 金鑰 ==========
    NOTION_TOKEN     = os.environ.get("NOTION_TOKEN", "")
    NOTION_DB_ID     = os.environ.get("NOTION_DB_ID", "")     # ⚠️ 美股新 DB,勿沿用台股 DB
    TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    # 註:GEMINI_API_KEY / TAVILY_API_KEY 由 llm_enrichment.py 直接讀 env,
    #     不在此鏡射,避免多一份會過期的副本。

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

    # ========== 公司英文名(V1.3 P0,LLM enrichment 新聞檢索用) ==========
    # 只服務新聞檢索的 query 精準度,不進 strategy_config_hash 也不進
    # universe_version — 對選股、分數、Top 10 與 cohort identity 完全中性。
    #
    # 為什麼需要:Tavily 以純 ticker 搜尋在美股歧義極高 —— APP、EA、ARM、
    # MU、STX、LIN、FAST、KEY 這類代號同時是常用英文字或他義縮寫,只給
    # ticker 會撈回完全無關的報導。公司名 + ticker 併用可大幅收斂。
    #
    # 維護:與 SCAN_POOL 同步。成分異動時兩份一起改;查無名稱者
    # llm_enrichment 會 fallback 至純 ticker(不拋錯、不中斷掃描)。
    COMPANY_NAMES = {
        'AAPL':  'Apple',
        'ABNB':  'Airbnb',
        'ADBE':  'Adobe',
        'ADI':   'Analog Devices',
        'ADP':   'Automatic Data Processing',
        'ADSK':  'Autodesk',
        'AEP':   'American Electric Power',
        'ALNY':  'Alnylam Pharmaceuticals',
        'AMAT':  'Applied Materials',
        'AMD':   'Advanced Micro Devices',
        'AMGN':  'Amgen',
        'AMZN':  'Amazon.com',
        'APP':   'AppLovin',
        'ARM':   'Arm Holdings',
        'ASML':  'ASML Holding',
        'AVGO':  'Broadcom',
        'AXON':  'Axon Enterprise',
        'BKNG':  'Booking Holdings',
        'BKR':   'Baker Hughes',
        'CDNS':  'Cadence Design Systems',
        'CEG':   'Constellation Energy',
        'CHTR':  'Charter Communications',
        'CMCSA': 'Comcast',
        'COST':  'Costco Wholesale',
        'CPRT':  'Copart',
        'CRWD':  'CrowdStrike',
        'CSCO':  'Cisco Systems',
        'CSX':   'CSX Corporation',
        'CTAS':  'Cintas',
        'CTSH':  'Cognizant Technology Solutions',
        'DASH':  'DoorDash',
        'DDOG':  'Datadog',
        'DXCM':  'DexCom',
        'EA':    'Electronic Arts',
        'EXC':   'Exelon',
        'FANG':  'Diamondback Energy',
        'FAST':  'Fastenal',
        'FER':   'Ferrovial',
        'FTNT':  'Fortinet',
        'GEHC':  'GE HealthCare',
        'GILD':  'Gilead Sciences',
        'GOOGL': 'Alphabet',
        'HON':   'Honeywell',
        'IDXX':  'IDEXX Laboratories',
        'INSM':  'Insmed',
        'INTC':  'Intel',
        'INTU':  'Intuit',
        'ISRG':  'Intuitive Surgical',
        'KDP':   'Keurig Dr Pepper',
        'KHC':   'Kraft Heinz',
        'KLAC':  'KLA Corporation',
        'LIN':   'Linde',
        'LITE':  'Lumentum Holdings',
        'LRCX':  'Lam Research',
        'MAR':   'Marriott International',
        'MCHP':  'Microchip Technology',
        'MDLZ':  'Mondelez International',
        'MELI':  'MercadoLibre',
        'META':  'Meta Platforms',
        'MNST':  'Monster Beverage',
        'MPWR':  'Monolithic Power Systems',
        'MRVL':  'Marvell Technology',
        'MSFT':  'Microsoft',
        # 2025 年由 MicroStrategy 更名為 Strategy;舊名在新聞中仍高度通用,
        # 兩者併列可同時命中改名前後的報導。
        'MSTR':  'Strategy (MicroStrategy)',
        'MU':    'Micron Technology',
        'NFLX':  'Netflix',
        'NVDA':  'NVIDIA',
        'NXPI':  'NXP Semiconductors',
        'ODFL':  'Old Dominion Freight Line',
        'ORLY':  "O'Reilly Automotive",
        'PANW':  'Palo Alto Networks',
        'PAYX':  'Paychex',
        'PCAR':  'PACCAR',
        'PDD':   'PDD Holdings',
        'PEP':   'PepsiCo',
        'PLTR':  'Palantir Technologies',
        'PYPL':  'PayPal',
        'QCOM':  'Qualcomm',
        'REGN':  'Regeneron Pharmaceuticals',
        'ROP':   'Roper Technologies',
        'ROST':  'Ross Stores',
        'SBUX':  'Starbucks',
        'SHOP':  'Shopify',
        'SNDK':  'SanDisk',
        'SNPS':  'Synopsys',
        'STX':   'Seagate Technology',
        'TMUS':  'T-Mobile US',
        'TRI':   'Thomson Reuters',
        'TSLA':  'Tesla',
        'TTWO':  'Take-Two Interactive',
        'TXN':   'Texas Instruments',
        'VRSK':  'Verisk Analytics',
        'VRTX':  'Vertex Pharmaceuticals',
        'WBD':   'Warner Bros. Discovery',
        'WDAY':  'Workday',
        'WDC':   'Western Digital',
        'WMT':   'Walmart',
        'XEL':   'Xcel Energy',
        'ZS':    'Zscaler',
    }

    # ========== 大盤指數 / 宏觀(D4, D5) ==========
    INDEX_TICKER            = "^GSPC"   # 主燈號指數(原 ^TWII 位)
    INDEX_TICKER_SECONDARY  = "^IXIC"   # 輔助(科技權重,池子主軸)
    SMALLCAP_TICKER         = "^RUT"    # 小型股(原櫃買 TPEx 位)
    VIX_TICKER              = "^VIX"    # 風險背景
    ES_FUTURES_TICKER       = "ES=F"    # S&P 期貨(盤前宏觀方向)
    NQ_FUTURES_TICKER       = "NQ=F"    # Nasdaq 期貨(盤前宏觀方向)
    MARKET_PROXY_ETF        = "SPY"     # 大盤盤前報價代理(原 TWSE MIS 位)
    MARKET_TECH_PROXY_ETF   = "QQQ"     # 科技股環境快照，不進正式評分

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
    # v1 只顯示、不進評分;V1.3 起保存完整掃描母體,不再只留 Top10。
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

    # --- 組合拳(台股月營收位;法人腿停用→實質不觸發,但 analyzers 仍引用) ---
    MONTH_REVENUE_COMBO_BONUS         = 1
    MONTH_REVENUE_COMBO_YOY_MIN       = 0.30
    MONTH_REVENUE_COMBO_INST_DAYS_MIN = 3

    # ========== 個股技術參數(原樣移植;美元計價) ==========
    MIN_PRICE_FILTER    = 10.0      # 低價股過濾(USD;NDX 內幾乎不觸發,保留通用性)
    # ── 流動性門檻(V1.2.1,2026-07-28 取代台股「張」概念的股數門檻)──
    # 舊制 MIN_AVG_VOLUME_LOTS=1000(= 100 萬股/日)與真實流動性反向:
    # 實測排除 MPWR(11.45 億美元/日,高於全池中位數 7.5 億)等 6 檔高價股,
    # 卻留下全池唯一低於 1 億的 FER。改用與股價無關的成交金額。
    # 門檻是執行容量護欄,不做報酬最佳化:全池最低 90M,20M 有 4.5x 邊際。
    LIQUIDITY_LOOKBACK_DAYS   = 20          # 完整交易日
    LIQUIDITY_STATISTIC       = "median"    # 對單日暴量穩健
    MIN_DOLLAR_VOLUME_USD     = 20_000_000  # median(Close x Volume)
    THRESHOLD_VOL_RATIO = 1.2       # 量比門檻(日線量比,美股資料可靠 ✅)
    PRICE_LOW_PCT         = -0.20
    PRICE_HIGH_PCT        = 0.20
    PRICE_CONSOLIDATE_PCT = 0.08
    CONSOLIDATE_BUY_DAYS  = 5
    LOW_BUY_DAYS          = 3
    HIGH_BUY_DAYS         = 3

    # ========== V1.2.0 計分核心(D9,2026-07-13 校準拍板) ==========
    # 證據鏈:三週分布(量縮 ~6 檔/日、盤整低接 8/5/9 次/週、超賣反彈 0 命中)
    #        + 第一份校準報告(2026-07-12,n=20:R −0.26/勝率 20%)
    DIP_VOL_DRY_RATIO            = 0.7    # 量縮門檻(回檔週實測 17.4 檔/日,候選無虞)
    DIP_RSI_OVERSOLD             = 35.0   # ②腿 RSI 門檻(<30 在 NDX 大型股近零命中)
    SCORE_CONSOLIDATION_DIP      = 10     # ③ 盤整甜點位(低接)
    SCORE_OVERSOLD_BOUNCE        = 7      # ② 低檔超賣反彈
    DIP_OVERSOLD_VOL_DRY_BONUS   = 1      # ②腿量縮加分(量縮降為加分項,非必要條件)
    SCORE_HEALTHY_PULLBACK       = 5      # 守均線健康拉回(回測 MA 不破 + 翻紅)
    SCORE_SOUL_ACCUMULATION      = 0      # 🔥 靈魂吸籌:+3 → 0(純標記,保留觀察通道)
    DIP_REQUIRE_YOY_NON_NEGATIVE = True   # 基本面否決:YoY<0 不給逢低腿分(無資料不擋)

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

    # ========== 精選設定(原樣移植) ==========
    TOP_N_RECOMMENDED   = 10
    MIN_PRIORITY_FOR_GO = 7
    # 法人批次併發數。US v1 的 get_institutional_batch 是零成本 stub,此值
    # 目前不影響任何行為;保留呼叫是為了未來接上真實資料源時免改主流程。
    INSTITUTIONAL_BATCH_MAX_WORKERS = 5

    # ========== LLM enrichment(P7.5 — V1.3 P0 已完成美股化) ==========
    # 2026-08-06:query builder、include_domains 與 prompt 全面改美股口徑
    # (公司英文名 + ticker、美國財經媒體與新聞稿線、美股研究助手 prompt、
    # ET 時區),原「搜台股來源」的停用理由消失,故重新啟用。
    # 仍可用 env `LLM_ENRICHMENT_ENABLED=false` 即時快關,無需改碼重推。
    LLM_ENRICHMENT_ENABLED       = True
    LLM_MODEL                    = "gemini-2.5-flash"
    LLM_NEWS_DAYS                = 3
    LLM_NEWS_MAX_RESULTS         = 5
    # 180s 是台股版 5 檔的預算;美股 df_go 上限為 TOP_N_RECOMMENDED=10 檔,
    # 以每檔 ~21s(Tavily+Gemini+Notion)估算需 ~210s,舊值會讓末尾 1~2 檔
    # 靜默落入「整段超時」。300s 對 scan.yml 的 20 分鐘 job timeout 仍有大幅餘裕。
    LLM_ENRICHMENT_TOTAL_TIMEOUT = 300
    LLM_SUMMARY_COLUMN           = "LLM 摘要"   # Notion 欄位名(新 DB 同名即可)

    # ========== 執行時段護欄(原 V13.8.7;改 ET 視窗) ==========
    # 正常排程窗:美東 08:30–09:30 ET(盤前掃描);窗外執行加註警示
    PREMARKET_QUOTE_ET_HOUR_START = 4
    # Yahoo 最後一筆盤前成交若超過此分鐘數，視為 stale_quote。
    # 60 分鐘可涵蓋盤前較稀疏標的，同時不接受開盤前仍停在 04:xx 的舊價。
    PREMARKET_QUOTE_MAX_AGE_MINUTES = 60
    DAILY_BAR_FINALIZATION_BUFFER_MINUTES = 15
    SCAN_NORMAL_ET_HOUR_START = 8
    SCAN_NORMAL_ET_MIN_START  = 30
    SCAN_NORMAL_ET_HOUR_END   = 9
    SCAN_NORMAL_ET_MIN_END    = 30

    # ════════════════════════════════════════════════════════════
    # 台股遺留常數(D2/D5/D6 停用區)已於 2026-08-06 整段刪除。
    #
    # 原本保留是為了讓「尚未移植完的 analyzers/main」在降級模式下不會
    # AttributeError。實測全 repo 靜態掃描:匯率、期現貨、除息、融資、
    # 大盤開盤量結構共 40 個常數在 config.py 之外**零引用**,且全 repo
    # 沒有任何 getattr(Config, ...) 動態取值,故刪除不影響任何分支。
    #
    # sources.py 的 D2/D5/D6 stub(get_margin_balance_5d_change 等)未動:
    # 它們不讀這些常數,且 main.py 仍呼叫其中一支以保留未來接源的介面。
    # ════════════════════════════════════════════════════════════
