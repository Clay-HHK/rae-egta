# When Retention Rewards Obsolescence: Reference-State Alignment for Evaluating Evolving RAG Systems

Code and data for the paper *When Retention Rewards Obsolescence:
Reference-State Alignment for Evaluating Evolving RAG Systems* (anonymous
submission).

## Overview

An LLM-based knowledge system that answers from Web content must update the
answers that later events change while retaining those that remain valid.
Retention is commonly scored by agreement with a pre-event system output or a
frozen benchmark answer. When an event invalidates that reference for a
query's temporal context, the score rewards the answer the update should
remove. The paper calls this failure **reference-state misalignment** and
contributes:

- **Reference-aligned evaluation (RAE).** Correctness is scored against the
  context-valid target, stage attribution against the post-event control
  state S1 = (K1, A0), and retention only on queries whose pre-event reference
  remains valid.
- **Evidence-Grounded Temporal Audit (EGTA).** Frozen benchmark answers are
  checked against dated, source-linked Web evidence, with isolated judgments
  from different LLM families and explicit abstention.

The repository contains the code and evidence behind the two main results:

1. **Audit.** EGTA accepts 6 evidence-backed supersessions among 47 audited
   pairs from the zsRE-derived locality set (4 under the stricter
   reference-time bound), marks 17 references retention-eligible, and leaves 23
   unresolved. Two blind expert reviewers independently confirmed all six
   accepted supersessions. This establishes misalignment in that lineage when
   the frozen answer is the retention reference. It is not a prevalence
   estimate, and the review does not estimate EGTA reliability on the other 41
   pairs.
2. **Controlled matched comparison.** Two policies match on target success,
   historical accuracy, stable-query drift, and evidence hit, yet pre-event
   agreement and context-valid accuracy select opposite policies. The policy
   selected by pre-event agreement is 23.1 points lower in context-valid
   accuracy. This isolates a possible selection consequence in a controlled
   setting; it does not measure how often it occurs among learned systems.

## Where each result lives

| Paper | Artifact |
|---|---|
| Table 1, EGTA outcomes for the 47 audited pairs | `audit/final/tables/audit_verdict_distribution.csv`, `audit_summary.csv` |
| The six accepted supersessions | `audit/final/tables/audit_accepted_cases.csv` |
| Appendix B, five-protocol audit accounting | `audit/final/tables/protocol_audit.csv`, `data/protocol_audit/` |
| Appendix B and C, per-item evidence packages | `audit/evidence/` |
| Appendix C, auditor and verifier judgments | `audit/runs/reported_round/` |
| Appendix C, development round | `audit/runs/development_round/` |
| Table 2, matched-capability reversal | `audit/final/tables/matched_comparison.csv`, `matched_selection_summary.csv` |
| Appendix D, event-type boundary | `audit/final/tables/robustness_event_types.csv`, `data/event_types/` |
| Appendix E, injected signal noise | `audit/final/tables/robustness_noise_selection.csv`, `data/noise_selection/` |
| Appendix E, generative-reader replay | `audit/final/tables/robustness_reader_selection.csv`, `data/reader_replay/` |
| Blind expert review of the six accepted cases | `audit/final/human_review/` |
| Allowed wording for each claim | `audit/final/reports/claim_evidence_map.md` |

## Repository layout

```
src/rae_egta/
  egta/            EGTA: item migration, evidence packages, isolated auditor runs,
                   blind verification, acceptance rule, aggregation, final bundle
  egta/prompts/    auditor and verifier prompt templates
  rag_post_event/  controlled RAG testbed: versioned document store, BM25
                   retrieval and reranking, answering policies, metadata and
                   intent noise, generative-reader replay, cluster bootstrap
  common/          run provenance
tests/             unit tests
audit/
  final/           compact evidence bundle used by the paper
  evidence/        dated evidence package for each audited pair
  runs/            reported round and development round of the audit
data/
  audit_input/     the 47 audited question-answer pairs
  protocol_audit/  five-protocol audit workbook
  matched_comparison/, event_types/, noise_runs/, noise_selection/,
  reader_replay/   controlled-testbed outputs and analyses
scripts/
  build_final_evidence.py   builds audit/final/
  run_experiments.sh        entry point used for the formal runs
```

## Running

There is no packaging configuration; imports resolve through `PYTHONPATH=src`.
The reported runs used Python 3.12.2 and PyTorch 2.6.0, and each
`run_manifest.json` records the software versions of its run.

Unit tests (one test compares `run_document.py` with its committed version, so
run them inside a git checkout):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Recompute the generative-reader selection:

```bash
python3 data/reader_replay/selection/recompute_reader_ranking.py data/reader_replay <outdir>
```

Controlled testbed entry points:

```bash
PYTHONPATH=src python -m rae_egta.rag_post_event.run_document --help
PYTHONPATH=src python -m rae_egta.rag_post_event.run_document_llm --help
PYTHONPATH=src python -m rae_egta.rag_post_event.summarize --help
```

The controlled arm uses lexical BM25 and a deterministic extractive reader, so
it does not need a GPU; only the generative-reader replay does. The formal runs
were executed on a Linux server with four NVIDIA T4 GPUs. The runners exit on
macOS unless `ALLOW_LOCAL_RAG_DOCUMENT_RUN=1` is set, which is intended for
local debugging only.

## Data notes

- **Per-row noise outputs are not included.** The ten
  `rag_postevent_metrics.csv` files under `data/noise_runs/<condition>/`
  (about 200 MB in total) are omitted. Each condition's `run_manifest.json` is
  included and records the full arguments; the controlled arm is deterministic,
  so rerunning `run_document` with those arguments regenerates the file. For
  the clean condition:

  ```bash
  PYTHONPATH=src python -m rae_egta.rag_post_event.run_document \
    --output-dir <dir> --run-id block_n_clean --events 400 --seeds 13 21 34 \
    --stable-queries-per-event 2 --add-fraction 0.1 --revise-fraction 0.5 \
    --retire-fraction 0.4 --distractors-per-event 2 --chunk-noise-paragraphs 0 \
    --retriever bm25 --top-k 5 --rerank-candidate-k 20 \
    --stress-axis lexical_schema --noise-factor clean --noise-rate 0.0 \
    --noise-assignment-seed 20260714
  ```

- **Generative-reader outputs are included** because they cannot be
  regenerated deterministically.
- **Paths in run records.** Absolute paths, host names, and working-tree
  listings recorded in run and analysis manifests were replaced with
  placeholders such as `<repo>`, `<server-repo>`, and `<omitted>`; other path
  fields were updated to this repository's layout.

## Evidence rules

- Every reported number traces to a CSV or report plus its `run_manifest.json`.
- Runs recorded with `git_dirty=true` are diagnostic only.
- Unresolved audit items enter no validity claim; the six accepted cases are
  evidence-backed acceptances under the prespecified decision rule, confirmed
  by blind expert review.
