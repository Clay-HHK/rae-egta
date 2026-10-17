#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-rag-postevent-sanity}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

DEFAULT_CONDA_PY="<server-env>/bin/python"
if [ -x "$DEFAULT_CONDA_PY" ]; then
  PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_CONDA_PY}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

OUT_ROOT="${OUT_ROOT:-runs}"
mkdir -p "$OUT_ROOT"

start_run_log() {
  mkdir -p "$OUT_DIR"
  RUN_LOG="$OUT_DIR/run.log"
  exec > >(tee "$RUN_LOG") 2>&1
  echo "[log] $RUN_LOG"
  echo "[env] host=$(hostname)"
  echo "[env] python=$($PYTHON_BIN -V)"
  echo "[env] PYTHONPATH=$PYTHONPATH"
}

run_rag_structured() {
  local run_id="$1"
  local run_out_dir="$2"
  local event_count="$3"
  shift 3
  mkdir -p "$run_out_dir"
  "$PYTHON_BIN" -m rae_egta.rag_post_event.run_structured \
    --output-dir "$run_out_dir" \
    --run-id "$run_id" \
    --events "$event_count" \
    --seeds "${RAG_SEEDS[@]}" \
    --methods "${RAG_METHODS[@]}" \
    --stable-queries-per-event "${RAG_POSTEVENT_STABLE_QUERIES:-2}" \
    "$@" \
    --device cuda
  "$PYTHON_BIN" -m rae_egta.rag_post_event.summarize \
    --input-dir "$run_out_dir" \
    --output "$run_out_dir/rag_postevent_report.md"
  echo "[done] RAG post-event metrics: $run_out_dir/rag_postevent_metrics.csv"
  echo "[done] RAG post-event report:  $run_out_dir/rag_postevent_report.md"
}

run_rag_document() {
  local run_id="$1"
  local run_out_dir="$2"
  local event_count="$3"
  shift 3
  mkdir -p "$run_out_dir"
  "$PYTHON_BIN" -m rae_egta.rag_post_event.run_document \
    --output-dir "$run_out_dir" \
    --run-id "$run_id" \
    --events "$event_count" \
    --seeds "${RAG_DOC_SEEDS[@]}" \
    --methods "${RAG_DOC_METHODS[@]}" \
    --stable-queries-per-event "${RAG_DOC_STABLE_QUERIES:-2}" \
    --top-k "${RAG_DOC_TOP_K:-5}" \
    "$@" \
    --device cuda
  "$PYTHON_BIN" -m rae_egta.rag_post_event.summarize \
    --input-dir "$run_out_dir" \
    --output "$run_out_dir/rag_postevent_report.md"
  echo "[done] Document RAG post-event metrics: $run_out_dir/rag_postevent_metrics.csv"
  echo "[done] Document RAG post-event report:  $run_out_dir/rag_postevent_report.md"
}

RAG_DOC_STRESS_DEFAULT_METHODS="no_update store_only stale_index naive_update query_rewrite_update old_ref_reg_0p70 postevent_ref_reg_0p85 old_ref_rewrite_reg_0p70 postevent_rewrite_reg_0p85"
RAG_DOC_LEGACY_SCALAR_GRID_METHODS="store_only query_rewrite_update old_ref_reg_0p70 postevent_ref_reg_0p85 old_ref_rewrite_reg_0p00 old_ref_rewrite_reg_0p05 old_ref_rewrite_reg_0p10 old_ref_rewrite_reg_0p15 old_ref_rewrite_reg_0p20 old_ref_rewrite_reg_0p25 old_ref_rewrite_reg_0p30 old_ref_rewrite_reg_0p35 old_ref_rewrite_reg_0p40 old_ref_rewrite_reg_0p45 old_ref_rewrite_reg_0p50 old_ref_rewrite_reg_0p55 old_ref_rewrite_reg_0p60 old_ref_rewrite_reg_0p65 old_ref_rewrite_reg_0p70 old_ref_rewrite_reg_0p75 old_ref_rewrite_reg_0p80 old_ref_rewrite_reg_0p85 old_ref_rewrite_reg_0p90 old_ref_rewrite_reg_0p95 old_ref_rewrite_reg_1p00 postevent_rewrite_reg_0p00 postevent_rewrite_reg_0p05 postevent_rewrite_reg_0p10 postevent_rewrite_reg_0p15 postevent_rewrite_reg_0p20 postevent_rewrite_reg_0p25 postevent_rewrite_reg_0p30 postevent_rewrite_reg_0p35 postevent_rewrite_reg_0p40 postevent_rewrite_reg_0p45 postevent_rewrite_reg_0p50 postevent_rewrite_reg_0p55 postevent_rewrite_reg_0p60 postevent_rewrite_reg_0p65 postevent_rewrite_reg_0p70 postevent_rewrite_reg_0p75 postevent_rewrite_reg_0p80 postevent_rewrite_reg_0p85 postevent_rewrite_reg_0p90 postevent_rewrite_reg_0p95 postevent_rewrite_reg_1p00"
RAG_DOC_R2_METHODS="no_update store_only query_rewrite_update oldref_adapted_preserver postevent_validity_filter intent_aware_postevent_filter"

echo "[env] host=$(hostname)"
echo "[env] python=$($PYTHON_BIN -V)"

case "$MODE" in
  unit-tests|tests)
    echo "[test] Running active LLM/RAG tests"
    "$PYTHON_BIN" -m unittest discover -s tests -v
    exit 0
    ;;
  rag-postevent-sanity)
    OUT_DIR="$OUT_ROOT/rag_postevent_sanity"
    start_run_log
    RAG_SEEDS=(${RAG_POSTEVENT_SEEDS:-13})
    RAG_METHODS=(${RAG_POSTEVENT_METHODS:-store_only naive_update old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_structured \
      rag_postevent_sanity \
      "$OUT_DIR" \
      "${RAG_POSTEVENT_EVENTS:-80}"
    exit 0
    ;;
  rag-postevent-smoke)
    OUT_DIR="$OUT_ROOT/rag_postevent_smoke"
    start_run_log
    RAG_SEEDS=(${RAG_POSTEVENT_SEEDS:-13 21 34})
    RAG_METHODS=(${RAG_POSTEVENT_METHODS:-no_update store_only naive_update replay_old old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_structured \
      rag_postevent_smoke \
      "$OUT_DIR" \
      "${RAG_POSTEVENT_EVENTS:-300}"
    exit 0
    ;;
  rag-postevent-method)
    OUT_DIR="$OUT_ROOT/rag_postevent_method"
    start_run_log
    RAG_SEEDS=(${RAG_POSTEVENT_SEEDS:-13 21 34})
    RAG_METHODS=(${RAG_POSTEVENT_METHODS:-no_update store_only naive_update replay_old old_ref_reg_0p50 old_ref_reg_0p70 old_ref_reg_0p90 postevent_ref_reg_0p50 postevent_ref_reg_0p70 postevent_ref_reg_0p85 postevent_ref_reg_0p95})
    run_rag_structured \
      rag_postevent_method \
      "$OUT_DIR" \
      "${RAG_POSTEVENT_EVENTS:-300}"
    exit 0
    ;;
  rag-postevent-formal)
    OUT_DIR="$OUT_ROOT/rag_postevent_formal"
    start_run_log
    RAG_SEEDS=(${RAG_POSTEVENT_SEEDS:-13 21 34 55 89})
    RAG_METHODS=(${RAG_POSTEVENT_METHODS:-no_update store_only naive_update replay_old old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_structured \
      rag_postevent_formal \
      "$OUT_DIR" \
      "${RAG_POSTEVENT_EVENTS:-500}"
    exit 0
    ;;
  rag-doc-postevent-sanity)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_sanity"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-no_update store_only old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_document \
      rag_doc_postevent_sanity \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-80}" \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-smoke)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_smoke"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-no_update store_only stale_index naive_update old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_document \
      rag_doc_postevent_smoke \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-400}" \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-robust)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_robust"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-no_update store_only stale_index naive_update old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_document \
      rag_doc_postevent_robust_revision_heavy \
      "$OUT_DIR/revision_heavy" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --add-fraction 0.05 \
      --revise-fraction 0.75 \
      --retire-fraction 0.20 \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    run_rag_document \
      rag_doc_postevent_robust_retire_heavy \
      "$OUT_DIR/retire_heavy" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --add-fraction 0.05 \
      --revise-fraction 0.25 \
      --retire-fraction 0.70 \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    run_rag_document \
      rag_doc_postevent_robust_high_distractor \
      "$OUT_DIR/high_distractor" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --add-fraction 0.10 \
      --revise-fraction 0.50 \
      --retire-fraction 0.40 \
      --distractors-per-event "${RAG_DOC_HIGH_DISTRACTORS:-8}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    run_rag_document \
      rag_doc_postevent_robust_chunk_noise \
      "$OUT_DIR/chunk_noise" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --add-fraction 0.10 \
      --revise-fraction 0.50 \
      --retire-fraction 0.40 \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_HIGH_CHUNK_NOISE:-4}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-formal)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_formal"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-no_update store_only stale_index naive_update old_ref_reg_0p70 postevent_ref_reg_0p85})
    run_rag_document \
      rag_doc_postevent_formal \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-1500}" \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-sanity)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_sanity"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_STRESS_DEFAULT_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_sanity \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-80}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-smoke)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_smoke"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_STRESS_DEFAULT_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_smoke \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-robust)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_robust"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_STRESS_DEFAULT_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_robust_revision_heavy \
      "$OUT_DIR/revision_heavy" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction 0.05 \
      --revise-fraction 0.75 \
      --retire-fraction 0.20 \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    run_rag_document \
      rag_doc_postevent_stress_robust_retire_heavy \
      "$OUT_DIR/retire_heavy" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction 0.05 \
      --revise-fraction 0.25 \
      --retire-fraction 0.70 \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    run_rag_document \
      rag_doc_postevent_stress_robust_high_distractor \
      "$OUT_DIR/high_distractor" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction 0.10 \
      --revise-fraction 0.50 \
      --retire-fraction 0.40 \
      --distractors-per-event "${RAG_DOC_HIGH_DISTRACTORS:-8}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    run_rag_document \
      rag_doc_postevent_stress_robust_chunk_noise \
      "$OUT_DIR/chunk_noise" \
      "${RAG_DOC_ROBUST_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction 0.10 \
      --revise-fraction 0.50 \
      --retire-fraction 0.40 \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_HIGH_CHUNK_NOISE:-4}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-formal)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_formal"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_STRESS_DEFAULT_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_formal \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-1500}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-legacy-scalar-grid-sanity)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_legacy_scalar_grid_sanity"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_LEGACY_SCALAR_GRID_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_legacy_scalar_grid_sanity \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-120}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-legacy-scalar-grid-smoke)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_legacy_scalar_grid_smoke"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_LEGACY_SCALAR_GRID_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_legacy_scalar_grid_smoke \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-legacy-scalar-grid-formal)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_legacy_scalar_grid_formal"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_LEGACY_SCALAR_GRID_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_legacy_scalar_grid_formal \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-1500}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}"
    exit 0
    ;;
  rag-doc-postevent-stress-r2-sanity)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_r2_sanity"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_R2_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_r2_sanity \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-120}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}" \
      --rerank-candidate-k "${RAG_DOC_RERANK_CANDIDATE_K:-20}"
    exit 0
    ;;
  rag-doc-postevent-stress-r2-smoke)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_r2_smoke"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_R2_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_r2_smoke \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-400}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}" \
      --rerank-candidate-k "${RAG_DOC_RERANK_CANDIDATE_K:-20}"
    exit 0
    ;;
  rag-doc-postevent-stress-r2-formal)
    OUT_DIR="$OUT_ROOT/rag_doc_postevent_stress_r2_formal"
    start_run_log
    RAG_DOC_SEEDS=(${RAG_DOC_POSTEVENT_SEEDS:-13 21 34})
    RAG_DOC_METHODS=(${RAG_DOC_POSTEVENT_METHODS:-$RAG_DOC_R2_METHODS})
    run_rag_document \
      rag_doc_postevent_stress_r2_formal \
      "$OUT_DIR" \
      "${RAG_DOC_POSTEVENT_EVENTS:-1500}" \
      --stress-axis lexical_schema \
      --add-fraction "${RAG_DOC_ADD_FRACTION:-0.10}" \
      --revise-fraction "${RAG_DOC_REVISE_FRACTION:-0.50}" \
      --retire-fraction "${RAG_DOC_RETIRE_FRACTION:-0.40}" \
      --distractors-per-event "${RAG_DOC_DISTRACTORS:-2}" \
      --chunk-noise-paragraphs "${RAG_DOC_CHUNK_NOISE:-0}" \
      --retriever "${RAG_DOC_RETRIEVER:-bm25}" \
      --rerank-candidate-k "${RAG_DOC_RERANK_CANDIDATE_K:-20}"
    exit 0
    ;;
  *)
    echo "Usage: $0 [unit-tests|tests|rag-postevent-sanity|rag-postevent-smoke|rag-postevent-method|rag-postevent-formal|rag-doc-postevent-sanity|rag-doc-postevent-smoke|rag-doc-postevent-robust|rag-doc-postevent-formal|rag-doc-postevent-stress-sanity|rag-doc-postevent-stress-smoke|rag-doc-postevent-stress-robust|rag-doc-postevent-stress-formal|rag-doc-postevent-stress-legacy-scalar-grid-sanity|rag-doc-postevent-stress-legacy-scalar-grid-smoke|rag-doc-postevent-stress-legacy-scalar-grid-formal|rag-doc-postevent-stress-r2-sanity|rag-doc-postevent-stress-r2-smoke|rag-doc-postevent-stress-r2-formal]" >&2
    exit 2
    ;;
esac
