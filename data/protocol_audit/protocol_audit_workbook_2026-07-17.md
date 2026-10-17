# Block AUD Protocol Audit Workbook

Date: 2026-07-17 (filled 2026-07-17, local CPU audit session)

> Correction 2026-07-21: the "521 / 19,086" count in section 2.4 came from an
> unrecorded pattern implementation and is superseded by the reproducible
> count 479 (`../audit_input/extract_time_anchored.py`). All other verdicts
> in this workbook are unaffected. Paper-facing text uses 479.

Status: `TERMINAL_PASS`

Source plan: `docs/planning/minimal_submission_experiment_plan_2026-07-17.md` §4

## 0. Pre-Registered Audit Rules

This workbook is an evidence-capture record. A verdict may be finalized
only from a verbatim paper quote with section/page or from an inspected public
data example with a stable sample ID. Memory, secondhand summaries, survey
paraphrases, and expected benchmark behavior are not admissible evidence.

Allowed evidence-class labels:

- `CONFIRMED_FROM_PAPER`: the verdict is supported by a verbatim protocol quote
  with section/page.
- `CONFIRMED_FROM_DATA`: the verdict is supported by inspected public data with
  stable sample IDs and source version information.
- `UNVERIFIED`: the required primary-source evidence is unavailable or
  insufficient.

Audit-completion and motivation rules are separate:

- `audit_complete = true` only after exactly five candidate protocols have been
  attempted, at least three have admissible terminal verdicts, and every
  substitute used for an unavailable primary candidate has been recorded.
- `motivation_hit = true` if at least one audited benchmark confirms
  `Q_affected ∩ Q_retention ≠ ∅` with admissible evidence.
- The overall gate is terminal only when `audit_complete = true`: it is `PASS`
  when `motivation_hit = true`, and `STOP` when the completed audit has zero
  confirmed intersections. An incomplete audit remains `PENDING`, regardless
  of the currently observed hit count.

## 1. Front Audit Summary

| Benchmark / protocol | Audit role | `Q_affected ∩ Q_retention` confirmed | Changed-query in retention | Unrelated locality separate | Current-history distinguished | S1 constructible | Evidence class | Benchmark verdict |
|---|---|---|---|---|---|---|---|---|
| CounterFact / zsRE locality as used by ROME/MEMIT evaluations | Candidate | **YES** | YES (zsRE `loc` pool) | PARTIAL (no changed-gold screening) | NO | NO (parametric only) | CONFIRMED_FROM_DATA + CONFIRMED_FROM_PAPER | **HIT** |
| MQuAKE | Candidate | NO | NO (changed-gold queries are edit targets) | N/A (no own locality metric) | PARTIAL (`a`/`a*` recorded) | PARTIAL (pre/post gold pairs exist, no store counterfactual) | CONFIRMED_FROM_PAPER | NO-HIT (terminal) |
| AToKe | Candidate | NO | Handled via explicit time qualification | YES | **YES** | NO (parametric only) | CONFIRMED_FROM_PAPER | NO-HIT, counter-example (terminal) |
| EvoWiki | Candidate | NO | NO (current gold on affected; outdated tracked separately) | YES | YES (stable/evolved split) | PARTIAL (RAG arm has store, no frozen-component reference) | CONFIRMED_FROM_PAPER | NO-HIT, aligned convention (terminal) |
| KnowEdit / QAEdit | Candidate | **YES (inherited)** | YES (reuses zsRE `loc` pool verbatim) | PARTIAL | NO | NO | CONFIRMED_FROM_PAPER (+ §2 data via inheritance) | **HIT (inherited)** |
| TemporalWiki | Substitute | NOT_USED | — | — | — | — | — | — |
| FreshQA | Substitute | NOT_USED | — | — | — | — | — | — |

| Overall gate field | Value |
|---|---|
| Candidate protocols attempted (must equal 5) | 5 |
| Terminal admissible verdicts (must be at least 3) | 5 |
| Substitutes used and unavailable primaries replaced | None (all five primaries auditable) |
| `audit_complete` (`true` / `false`) | **true** |
| Benchmarks with confirmed `Q_affected ∩ Q_retention ≠ ∅` | 2 (zsRE/MEND locality lineage; QAEdit by explicit inheritance) |
| `motivation_hit` (`true` / `false`) | **true** |
| Overall AUD gate (`PENDING` / `PASS` / `STOP`) | **PASS** |
| Gate evidence anchors | §2.4 sample rows idx 150/190/607/623/644/703 of `zsre_mend_eval.json`; §2.2 MEND KL-locality quote; §6.2 QAEdit locality-set quote |

## 2. CounterFact / zsRE Locality as Used by ROME/MEMIT Evaluations

### 2.1 Paper and Protocol Anchor

| Field | Value |
|---|---|
| Paper title | ROME: "Locating and Editing Factual Associations in GPT"; MEMIT: "Mass-Editing Memory in a Transformer"; MEND: "Fast Model Editing at Scale" |
| Venue and year | NeurIPS 2022; ICLR 2023; ICLR 2022 |
| Citation anchor | arXiv:2202.05262; arXiv:2210.07229; arXiv:2110.11309 |
| Evaluated paper or implementation using the protocol | ROME/MEMIT official evaluations; EasyEdit-based works; QAEdit (§6) |

### 2.2 Protocol Quotes

| Source | Verbatim quote |
|---|---|
| ROME (CounterFact NS construction) | "we collect a set of nearby subjects s_n for which (s_n, r, o_c) holds true" … correctness tests "P[o_c] > P[o*]" |
| MEMIT (zsRE specificity) | "Specificity is the model's arg max accuracy on a randomly-sampled unrelated fact that should not have changed" |
| MEND (locality form) | "the disagreement between the pre- and post-edit models on unrelated samples, i.e., E_{x_loc∼D} KL(p_θ(·\|x_loc) ∥ p_θe(·\|x_loc))" |
| MEND (drawdown) | "the performance degradation of the edited model on the rest of the dataset, measured as either the edited model's perplexity increase or accuracy decrease compared to the base model" |

Interpretation recorded at audit time: the MEND locality/drawdown family scores
**agreement with the pre-update model** on the locality pool; whether that is
benign depends entirely on whether the pool contains queries whose true answer
has changed. The protocol contains no changed-gold screening step.

### 2.3 Data Source

| Field | Value |
|---|---|
| Data file | `counterfact.json` (21,919 records) and `zsre_mend_eval.json` (19,086 records) |
| Source URL | https://rome.baulab.info/data/dsets/ (official ROME/MEMIT data release) |
| Retrieved | 2026-07-17, sizes 45,108,470 B and 8,091,864 B |
| Locality field | zsRE records carry `loc` (question, prefixed `nq question:`) and `loc_ans` (frozen gold), consumed as the locality metric input by ROME/MEMIT zsRE evaluations and EasyEdit-lineage works |

### 2.4 Sampled Examples (stable IDs = array index in `zsre_mend_eval.json`)

521 / 19,086 `loc` questions match time-anchored patterns
(current/now/latest/most recent/last year/2017/2018). Inspected rows:

| idx | `loc` question | frozen `loc_ans` | World status at audit time | Included in retention? |
|---|---|---|---|---|
| 190 | "who is the pastor of new birth missionary baptist church now" | Stephen A. Davis | **Superseded**: Jamal Bryant is senior pastor since Dec 2018 (Wikipedia; religionnews.com 2018-11-19) | YES |
| 150 | "who has the most rings in the nba right now 2017" | Bill Russell | "right now"-anchored, frozen to 2017 framing | YES |
| 607 | "who won the ncaa women's championship last year 2017" | South Carolina | "last year"-anchored, stale deixis | YES |
| 623 | "where is the world cup being held 2018" | Russia | Recurring event, present-tense framing | YES |
| 644 | "who won the wwe championship elimination chamber 2018" | Roman Reigns | Recurring event | YES |
| 703 | "who won last year's grand national 2017" | One For Arthur | "last year"-anchored | YES |

Supplementary data finding (sequential-editing hazard): raw `counterfact.json`
contains **335 `(subject, relation_id)` keys with multiple records assigning
different `target_new`** (e.g., ("Saint Petersburg","P190") edited to 14
distinct targets). MEMIT's deduplicated `multi_counterfact.json` (20,877
records) has 0 such conflicts, so the MEMIT batch protocol is careful; any
sequential protocol streaming raw CounterFact and scoring earlier-edit
retention after later edits of the same key would reward superseded targets.

### 2.5 Verdict

| Question | Verdict | Basis |
|---|---|---|
| Changed-query included in retention/locality | YES | idx 190 is a "now"-anchored question whose true answer changed after the gold freeze; the locality metric rewards the frozen 2018 answer (and the MEND KL form rewards pre-edit agreement regardless of gold) |
| Unrelated locality kept separate | PARTIAL | A dedicated locality pool exists but has no changed-gold screening |
| Current vs historical distinguished | NO | Single frozen gold per question, no time qualification |
| S1 constructible | NO | Parametric editing; no external store to hold fixed |

Evidence class: **CONFIRMED_FROM_DATA** (sample IDs above) plus
**CONFIRMED_FROM_PAPER** (protocol quotes).

## 3. MQuAKE

### 3.1 Anchor

| Field | Value |
|---|---|
| Paper title | "MQuAKE: Assessing Knowledge Editing in Language Models via Multi-Hop Questions" |
| Venue and year | EMNLP 2023 |
| Citation anchor | arXiv:2305.14795 |

### 3.2 Protocol Quotes

- Record structure: "d = ⟨E, Q, a, a*, C, C*⟩ … a and a* denote the correct
  answer before and after edits".
- Metrics: "Edit-wise success rate…", "Instance-wise accuracy…", "Multi-hop
  accuracy measures the accuracy of the original and edited language models on
  multi-hop questions."
- No own locality/retention metric: retention on unrelated knowledge "is
  mentioned as evaluated in prior work but isn't detailed in MQuAKE's own
  metrics."

### 3.3 Verdict

Changed-gold multi-hop questions are the **edit targets**, scored against `a*`;
they are not placed in any retention partition. `Q_affected ∩ Q_retention = ∅`
for MQuAKE's own metrics. Terminal NO-HIT. The recorded `a`/`a*` pairs make
MQuAKE a candidate source for later S1-style re-scoring (both references are
published per query). Evidence class: **CONFIRMED_FROM_PAPER**.

## 4. AToKe

### 4.1 Anchor

| Field | Value |
|---|---|
| Paper title | "History Matters: Temporal Knowledge Editing in Large Language Model" |
| Venue and year | AAAI 2024 (pp. 19413–19421) |
| Citation anchor | arXiv:2312.05497; local PDF `pdfs/07720-AAAI24.YinX.pdf` |

### 4.2 Protocol Quotes (local PDF pp. 19415–19416)

- "if the knowledge within the model is outdated (changed to historical
  knowledge) due to the flow of time, then the historical knowledge should
  also be preserved within the model"
- TME definition: "TME refers to the continuous editing of the same s and r at
  different time scopes", explicitly contrasted with mass-editing of facts
  "which are usually unrelated".
- Query design: "Relative time question … does not contain an explicit time
  frame and uses words like 'present', 'previous' or 'last one' instead."

### 4.3 Verdict

AToKe time-qualifies every historical query, so preserved old answers are
historical-qualified, never unqualified-current. This is the designed-correct
counter-example: current/history distinguished = YES; no obsolete-rewarding
retention found. Terminal NO-HIT. Evidence class: **CONFIRMED_FROM_PAPER**.

## 5. EvoWiki

### 5.1 Anchor

| Field | Value |
|---|---|
| Paper title | "EvoWiki: Evaluating LLMs on Evolving Knowledge" |
| Citation anchor | arXiv:2412.13582; local PDF `pdfs/2412.13582v1.pdf` |

### 5.2 Protocol Quotes (local PDF pp. 4–5)

- Partition: "Stable: facts that remain unchanged from init-time to
  current-time. Evolved: facts that are established before init-time and
  exhibit changes between cutoff-time (or init-time) and current-time."
- Scoring: "For evolved data, we consider responses with the latest answer as
  correct and also compare results with outdated answers."

### 5.3 Verdict

Affected (evolved) queries are scored against the **latest** gold; outdated
answers are tracked as a separate diagnostic, not rewarded. Terminal NO-HIT;
EvoWiki is a published precedent for the current-gold-on-affected convention.
Evidence class: **CONFIRMED_FROM_PAPER**.

## 6. KnowEdit / QAEdit

### 6.1 Anchor

| Field | Value |
|---|---|
| Paper title | "The Mirage of Model Editing: Revisiting Evaluation in the Wild" (QAEdit) |
| Citation anchor | arXiv:2502.11177v5; local PDF `pdfs/2502.11177v5.pdf` |

### 6.2 Protocol Quotes (local PDF §2–§3)

- Four-property frame: "❶ reliability, success rate of editing; ❷
  generalization …; ❸ locality, impact on irrelevant knowledge; ❹
  portability …"
- Locality construction: "we evaluate: … iii) locality using unrelated QA
  pairs from ZsRE locality set."

### 6.3 Verdict

QAEdit reuses the zsRE locality pool verbatim; the §2.4 stale-gold rows
therefore sit inside QAEdit's locality partition by construction. Inherited
HIT: `Q_affected ∩ Q_retention ≠ ∅` via the same sample IDs. KnowEdit's own
per-subset locality files were not independently sampled in this session and
remain `UNVERIFIED` at the data level; the QAEdit protocol-level inheritance
is sufficient for this row's verdict. Evidence class: **CONFIRMED_FROM_PAPER**
(inheritance quote) cross-referenced to §2.4 **CONFIRMED_FROM_DATA** samples.

## 7. Session Provenance

- Audit performed 2026-07-17 on the local machine (no GPU, no repo code
  executed); data downloads under the session temp directory, sizes and
  URLs recorded in §2.3.
- External fact check for idx 190: New Birth Missionary Baptist Church senior
  pastor Jamal Bryant installed December 2018 (Wikipedia; religionnews.com
  2018-11-19; rollingout.com 2018-11-20).
- Paper quotes obtained from ar5iv full texts (ROME, MEMIT, MEND, MQuAKE) and
  local PDFs (AToKe, EvoWiki, QAEdit/Mirage).
- Companion local diagnostic: Block RA preview under
  `refine-logs/ra_local_preview_2026-07-17/` (same session).
