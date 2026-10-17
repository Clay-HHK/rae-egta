# Robustness: Q_eval selection under signal noise

The post-hoc selection reanalysis uses one common `Q_eval` denominator for
both criteria and a target-success floor of 0.10. Ranking reversal is observed
in 10/10
conditions (`all=true`), spanning clean, missing
metadata, corrupted metadata, and intent error at 5%, 10%, and 20%.
Tie-robust current-valid regret ranges from
23.1% to 37.7%.

This supports a controlled robustness statement only. Noise is injected into
the synthetic generator and does not estimate naturally occurring production
errors. The inference table records paired cluster-bootstrap intervals for the
change in regret relative to clean; source runs and their hashes are listed in
`source_inventory.csv`.
