# Block CL — Controlled corpus x real LLM reader (dual model family)

Answers the reviewer critique that results come from a deterministic extractive
pipeline, not a real generative reader. Real-LLM readers over the controlled
document corpus (no new data-generation rules), 120 events, 10,000-replicate
paired `(seed,event_id)` cluster bootstrap. `exploratory=true`,
`preregistered_closure=false`, `git_dirty=false`.

## Headline result — selective method/reference separation holds on two families
- **Qwen3.5-9B** (open): current-retrieval methods selective accuracy `0.97-1.0`
  vs `no_update` `0.0` (CI excludes 0 on target and obsolete); coverage
  abstention-limited (~0.52 target).
- **Grok 4.5** (frontier, cross-family): current-retrieval unconditional target
  success `1.0` and selective accuracy `1.0` vs `no_update` `0.0`, CIs `[1.0,1.0]`,
  near-full coverage.

## Files
- `qwen_metrics/` , `grok_metrics/` — per-reader `rag_document_llm_metrics.csv` +
  `run_manifest.json` (provenance); Qwen also has `subsample.json` (paired subsample design)
- `qwen_inference/` , `grok_inference/` — `llm_cell_cluster_ci.csv` +
  `llm_current_vs_old_contrasts.csv` + `analysis_manifest.json`

Raw reader request/response caches (`reader_artifacts/*.jsonl`) are preserved on
T4 but excluded here for size, matching the package policy on large reader caches.

## Provenance / status
Exploratory real-LLM evidence (dual family). `git_dirty=false`.
Runs: Qwen `exploratory_formal_20260717T172024Z`, Grok
`grok_crossfamily_20260717T230453Z`. Keep ABSTAIN a distinct class; report
coverage and selective accuracy separately. Do NOT claim pre-registered closure or
production deployment. See `docs/evidence_ledger.md` (Production RAG / API-LLM row).
