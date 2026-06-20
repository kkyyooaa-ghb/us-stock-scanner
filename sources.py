"""
資料源封裝(美股版):yfinance 單源
血統:台股 stock-scanner V13.13.8 sources.py → V1.0.0-US(2026-06-09)

設計原則:
  1. import 介面 100% 相容台股版 — main.py / analyzers.py / outputs.py /
     backtest_picks.py 原 import 的每一個名稱,本檔都提供:
       - 可移植者 → 美股實作(yfinance,已 spike 驗證)
       - 台股專屬者 → graceful stub(同形狀回 ok=False / 空,
         對應分支自然不觸發,主流程不崩 — 沿用 V13.10.2「失敗優雅」哲學)
  2. 盤前資料邊界(2026-06-09 實測定論):
       盤前「價」✅ 可靠(preMarketPrice)→ get_premarket_quote / get_market_premarket
       盤前「量」❌ 不可靠(5 天僅 1 天有真實量)→ 方向 A:停用,C 階段改開盤後補掃
  3. 季營收 YoY 取代月營收:cache 週更(本檔 --seed-revenue),
     回傳 dict 鍵名與台股 get_month_revenue_score 完全一致。

對照表(台股 → 美股):
  get_twii_trend          → get_index_trend(^GSPC)     [別名保留]
  get_otc_index_history   → get_smallcap_index_history(^RUT) [別名保留]
  get_twse_mis            → get_market_premarket(SPY 盤前)   [別名保留]
  get_month_revenue_score → get_quarter_revenue_score        [別名保留]
  get_dxy_daily           → 原樣移植(DXY 跨市場通用)
  get_forex_* / 期貨 / 法人 / 融資 / 除息 / MIS量 → stub(ok=False/空)
  新增:get_premarket_quote(個股盤前跳空)、get_vix_daily、get_futures_macro
"""
import json
import os
import time
import warnings
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests  # 保留:LLM/Notion/TG 模組沿用 requests;本檔美股路徑不直接用

from config import Config

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

# 美東時區:用 zoneinfo 正確處理 DST(EDT/EST 自動切換)
try:
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except Exception:                      # 極端環境 fallback:固定 EDT(-4)
    ET_TZ = timezone(timedelta(hours=-4))

TPE_TZ = timezone(timedelta(hours=8))  # 台北(TG 推播顯示用)


# ==========================================================================
# 時間工具
# ==========================================================================
def get_et_time() -> str:
    """美東當下時間字串(市場邏輯用)"""
    return datetime.now(ET_TZ).strftime('%Y-%m-%d %H:%M:%S')


def get_et_date(offset_days: int = 0) -> str:
    """美東日期(資料區間端點用;美股交易日以 ET 為準)"""
    return (datetime.now(ET_TZ) + timedelta(days=offset_days)).strftime('%Y-%m-%d')


def get_tw_time() -> str:
    """台北當下時間字串 — outputs.py 推播時間戳沿用(讀者在台北)"""
    return datetime.now(TPE_TZ).strftime('%Y-%m-%d %H:%M:%S')


def get_tw_date(offset_days: int = 0) -> str:
    """相容別名:台股版以台北日期當資料端點;美股版導向 ET 日期"""
    return get_et_date(offset_days)


def is_in_scan_window() -> bool:
    """執行時段護欄(原 V13.8.7):是否在美東盤前掃描窗 08:30–09:30 ET 內"""
    now = datetime.now(ET_TZ)
    start = now.replace(hour=Config.SCAN_NORMAL_ET_HOUR_START,
                        minute=Config.SCAN_NORMAL_ET_MIN_START,
                        second=0, microsecond=0)
    end = now.replace(hour=Config.SCAN_NORMAL_ET_HOUR_END,
                      minute=Config.SCAN_NORMAL_ET_MIN_END,
                      second=0, microsecond=0)
    return start <= now <= end


# 行事曆物件快取(避免每次呼叫重建)
_NYSE_CAL = None


def is_trading_day(date_et: str = None) -> dict:
    """
    V1.1.1-US(交易日護欄,P2):今天美東是不是 NYSE 交易日?

    根治「國定假日仍觸發掃描寫 Notion」的問題(對應台股已修補的同類議題)。
    用 exchange-calendars 的 NYSE(XNYS)行事曆,自動處理:
      - 固定/浮動假日(國慶、感恩節、聖誕…)
      - 週末
      - 半日市(感恩節隔天、聖誕前夕 → 仍是交易日,額外標 half_day)

    Args:
        date_et: "YYYY-MM-DD"(美東日期);None 則用今日美東日期
    回傳:
        ok        : 行事曆是否成功載入(False = 套件缺/載入失敗 → fail-open)
        is_session: 今天是否為交易日
        half_day  : 是否為半日市(僅交易日有意義)
        date      : 判斷的美東日期
        reason    : 人類可讀說明
    ⚠️ fail-open 設計:若 exchange-calendars 未安裝或載入失敗,回 ok=False
       且 is_session=True(不擋),並印警告 — 寧可多跑一次,不可因套件問題漏掉
       真正的交易日。但正常情況(套件可用)就是確定性護欄。
    """
    global _NYSE_CAL
    if date_et is None:
        date_et = datetime.now(ET_TZ).strftime('%Y-%m-%d')

    try:
        if _NYSE_CAL is None:
            import exchange_calendars as xcals
            _NYSE_CAL = xcals.get_calendar("XNYS")
        cal = _NYSE_CAL

        is_sess = bool(cal.is_session(date_et))
        half = False
        reason = "交易日"
        if is_sess:
            try:
                close_et = cal.session_close(date_et).tz_convert("America/New_York")
                if close_et.hour < 16:
                    half = True
                    reason = f"半日市(收 {close_et.strftime('%H:%M')} ET)"
            except Exception:
                pass
        else:
            reason = "休市(假日/週末)"

        return {"ok": True, "is_session": is_sess, "half_day": half,
                "date": date_et, "reason": reason}
    except ImportError:
        print("  ⚠️  exchange-calendars 未安裝 → 交易日護欄停用(fail-open,照常執行)")
        print("     建議:pip install exchange-calendars")
        return {"ok": False, "is_session": True, "half_day": False,
                "date": date_et, "reason": "護欄停用(套件缺)"}
    except Exception as e:
        print(f"  ⚠️  交易日判斷失敗({e})→ fail-open,照常執行")
        return {"ok": False, "is_session": True, "half_day": False,
                "date": date_et, "reason": f"護欄異常: {e}"}


# ==========================================================================
# yfinance 核心(台股 V13.3.1/V13.6.0 原樣移植 — 跨市場通用)
# ==========================================================================
def download_stock_batch(tickers: list[str]) -> pd.DataFrame:
    """批次抓日線;失敗批次自動重試 1 次(yfinance 偶發 rate limit)"""
    if not HAS_YF:
        print("  ❌ yfinance 未安裝")
        return pd.DataFrame()

    frames = []
    failed_batches = []
    batches = [tickers[i:i + Config.BATCH_SIZE]
               for i in range(0, len(tickers), Config.BATCH_SIZE)]
    for idx, batch in enumerate(batches):
        try:
            df = yf.download(batch, period="120d", progress=False, auto_adjust=True)
            if df.empty:
                failed_batches.append(batch)
            else:
                frames.append(df)
        except Exception as e:
            print(f"  ⚠️  批次下載失敗:{e}")
            failed_batches.append(batch)
        if idx < len(batches) - 1:
            time.sleep(0.5)

    if failed_batches:
        print(f"  🔄 重試 {len(failed_batches)} 個失敗批次...")
        time.sleep(2)
        for batch in failed_batches:
            try:
                df = yf.download(batch, period="120d", progress=False, auto_adjust=True)
                if not df.empty:
                    frames.append(df)
                    print(f"    ✅ 重試成功 {len(batch)} 檔")
            except Exception as e:
                print(f"    ❌ 重試仍失敗:{e}")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1) if len(frames) > 1 else frames[0]


def get_series(data: pd.DataFrame, field: str, ticker: str) -> pd.Series:
    try:
        if isinstance(data.columns, pd.MultiIndex):
            return data[(field, ticker)].dropna()
        return data[field].dropna()
    except Exception:
        return pd.Series(dtype=float)


def yf_close_series(df: pd.DataFrame) -> pd.Series:
    """單一 ticker 的 Close 統一回 1D Series(處理 MultiIndex 情況)"""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    cols = (df.columns.get_level_values(0)
            if isinstance(df.columns, pd.MultiIndex) else df.columns)
    if 'Close' not in cols:
        return pd.Series(dtype=float)
    close = df['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close


def get_index_data(ticker: str = None, period: str = "60d") -> pd.DataFrame:
    """指數日線(預設主燈號指數 ^GSPC)"""
    ticker = ticker or Config.INDEX_TICKER
    try:
        return yf.download(ticker, period=period, progress=False, auto_adjust=True)
    except Exception:
        return pd.DataFrame()


def _check_yf_data_quality(data: pd.DataFrame, tickers: list[str],
                           min_bars: int = None) -> dict:
    """評估 yfinance 回傳資料的品質(原樣移植)"""
    min_bars = min_bars or Config.DATA_MIN_BARS_PER_TICKER
    success, failed = [], []

    if data is None or data.empty:
        return {'success_tickers': [], 'failed_tickers': list(tickers),
                'success_ratio': 0.0}

    for tk in tickers:
        try:
            close = get_series(data, 'Close', tk)
            if len(close) >= min_bars:
                success.append(tk)
            else:
                failed.append(tk)
        except Exception:
            failed.append(tk)

    ratio = len(success) / len(tickers) if tickers else 0.0
    return {
        'success_tickers': success,
        'failed_tickers':  failed,
        'success_ratio':   ratio,
    }


def download_stock_history(tickers: list[str]) -> tuple[pd.DataFrame, str]:
    """
    P0 抽象層(V13.6.0 結構保留)。
    V1.0.0-US:單源 yfinance(FinMind 腿已移除;Config.DATA_FALLBACK_ENABLED=False)。
    未來接 Alpha Vantage / Finnhub 備援時,在本函式補第二腿即可,呼叫端不動。
    """
    print(f"  📡 [US-V1] 抓 {len(tickers)} 檔股價(yfinance)...")
    yf_data = download_stock_batch(tickers)

    quality = _check_yf_data_quality(yf_data, tickers)
    print(f"  📊 yfinance 品質:{len(quality['success_tickers'])}/{len(tickers)} "
          f"成功({quality['success_ratio']:.1%})")
    if quality['failed_tickers']:
        bad = quality['failed_tickers']
        print(f"  ⚠️  缺漏 {len(bad)} 檔(可能下市/改名/成分異動):{bad[:8]}"
              f"{' ...' if len(bad) > 8 else ''}")
        print(f"     → 本輪略過缺漏檔;若持續缺漏請核對 Nasdaq-100 成分快照")

    return yf_data, 'yfinance'


# ==========================================================================
# 大盤趨勢(原 get_twii_trend → ^GSPC;回傳 dict 形狀完全一致)
# ==========================================================================
def get_index_trend() -> dict:
    """
    抓主燈號指數(^GSPC)近 N 日,算「連漲/連跌天數」與「累計漲跌幅」。
    回傳鍵:ok / trend(bull|bear|neutral) / consecutive / cum_pct /
           last_close / first_close / n_bars / reason
    """
    if not HAS_YF:
        return {"ok": False, "trend": "neutral", "err": "yfinance 未安裝"}

    try:
        n = Config.TWII_TREND_LOOKBACK_DAYS
        period = f"{max(n * 2, 10)}d"
        d = yf.download(Config.INDEX_TICKER, period=period,
                        progress=False, auto_adjust=True)
        close = yf_close_series(d).dropna()
        if len(close) < 2:
            return {"ok": False, "trend": "neutral", "err": "資料不足"}

        close = close.tail(n)
        n_bars = len(close)
        first_v = float(close.iloc[0])
        last_v = float(close.iloc[-1])
        cum_pct = (last_v / first_v - 1) if first_v else 0.0

        # 連漲/連跌天數(由最近往回數;與台股版同邏輯)
        diffs = close.diff().dropna()
        consecutive = 0
        for v in reversed(diffs.tolist()):
            if v > 0:
                if consecutive >= 0:
                    consecutive += 1
                else:
                    break
            elif v < 0:
                if consecutive <= 0:
                    consecutive -= 1
                else:
                    break
            else:
                break

        bull_by_consec = consecutive >= Config.TWII_TREND_BULL_DAYS
        bear_by_consec = consecutive <= -Config.TWII_TREND_BEAR_DAYS
        bull_by_pct    = cum_pct >=  Config.TWII_TREND_PCT_THRESHOLD
        bear_by_pct    = cum_pct <= -Config.TWII_TREND_PCT_THRESHOLD

        if bull_by_consec or bull_by_pct:
            trend = "bull"
        elif bear_by_consec or bear_by_pct:
            trend = "bear"
        else:
            trend = "neutral"

        parts = []
        if abs(consecutive) >= 2:
            parts.append(f"連{'漲' if consecutive > 0 else '跌'} {abs(consecutive)} 天")
        if abs(cum_pct) >= 0.005:
            parts.append(f"{n_bars}日累計 {cum_pct * 100:+.1f}%")
        reason = " + ".join(parts) if parts else "盤整"

        print(f"  📊 大盤趨勢({Config.INDEX_TICKER} {n_bars} 日):{trend}"
              f"  [{first_v:.0f} → {last_v:.0f}{'，' if parts else ''}{reason}]")

        return {
            "ok":          True,
            "trend":       trend,
            "consecutive": consecutive,
            "cum_pct":     cum_pct,
            "last_close":  last_v,
            "first_close": first_v,
            "n_bars":      n_bars,
            "reason":      reason,
        }
    except Exception as e:
        return {"ok": False, "trend": "neutral",
                "err": f"yfinance {Config.INDEX_TICKER} 失敗:{e}"}


get_twii_trend = get_index_trend   # 相容別名(analyzers 局部 import 用)


# ==========================================================================
# 小型股指數(原 P2 櫃買 → ^RUT;回傳 DataFrame 形狀一致:date index + price 欄)
# ==========================================================================
def get_smallcap_index_history(days: int = None) -> pd.DataFrame:
    """^RUT 日線歷史 → DataFrame(index=date, columns=['price'])"""
    days = days or Config.OTC_HISTORY_DAYS
    if not HAS_YF:
        print("  ❌ yfinance 未安裝")
        return pd.DataFrame()
    try:
        d = yf.download(Config.SMALLCAP_TICKER, period=f"{days}d",
                        progress=False, auto_adjust=True)
        close = yf_close_series(d).dropna()
        if close.empty:
            print("  ⚠️  小型股指數抓取失敗或無資料")
            return pd.DataFrame()
        df = close.to_frame(name='price').astype(float)
        df.index = pd.to_datetime(df.index)
        print(f"  ✅ 小型股指數({Config.SMALLCAP_TICKER}):{len(df)} 筆"
              f"({df.index[0].strftime('%m-%d')} ~ {df.index[-1].strftime('%m-%d')})")
        return df
    except Exception as e:
        print(f"  ⚠️  小型股指數抓取失敗:{e}")
        return pd.DataFrame()


get_otc_index_history = get_smallcap_index_history   # 相容別名


# ==========================================================================
# VIX / DXY / ES·NQ 期貨(宏觀背景;只顯示、不投票)
# ==========================================================================
def _daily_chg(ticker: str, period: str = "10d") -> dict:
    """通用:抓日線最後兩根算變化"""
    d = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    close = yf_close_series(d).dropna()
    if len(close) < 2:
        return {"ok": False, "err": f"{ticker} 資料不足"}
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    chg_pct = (last / prev - 1) * 100 if prev else 0.0
    return {"ok": True, "last": last, "prev": prev,
            "chg_pct": round(chg_pct, 2)}


def get_vix_daily() -> dict:
    """VIX 水位 + 分級標籤(風險背景)"""
    if not HAS_YF:
        return {"ok": False, "err": "yfinance 未安裝"}
    try:
        r = _daily_chg(Config.VIX_TICKER)
        if not r["ok"]:
            return r
        level = r["last"]
        if level >= Config.VIX_EXTREME_LEVEL:
            tag = "🚨 VIX 高壓"
        elif level >= Config.VIX_ELEVATED_LEVEL:
            tag = "⚠️ VIX 偏高"
        else:
            tag = ""
        r.update({"level": level, "tag": tag})
        return r
    except Exception as e:
        return {"ok": False, "err": f"VIX 失敗:{e}"}


def get_dxy_daily() -> dict:
    """DXY 日線變化(原 PR2-A 概念移植;美股版同樣只當背景副標)"""
    if not HAS_YF:
        return {"ok": False, "err": "yfinance 未安裝"}
    try:
        r = _daily_chg(Config.DXY_TICKER)
        if not r["ok"]:
            return r
        chg = r["chg_pct"]
        if chg >= Config.DXY_SIGNIFICANT_PCT:
            direction = "strong"     # 美元走強
        elif chg <= -Config.DXY_SIGNIFICANT_PCT:
            direction = "weak"       # 美元走弱
        else:
            direction = "flat"
        r.update({"dir": direction})
        return r
    except Exception as e:
        return {"ok": False, "err": f"DXY 失敗:{e}"}


def get_futures_macro() -> dict:
    """
    ES=F / NQ=F 期貨隔夜方向(美股版「盤前宏觀」;取代亞洲匯率共振的位置)
    回傳:ok / es(dict) / nq(dict) / tag(顯著時的一行註記)
    """
    if not HAS_YF:
        return {"ok": False, "err": "yfinance 未安裝"}
    try:
        es = _daily_chg(Config.ES_FUTURES_TICKER)
        nq = _daily_chg(Config.NQ_FUTURES_TICKER)
        if not (es.get("ok") and nq.get("ok")):
            return {"ok": False, "err": "ES/NQ 期貨資料不足", "es": es, "nq": nq}
        sig = Config.FUTURES_SIG_PCT
        tag = ""
        if es["chg_pct"] >= sig and nq["chg_pct"] >= sig:
            tag = f"🟢 期貨偏多(ES {es['chg_pct']:+.1f}% / NQ {nq['chg_pct']:+.1f}%)"
        elif es["chg_pct"] <= -sig and nq["chg_pct"] <= -sig:
            tag = f"🔴 期貨偏空(ES {es['chg_pct']:+.1f}% / NQ {nq['chg_pct']:+.1f}%)"
        elif abs(es["chg_pct"]) >= sig or abs(nq["chg_pct"]) >= sig:
            tag = f"⚠️ 期貨分歧(ES {es['chg_pct']:+.1f}% / NQ {nq['chg_pct']:+.1f}%)"
        return {"ok": True, "es": es, "nq": nq, "tag": tag}
    except Exception as e:
        return {"ok": False, "err": f"ES/NQ 失敗:{e}"}


# ==========================================================================
# 盤前報價(2026-06-09 實測:盤前「價」可靠、盤前「量」不可靠)
# ==========================================================================
def get_premarket_quote(ticker: str) -> dict:
    """
    個股盤前跳空(D7 新訊號)。
    回傳:ok / price / prev_close / gap / gap_pct / session / src
    非盤前時段:session='regular_or_closed',price 為最近成交價(仍可算 gap)。
    """
    if not HAS_YF:
        return {"ok": False, "err": "yfinance 未安裝"}
    try:
        tk = yf.Ticker(ticker)
        price, src = None, ""
        prev_close = None

        # 路徑 1:info 的 preMarketPrice(實測可靠)
        try:
            info = tk.info or {}
            pm = info.get("preMarketPrice")
            prev_close = info.get("regularMarketPreviousClose") \
                or info.get("previousClose")
            if pm:
                price, src = float(pm), "preMarketPrice"
        except Exception:
            info = {}

        # 路徑 2:fast_info fallback(取最近成交價)
        if price is None:
            try:
                fi = tk.fast_info
                price = float(getattr(fi, "last_price", None) or 0) or None
                if prev_close is None:
                    prev_close = float(getattr(fi, "previous_close", None) or 0) or None
                if price is not None:
                    src = "fast_info.last_price"
            except Exception:
                pass

        if price is None or not prev_close:
            return {"ok": False, "err": f"{ticker} 盤前/最近價缺漏"}

        prev_close = float(prev_close)
        gap = round(price - prev_close, 4)
        gap_pct = round(gap / prev_close * 100, 2)

        now_et = datetime.now(ET_TZ)
        in_pre = (4 <= now_et.hour < 9) or (now_et.hour == 9 and now_et.minute < 30)
        session = "premarket" if (in_pre and src == "preMarketPrice") \
            else "regular_or_closed"

        return {"ok": True, "price": float(price), "prev_close": prev_close,
                "gap": gap, "gap_pct": gap_pct, "session": session, "src": src}
    except Exception as e:
        return {"ok": False, "err": f"{ticker} 盤前報價失敗:{e}"}


def get_market_premarket() -> dict:
    """
    大盤盤前狀態(原 get_twse_mis 位):SPY 盤前價 vs 前收。
    回傳鍵與台股版完全一致:ok / price / prev_close / gap / gap_pct / trade_time
    """
    q = get_premarket_quote(Config.MARKET_PROXY_ETF)
    if not q.get("ok"):
        return {"ok": False, "err": q.get("err", "SPY 盤前報價失敗")}
    return {
        "ok":         True,
        "price":      q["price"],
        "prev_close": q["prev_close"],
        "gap":        q["gap"],
        "gap_pct":    q["gap_pct"],
        "trade_time": get_et_time() + f" ET({q['session']})",
    }


get_twse_mis = get_market_premarket   # 相容別名(main.py import 用)


# ==========================================================================
# 季營收 YoY(D3;原 P4 月營收位 — cache 模式沿用,回傳鍵名完全一致)
# ==========================================================================
_QUARTER_REVENUE_CACHE = None


def _load_quarter_revenue_cache() -> dict:
    global _QUARTER_REVENUE_CACHE
    if _QUARTER_REVENUE_CACHE is not None:
        return _QUARTER_REVENUE_CACHE

    path = Config.QUARTER_REVENUE_CACHE_PATH
    if not os.path.exists(path):
        print(f"  ⚠️  季營收 cache 不存在({path}),"
              f"請先執行:python sources.py --seed-revenue")
        _QUARTER_REVENUE_CACHE = {}
        return _QUARTER_REVENUE_CACHE

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"  ⚠️  季營收 cache 讀取失敗:{e}")
        _QUARTER_REVENUE_CACHE = {}
        return _QUARTER_REVENUE_CACHE

    # 過期檢查(週更節奏;>21 天仍可用但警告 — 沿用台股 stale 哲學)
    gen = raw.get("generated_at", "")
    try:
        gen_dt = datetime.strptime(gen[:10], "%Y-%m-%d")
        age = (datetime.now() - gen_dt).days
        if age > Config.QUARTER_REVENUE_CACHE_STALE_DAYS:
            print(f"  ⚠️  季營收 cache 已 {age} 天未更新(門檻 "
                  f"{Config.QUARTER_REVENUE_CACHE_STALE_DAYS}),建議重跑 seed")
    except Exception:
        pass

    _QUARTER_REVENUE_CACHE = raw.get("stocks", {})
    return _QUARTER_REVENUE_CACHE


def get_quarter_revenue_score(stock_id: str) -> dict:
    """
    從本地 cache 取得單檔「季營收 YoY」加分結果。
    回傳鍵名與台股 get_month_revenue_score 完全一致:
      ok / yoy / score / tag / ym / revenue_yi
      - ym:美股版為財報季標籤,如 "2026Q1"
      - revenue_yi:⚠️ 單位改「十億美元(B USD)」(台股版為億台幣;
        鍵名保留以相容 main.py,顯示文案於 main 美股版調整)
    """
    sid = stock_id.split(".")[0] if "." in stock_id else stock_id
    sid = sid.upper()

    if sid in Config.REVENUE_EXCLUDED_STOCKS:
        return {"ok": False, "err": f"{sid} 產業豁免"}

    cache = _load_quarter_revenue_cache()
    info = cache.get(sid)
    if info is None:
        return {"ok": False, "err": f"{sid} 無季營收資料"}

    yoy = info.get("yoy")
    if yoy is None:
        return {"ok": False, "err": f"{sid} YoY 算不出(去年同季資料缺)"}

    score = 0
    tag = None
    for threshold, pts, label in Config.QUARTER_REVENUE_YOY_TIERS:
        if yoy >= threshold:
            score = pts
            tag = label
            break

    return {
        "ok":         True,
        "yoy":        yoy,
        "score":      score,
        "tag":        tag,
        "ym":         info.get("quarter_label", "?"),
        "revenue_yi": round(info.get("latest_revenue", 0) / 1e9, 2),  # B USD
    }


get_month_revenue_score = get_quarter_revenue_score   # 相容別名


def reset_month_revenue_cache() -> None:
    """測試用:強制重讀 cache(名稱相容台股版)"""
    global _QUARTER_REVENUE_CACHE
    _QUARTER_REVENUE_CACHE = None


reset_quarter_revenue_cache = reset_month_revenue_cache


def seed_quarter_revenue_cache(tickers: list[str] = None,
                               sleep_sec: float = 0.4) -> dict:
    """
    季營收 cache 種子(原 seed_month_revenue_cache.py 位;週更即可)。
    來源:yfinance quarterly income statement(Total Revenue);
    YoY = 最新季 / 去年同季 - 1(需 ≥ 5 季)。
    用法:python sources.py --seed-revenue
    """
    if not HAS_YF:
        print("❌ yfinance 未安裝")
        return {}

    tickers = tickers or Config.SCAN_POOL
    out, ok_n, fail = {}, 0, []
    print(f"🌱 季營收 seed:{len(tickers)} 檔(yfinance quarterly income stmt)...")

    for i, t in enumerate(tickers, 1):
        sid = t.upper()
        try:
            tk = yf.Ticker(sid)
            qf = None
            for attr in ("quarterly_income_stmt", "quarterly_financials"):
                cand = getattr(tk, attr, None)
                if cand is not None and not cand.empty:
                    qf = cand
                    break
            if qf is None:
                fail.append(sid)
                continue

            rev_row = None
            for key in ("Total Revenue", "TotalRevenue", "Revenue"):
                if key in qf.index:
                    rev_row = qf.loc[key]
                    break
            if rev_row is None:
                fail.append(sid)
                continue

            rev = rev_row.dropna()
            if len(rev) < 5:
                out[sid] = {"yoy": None, "quarters": int(len(rev))}
                continue

            latest = float(rev.iloc[0])
            year_ago = float(rev.iloc[4])
            yoy = (latest - year_ago) / abs(year_ago) if year_ago else None

            q_end = rev.index[0]
            try:
                q_label = f"{q_end.year}Q{(q_end.month - 1) // 3 + 1}"
            except Exception:
                q_label = str(q_end)[:10]

            out[sid] = {
                "yoy":            round(yoy, 4) if yoy is not None else None,
                "latest_revenue": latest,
                "quarter_label":  q_label,
                "quarters":       int(len(rev)),
            }
            ok_n += 1
        except Exception as e:
            fail.append(sid)
            print(f"    ⚠️  {sid} 失敗:{str(e)[:50]}")

        if i % 20 == 0:
            print(f"    進度 {i}/{len(tickers)}")
        time.sleep(sleep_sec)   # 100 檔 × info 呼叫,放慢避免 429

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source":       "yfinance quarterly_income_stmt",
        "stocks":       out,
    }
    path = Config.QUARTER_REVENUE_CACHE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"✅ 季營收 cache 完成:{ok_n}/{len(tickers)} 檔有 YoY → {path}")
    if fail:
        print(f"   失敗 {len(fail)} 檔:{fail[:10]}{' ...' if len(fail) > 10 else ''}")
    reset_month_revenue_cache()
    return payload


# ==========================================================================
# 台股專屬 stub 區(D2/D5/D6)— 同形狀 graceful empty,主流程分支自然不觸發
# ==========================================================================
_STUB_WARNED: set = set()


def _stub_note(name: str, reason: str):
    """每個 stub 每次執行只提示一次,避免洗版"""
    if name not in _STUB_WARNED:
        print(f"  ℹ️  [US-V1] {name} 停用:{reason}")
        _STUB_WARNED.add(name)


def get_mis_open_volume_change() -> dict:
    """D2 方向A:盤前量已實測不可靠 → 停用;C 階段改開盤後補掃再啟用"""
    _stub_note("大盤開盤量結構(P7)", "yfinance 盤前量不可靠(2026-06-09 實測),方向A 停用")
    return {"ok": False, "err": "US v1 停用(方向A):盤前量不可得"}


def reset_mis_open_volume_cache() -> None:
    pass


def get_margin_balance_5d_change() -> dict:
    """D6:融資餘額為台股專屬(美股可改 short interest,留待後續)"""
    _stub_note("融資餘額 5 日變化", "台股專屬資料;美股對應為 short interest(未排期)")
    return {"ok": False, "err": "US v1 停用:台股專屬資料"}


def reset_margin_balance_cache() -> None:
    pass


def get_institutional_for_stock(stock_id: str, days: int = 10) -> pd.DataFrame:
    """D6:三大法人為台股專屬(美股 13F 季頻且延遲,無盤前對應)"""
    return pd.DataFrame()


def get_institutional_batch(stock_ids: list[str], days: int = 10,
                            max_workers: int = 5) -> dict[str, pd.DataFrame]:
    _stub_note("三大法人", "台股專屬資料;美股 13F 季頻延遲,無盤前對應")
    return {sid: pd.DataFrame() for sid in stock_ids}


def get_futures_daily(days: int = 10) -> pd.DataFrame:
    """D6:台指期專屬;美股宏觀期貨方向改用 get_futures_macro()"""
    _stub_note("台指期日資料", "改用 get_futures_macro()(ES=F/NQ=F)")
    return pd.DataFrame()


def get_futures_after_market(days: int = 3) -> pd.DataFrame:
    return pd.DataFrame()


def get_futures_institutional(days: int = 10) -> pd.DataFrame:
    return pd.DataFrame()


def get_dividend_estimate() -> dict:
    """D6:加權指數除息扣點為台股 basis 修正專用"""
    return {"ok": False, "points": 0.0, "err": "US v1 停用:台股專屬"}


def get_forex_rates(days: int = 10) -> pd.DataFrame:
    """D5:亞洲匯率共振停用;宏觀背景改 get_futures_macro()+get_vix_daily()"""
    _stub_note("亞洲匯率共振", "台股出口商邏輯;美股宏觀改 ES/NQ 期貨 + VIX")
    return pd.DataFrame()


def get_forex_5m(symbol: str) -> dict:
    return {"ok": False, "err": "US v1 停用:FX 5m 模組(D5)"}


def get_forex_prev_close(symbol: str, prev_date_str: str = None):
    return None


# ==========================================================================
# CLI:python sources.py --seed-revenue
# ==========================================================================
if __name__ == "__main__":
    import sys
    if "--seed-revenue" in sys.argv:
        seed_quarter_revenue_cache()
    else:
        print("用法:python sources.py --seed-revenue   # 產生季營收 YoY cache")
        print("自我測試:")
        print(" ", get_index_trend().get("trend"))
        print(" ", get_market_premarket())
