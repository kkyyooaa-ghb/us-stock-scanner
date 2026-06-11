"""
輸出模組(美股版)V1.0.0-US:Notion 同步 + Telegram 推播
血統:台股 stock-scanner V13.13.8 outputs.py → 架構2移植

與台股版差異(2026-06-11,對齊 Notion「美股掃描」DB schema):
  🔁 欄位改名:「月營收YoY」→「季營收YoY」、「匯率共振」→「盤前宏觀」
  ✅ 新增寫入:「盤前跳空%」(row['PreGapPct'],D7 新訊號;None 留空)
  🔁 build_one_line_summary 主題 emoji 換美股主題
  ✅ 保留:冪等 title(YYYY-MM-DD_Ticker)、V13.13.5 進場參考價 0→null、
     _set_number NaN 防呆、3 次重試、TG HTML 推播
  ℹ️ 回填欄(掃描日收盤價 / D+1/D+3/D+5報酬% / R值 / 是否觸發停損 / 回顧備註)
     由後續 track_performance 腳本寫,本模組不碰(沿用 V13.9.3 Stage 2/3 設計)
  ⚠️ NOTION_DB_ID 需設為美股掃描 DB:37c323c3fc0180d3a84acdea1a5ca2af
"""
import requests
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from config import Config
from sources import get_tw_time


NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"


def sync_notion(row: dict, macro_tag: str,
                basis: float, chip_tag: str,
                *,
                scan_date: str = "",
                market_light: str = "",
                themes: Optional[List[str]] = None) -> bool:
    """
    把單檔精選股寫入 Notion 美股掃描 DB (daily_picks)。

    使用「記錄日期」(Title) = "YYYY-MM-DD_Ticker" 保證冪等
    (同日重跑會 update 既有 page,不會重複新增)。

    Args:
        row          : df_go 的單筆 row.to_dict()
        macro_tag    : 盤前宏觀 tag(原 forex_tag 槽位)→ 寫入「盤前宏觀」欄
        basis        : Deprecated(保留以維持呼叫端介面相容)
        chip_tag     : Deprecated(保留以維持呼叫端介面相容)
        scan_date    : "YYYY-MM-DD" → 寫入「掃描日期」;留空則用今日
        market_light : "🔴" / "🟡" / "🟢" → 寫入「大盤燈號」
        themes       : ["🧠 AI半導體", ...] → 寫入「主題共振」(multi-select)

    Returns:
        True 寫入成功 / False 失敗
    """
    if not Config.NOTION_TOKEN or not Config.NOTION_DB_ID:
        return False

    ticker = str(row.get("Ticker", "")).strip()
    if not ticker:
        return False

    if not scan_date:
        scan_date = datetime.now().strftime("%Y-%m-%d")

    title = f"{scan_date}_{ticker}"

    headers = {
        "Authorization":  f"Bearer {Config.NOTION_TOKEN}",
        "Content-Type":   "application/json",
        "Notion-Version": NOTION_API_VERSION,
    }

    # 1. 查既有 page(冪等性)
    existing_id = _query_page_by_title(title, headers)

    # 2. 構建 properties payload
    properties = _build_properties(row, scan_date, market_light, macro_tag,
                                   themes, title)

    # 3. 寫入(update 或 create)
    for attempt in range(3):
        try:
            if existing_id:
                resp = requests.patch(
                    f"{NOTION_API_BASE}/pages/{existing_id}",
                    headers=headers,
                    json={"properties": properties},
                    timeout=10,
                )
            else:
                resp = requests.post(
                    f"{NOTION_API_BASE}/pages",
                    headers=headers,
                    json={
                        "parent": {"database_id": Config.NOTION_DB_ID},
                        "properties": properties,
                    },
                    timeout=10,
                )

            if resp.status_code in (200, 201):
                return True
            else:
                print(f"  Notion 寫入失敗 ({title}): "
                      f"HTTP {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"  Notion 失敗 (第 {attempt+1} 次,{title}): {e}")
        time.sleep(2)
    return False


def _query_page_by_title(title: str, headers: Dict) -> Optional[str]:
    """查 DB 中「記錄日期」(Title) 等於 title 的 page,回傳 page_id 或 None"""
    try:
        resp = requests.post(
            f"{NOTION_API_BASE}/databases/{Config.NOTION_DB_ID}/query",
            headers=headers,
            json={
                "filter": {
                    "property": "記錄日期",
                    "title": {"equals": title},
                },
                "page_size": 1,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            return results[0]["id"] if results else None
    except Exception:
        pass
    return None


def _build_properties(row: Dict[str, Any],
                      scan_date: str,
                      market_light: str,
                      macro_tag: str,
                      themes: Optional[List[str]],
                      title: str) -> Dict[str, Any]:
    """根據 row dict 構建 Notion properties payload(美股掃描 DB schema)"""
    props: Dict[str, Any] = {
        "記錄日期": {"title": [{"text": {"content": title}}]},
        "掃描日期": {"date": {"start": scan_date}},
    }

    ticker = row.get("Ticker")
    if ticker:
        props["股票代號"] = {"select": {"name": str(ticker)}}

    status = row.get("Status")
    if status:
        props["Status"] = {
            "rich_text": [{"text": {"content": str(status)[:2000]}}]
        }

    # MA基準: 用 Support(一律 = MA60)
    _set_number(props, "MA基準",        row.get("Support"))
    _set_number(props, "Priority 分數", row.get("Priority"))

    # V13.13.5(沿用):進場參考價 0 → null
    # 「⚠️ 已偏離」狀態 analyzers 回傳 entry_low = 0.0(sentinel),不該寫成 number=0
    entry_low_val = row.get("EntryLow")
    if entry_low_val is not None and float(entry_low_val) > 0:
        _set_number(props, "進場參考價", entry_low_val)

    _set_number(props, "停損價",     row.get("StopLoss"))
    _set_number(props, "季營收YoY",  row.get("YoY"))          # 原月營收YoY 欄
    _set_number(props, "連買天數",   row.get("ConsecDays"))
    _set_number(props, "盤前跳空%",  row.get("PreGapPct"))    # D7 新訊號(None 留空)

    dist_tag = row.get("DistTag")
    if dist_tag:
        props["DistTag"] = {"select": {"name": str(dist_tag)}}

    if themes:
        props["主題共振"] = {
            "multi_select": [{"name": str(t)} for t in themes if t]
        }

    if market_light:
        props["大盤燈號"] = {"select": {"name": str(market_light)}}

    if macro_tag:
        props["盤前宏觀"] = {                                  # 原匯率共振欄
            "rich_text": [{"text": {"content": str(macro_tag)[:2000]}}]
        }

    return props


def _set_number(props: Dict, key: str, value: Any) -> None:
    """安全設定 number 欄位:None / NaN / 非數字會跳過(該欄保留空值)"""
    if value is None:
        return
    try:
        f = float(value)
        if f != f:   # NaN
            return
        props[key] = {"number": f}
    except (ValueError, TypeError):
        pass


def send_telegram(message: str) -> None:
    if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
        print("  ⚠️  Telegram Token 未設定,跳過推播")
        return

    url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, json={
                "chat_id":    Config.TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "HTML",
            }, timeout=10)
            if r.status_code == 200:
                print("  ✅ Telegram 推播成功")
                return
        except Exception as e:
            print(f"  ⚠️  Telegram 失敗(第 {attempt+1} 次):{e}")
        time.sleep(2)


def build_one_line_summary(macro_result: dict, futures_result: dict,
                           recommended: list, warnings_list: list,
                           decision_summary: dict = None) -> str:
    """
    「筆記本寫一句話」:方向 | 觀察標的 | 進場條件(木桶 FINAL 燈號優先)
    美股版:主題 emoji 換美股主題;futures_result 保留簽名相容(未使用)。
    """
    # ── 第一段:方向(木桶 FINAL 燈號)──
    final_light = "🟡"
    if decision_summary:
        final_light = decision_summary.get("FINAL", {}).get("light", "🟡")

    direction = {
        "🟢": "方向偏多",
        "🔴": "方向偏空或觀望",
        "🟡": "方向盤整",
    }.get(final_light, "方向盤整")

    # ── 第二段:觀察標的(加主題共振標籤)──
    if recommended:
        top    = recommended[0]
        ticker = top["Ticker"]
        status = top.get("Status", "")
        if "AI半導體" in status:
            theme_label = " 🧠"
        elif "記憶體儲存" in status:
            theme_label = " 💾"
        elif "半導體設備" in status:
            theme_label = " 🔧"
        elif "軟體雲端" in status:
            theme_label = " 💻"
        elif "資安" in status:
            theme_label = " 🔒"
        elif "AI電力" in status:
            theme_label = " ⚡"
        else:
            theme_label = ""
        watch = f"觀察 {ticker}{theme_label}"
    elif warnings_list:
        watch = f"避開 {warnings_list[0]['Ticker']}"
    else:
        watch = "無觀察名單"

    # ── 第三段:進場條件(木桶理論後的總結)──
    dist_tag = recommended[0].get("DistTag", "") if recommended else ""
    is_caution = ("偏離待回" in dist_tag) or ("偏高" in dist_tag)
    is_down    = "↓" in dist_tag

    if final_light == "🔴":
        entry = "今天觀望,勿輕易進場"
    elif final_light == "🟡":
        if "甜點價" in dist_tag:
            entry = "倉位減半試進"
        elif is_caution:
            entry = "等回測月線,倉位減半"
        else:
            entry = "縮手,等訊號齊全"
    else:  # 🟢
        if "甜點價" in dist_tag:
            entry = "現在甜點價,可正常進場"
        elif is_caution:
            entry = "等反彈站回月線再進" if is_down else "偏高,等拉回再進"
        elif "已偏離" in dist_tag:
            entry = "已偏離,等下次回測"
        else:
            entry = "站回月線才進"

    return f"{direction}。{watch}。{entry}。"
