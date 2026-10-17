"""System-level ranking reanalysis for the exploratory LLM-reader (CL) block.

Reanalysis only: reads the per-row CSVs of the exploratory dual-family
LLM-reader runs and asks whether ranking candidate methods by pre-event
agreement selects a different method than ranking by current-valid accuracy,
i.e. whether the reference-choice reversal observed in the deterministic
testbed also appears under real LLM readers. No new inference is run; the
block stays exploratory (see the run README: exploratory=true).

Operational definitions (validated against field semantics in the CSVs):
- pre-event agreement per row: the reader's answer contains the value the
  pre-event system would supply, i.e. conflicting_canonical_contained on
  target/obsolete_unqualified rows (conflicting == old value there) and
  correct_canonical_contained on historical/current_valid_stable rows
  (correct == old value on historical; stable values are unchanged).
  This is a containment proxy for agreement with S0, not a rerun of S0
  under the same reader.
- current-valid accuracy per row: correct_canonical_contained on
  current-context rows (target, obsolete_unqualified, current_valid_stable).
- ABSTAIN rows count as neither agreement nor correctness in the
  unconditional metrics; selective variants condition on answered rows.

Uncertainty: paired (seed, event_id) cluster bootstrap, B=2000, seed 20260721.

Usage: python3 recompute_reader_ranking.py <controlled_llm_cl_dir> <outdir>
"""
import csv
import json
import random
import sys
from collections import defaultdict

B = 2000
BOOT_SEED = 20260721
CURRENT_TYPES = {"target", "obsolete_unqualified", "current_valid_stable"}


def preev_agree(row):
    if row["query_type"] in ("target", "obsolete_unqualified"):
        return int(row["conflicting_canonical_contained"])
    return int(row["correct_canonical_contained"])


def load(path):
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        r["_preev"] = preev_agree(r)
        r["_correct"] = int(r["correct_canonical_contained"])
        r["_abstain"] = int(r["scoring_class"] == "ABSTAIN")
        r["_cluster"] = (r["seed"], r["event_id"])
    return rows


def metrics(rows):
    cur = [r for r in rows if r["query_type"] in CURRENT_TYPES]
    obs = [r for r in rows if r["query_type"] == "obsolete_unqualified"]
    hist = [r for r in rows if r["query_type"] == "historical"]
    ans = [r for r in rows if not r["_abstain"]]
    cur_ans = [r for r in cur if not r["_abstain"]]

    def mean(rs, key):
        return sum(r[key] for r in rs) / len(rs) if rs else float("nan")

    return {
        "preev_retention": mean(rows, "_preev"),
        "preev_retention_selective": mean(ans, "_preev"),
        "current_valid_acc": mean(cur, "_correct"),
        "current_valid_acc_selective": mean(cur_ans, "_correct"),
        "obsolete_rate": mean(obs, "_preev"),
        "historical_acc": mean(hist, "_correct"),
        "abstention_rate": mean(rows, "_abstain"),
        "n_rows": len(rows),
    }


def boot_ci(rows_by_cluster, stat, rng):
    clusters = sorted(rows_by_cluster)
    vals = []
    for _ in range(B):
        draw = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        pooled = [r for c in draw for r in rows_by_cluster[c]]
        vals.append(stat(pooled))
    vals.sort()
    return vals[int(0.025 * B)], vals[int(0.975 * B)]


def main(cl_dir, outdir):
    out_rows = []
    selections = {}
    for reader, sub in [("qwen3p5_9b", "qwen_metrics"),
                        ("grok4p5", "grok_metrics")]:
        rows = load(f"{cl_dir}/{sub}/rag_document_llm_metrics.csv")
        for retriever in sorted({r["retriever"] for r in rows}):
            arm = [r for r in rows if r["retriever"] == retriever]
            per_method = {}
            for method in sorted({r["method"] for r in arm}):
                mrows = [r for r in arm if r["method"] == method]
                m = metrics(mrows)
                byc = defaultdict(list)
                for r in mrows:
                    byc[r["_cluster"]].append(r)
                rng = random.Random(BOOT_SEED)
                m["preev_ci"] = boot_ci(
                    byc, lambda p: metrics(p)["preev_retention"], rng)
                m["cv_ci"] = boot_ci(
                    byc, lambda p: metrics(p)["current_valid_acc"], rng)
                per_method[method] = m
                out_rows.append({"reader": reader, "retriever": retriever,
                                 "method": method, **{
                                     k: (round(v, 4) if isinstance(v, float)
                                         else v)
                                     for k, v in m.items()
                                     if not k.endswith("_ci")},
                                 "preev_ci_lo": round(m["preev_ci"][0], 4),
                                 "preev_ci_hi": round(m["preev_ci"][1], 4),
                                 "cv_ci_lo": round(m["cv_ci"][0], 4),
                                 "cv_ci_hi": round(m["cv_ci"][1], 4)})
            pick_pre = max(per_method, key=lambda m: per_method[m]["preev_retention"])
            pick_pre_sel = max(per_method,
                               key=lambda m: per_method[m]["preev_retention_selective"])
            pick_cv = max(per_method, key=lambda m: per_method[m]["current_valid_acc"])
            selections[f"{reader}/{retriever}"] = {
                "argmax_preev_retention": pick_pre,
                "argmax_preev_retention_selective": pick_pre_sel,
                "argmax_current_valid_acc": pick_cv,
                "reversal": pick_pre != pick_cv,
                "regret_current_valid": round(
                    per_method[pick_cv]["current_valid_acc"]
                    - per_method[pick_pre]["current_valid_acc"], 4),
            }
    with open(f"{outdir}/reader_ranking.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    with open(f"{outdir}/reader_selection.json", "w") as f:
        json.dump(selections, f, indent=1)
    print(json.dumps(selections, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
