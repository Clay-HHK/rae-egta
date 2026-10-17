# Human expert review protocol

Status: `PENDING_HUMAN`

Scope: the six EGTA-accepted supersessions only. Completion supports
accepted-case correctness and evidence sufficiency; it does not estimate
full-pool EGTA reliability. To evaluate reliability, apply the same protocol
to all 47 unique pairs.

1. The administrator sends only `cases/` and one blank reviewer CSV to each
   reviewer. Do not send `admin/case_key.csv`, EGTA verdicts, or model outputs.
2. Two qualified reviewers label every case independently.
3. Freeze both completed reviewer CSVs before comparison; record their SHA256.
4. Exact agreement requires the same verdict, answer state/value, event type,
   and compatible change-date interval.
5. An independent adjudicator resolves every disagreement from the same frozen
   evidence. No new evidence may be added silently; additions require a new
   versioned case packet.
6. The administrator completes the signoff only after identity, independence,
   hashes, and adjudication completeness have been checked.

Allowed verdicts: `superseded_revision`, `superseded_retirement`,
`still_valid`, `historically_valid`, `ambiguous_context`,
`insufficient_evidence`, `source_conflict`.
