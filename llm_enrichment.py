"""
llm_enrichment.py — LLM 精選股深度摘要(V13.11.2 / P7.5)

設計理念:
  09:25 規則型選股完成後,LLM 對精選 5 檔每檔產 200 字摘要,寫入 Notion。
  規則型抓不到的維度(減持、業績預警、訂單、突發事件)由 LLM 補強。

哲學:
  - 規則型優先,LLM 補充不取代:09:25 主流程零變更,本模組只在末段補摘要
  - 失敗優雅:任一階段失敗 → 該檔留空白,主流程不受影響
  - 校準在實戰後:第一週只寫 Notion,不發 TG(觀察品質後再上)
  - 防幻覺:Prompt 嚴格要求「只能根據提供的新聞」+ 結構化 JSON 輸出

================================================================
V13.11.2 改動(2026-05-25 兩個邊界 bug 修正)
================================================================
  Bug 1:LLM 摘要日期過舊
    5/25 真實跑 2882.TW 國泰金摘要返回「2023 年 Q2 利息收入」(3 年前舊聞)。
    根因:Tavily `days=3` 是「優先返回」不是「硬過濾」,當近 3 天無新聞時
    fallback 撈較舊庫存;PROMPT L162「事件過 7 天可降低權重但仍列入」太寬容,
    Gemini 把舊聞當主摘要;程式端沒做 published_date 二次過濾。

    修法(三道防線):
      a) 程式端硬過濾:NEWS_MAX_AGE_DAYS = 14 天,published_date 缺失保留
      b) PROMPT 強化:14 天前忽略 + highlight 取最新日期 + 全無近期則明示
      c) PROMPT 強制 minified JSON(無換行無縮排,避免 pretty-print 浪費 token)

  Bug 2:Gemini JSON 截斷率仍高
    5/25 真實跑 3/8 檔仍因 JSON 截斷失敗(2376/3017/3231),雖然 V13.11.1
    已從 500 提到 1500。根因:Gemini Flash 啟用 response_mime_type=JSON 後
    傾向 pretty-print(縮排換行),token 用量 2~3 倍。

    修法:
      a) max_output_tokens 1500 → 3000(雙保險,Flash 免費 token 不要錢)
      b) PROMPT 強制 minified(配合上面修法 c,Gemini 縮排率會降)
================================================================

技術選型:
  - LLM:Google Gemini 2.5 Flash(Free tier 1,500 RPD,5 檔/天 × 22 天/月 = 110 次,完全免費)
  - 新聞:Tavily(免費 1,000 次/月,中文友善)
  - SDK:google-genai(新版,舊 google-generativeai 已 deprecated)
  - Notion API:沿用既有 REST(不依賴 outputs.py)

時間預算:
  - 5 檔 × (Tavily 5s + Gemini 15s + Notion 1s) ≈ 105s
  - 整段 timeout 預留 180s(3 分鐘)

環境變數:
  - GEMINI_API_KEY        (必需)
  - TAVILY_API_KEY        (必需)
  - LLM_ENRICHMENT_ENABLED (可選,預設依 Config 開關;失敗時設 "false" 可快速關閉)
  - NOTION_TOKEN          (沿用既有)
  - NOTION_DB_ID          (沿用既有)

模組對外介面:
  run_llm_enrichment_phase(picks_df, scan_date) — 主入口,在 main.py 末段呼叫
  其他函式為內部 helper,可獨立單元測試
"""
import os
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import Config


# ============================================================
# 0. 內部常數與工具
# ============================================================
NOTION_API_BASE   = "https://api.notion.com/v1"
NOTION_VERSION    = "2022-06-28"
NOTION_TIMEOUT    = 15
TAVILY_TIMEOUT    = 10
GEMINI_TIMEOUT    = 30

# V13.11.2:新聞日期硬過濾門檻(天)
#   5/25 暴露:2882.TW 國泰金摘要返回 2023 Q2 舊聞,Tavily days=3 是「優先」不是「硬過濾」。
#   14 天平衡:嚴一點避免舊聞,鬆一點容忍小型股一週沒新聞的情況。
#   published_date 缺失的新聞保留(信任 Tavily 預設過濾)。
NEWS_MAX_AGE_DAYS = 14


def _now_tw_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def _is_enabled() -> bool:
    """檢查 LLM enrichment 是否啟用(Config + 環境變數雙重控制)"""
    if not Config.LLM_ENRICHMENT_ENABLED:
        return False
    env_override = os.getenv("LLM_ENRICHMENT_ENABLED", "").lower()
    if env_override in ("false", "0", "no"):
        return False
    return True


# ============================================================
# 1. Tavily 新聞抓取
# ============================================================
def get_news_for_stock_tavily(stock_id: str, stock_name: str = "",
                               days: int = 3, max_results: int = 5) -> dict:
    """
    用 Tavily 抓近 N 天的中文新聞。
    
    參數:
      stock_id   : 股票代號(可含 .TW/.TWO 後綴,內部自動清理)
      stock_name : 公司簡稱(可選,加進 query 提升精準度)
      days       : 近 N 天(預設 3)
      max_results: 結果上限(預設 5)
    
    回傳:
      成功 → {"ok": True, "news": [{"title": "...", "content": "...", "url": "...", "published": "..."}, ...]}
      失敗 → {"ok": False, "err": "...", "news": []}
    
    說明:
      - Tavily 中文支援不錯,query 直接用中文
      - 限制 include_domains 到台灣財經主流媒體,提升相關性
      - search_depth 用 "basic"(快、便宜),"advanced" 留給未來需要時升級
    """
    token = os.getenv("TAVILY_API_KEY", "")
    if not token:
        return {"ok": False, "err": "TAVILY_API_KEY 未設定", "news": []}

    sid_clean = stock_id.replace(".TWO", "").replace(".TW", "")
    query_parts = []
    if stock_name:
        query_parts.append(stock_name)
    query_parts.append(sid_clean)
    query_parts.append("台股")
    query = " ".join(query_parts)

    payload = {
        "api_key":         token,
        "query":           query,
        "search_depth":    "basic",
        "topic":           "news",
        "days":            days,
        "max_results":     max_results,
        "include_answer":  False,
        "include_raw_content": False,
        # 台灣主流財經媒體優先(可依實戰逐步擴充)
        "include_domains": [
            "cnyes.com", "moneydj.com", "ec.ltn.com.tw",
            "udn.com", "ettoday.net", "businessweekly.com.tw",
            "ctee.com.tw", "wealth.com.tw",
        ],
    }

    try:
        r = requests.post("https://api.tavily.com/search",
                          json=payload, timeout=TAVILY_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.Timeout:
        return {"ok": False, "err": f"Tavily timeout({TAVILY_TIMEOUT}s)", "news": []}
    except Exception as e:
        return {"ok": False, "err": f"Tavily 連線失敗:{e}", "news": []}

    raw_results = data.get("results", [])
    if not raw_results:
        return {"ok": True, "news": [], "note": "Tavily 回傳 0 筆新聞(近期無相關報導)"}

    # V13.11.2:程式端 published_date 二次硬過濾(超過 NEWS_MAX_AGE_DAYS 天硬丟)
    # published_date 缺失或解析失敗的保留(信任 Tavily 預設 days 過濾)
    now_tw = datetime.now(timezone(timedelta(hours=8)))
    cutoff = now_tw - timedelta(days=NEWS_MAX_AGE_DAYS)
    kept, dropped = [], 0
    for r in raw_results:
        pub_str = r.get("published_date", "") or ""
        if pub_str:
            try:
                # Tavily 通常回 ISO 8601 格式(如 2026-05-22T08:30:00Z 或 +0800)
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                # 統一轉成 +08:00 比較
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone(timedelta(hours=8)))
                if pub_dt < cutoff:
                    dropped += 1
                    continue  # 超過 14 天硬丟
            except (ValueError, TypeError):
                pass  # 解析失敗保留(不擋)
        kept.append(r)

    if not kept:
        msg = f"Tavily 5 筆全為 {NEWS_MAX_AGE_DAYS} 天前舊聞" if dropped else "Tavily 回傳 0 筆新聞(近期無相關報導)"
        return {"ok": True, "news": [], "note": msg}

    raw_results = kept

    news = []
    for r in raw_results:
        news.append({
            "title":     r.get("title", "")[:200],
            "content":   r.get("content", "")[:500],   # 控長度,Gemini 上下文預算用
            "url":       r.get("url", ""),
            "published": r.get("published_date", ""),
        })
    note = f"已過濾 {dropped} 筆 {NEWS_MAX_AGE_DAYS} 天前舊聞" if dropped else ""
    return {"ok": True, "news": news, "note": note}


# ============================================================
# 2. Gemini 摘要生成
# ============================================================
PROMPT_TEMPLATE = """你是台股研究助手。給定一檔股票 + 近期新聞,產出 200 字以內的中文摘要,嚴格分三段:

🚨 風險警示(0-2 條,每條 ≤ 30 字):減持公告、業績預警、監管調查、客戶事件、競品威脅。沒有則寫「無顯著風險」。
✨ 利好催化(0-2 條,每條 ≤ 30 字):大單、業績暴增、新品上市、政策受惠、客戶擴張。沒有則寫「無顯著催化」。
📢 最新動態(1 條,≤ 40 字):**以新聞日期最新的事件**為主,純客觀描述。

**嚴格規則**:
1. 只能根據下方提供的新聞,**禁止**杜撰或補充新聞外的資訊。
2. **時效規則**:每條新聞前綴的 [N] YYYY-MM-DD 是發佈日期。
   - **忽略日期早於 {cutoff_date} 的新聞**(視為舊聞,不可用於 risk/catalyst/highlight)。
   - 若所有新聞日期都早於 {cutoff_date},三段全部寫「近期無顯著事件」。
   - highlight 必須引用列表中**日期最新**的有效事件,不可挑舊聞當主摘要。
3. 若新聞為空或全無關股票本身,三段全部寫「無相關新聞」。
4. **輸出 minified JSON,絕對禁止換行、縮排、空格美化、markdown 標記、解釋文字**:
   {{"risk":["..."],"catalyst":["..."],"highlight":"..."}}
5. risk/catalyst 為陣列(0~2 個元素);highlight 為單字串。

【股票】{stock_name}({stock_id})
【今日日期】{today_date}
【新聞時效門檻】不採用早於 {cutoff_date} 的事件

【近期新聞(已過濾 {age_days} 天前舊聞)】
{news_block}

請輸出 minified JSON(單行,無縮排):"""


def _format_news_block(news_list: list) -> str:
    """把 news list 格式化為 prompt 可讀的文字塊。"""
    if not news_list:
        return "(無相關新聞)"
    lines = []
    for i, n in enumerate(news_list, 1):
        pub = n.get("published", "")[:10] if n.get("published") else "?"
        title = n.get("title", "")
        content = n.get("content", "")
        lines.append(f"[{i}] {pub} | {title}\n    {content}")
    return "\n".join(lines)


def _extract_json_from_response(text: str) -> Optional[dict]:
    """
    從 LLM response 中抽 JSON。處理常見變異:
    - 純 JSON
    - ```json ... ``` markdown code block 包裹
    - JSON 前後有解釋文字
    
    回傳 dict 或 None。
    """
    if not text:
        return None

    text = text.strip()

    # 嘗試 1:純 JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 嘗試 2:去 markdown code block 包裹
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 嘗試 3:抓最外層 {...}(處理前後有解釋文字的情況)
    m = re.search(r'\{[^{}]*"highlight"[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # 嘗試 4:寬鬆抓任何 {...}
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


def enrich_pick_with_gemini(stock_id: str, stock_name: str,
                             news_list: list, days: int = 3) -> dict:
    """
    用 Gemini 2.5 Flash 對單檔股票產出風險/利好/動態摘要。
    
    回傳:
      成功 → {"ok": True, "risk": [...], "catalyst": [...], "highlight": "...",
              "summary_text": "拼接好的可讀文字(供 Notion 寫入用)"}
      失敗 → {"ok": False, "err": "...", "summary_text": ""}
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"ok": False, "err": "GEMINI_API_KEY 未設定", "summary_text": ""}

    # Lazy import,避免主程式啟動時 import 失敗影響其他模組
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return {"ok": False,
                "err": "google-genai SDK 未安裝(pip install google-genai)",
                "summary_text": ""}

    news_block = _format_news_block(news_list)
    # V13.11.2:PROMPT 需要 today_date / cutoff_date 用於時效判斷
    now_tw = datetime.now(timezone(timedelta(hours=8)))
    today_date  = now_tw.strftime("%Y-%m-%d")
    cutoff_date = (now_tw - timedelta(days=NEWS_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    prompt = PROMPT_TEMPLATE.format(
        stock_name  = stock_name or "未知公司",
        stock_id    = stock_id,
        today_date  = today_date,
        cutoff_date = cutoff_date,
        age_days    = NEWS_MAX_AGE_DAYS,
        news_block  = news_block,
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model    = Config.LLM_MODEL,
            contents = prompt,
            config   = genai_types.GenerateContentConfig(
                temperature       = 0.3,    # 低溫降低幻覺
                max_output_tokens = 3000,   # V13.11.2: 1500→3000 雙保險
                                            # 5/25 真實跑 3/8 仍因 JSON 截斷失敗。
                                            # 根因:Gemini Flash 啟用 JSON mode 後
                                            # 傾向 pretty-print(縮排換行),token 用量
                                            # 2~3 倍。即使 PROMPT 改強制 minified,
                                            # 仍給 3000 token buffer(Flash 免費)。
                response_mime_type = "application/json",  # 強制 JSON 輸出
            ),
        )
        raw_text = response.text or ""
    except Exception as e:
        return {"ok": False, "err": f"Gemini 呼叫失敗:{e}", "summary_text": ""}

    parsed = _extract_json_from_response(raw_text)
    if not parsed:
        return {"ok": False,
                "err": f"Gemini 回傳無法 parse 為 JSON(前 100 字:{raw_text[:100]})",
                "summary_text": ""}

    risk      = parsed.get("risk", []) or []
    catalyst  = parsed.get("catalyst", []) or []
    highlight = parsed.get("highlight", "") or ""

    # 防呆:強制成 list / str
    if isinstance(risk, str):     risk = [risk]
    if isinstance(catalyst, str): catalyst = [catalyst]

    risk     = [str(x)[:60] for x in risk if x][:2]
    catalyst = [str(x)[:60] for x in catalyst if x][:2]
    highlight = str(highlight)[:80]

    # 拼接成可讀文字供 Notion / TG 顯示
    lines = []
    if risk:
        lines.append("🚨 " + "/".join(risk))
    else:
        lines.append("🚨 無顯著風險")
    if catalyst:
        lines.append("✨ " + "/".join(catalyst))
    else:
        lines.append("✨ 無顯著催化")
    if highlight:
        lines.append("📢 " + highlight)
    summary_text = "\n".join(lines)

    return {
        "ok":           True,
        "risk":         risk,
        "catalyst":     catalyst,
        "highlight":    highlight,
        "summary_text": summary_text,
    }


# ============================================================
# 3. Notion 「LLM 摘要」欄位寫入
# ============================================================
def _notion_headers() -> dict:
    token = os.getenv("NOTION_TOKEN", "")
    return {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def _query_page_id_by_title(db_id: str, title: str) -> Optional[str]:
    """根據 Title(記錄日期)查 Notion page id。沿用 outputs.py 同款邏輯。"""
    url = f"{NOTION_API_BASE}/databases/{db_id}/query"
    payload = {
        "filter": {
            "property": "記錄日期",
            "title":    {"equals": title},
        },
        "page_size": 1,
    }
    try:
        r = requests.post(url, headers=_notion_headers(),
                          json=payload, timeout=NOTION_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    ⚠️  Notion query 失敗({title}):{e}")
        return None

    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("id")


def update_notion_llm_summary(scan_date: str, stock_id: str,
                                summary_text: str) -> bool:
    """
    更新 Notion daily_picks DB 的「LLM 摘要」欄位。
    
    流程:
      1. 用 Title `YYYY-MM-DD_股票代號` query 取 page_id
      2. PATCH page 寫入 LLM 摘要(Rich Text,最多 2000 字)
    
    回傳:True 成功 / False 失敗
    
    前置:Notion DB 必須先手動加 Rich Text 欄位「LLM 摘要」
    """
    db_id = os.getenv("NOTION_DB_ID", "")
    token = os.getenv("NOTION_TOKEN", "")
    if not db_id or not token:
        print(f"    ⚠️  NOTION_DB_ID 或 NOTION_TOKEN 未設定,無法寫入 {stock_id}")
        return False

    title = f"{scan_date}_{stock_id}"
    page_id = _query_page_id_by_title(db_id, title)
    if not page_id:
        print(f"    ⚠️  Notion 找不到 page({title}),可能護欄擋下未寫入或 schema 異常")
        return False

    properties = {
        Config.LLM_SUMMARY_COLUMN: {
            "rich_text": [{"text": {"content": summary_text[:2000]}}]
        }
    }
    url = f"{NOTION_API_BASE}/pages/{page_id}"

    try:
        r = requests.patch(url, headers=_notion_headers(),
                           json={"properties": properties},
                           timeout=NOTION_TIMEOUT)
        if r.status_code != 200:
            print(f"    ⚠️  Notion PATCH 失敗({title}):HTTP {r.status_code}")
            print(f"        body:{r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"    ⚠️  Notion PATCH 例外({title}):{e}")
        return False


# ============================================================
# 4. 主入口:一站式 enrichment
# ============================================================
def run_llm_enrichment_phase(picks: list, scan_date: str) -> dict:
    """
    對精選清單跑完整 LLM enrichment 流程(主入口)。
    
    參數:
      picks: list of dict, 每個 dict 必須含:
        - "stock_code" 或 "ticker"(優先取 stock_code,fallback ticker)
        - "stock_name"(公司簡稱,可選)
      scan_date: "YYYY-MM-DD"(用於組 Notion Title)
    
    回傳統計 dict:
      {
        "total":      5,
        "success":    4,    # Gemini + Notion 雙重成功
        "news_fail":  1,    # Tavily 失敗(空新聞仍會跑 Gemini)
        "llm_fail":   0,    # Gemini 失敗
        "notion_fail":0,    # Notion 寫入失敗
        "elapsed_sec": 87.3,
        "details": [
          {"stock_id": "2330.TW", "ok": True,  "summary_text": "..."},
          {"stock_id": "2317.TW", "ok": False, "err": "..."},
          ...
        ]
      }
    
    呼叫端應:
      from llm_enrichment import run_llm_enrichment_phase
      stats = run_llm_enrichment_phase(picks_list, scan_date_str)
      print(f"  LLM enrichment 完成:{stats['success']}/{stats['total']}")
    """
    print("\n" + "=" * 65)
    print(f"🤖 V13.11.2 P7.5 LLM 精選報告 enrichment")
    print(f"   執行時間 :{_now_tw_str()}")
    print(f"   掃描日期 :{scan_date}")
    print(f"   標的數量 :{len(picks)} 檔")
    print(f"   LLM 模型 :{Config.LLM_MODEL}")
    print(f"   新聞時效 :≤ {NEWS_MAX_AGE_DAYS} 天(V13.11.2 硬過濾)")
    print("=" * 65)

    if not _is_enabled():
        print("  ⏸  LLM enrichment 已關閉(Config 或環境變數)")
        return {"total": len(picks), "success": 0, "skipped": True,
                "elapsed_sec": 0, "details": []}

    if not picks:
        print("  ℹ️  picks 為空,無需 enrichment")
        return {"total": 0, "success": 0, "elapsed_sec": 0, "details": []}

    stats = {
        "total":       len(picks),
        "success":     0,
        "news_fail":   0,
        "llm_fail":    0,
        "notion_fail": 0,
        "details":     [],
    }

    t_start = time.time()

    for idx, pick in enumerate(picks, 1):
        stock_id   = pick.get("stock_code") or pick.get("ticker", "")
        stock_name = pick.get("stock_name", "")
        if not stock_id:
            print(f"  [{idx}/{len(picks)}] ⚠️  缺少 stock_code,跳過")
            stats["details"].append({"ok": False, "err": "缺少 stock_code"})
            continue

        print(f"\n  [{idx}/{len(picks)}] 🔍 {stock_id} {stock_name}")

        # 整段超時護欄(避免單一階段 hang 住整支主程式)
        if time.time() - t_start > Config.LLM_ENRICHMENT_TOTAL_TIMEOUT:
            print(f"    ⏰ 已超過總時限 {Config.LLM_ENRICHMENT_TOTAL_TIMEOUT}s,後續跳過")
            stats["details"].append({"stock_id": stock_id, "ok": False,
                                      "err": "整段超時"})
            continue

        # Step 1: Tavily 抓新聞
        news_result = get_news_for_stock_tavily(
            stock_id, stock_name,
            days        = Config.LLM_NEWS_DAYS,
            max_results = Config.LLM_NEWS_MAX_RESULTS,
        )
        if not news_result["ok"]:
            print(f"    ⚠️  Tavily 失敗:{news_result.get('err', '')},仍嘗試以空新聞跑 Gemini")
            stats["news_fail"] += 1
            news_list = []
        else:
            news_list = news_result.get("news", [])
            note = news_result.get("note", "")
            print(f"    📰 Tavily 取得 {len(news_list)} 筆新聞{('  '+note) if note else ''}")

        # Step 2: Gemini 摘要
        enrich = enrich_pick_with_gemini(stock_id, stock_name, news_list,
                                          days=Config.LLM_NEWS_DAYS)
        if not enrich["ok"]:
            print(f"    ❌ Gemini 失敗:{enrich.get('err', '')}")
            stats["llm_fail"] += 1
            stats["details"].append({"stock_id": stock_id, "ok": False,
                                      "err": enrich.get("err", "")})
            continue

        summary_text = enrich["summary_text"]
        print(f"    🤖 Gemini 摘要:")
        for line in summary_text.split("\n"):
            print(f"        {line}")

        # Step 3: Notion 寫入
        notion_ok = update_notion_llm_summary(scan_date, stock_id, summary_text)
        if notion_ok:
            print(f"    ✅ Notion 寫入成功")
            stats["success"] += 1
            stats["details"].append({"stock_id": stock_id, "ok": True,
                                      "summary_text": summary_text})
        else:
            stats["notion_fail"] += 1
            stats["details"].append({"stock_id": stock_id, "ok": False,
                                      "err": "Notion 寫入失敗",
                                      "summary_text": summary_text})

    stats["elapsed_sec"] = round(time.time() - t_start, 1)

    print("\n" + "-" * 65)
    print(f"  📊 LLM enrichment 完成 / 耗時 {stats['elapsed_sec']}s")
    print(f"     成功     :{stats['success']}/{stats['total']}")
    print(f"     Tavily 失敗:{stats['news_fail']}")
    print(f"     Gemini 失敗:{stats['llm_fail']}")
    print(f"     Notion 失敗:{stats['notion_fail']}")
    print("=" * 65)

    return stats
