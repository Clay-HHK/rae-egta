# Reference-Time Anchor Provenance（zsRE locality gold 冻结时间）

日期：2026-07-22。用途：为 EGTA 的 change_after_reference 判定提供
有出处的 reference_time 区间，替代旧 workbook 中无出处的"约 2018"。

## 证据链

1. 审计对象 `zsre_mend_eval.json`（ROME/MEMIT 官方发布，sha256
   `8a371d51...`，见 audit_input workbook §2）的 `loc` 字段带
   `nq question:` 前缀。
2. MEND（Mitchell et al., Fast Model Editing at Scale, arXiv:2110.11309,
   ICLR 2022；github.com/eric-mitchell/mend）明确使用 Natural
   Questions 数据构造 qa 实验的 drawdown/locality 项（README:
   "training will use Natural Questions data (not zsRE data) for
   computing drawdown in the qa experiment"）。
3. Natural Questions（Kwiatkowski et al., TACL vol.7, 2019；ACL
   Anthology Q19-1026）：真实 Google 查询 + 对 Wikipedia 页面的人工
   标注短答案。golds 反映的是 NQ 标注时所见的 Wikipedia 状态，
   不晚于其发表年份 2019。
4. MEND 复用 NQ 标注原文，未重新标注（无任何重标注声明）。

## 采用的 reference_time 区间

- 主区间（用于 acceptance 判定）：
  earliest = null（查询采集期未公开），
  latest = 2019-12-31，precision = year，
  provenance = "NQ (TACL 2019) annotation no later than publication year"。
- 敏感性上界（最保守，报告用）：latest = 2021-10-31
  （MEND arXiv v1 提交，数据文件已存在的最早公开证据）。
  聚合统计可另报"在 2021-10-31 上界下仍成立的 supersession 数"。

## 判定含义

- accepted supersession 要求 event_date.earliest > reference_time.latest
  （主区间 2019-12-31）。
- 变化发生于 2019 年及以前的 item（如 legacy 标记 stale_at_freeze 的
  snooker 场馆 2016 案例）不满足主区间条件，只能表述为
  "differs as of audit date"，不进入 source-supported supersession。
- 论文表述引用本链条：zsRE-MEND locality lineage 的 frozen golds
  继承自 NQ 2019 年前的 Wikipedia 标注。

## 引用

- Mitchell et al. 2021, Fast Model Editing at Scale, arXiv:2110.11309
- github.com/eric-mitchell/mend（NQ 用于 qa drawdown 的声明）
- Kwiatkowski et al. 2019, Natural Questions, TACL 7, Q19-1026
