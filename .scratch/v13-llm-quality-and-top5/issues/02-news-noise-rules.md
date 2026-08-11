# 新聞非事件排除規則

Type: task
Status: resolved
Blocked by: -

## Scope

`llm_enrichment` PROMPT_TEMPLATE:新增非事件排除規則。

## Acceptance

- 見 `../spec.md`。

## Comments

- 2026-08-11 claimed。證據(12 筆抽查):
  - ASML 8/10 把「PancakeSwap 將 ASML 列為 bStocks 產品」當利好催化,
    並提及可疑的「競爭對手 Source Foundry」。
  - ASML 8/7、AMAT 8/7 把「股價下跌 10%」「上月跌 29.78%」當風險。

## Answer

- 四類排除:純價格波動、平台上架與衍生商品、泛泛評論與內容農場標題、
  過期重述。並補「全被排除時寫『無相關新聞』」避免硬湊。
- 選擇改 prompt 而非砍 `include_domains`:雜訊是內容類型問題不是來源
  問題,砍網域會連帶失去有效報導。
- 實效需下次掃描驗證,特別是排除規則是否過嚴而讓摘要變空。
