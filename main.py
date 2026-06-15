"""
美股盤前掃描系統 V1.0.0-US — 主程式
血統:台股 stock-scanner V13.13.8 main.py(817 行)→ 架構2移植(2026-06-09 拍板)

═══════════════════════════════════════════════════════════════
與台股版 run_scanner() 的差異(對齊 config.py 決策 D1–D8):
═══════════════════════════════════════════════════════════════
  流程對照:
    台股:匯率 → 台指期 → TWSE MIS → P7量結構 → 櫃買 → 掃描 → 主題 → 燈號
    美股:宏觀(ES/NQ+VIX+DXY)→ SPY盤前 → 小型股^RUT → 掃描 → 主題
         → 精選盤前跳空(D7新訊號) → 燈號
  ❌ 移除:匯率區塊、期現貨區塊、融資 header、P7 量結構(D2 方向A)、
          除息扣點、三大法人批次抓取(stub 直回空,呼叫保留供未來接源)
  ✅ 新增:精選檔盤前跳空標注(只對 df_go ≤10 檔呼叫,控 API 量)
  🔁 時區:所有「當日」判斷改美東 ET;Notion 護欄窗改 is_in_scan_window()
  🔁 季營收 YoY 取代月營收(讀同一 cache 介面,鍵名相容)
  ⚠️ D8:評分/燈號規則為台股形狀起點,未經美股校準;調整需 P9 樣本 n≥15
═══════════════════════════════════════════════════════════════
"""
import pandas as pd
import time
import traceback
import sys
import os                                          # 讀 FORCE_NOTION_SYNC env

from datetime import datetime

from config import Config
from sources import (
    get_tw_time, get_et_time, get_et_date,
    download_stock_batch, get_series, get_institutional_batch,
    get_market_premarket,                           # 原 get_twse_mis 位(SPY 盤前)
    download_stock_history,
    get_quarter_revenue_score,                      # 原月營收位(D3)
    get_premarket_quote,                            # D7 新訊號:個股盤前跳空
    is_in_scan_window,
    ET_TZ,
)
from analyzers import (
    analyze_macro,                                  # 取代 analyze_forex/futures
    analyze_market_open,
    analyze_institutional_for_stock,
    classify_stock_position, determine_status,
    calculate_atr, calculate_entry_stop_levels,
    classify_dist_tag,
    calculate_rsi, diagnose_dip_setup,              # 觀察期診斷(第一步)
    analyze_otc_index,                              # 小型股 ^RUT
)
from outputs import sync_notion, send_telegram

# LLM enrichment 獨立模組(失敗優雅,import 失敗不影響主流程 — 原 P7.5)
try:
    from llm_enrichment import run_llm_enrichment_phase
    _LLM_ENRICHMENT_AVAILABLE = True
except ImportError as _llm_e:
    print(f"  ⚠️  llm_enrichment 模組 import 失敗(P7.5):{_llm_e}")
    _LLM_ENRICHMENT_AVAILABLE = False
    def run_llm_enrichment_phase(*args, **kwargs):  # noqa
        return {"total": 0, "success": 0, "skipped": True, "elapsed_sec": 0, "details": []}


def _premarket_gap_note(gap_pct: float) -> str:
    """D7:精選檔盤前跳空標注(v1 只顯示、不進評分;n≥15 後再定權重)"""
    if abs(gap_pct) >= Config.PREMARKET_GAP_EXTREME_PCT:
        return f"🚨 盤前極端跳空 {gap_pct:+.1f}%(多為財報/事件,謹慎)"
    if abs(gap_pct) >= Config.PREMARKET_GAP_SIG_PCT:
        arrow = "⬆️" if gap_pct > 0 else "⬇️"
        return f"⚡ 盤前跳空{arrow} {gap_pct:+.1f}%"
    return ""


def run_scanner():
    start_time = time.time()
    print(f"\n{'='*55}")
    print(f"🚀 美股盤前掃描 V1.0.0-US  {get_et_time()} ET / {get_tw_time()} 台北")
    print(f"{'='*55}\n")

    # ========== 第一件事:盤前宏觀(原匯率+期現貨位) ==========
    macro = analyze_macro()

    # ========== 第二件事:SPY 盤前(原 TWSE MIS 位) ==========
    print("\n📊 抓取 SPY 盤前報價...")
    mis_data = get_market_premarket()
    if mis_data.get("ok"):
        print(f"  SPY {mis_data['price']:,.2f} ({mis_data['gap']:+.2f}, "
              f"{mis_data['gap_pct']:+.2f}%)  時間 {mis_data['trade_time']}")
    else:
        print(f"  ⚠️  SPY 盤前失敗:{mis_data.get('err', '')}")

    # 開盤情境(美股版真正接上 — 吃 SPY 盤前 gap;台股版此函式為死碼)
    market_open = analyze_market_open(mis_data)
    if market_open.get("tag"):
        print(f"  {market_open['tag']}  → {market_open['advice']}")

    # ========== 小型股位階(原 P2 櫃買位) ==========
    otc_data = analyze_otc_index()

    # ========== 第三件事:個股掃描 ==========
    print(f"\n🛡️  下載 {len(Config.SCAN_POOL)} 檔股價...")
    price_data, data_source = download_stock_history(Config.SCAN_POOL)
    if price_data.empty:
        print("❌ 股價下載失敗")
        return
    print(f"  📌 本次資料源:{data_source}")

    # 主題反查表 {ticker → theme_name}
    ticker_to_theme = {
        t: theme
        for theme, tickers in Config.THEME_POOLS.items()
        for t in tickers
    }

    # 市場廣度(多頭股數比例)
    bull_count = 0
    for t in Config.SCAN_POOL:
        hist = get_series(price_data, 'Close', t)
        if len(hist) >= 20:
            if float(hist.iloc[-1]) > float(hist.rolling(20).mean().iloc[-1]):
                bull_count += 1
    breadth_pct = bull_count / len(Config.SCAN_POOL)
    print(f"  市場廣度:{bull_count}/{len(Config.SCAN_POOL)} = {breadth_pct:.1%}")

    # 法人批次(US v1:stub 直回空 dict,零成本;保留呼叫供未來接源免改流程)
    inst_cache = get_institutional_batch(
        Config.SCAN_POOL,
        days=10,
        max_workers=Config.FINMIND_MAX_WORKERS,
    )

    # 個股掃描迴圈
    print(f"\n🔍 逐股分析...")
    results = []

    # 「當日」判斷改美東:盤前掃描時 yfinance 日線通常尚無今日 bar,
    # 但保留防呆 — 若出現今日盤中部分資料,一律剔除,確保用 T-1 完整日線
    et_now        = datetime.now(ET_TZ)
    today_str     = et_now.strftime('%Y-%m-%d')
    market_closed = et_now.hour >= 17   # 美東 17:00 後視為收盤完整

    for idx, ticker in enumerate(Config.SCAN_POOL, 1):
        try:
            hist = get_series(price_data, 'Close',  ticker)
            vol  = get_series(price_data, 'Volume', ticker)
            high = get_series(price_data, 'High',   ticker)
            low  = get_series(price_data, 'Low',    ticker)
            open_data = get_series(price_data, 'Open', ticker)

            # 排除當日盤中即時資料
            if not market_closed and len(hist) > 0 and str(hist.index[-1].date()) == today_str:
                hist      = hist.iloc[:-1]
                vol       = vol.iloc[:-1]       if len(vol)       > 0 else vol
                high      = high.iloc[:-1]      if len(high)      > 0 else high
                low       = low.iloc[:-1]       if len(low)       > 0 else low
                open_data = open_data.iloc[:-1] if len(open_data) > 0 else open_data

            # 需要 MA60 故要求至少 60 筆歷史資料
            if len(hist) < Config.MA_LONG_PERIOD or len(vol) < 5:
                continue

            price = float(hist.iloc[-1])
            if price < Config.MIN_PRICE_FILTER:
                continue

            # 流動性過濾 — 日均量 < 100 萬股直接跳過(NDX 內幾乎不觸發,保留防呆)
            avg_volume = float(vol.tail(20).mean())
            if avg_volume < Config.MIN_AVG_VOLUME_LOTS * 1000:
                continue

            # 雙均線 — MA20 短期支撐 / MA60 位階判定(月線)
            ma20 = round(float(hist.rolling(Config.MA_SHORT_PERIOD).mean().iloc[-1]), 2)
            ma60 = round(float(hist.rolling(Config.MA_LONG_PERIOD).mean().iloc[-1]), 2)
            vol_ratio = round(float(vol.iloc[-1] / vol.mean()), 2)

            # 14 日 ATR 與股性百分比
            atr     = calculate_atr(high, low, hist, period=Config.ATR_PERIOD)
            atr_pct = round((atr / price) * 100, 1) if price > 0 else 0.0

            is_black_k = (len(open_data) > 0 and
                          price < float(open_data.iloc[-1]) * 0.99)

            # 主題股趨勢模式 — 偏離 MA60 > THEME_MA_SWITCH_PCT 時改用 MA20
            # (AI/半導體強勢股走「均線多頭排列不回月線」型態,與台股主題股同理)
            theme_name   = ticker_to_theme.get(ticker)
            dist_ma60    = (price - ma60) / ma60 if ma60 > 0 else 0
            use_ma20_ref = (theme_name is not None and
                            dist_ma60 > Config.THEME_MA_SWITCH_PCT)
            pos_ref      = ma20 if use_ma20_ref else ma60
            pos_ref_label = "MA20(主題趨勢)" if use_ma20_ref else "MA60"

            position = classify_stock_position(price, pos_ref)
            if use_ma20_ref:
                print(f"    🎯 [{ticker}] 主題:{theme_name} 偏離MA60 "
                      f"{dist_ma60*100:+.1f}% → 改用{pos_ref_label}判位階")

            broker_info = analyze_institutional_for_stock(
                ticker, inst_cache.get(ticker)
            )

            status, priority = determine_status(
                position, broker_info, vol_ratio, is_black_k
            )

            five_day_high = round(float(high.tail(5).max()), 2) if len(high) >= 5 else price
            resistance    = max(five_day_high, ma20)

            hits = broker_info.get("key_broker_hits", [])
            broker_note = ""
            if hits:
                names = [
                    f"{h['name']}連買{h['consec_days']}天(+{int(h['total_lot'])}張)"
                    for h in hits[:2]
                ]
                broker_note = " | " + "、".join(names)

            # ========== 季營收 YoY 加分(D3,原 P4 月營收位) ==========
            # cache 介面與台股版相同;組合拳(法人腿)在 US v1 stub 下自然不觸發
            mr_note = ""
            mr_score = 0
            mr = get_quarter_revenue_score(ticker)
            if mr.get("ok"):
                mr_score = mr["score"]
                max_consec = broker_info.get("max_consec_days", 0)
                if (mr["yoy"] >= Config.MONTH_REVENUE_COMBO_YOY_MIN
                        and max_consec >= Config.MONTH_REVENUE_COMBO_INST_DAYS_MIN):
                    mr_score += Config.MONTH_REVENUE_COMBO_BONUS

                priority += mr_score
                if mr.get("tag"):
                    mr_note = f" | {mr['tag']}({mr['yoy']*100:+.1f}%)"

            # DistTag(ATR 倍數動態)+ 進場/停損
            dist_to_ma60 = (price - ma60) / ma60 if ma60 > 0 else 0
            dist_tag, dist_direction, dist_atr_mult = classify_dist_tag(
                price=price, ma60=ma60, atr=atr
            )
            entry_low, entry_high, stop_loss = calculate_entry_stop_levels(
                price=price, ma60=ma60, atr=atr,
                dist_tag=dist_tag, direction=dist_direction
            )

            # ── 觀察期診斷(第一步,D8:純記錄不計分,為第二階段計分腿蒐證)──
            rsi      = calculate_rsi(hist, period=14)
            rsi_prev = calculate_rsi(hist.iloc[:-1], period=14) if len(hist) > 15 else -1.0
            dip = diagnose_dip_setup(
                close=hist, vol=vol, price=price, ma20=ma20, ma60=ma60,
                rsi=rsi, rsi_prev=rsi_prev, vol_ratio=vol_ratio, dist_tag=dist_tag,
            )
            # ───────────────────────────────────────────────────────────

            results.append({
                'Ticker':      ticker,
                'Price':       price,
                'MA20':        ma20,
                'MA60':        ma60,
                'VolRatio':    vol_ratio,
                'Support':     ma60,
                'Resistance':  resistance,
                'Position':    position,
                'Status':      status + broker_note + mr_note,
                'Priority':    priority,
                'Score':       priority + vol_ratio,
                'ConsecDays':  broker_info.get("max_consec_days", 0),
                'DistMA60Pct': round(dist_to_ma60 * 100, 1),
                'DistTag':     dist_tag,
                'EntryLow':    entry_low,
                'EntryHigh':   entry_high,
                'StopLoss':    stop_loss,
                'ATR':         round(atr, 2),
                'ATR_Pct':     atr_pct,
                'DistDirection': dist_direction,
                'DistATRMult':   round(dist_atr_mult, 2),
                'YoY':           mr.get("yoy") if mr.get("ok") else None,
                'PreGapPct':     None,   # D7:精選後才補抓(控 API 量)
                # ── 觀察期診斷欄(第一步,純記錄)──
                'RSI':           rsi,
                'VolDry':        int(dip["vol_dry"]),
                'NearMA60':      int(dip["near_ma60"]),
                'Oversold':      int(dip["oversold"]),
                'RsiTurnUp':     int(dip["rsi_turn_up"]),
                'HoldMA':        int(dip["hold_ma"]),
                'SetupType':     dip["setup_type"],
            })

            if idx % 20 == 0:
                print(f"  已分析 {idx}/{len(Config.SCAN_POOL)} 檔")
        except Exception as e:
            print(f"  ⚠️  {ticker} 處理失敗:{e}")
            continue

    df_all = pd.DataFrame(results).sort_values('Score', ascending=False)

    # ========== 主題共振加分(規則原樣;標籤換美股 D1) ==========
    ticker_themes_map = {}    # {ticker: ['🧠 AI半導體', ...]}
    THEME_LABELS = {
        # theme_key: (TG/Notion Status 用長標, Notion 主題共振 multi-select 用短標)
        'ai_semi':          ('🧠 AI半導體共振',   '🧠 AI半導體'),
        'memory_storage':   ('💾 記憶體儲存共振', '💾 記憶體儲存'),
        'semi_eq':          ('🔧 半導體設備共振', '🔧 半導體設備'),
        'megacap':          ('🏛️ 超大型權值共振', '🏛️ 權值'),
        'software':         ('💻 軟體雲端共振',   '💻 軟體雲端'),
        'cybersec':         ('🔒 資安共振',       '🔒 資安'),
        'biotech':          ('🧬 生技共振',       '🧬 生技'),
        'consumer':         ('🛒 消費共振',       '🛒 消費'),
        'datacenter_power': ('⚡ AI電力共振',     '⚡ AI電力'),
        'fintech_crypto':   ('🪙 金融科技共振',   '🪙 金融科技'),
        'net_consumer':     ('🌐 網路消費共振',   '🌐 網路消費'),
    }

    if not df_all.empty:
        theme_triggered = {}   # {theme_name: [idx_list]}
        for i, row in df_all.iterrows():
            t = ticker_to_theme.get(row['Ticker'])
            if t and row['Priority'] >= 5:
                theme_triggered.setdefault(t, []).append(i)

        for theme, idxs in theme_triggered.items():
            if len(idxs) >= 2:
                boost = Config.THEME_BOOST_SCORE
                theme_label, theme_short = THEME_LABELS.get(
                    theme, (f'🔥 {theme}共振', f'🔥 {theme}')
                )
                for i in idxs:
                    df_all.at[i, 'Score']    += boost
                    df_all.at[i, 'Priority'] += boost
                    old_status = df_all.at[i, 'Status']
                    if theme_label not in old_status:
                        df_all.at[i, 'Status'] = old_status + f' | {theme_label}'
                    tk = df_all.at[i, 'Ticker']
                    if theme_short not in ticker_themes_map.get(tk, []):
                        ticker_themes_map.setdefault(tk, []).append(theme_short)
                print(f"  🎯 {theme_label}:{len(idxs)} 檔同時觸發 → 各 +{boost} 分")

        df_all = df_all.sort_values('Score', ascending=False)

    # ========== 精選 ==========
    df_go_raw = df_all[df_all['Priority'] >= Config.MIN_PRIORITY_FOR_GO]
    df_go     = df_go_raw.head(Config.TOP_N_RECOMMENDED)
    df_warn   = df_all[df_all['Priority'] < 0]

    print(f"\n📊 分析結果:")
    print(f"  符合進場門檻(priority>={Config.MIN_PRIORITY_FOR_GO}):{len(df_go_raw)} 檔")
    print(f"  精選 Top {Config.TOP_N_RECOMMENDED}:{len(df_go)} 檔")
    print(f"  反向警告:{len(df_warn)} 檔")
    for _, row in df_go.iterrows():
        print(f"  ✓ {row['Ticker']:8s}  {row['Price']:9.2f}  量比:{row['VolRatio']:.2f}  {row['Status']}")

    # ========== D7:精選檔盤前跳空(只打 df_go ≤10 檔,控 API 量) ==========
    if Config.PREMARKET_GAP_ENABLED and len(df_go) > 0:
        print(f"\n⚡ 精選檔盤前跳空檢查({len(df_go)} 檔)...")
        for i, row in df_go.iterrows():
            tk = row['Ticker']
            try:
                q = get_premarket_quote(tk)
                if q.get("ok") and q.get("session") == "premarket":
                    gp = q["gap_pct"]
                    df_go.at[i, 'PreGapPct'] = gp
                    note = _premarket_gap_note(gp)
                    if note:
                        df_go.at[i, 'Status'] = row['Status'] + f" | {note}"
                        print(f"  {tk:8s} {note}")
                    else:
                        print(f"  {tk:8s} 盤前 {gp:+.2f}%(平穩)")
                else:
                    print(f"  {tk:8s} 無盤前報價({q.get('err', q.get('session', ''))})")
            except Exception as e:
                print(f"  ⚠️  {tk} 盤前跳空檢查失敗:{e}")
            time.sleep(0.3)

    # ========== 決策摘要(前移供 Notion 大盤燈號用 — 原 V13.9.3) ==========
    decision = {}
    try:
        from analyzers import generate_decision_summary
        decision = generate_decision_summary(macro, market_open, df_go,
                                             otc=otc_data)
    except Exception as e:
        print(f"  ⚠️  決策摘要計算失敗:{e}")
    final_light = decision.get("FINAL", {}).get("light", "🟡") if decision else "🟡"

    # ========== 同步 Notion(護欄沿用 V13.9.5 哲學,窗改 ET) ==========
    print("\n📤 同步 Notion (daily_picks)...")
    synced = 0
    macro_tag = macro.get("tag", "未知")            # 原 forex_tag 槽位
    chip_tag  = "⚖️ 籌碼中性"                       # 法人停用,固定中性(欄位相容)

    _in_window = is_in_scan_window()
    _force     = os.environ.get('FORCE_NOTION_SYNC', 'false').lower() == 'true'

    if not _in_window and not _force:
        _et = datetime.now(ET_TZ)
        print(f"  ⏰ 非排程時段({_et.strftime('%H:%M')} ET,護欄窗 "
              f"{Config.SCAN_NORMAL_ET_HOUR_START:02d}:{Config.SCAN_NORMAL_ET_MIN_START:02d}-"
              f"{Config.SCAN_NORMAL_ET_HOUR_END:02d}:{Config.SCAN_NORMAL_ET_MIN_END:02d})")
        print(f"  → 跳過 Notion sync,避免污染 daily_picks")
        print(f"  → 若需強制寫入,workflow_dispatch 勾選 force_notion_sync=true")
    else:
        if _force and not _in_window:
            print(f"  ⚠️  非排程時段但 force_notion_sync=true → 強制寫入 Notion")

        # 小型股偏弱 + 主題股 → 加註提醒(原 P2 規則,文案換美股)
        otc_weak = bool(otc_data.get('ok') and otc_data.get('weak'))
        for idx_row, row in df_go.iterrows():
            row_dict = row.to_dict()
            status   = row_dict['Status']

            if otc_weak and ticker_to_theme.get(row_dict['Ticker']):
                status = status + " | ⚠️ 小型股偏弱"

            row_dict['Status'] = status

            themes = ticker_themes_map.get(row_dict.get('Ticker', ''), [])

            if sync_notion(
                row_dict, macro_tag, 0, chip_tag,
                scan_date=today_str,
                market_light=final_light,
                themes=themes,
            ):
                synced += 1
            time.sleep(0.3)
        print(f"  已同步 {synced}/{len(df_go)} 筆")

    # ========== LLM 精選報告 enrichment(原 P7.5,護欄一致) ==========
    if _LLM_ENRICHMENT_AVAILABLE and (synced > 0) and (_in_window or _force):
        try:
            llm_picks = []
            for _, row in df_go.iterrows():
                llm_picks.append({
                    "stock_code": row.get("Ticker", ""),
                    "stock_name": "",
                })
            llm_stats = run_llm_enrichment_phase(llm_picks, today_str)
            print(f"  📊 LLM enrichment 結果:{llm_stats['success']}/{llm_stats['total']} "
                  f"成功(耗時 {llm_stats.get('elapsed_sec', 0)}s)")
        except Exception as _e:
            print(f"  ⚠️  LLM enrichment 整段例外:{_e}(主流程不受影響)")
    elif not _LLM_ENRICHMENT_AVAILABLE:
        print("  ⏸  LLM enrichment 模組未載入,跳過 P7.5")
    elif synced == 0:
        print("  ⏸  Notion 未寫入(synced=0),跳過 LLM enrichment")
    else:
        print("  ⏸  非排程時段且未強制 → 跳過 LLM enrichment")

    elapsed = round(time.time() - start_time, 1)

    # ========== 組裝 Telegram 訊息 ==========
    # 宏觀區塊(原匯率+期現貨位)
    vix_str = (f"  VIX {macro['vix_level']:.1f} {macro.get('vix_tag', '')}\n"
               if macro.get('vix_level') is not None else "")
    dxy_str = (f"  DXY {macro.get('dxy_chg', 0):+.2f}%({macro.get('dxy_dir', '')})\n"
               if macro.get('ok') else "")
    macro_block = (
        f"<b>盤前宏觀</b>  {macro.get('tag', '')}\n"
        f"  ES {macro.get('es_chg', 0):+.2f}%   NQ {macro.get('nq_chg', 0):+.2f}%\n"
        f"{vix_str}"
        f"{dxy_str}"
    )

    # SPY 盤前區塊(原 TWSE MIS 區塊;分級沿用 ±0.3/±1.0)
    if mis_data.get("ok"):
        mis_px  = mis_data['price']
        mis_gap = mis_data['gap']
        mis_pct = mis_data['gap_pct']
        sign    = "+" if mis_gap >= 0 else ""
        if mis_pct >= 1.0:
            mis_scenario = "🔴 大幅開高 ⚠️ 追高陷阱"
        elif mis_pct >= 0.3:
            mis_scenario = "🟠 開高"
        elif mis_pct >= -0.3:
            mis_scenario = "🟡 平盤附近"
        elif mis_pct >= -1.0:
            mis_scenario = "🟠 開低"
        else:
            mis_scenario = "🔴 大幅開低 ⚠️ 警惕殺低"
        mis_block = (
            f"<b>SPY 盤前</b>  {mis_px:,.2f} ({sign}{mis_gap:.2f}, {sign}{mis_pct:.2f}%)\n"
            f"  {mis_scenario}\n\n"
        )
    else:
        mis_block = ""

    # 資料源標示
    source_emoji = {'yfinance': '🟢', 'mixed': '🟠'}.get(data_source, '⚪')
    source_line = f"  📡 資料源:{source_emoji} {data_source}  |  廣度 {breadth_pct:.0%}\n"

    # 小型股區塊(原櫃買區塊)
    if otc_data.get('ok'):
        otc_block = (
            f"<b>小型股 ^RUT</b>  {otc_data['tag']}\n"
            f"  {otc_data['price']:,.2f}  MA{Config.OTC_MA_PERIOD} "
            f"{otc_data['ma']:,.2f}  ({otc_data['dist_ma_pct']:+.2%})\n\n"
        )
    else:
        otc_block = ""

    # 執行時段護欄(窗外執行加註提示 — 原 V13.8.7 哲學)
    stale_line = ""
    if not _in_window:
        stale_line = ("<i>⚠️ 非盤前排程時段執行,資料非當日盤前即時</i>\n\n")

    msg = (
        f"<b>📊 美股盤前監控 V1.0.0-US  {get_tw_time()} 台北</b>\n"
        f"<i>{get_et_time()} ET</i>\n\n"
        f"{stale_line}"
        f"{macro_block}"
        f"{source_line}\n"
        f"{mis_block}"
        f"{otc_block}"
        f"<b>精選 {len(df_go)} 檔</b>\n"
    )
    for _, row in df_go.iterrows():
        entry_low  = row.get('EntryLow', 0)
        entry_high = row.get('EntryHigh', 0)
        stop_loss  = row.get('StopLoss', 0)
        atr_pct    = row.get('ATR_Pct', 0)
        atr_tag    = f" ATR{atr_pct:.1f}%" if atr_pct > 0 else ""
        if entry_low > 0 and entry_high > 0:
            entry_text = f" 進{entry_low:g}-{entry_high:g} 損{stop_loss:g}"
        else:
            entry_text = f" 損{stop_loss:g}"
        msg += (f"• {row['Ticker']:6s} | {row['DistTag']} {row['DistMA60Pct']:+.1f}%"
                f"{atr_tag}{entry_text}\n"
                f"    {row['Status']}\n")

    # 決策摘要
    try:
        if decision:
            msg += (
                f"\n<b>🎯 決策摘要</b>\n"
                f"  {decision['L1']['light']} {decision['L1']['name']}:{decision['L1']['reason']}\n"
                f"     → {decision['L1']['advice']}\n"
                f"  {decision['L2']['light']} {decision['L2']['name']}:{decision['L2']['reason']}\n"
                f"     → {decision['L2']['advice']}\n"
                f"  {decision['L3']['light']} {decision['L3']['name']}:{decision['L3']['reason']}\n"
                f"     → {decision['L3']['advice']}\n"
                f"\n"
                f"<b>{decision['FINAL']['light']} 結論</b>:{decision['FINAL']['advice']}\n"
            )
    except Exception as e:
        print(f"  ⚠️  決策摘要生成失敗:{e}")

    msg += f"\n⏱ 耗時 {elapsed}s"
    send_telegram(msg)

    # 儲存 CSV
    df_all.to_csv("scan_result.csv", index=False, encoding='utf-8-sig')
    print(f"\n💾 scan_result.csv 已儲存")
    print(f"✅ 完成!耗時 {elapsed}s\n")


if __name__ == "__main__":
    try:
        run_scanner()
    except Exception as e:
        print("\n" + "=" * 55)
        print(f"❌ 主程式崩潰:{type(e).__name__}: {e}")
        print("=" * 55)
        traceback.print_exc()
        # 即使崩潰也寫入空 CSV,避免 upload-artifact 找不到檔案
        with open("scan_result.csv", "w", encoding="utf-8-sig") as f:
            f.write("Ticker,Price,MA20,MA60,VolRatio,Support,Resistance,Position,Status,"
                    "Priority,Score,ConsecDays,DistMA60Pct,DistTag,EntryLow,EntryHigh,"
                    "StopLoss,ATR,ATR_Pct,DistDirection,DistATRMult,YoY,PreGapPct,"
                    "RSI,VolDry,NearMA60,Oversold,RsiTurnUp,HoldMA,SetupType\n")
            f.write(f"ERROR,0,0,0,0,0,0,-,{type(e).__name__}: {e},-99,-99,0,0,-,0,0,0,0,0,,0,,,"
                    "-1,0,0,0,0,0,none\n")
        sys.exit(1)
