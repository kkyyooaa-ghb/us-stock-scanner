"""
分析模組(美股版)V1.0.0-US
血統:台股 stock-scanner V13.13.8 analyzers.py(1939 行)→ 架構2移植

═══════════════════════════════════════════════════════════════
本檔與台股版的對照(對齊 config.py 決策 D1–D8):
═══════════════════════════════════════════════════════════════
  ❌ 刪除 analyze_forex()(865 行):亞洲匯率共振為台股出口商邏輯(D5)
  ❌ 刪除 analyze_futures()(275 行):台指期 basis/外資空單為台股專屬(D6)
  ✅ 新增 analyze_macro():ES/NQ 期貨 + VIX + DXY 盤前宏觀背景
      — v1 只顯示、不投權重(校準歸零 D8,等美股樣本 n≥15)
  🔁 analyze_market_open():改吃 SPY 盤前 gap(原版吃 idx_df 且 main 從未呼叫
      = 台股版死碼;美股版真正接上)
  🔁 analyze_otc_index():櫃買 → 小型股 ^RUT,降級邏輯不變(綠→黃)
  🔁 generate_decision_summary():L1 改「指數趨勢+期貨+VIX」,
      L2 改「SPY 盤前情境+小型股偏弱」,L3 不變(甜點價計數)
  ✅ 原樣移植:analyze_institutional_for_stock(吃 stub 空資料自動 no-op)、
      calculate_atr、get_effective_atr、classify_dist_tag、
      calculate_entry_stop_levels、classify_stock_position、determine_status
      — 純技術核心,跨市場通用,一行未改(含 V13.9.6 ATR floor、V13.7.0 方向)
═══════════════════════════════════════════════════════════════
"""
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Literal

from config import Config
from sources import (
    get_series, get_index_data, yf_close_series,
    get_institutional_for_stock,                 # stub:回空 df,函式自動 no-op
    get_futures_macro, get_vix_daily, get_dxy_daily,
    ET_TZ,
)


# ==========================================
# V1.0.0-US:盤前宏觀背景(取代 analyze_forex + analyze_futures 的 L1 輸入位)
# ==========================================
def analyze_macro() -> dict:
    """
    美股盤前宏觀:ES=F / NQ=F 期貨隔夜方向 + VIX 水位 + DXY 背景。

    設計(D5/D8):
      - v1 全部「只顯示、不投權重」:bias 僅供 L1 reasons 與 TG 顯示,
        不像台股匯率共振那樣直接決定紅綠燈(那套門檻是台股校準的)
      - 等美股 P9 樣本 n≥15 後,再決定 ES/NQ/VIX 是否進入燈號投票

    回傳:
      ok / tag(一行摘要,給 TG header 與 Notion forex_tag 槽位)
      bias("bull"/"bear"/"neutral")/ reasons(list)
      es_chg / nq_chg / vix_level / vix_tag / dxy_dir / dxy_chg
    """
    print("🌐 盤前宏觀背景(ES/NQ + VIX + DXY)...")
    out = {
        "ok": False, "tag": "⚪ 宏觀資料缺", "bias": "neutral",
        "reasons": [], "es_chg": 0.0, "nq_chg": 0.0,
        "vix_level": None, "vix_tag": "", "dxy_dir": "flat", "dxy_chg": 0.0,
    }

    # --- ES/NQ 期貨 ---
    fut = get_futures_macro()
    if fut.get("ok"):
        es_chg = fut["es"]["chg_pct"]
        nq_chg = fut["nq"]["chg_pct"]
        out["es_chg"], out["nq_chg"] = es_chg, nq_chg
        sig = Config.FUTURES_SIG_PCT
        if es_chg >= sig and nq_chg >= sig:
            out["bias"] = "bull"
            out["reasons"].append(f"期貨偏多 ES{es_chg:+.1f}%/NQ{nq_chg:+.1f}%")
        elif es_chg <= -sig and nq_chg <= -sig:
            out["bias"] = "bear"
            out["reasons"].append(f"期貨偏空 ES{es_chg:+.1f}%/NQ{nq_chg:+.1f}%")
        elif abs(es_chg) >= sig or abs(nq_chg) >= sig:
            out["reasons"].append(f"期貨分歧 ES{es_chg:+.1f}%/NQ{nq_chg:+.1f}%")
        print(f"  ES {es_chg:+.2f}%  NQ {nq_chg:+.2f}%")
    else:
        print(f"  ⚠️  ES/NQ 失敗:{fut.get('err', '')}")

    # --- VIX ---
    vix = get_vix_daily()
    if vix.get("ok"):
        out["vix_level"] = vix["level"]
        out["vix_tag"] = vix.get("tag", "")
        if vix.get("tag"):
            out["reasons"].append(f"{vix['tag']} {vix['level']:.1f}")
        print(f"  VIX {vix['level']:.1f} ({vix['chg_pct']:+.1f}%) {vix.get('tag', '')}")
    else:
        print(f"  ⚠️  VIX 失敗:{vix.get('err', '')}")

    # --- DXY(背景副標,沿用台股 PR2-A 哲學:不投票) ---
    dxy = get_dxy_daily()
    if dxy.get("ok"):
        out["dxy_dir"] = dxy.get("dir", "flat")
        out["dxy_chg"] = dxy.get("chg_pct", 0.0)
        print(f"  DXY {dxy['last']:.2f} ({dxy['chg_pct']:+.2f}%, {out['dxy_dir']})")

    # --- 一行摘要 tag ---
    ok_any = fut.get("ok") or vix.get("ok")
    out["ok"] = bool(ok_any)
    if not ok_any:
        return out

    if out["vix_level"] is not None and out["vix_level"] >= Config.VIX_EXTREME_LEVEL:
        out["tag"] = f"🚨 VIX {out['vix_level']:.0f} 高壓"
    elif out["bias"] == "bull":
        out["tag"] = f"🟢 期貨偏多(ES{out['es_chg']:+.1f}%)"
    elif out["bias"] == "bear":
        out["tag"] = f"🔴 期貨偏空(ES{out['es_chg']:+.1f}%)"
    else:
        out["tag"] = "🟡 宏觀中性"
    if out["vix_tag"] and "VIX" not in out["tag"]:
        out["tag"] += f" | {out['vix_tag']}"
    return out


# ==========================================
# 開盤情境判定(美股版:吃 SPY 盤前 gap)
# 註:台股版吃 idx_df 但 main 從未呼叫(死碼);美股版真正接上 —
#    main 把 get_market_premarket() 的結果傳進來,依 gap_pct 分級。
# ==========================================
def analyze_market_open(mis_data: dict) -> dict:
    """
    依 SPY 盤前 gap 判定開盤情境。
    參數:mis_data = get_market_premarket() 回傳(gap_pct 為百分比形式,-0.74 = -0.74%)
    回傳:scenario / tag / advice / gap_pct(沿用台股鍵名,L2 與 TG 共用)
    """
    try:
        if not mis_data or not mis_data.get("ok"):
            return {"scenario": "unknown", "tag": "", "advice": "", "gap_pct": 0}

        gap_pct = float(mis_data.get("gap_pct", 0.0))   # 百分比形式

        if gap_pct > Config.MARKET_GAP_HUGE:
            scenario = "huge_gap_up"
            tag = "🚫 追高陷阱"
            advice = f"SPY 盤前 {gap_pct:+.2f}% 跳空,🎯 甜點價也不追,等回測"
        elif gap_pct < -Config.MARKET_GAP_HUGE:
            scenario = "panic"
            tag = "🚫 恐慌殺盤"
            advice = f"SPY 盤前 {gap_pct:+.2f}% 急殺,全套訊號失效,開盤 30 分後再看"
        elif abs(gap_pct) <= Config.MARKET_GAP_NORMAL:
            scenario = "normal"
            tag = "✅ 正常進場"
            advice = "可依 3 色標籤正常執行(🎯 甜點價優先)"
        elif gap_pct < 0:
            scenario = "minor_dip"
            tag = "🟡 短線測試"
            advice = "可少量試單,觀察開盤後量能"
        else:
            scenario = "mild_gap_up"
            tag = "🟠 溫和開高"
            advice = "🎯 甜點價可進,📍 偏高等拉回"

        return {
            "scenario":   scenario,
            "tag":        tag,
            "advice":     advice,
            "current_px": mis_data.get("price"),
            "prev_close": mis_data.get("prev_close"),
            "gap_pct":    gap_pct,
        }
    except Exception as e:
        print(f"  ⚠️  開盤情境分析失敗:{e}")
        return {"scenario": "error", "tag": "", "advice": "", "gap_pct": 0}


# ==========================================
# 小型股指數分析(原 P2 櫃買 → ^RUT;降級邏輯一字未改)
# ==========================================
def analyze_otc_index() -> dict:
    """
    ^RUT(Russell 2000)MA20 位階 — 原櫃買「中小型偏弱」維度的美股對應。
    回傳鍵與台股版完全一致:ok/price/ma/dist_ma_pct/weak/tag/date
    規則:跌破 MA20 超過 |OTC_WEAKNESS_THRESHOLD| → weak=True → L2 綠燈降黃
    """
    if not Config.OTC_INDEX_ENABLED:
        return {"ok": False, "err": "已停用(OTC_INDEX_ENABLED=False)"}

    print("🏛️  小型股指數分析(^RUT,補完 L2)...")
    try:
        from sources import get_otc_index_history
        df = get_otc_index_history(days=Config.OTC_HISTORY_DAYS)
    except Exception as e:
        print(f"  ❌ 小型股指數抓取崩潰:{e}")
        return {"ok": False, "err": f"抓取崩潰: {e}"}

    if df.empty or len(df) < Config.OTC_MA_PERIOD:
        return {"ok": False,
                "err": f"資料不足({len(df)}/需要 {Config.OTC_MA_PERIOD})"}

    # 排除可能的當日盤中資料(盤前掃描應以 T-1 完整日線判位階)
    today_str = datetime.now(ET_TZ).strftime('%Y-%m-%d')
    if str(df.index[-1].date()) == today_str:
        df = df.iloc[:-1]
        if len(df) < Config.OTC_MA_PERIOD:
            return {"ok": False, "err": "排除當日盤中資料後資料不足"}

    price = float(df['price'].iloc[-1])
    ma    = float(df['price'].rolling(Config.OTC_MA_PERIOD).mean().iloc[-1])
    if ma <= 0:
        return {"ok": False, "err": f"MA{Config.OTC_MA_PERIOD} 計算為 0"}

    dist_ma_pct = (price - ma) / ma
    weak        = dist_ma_pct < Config.OTC_WEAKNESS_THRESHOLD

    if weak:
        tag = f"🔴 跌破 MA{Config.OTC_MA_PERIOD}(小型股偏弱)"
    elif dist_ma_pct < 0.005:
        tag = f"🟡 貼近 MA{Config.OTC_MA_PERIOD}"
    else:
        tag = f"🟢 站上 MA{Config.OTC_MA_PERIOD}"

    date_str = df.index[-1].strftime('%Y-%m-%d')
    print(f"  ^RUT {price:.2f}(MA{Config.OTC_MA_PERIOD} {ma:.2f}, "
          f"{dist_ma_pct:+.2%})  {tag}  [vs {date_str}]")

    return {
        "ok":          True,
        "price":       price,
        "ma":          ma,
        "dist_ma_pct": dist_ma_pct,
        "weak":        weak,
        "tag":         tag,
        "date":        date_str,
    }


# ==========================================
# 決策摘要(Decision Hierarchy)— 美股版重組
# ==========================================
def generate_decision_summary(macro: dict, market_open: dict, df_go,
                              otc: dict = None) -> dict:
    """
    三層燈號(結構沿用台股 V13.13.0,輸入源換美股):
      L1 市場環境:指數趨勢(^GSPC,原 TWII 權重邏輯)+ 期貨 bias + VIX
      L2 大盤狀態:SPY 盤前情境 + 小型股(^RUT)偏弱降級
      L3 個股訊號:甜點價計數(原樣;basis 腿移除)
      FINAL:任一紅→紅;任一黃→黃;全綠→綠(一字未改)

    ⚠️ D8:所有燈號規則為台股形狀的「起點」,未經美股校準;
       權重調整一律等美股 P9 樣本 n≥15。
    """
    summary = {}

    # ========== L1:市場環境 ==========
    try:
        # 指數趨勢(原 V13.13.0 TWII 趨勢權重 → ^GSPC;局部 import 避免循環依賴)
        try:
            from sources import get_index_trend
            idx_trend = get_index_trend()
        except Exception as e:
            idx_trend = {"ok": False, "trend": "neutral", "err": str(e)}

        trend        = idx_trend.get("trend", "neutral") if idx_trend.get("ok") else "neutral"
        trend_reason = idx_trend.get("reason", "")

        bias      = macro.get("bias", "neutral")
        vix_level = macro.get("vix_level")
        vix_high  = vix_level is not None and vix_level >= Config.VIX_ELEVATED_LEVEL
        vix_ext   = vix_level is not None and vix_level >= Config.VIX_EXTREME_LEVEL

        reasons_l1 = []
        red_l1 = False

        # 紅燈條件(v1 保守):VIX 高壓,或「指數空頭 + 期貨偏空」雙確認
        if vix_ext:
            red_l1 = True
            reasons_l1.append(f"🚨 VIX {vix_level:.0f} 高壓")
        if trend == "bear" and bias == "bear":
            red_l1 = True
            reasons_l1.append(f"指數{trend_reason} + 期貨偏空(雙確認)")
        elif trend == "bear" and bias != "bull":
            reasons_l1.append(f"📉 指數{trend_reason}")
        elif bias == "bear":
            if trend == "bull":
                # 期貨偏空但指數連漲 → 對沖/獲利了結情境,不直接紅(沿用 V13.13.0 哲學)
                reasons_l1.append(f"⚠️ 期貨偏空但指數{trend_reason}(對沖情境)")
            else:
                reasons_l1.append("期貨偏空")

        green_l1 = False
        # 綠燈條件:指數多頭 + 期貨不偏空 + VIX 未升高
        if (not red_l1 and trend == "bull" and bias != "bear" and not vix_high):
            green_l1 = True
            reasons_l1.append(f"📈 指數{trend_reason} + 宏觀無警訊")
        elif (not red_l1 and bias == "bull" and trend != "bear" and not vix_high):
            green_l1 = True
            reasons_l1.append("期貨偏多 + 指數不弱")

        if red_l1:
            l1_light = "🔴"
            l1_advice = "風險升高,今天休息"
        elif green_l1:
            l1_light = "🟢"
            l1_advice = "環境健康,可正常部位"
        else:
            l1_light = "🟡"
            if trend == "bull":
                l1_advice = f"宏觀中性但指數{trend_reason},可輕試 30%"
            elif trend == "bear":
                l1_advice = f"宏觀中性 + 指數{trend_reason},建議休息"
            else:
                l1_advice = "宏觀中性,建議減量 50%"
            if not reasons_l1:
                if vix_high:
                    reasons_l1.append(f"⚠️ VIX {vix_level:.0f} 偏高")
                if trend != "neutral" and trend_reason:
                    reasons_l1.append(f"📊 指數{trend_reason}")

        summary['L1'] = {
            'light':  l1_light,
            'name':   "市場環境",
            'reason': "、".join(reasons_l1) or "訊號中性",
            'advice': l1_advice,
        }
    except Exception as e:
        summary['L1'] = {'light': '⚪', 'name': '市場環境',
                         'reason': f'分析失敗: {e}', 'advice': '請手動判讀'}

    # ========== L2:大盤狀態 ==========
    try:
        scenario   = market_open.get('scenario', '')
        market_tag = market_open.get('tag', '')

        reasons_l2 = []
        red_l2 = False
        green_l2 = True

        if scenario in ('huge_gap_up', 'panic'):
            red_l2 = True
            green_l2 = False
            reasons_l2.append(market_tag.replace('🚫 ', ''))
        elif scenario == 'minor_dip':
            green_l2 = False
            reasons_l2.append("盤前小跌測試")
        elif scenario == 'normal':
            reasons_l2.append("盤前平穩")
        elif scenario == 'mild_gap_up':
            reasons_l2.append("盤前溫和開高")
        elif scenario in ('unknown', 'error', ''):
            green_l2 = False
            reasons_l2.append("盤前資料缺")

        # 小型股位階納入 L2(原 P2 規則一字未改:綠→黃,黃維持,紅維持)
        otc_weak = False
        if otc and otc.get('ok'):
            otc_dist_pct = otc.get('dist_ma_pct', 0.0)
            if otc_dist_pct < Config.OTC_WEAKNESS_THRESHOLD:
                otc_weak = True
                reasons_l2.append(f"小型股偏弱 {otc_dist_pct:+.1%}")

        if red_l2:
            l2_light = "🔴"
            l2_advice = "時機不佳,觀望"
        elif green_l2:
            if otc_weak:
                l2_light = "🟡"
                l2_advice = "大盤撐盤但小型股偏弱,部位縮半"
            else:
                l2_light = "🟢"
                l2_advice = "時機 OK,可執行"
        else:
            l2_light = "🟡"
            l2_advice = "時機勉強,部位縮半"

        summary['L2'] = {
            'light':  l2_light,
            'name':   "大盤狀態",
            'reason': "、".join(reasons_l2) or "訊號中性",
            'advice': l2_advice,
        }
    except Exception as e:
        summary['L2'] = {'light': '⚪', 'name': '大盤狀態',
                         'reason': f'分析失敗: {e}', 'advice': '請手動判讀'}

    # ========== L3:個股訊號(原樣;basis 腿移除) ==========
    try:
        sweet_count = 0
        caution_count = 0
        sweet_tickers = []
        caution_tickers = []

        if df_go is not None and len(df_go) > 0:
            for _, row in df_go.iterrows():
                tag = row.get('DistTag', '')
                ticker = row.get('Ticker', '')
                if '甜點價' in tag:
                    sweet_count += 1
                    sweet_tickers.append(ticker)
                elif '偏離待回' in tag or '偏高' in tag:
                    caution_count += 1
                    caution_tickers.append(ticker)

        reasons_l3 = [f"{sweet_count} 檔甜點價"]

        if sweet_count == 0 and caution_count == 0:
            l3_light = "🔴"
            l3_advice = "無進場標的"
        elif sweet_count >= 2:
            l3_light = "🟢"
            l3_advice = f"可關注:{', '.join(sweet_tickers[:3])}"
        elif sweet_count == 1:
            l3_light = "🟡"
            l3_advice = f"僅 1 檔甜點價:{sweet_tickers[0]}"
        else:
            l3_light = "🟡"
            l3_advice = "只有偏高檔,等拉回"

        summary['L3'] = {
            'light':   l3_light,
            'name':    "個股訊號",
            'reason':  "、".join(reasons_l3),
            'advice':  l3_advice,
            'sweet':   sweet_tickers,
            'caution': caution_tickers,
        }
    except Exception as e:
        summary['L3'] = {'light': '⚪', 'name': '個股訊號',
                         'reason': f'分析失敗: {e}', 'advice': '請手動判讀'}

    # ========== FINAL(一字未改) ==========
    lights = [summary[k]['light'] for k in ('L1', 'L2', 'L3') if summary[k]['light'] != '⚪']
    if '🔴' in lights:
        final_light = '🔴'
        final_advice = '今天休息 / 減倉 — 有紅燈,保護本金優先'
    elif '🟡' in lights:
        final_light = '🟡'
        final_advice = '減量 50% 操作 — 訊號不齊,留彈藥'
    else:
        final_light = '🟢'
        final_advice = '正常部位執行 — 3 層綠燈,可放心'

    summary['FINAL'] = {
        'light':  final_light,
        'advice': final_advice,
    }

    return summary


# ==========================================
# 法人分析(原樣移植;US v1 吃 stub 空 df → 自動 no-op,未來接 13F/其他源免改)
# ==========================================
def analyze_institutional_for_stock(stock_id: str,
                                    df: pd.DataFrame = None) -> dict:
    if df is None:
        try:
            df = get_institutional_for_stock(stock_id, 10)
        except Exception as e:
            print(f"    ⚠️  {stock_id} 法人資料抓取失敗:{e}")
            return {"has_data": False, "key_broker_hits": [],
                    "max_consec_days": 0, "day_trader_warn": False}

    if df is None or df.empty or 'buy' not in df.columns or 'sell' not in df.columns:
        return {"has_data": False, "key_broker_hits": [],
                "max_consec_days": 0, "day_trader_warn": False}

    try:
        df['date']    = pd.to_datetime(df['date'])
        df['net_lot'] = (df['buy'] - df['sell']) / 1000
    except Exception:
        return {"has_data": False, "key_broker_hits": [],
                "max_consec_days": 0, "day_trader_warn": False}

    results = {
        "has_data":        True,
        "key_broker_hits": [],
        "max_consec_days": 0,
        "day_trader_warn": False,
    }

    name_col = 'name' if 'name' in df.columns else None
    if name_col is None:
        return results

    role_map = [
        ("投信", ["Investment_Trust", "投信"],
         Config.INVESTMENT_TRUST_BUY_DAYS, Config.INVESTMENT_TRUST_MIN_LOTS, "波段"),
        ("外資", ["Foreign_Investor", "外資不含外資自營商", "外資"],
         Config.FOREIGN_BUY_DAYS,         Config.FOREIGN_MIN_LOTS,           "波段"),
    ]

    for display_name, keywords, min_days, min_total_lots, role_type in role_map:
        mask = df[name_col].astype(str).apply(
            lambda x: any(k in x for k in keywords)
        )
        sub = df[mask]
        if sub.empty:
            continue
        try:
            daily = sub.groupby('date')['net_lot'].sum().sort_index()

            consec_days = 0
            for v in reversed(daily.values):
                if v > 0:
                    consec_days += 1
                else:
                    break

            consec_total_lots = float(daily.tail(consec_days).sum()) if consec_days > 0 else 0

            if consec_days >= min_days and consec_total_lots >= min_total_lots:
                results["key_broker_hits"].append({
                    "name":        display_name,
                    "type":        role_type,
                    "consec_days": consec_days,
                    "total_lot":   round(consec_total_lots, 1),
                })
                results["max_consec_days"] = max(results["max_consec_days"], consec_days)
        except Exception:
            continue

    try:
        dealer_mask = df[name_col].astype(str).str.contains(
            '自營商避險|Dealer_Hedge', na=False, regex=True
        )
        dealer = df[dealer_mask]
        if not dealer.empty:
            daily = dealer.groupby('date')['net_lot'].sum().sort_index()
            if len(daily) >= 5:
                today_val = float(daily.iloc[-1])
                past_avg  = float(daily.iloc[-6:-1].mean())
                if (today_val > Config.DEALER_HEDGE_MIN_LOTS and
                        past_avg > 0 and
                        today_val > past_avg * Config.DEALER_HEDGE_SURGE_RATIO):
                    results["day_trader_warn"] = True
    except Exception:
        pass

    return results


# ==========================================
# 以下純技術核心:台股版原樣移植,一行未改
# (Wilder ATR / ATR% floor / DistTag ATR 倍數 / 進場停損 / 位階 / 狀態)
# ==========================================
def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                  period: int = 14) -> float:
    """V13.6.0: P1 — 計算 ATR(Wilder's smoothing,業界標準)
    回傳:最新一根的 ATR 值(與股價同單位)。資料不足時回傳 0.0。
    """
    if len(close) < period + 1 or len(high) < period + 1 or len(low) < period + 1:
        return 0.0

    n_keep = period + 5   # 多保留幾根以提高 Wilder smoothing 穩定性
    high  = high.tail(n_keep).reset_index(drop=True)
    low   = low.tail(n_keep).reset_index(drop=True)
    close = close.tail(n_keep).reset_index(drop=True)

    if len(close) < period + 1:
        return 0.0

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low  - prev_close).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).dropna()

    if len(tr) < period:
        return 0.0

    atr = float(tr.iloc[:period].mean())
    for v in tr.iloc[period:]:
        atr = (atr * (period - 1) + float(v)) / period

    return atr if atr > 0 else 0.0


def get_effective_atr(atr: float, price: float) -> tuple[float, bool]:
    """V13.9.6: ATR% 死魚盤 floor 保護(原樣移植)
    ATR% < ATR_PCT_FLOOR 時以 price × ATR_PCT_FLOOR_REPLACE 取代,
    避免低波動股 1×ATR 容忍區過窄、剛起漲就被誤判 ⚠️ 已偏離。
    """
    if atr <= 0 or price <= 0:
        return (atr, False)

    atr_pct = atr / price
    if atr_pct < Config.ATR_PCT_FLOOR:
        return (price * Config.ATR_PCT_FLOOR_REPLACE, True)
    return (atr, False)


def classify_dist_tag(price: float, ma60: float,
                      atr: float) -> tuple[str, str, float]:
    """V13.7.0: 依 ATR 倍數判斷 dist_tag(原樣移植)
      |dist| ≤ 1×ATR → 🎯 甜點價;1~2×ATR → 📍 偏離待回 ↑/↓;> 2×ATR → ⚠️ 已偏離
      ATR=0 fallback 固定百分比;V13.9.6 floor 透過 get_effective_atr。
    回傳:(tag, direction, atr_mult)
    """
    if ma60 <= 0:
        return ("⚠️ 已偏離", "", 0.0)

    diff      = price - ma60
    abs_diff  = abs(diff)
    direction = "up" if diff > 0 else ("down" if diff < 0 else "")

    if atr <= 0:
        pct = abs_diff / ma60
        if pct <= Config.DIST_SWEET_PCT:
            return ("🎯 甜點價", direction, 0.0)
        elif pct <= Config.DIST_CAUTION_PCT:
            arrow = " ↑" if direction == "up" else (" ↓" if direction == "down" else "")
            return (f"📍 偏離待回{arrow}", direction, 0.0)
        else:
            return ("⚠️ 已偏離", direction, 0.0)

    atr_eff, _ = get_effective_atr(atr, price)
    atr_mult = abs_diff / atr_eff
    if atr_mult <= Config.DIST_SWEET_ATR_MULT:
        return ("🎯 甜點價", direction, atr_mult)
    elif atr_mult <= Config.DIST_CAUTION_ATR_MULT:
        arrow = " ↑" if direction == "up" else " ↓"
        return (f"📍 偏離待回{arrow}", direction, atr_mult)
    else:
        return ("⚠️ 已偏離", direction, atr_mult)


def calculate_entry_stop_levels(price: float, ma60: float, atr: float,
                                dist_tag: str,
                                direction: str = "") -> tuple[float, float, float]:
    """V13.6.0+V13.7.0+V13.9.6: 進場區間與停損(原樣移植)
      🎯 甜點:MA60 ~ +0.5×ATR,損 -1.5×ATR
      📍 偏回↑:MA60+0.5~1.5×ATR(等下殺),損 -1.2×ATR
      📍 偏回↓:MA60-0.5×ATR ~ MA60(等反彈),損 -1.2×ATR
      ⚠️ 已偏離:不建議進場(0,0),損 -1.5×ATR 參考
    """
    if atr <= 0:
        if "甜點價" in dist_tag:
            return (round(ma60, 2), round(ma60 * 1.03, 2), round(ma60 * 0.97, 2))
        elif "偏離待回" in dist_tag or "偏高" in dist_tag:
            if direction == "down":
                return (round(ma60 * 0.96, 2), round(ma60, 2),
                        round(price * 0.97, 2))
            else:
                return (round(ma60 * 1.02, 2), round(ma60 * 1.04, 2),
                        round(ma60, 2))
        else:
            return (0.0, 0.0, round(price * 0.97, 2))

    atr_eff, _ = get_effective_atr(atr, price)

    buf       = atr_eff * Config.ATR_ENTRY_BUFFER_MULT       # 0.5×ATR
    stop_def  = atr_eff * Config.ATR_STOP_MULT_DEFAULT       # 1.5×ATR
    stop_tigh = atr_eff * Config.ATR_STOP_MULT_TIGHT         # 1.2×ATR
    pull_w    = atr_eff * Config.ATR_ENTRY_PULLBACK_MULT     # 1.0×ATR

    if "甜點價" in dist_tag:
        entry_low  = round(ma60, 2)
        entry_high = round(ma60 + buf, 2)
        stop_loss  = round(price - stop_def, 2)

    elif "偏離待回" in dist_tag or "偏高" in dist_tag:
        if direction == "down":
            entry_low  = round(ma60 - buf, 2)
            entry_high = round(ma60, 2)
        else:
            entry_low  = round(ma60 + buf, 2)
            entry_high = round(ma60 + buf + pull_w, 2)
        stop_loss = round(price - stop_tigh, 2)

    else:  # 已偏離
        entry_low  = 0.0
        entry_high = 0.0
        stop_loss  = round(price - stop_def, 2)

    # 防呆:停損不可高於進場下緣
    if entry_low > 0 and stop_loss >= entry_low:
        stop_loss = round(entry_low * 0.97, 2)

    return (entry_low, entry_high, stop_loss)


def classify_stock_position(
    price: float, ma: float
) -> Literal["low", "transition_low", "consolidate", "transition_high", "high"]:
    dist = (price - ma) / ma
    if dist < Config.PRICE_LOW_PCT:
        return "low"
    elif dist > Config.PRICE_HIGH_PCT:
        return "high"
    elif abs(dist) <= Config.PRICE_CONSOLIDATE_PCT:
        return "consolidate"
    elif dist > 0:
        return "transition_high"
    else:
        return "transition_low"


def determine_status(position: str, broker_info: dict,
                     vol_ratio: float, is_black_k: bool) -> tuple[str, int]:
    """原樣移植。US v1 註:法人 stub 下 hits/consec 恆為 0,
    法人相關分支自然不觸發;量比 + 位階分支照常運作。"""
    hits        = broker_info.get("key_broker_hits", [])
    consec      = broker_info.get("max_consec_days", 0)
    day_trader  = broker_info.get("day_trader_warn", False)

    if day_trader:
        return ("🎯 隔日沖警訊｜避開", -10)

    if hits and is_black_k:
        return ("⚠️ 假明牌(買超收黑 K)", -5)

    if position == "high" and consec >= Config.HIGH_BUY_DAYS:
        return ("⚠️ 高檔出貨警訊", -8)

    if position == "consolidate" and consec >= Config.CONSOLIDATE_BUY_DAYS:
        if vol_ratio < 1.0:
            return ("🍦 盤整甜點位｜量能平淡", 7)
        return ("🍦 盤整甜點位｜主力吸籌", 10)

    if position == "low" and consec >= Config.LOW_BUY_DAYS:
        return ("🌱 低檔摸底｜停損 5%", 7)

    if position == "transition_high" and consec >= Config.CONSOLIDATE_BUY_DAYS:
        return ("🌤️ 強勢延伸｜輕倉跟進", 5)

    if position == "transition_low" and consec >= Config.LOW_BUY_DAYS:
        return ("⏸️ 築底中｜觀察反轉", 5)

    if vol_ratio > Config.THRESHOLD_VOL_RATIO and position in ("consolidate", "low", "transition_low"):
        return ("🔥 靈魂吸籌", 3)

    return ("🔎 觀望", 0)
