# Claim-to-evidence map

| Claim | Final artifact | Supported wording | Boundary |
|---|---|---|---|
| Misalignment exists in a published lineage | `tables/audit_summary.csv`, `tables/audit_accepted_cases.csv` | EGTA accepts six evidence-backed supersessions in the 47-pair audit sample | Not benchmark-wide prevalence |
| A finite misaligned fraction can change selection | `tables/rq2_sensitivity_*.csv`, `figures/fig_sensitivity_bridge.*` | Small `p` suffices when the aligned-method margin is small | Scenario analysis, not an empirical prevalence estimate |
| Reference choice changes the matched comparison | `tables/matched_comparison.csv`, `tables/matched_selection_summary.csv` | Same target/control axes, opposite criterion choices, 23.1-point regret | Controlled diagnostic policies, not learned-method frequency |
| Event boundary | `tables/robustness_event_types.csv` | ADD is conflict-free; REVISE and RETIRE reproduce the obsolete conflict | Controlled event generator |
| Signal-noise robustness | `tables/robustness_noise_selection.csv` | `Q_eval` ranking reversal occurs in all ten injected-noise conditions | Injected noise; post-hoc derived selection analysis |
| Reader transfer | `tables/robustness_reader_selection.csv` | Criterion choices differ on all three exploratory reader arms | Exploratory; not production generalization |
