# Evidence Backfill 检索规范（所有批次共用）

对工作清单中的每个 item，产出一个 `<item_id>.json` 写入本目录。

## 输出格式（严格遵守）

```json
{
  "item_id": "...",
  "question": "...",
  "frozen_answer": "...",
  "backfill_status": "complete | insufficient_candidate",
  "access_date": "2026-07-22",
  "evidence_sources": [
    {
      "url": "实际可访问的 URL",
      "title": "页面标题（从页面读取，不猜）",
      "publisher": "机构/站点名",
      "source_tier": "official_primary | authoritative_database | versioned_encyclopedia | reliable_secondary | other",
      "publication_or_update_date": "YYYY-MM-DD 或 null（页面可见才填，绝不猜）",
      "access_date": "2026-07-22",
      "content_sha256": null,
      "spans": [
        {"span_id": "s1", "text": "逐字引用的短证据片段(<=300字符)", "locator": "页面位置描述", "supports": ["frozen_answer" 或 "current_answer" 或 "change_date"]}
      ]
    }
  ],
  "search_log": "简短检索记录：查了什么、放弃了什么、为何",
  "change_date_hint": "若证据含变化日期则记 YYYY-MM-DD 或区间，否则 null"
}
```

## 规则

1. 每 item 最多 2 个合格 source、最多 4 次网页抓取。合格 = URL 可访问 +
   有支持性内容片段。超出预算即停，如实标 insufficient_candidate。
2. 证据目标：(a) frozen answer 在过去是否成立；(b) 当前答案是什么/
   是否已变化；(c) 变化发生的日期。三者能覆盖几个算几个，spans 的
   supports 标签如实标注。
3. 优先级：官方机构/公告 > 权威数据库 > 有版本记录的百科（Wikipedia
   可用，标 versioned_encyclopedia）> 可靠新闻/二级来源。
4. 诚实红线：绝不编造 URL、标题、日期。publication_or_update_date
   只在页面明确可见时填写，否则 null。span 必须是页面上逐字存在的文本。
5. legacy_current_answer / legacy_note 只是检索线索，不是结论；
   证据要独立支持判断，若与线索矛盾按证据如实记录。
6. seed_url 批次：先验证 seed URL 是否仍可访问且支持性内容仍在，
   在此基础上提取 title/date/span；失效则另找替代源。
7. year-anchored（C_year_only）item：证据只需支持"该答案对其锚定
   年份成立"，不需要追当前值。
8. 完成所有 item 后，返回一段精简摘要：每 item 一行
   `item_id前8位 | status | 源数 | tier | 有无change_date`，
   外加异常说明。不要在返回中粘贴完整 JSON。
