You are the isolated blind verifier for an Evidence-Grounded Temporal Audit.

Use only the request below. It intentionally contains no auditor identity,
auditor verdict, proposed auditor answer, disagreement reason, or legacy
annotation. Do not infer or request those fields. Do not browse or call tools.
Return exactly one JSON object matching the supplied output schema, with no
Markdown fences or prose outside the object. `manual_override` must be false.
Resolve the item from the frozen evidence only; otherwise abstain.

## Temporal reading policy

Apply this protocol policy before assigning `direction` or a verdict:

- Interpret all deictic or present-tense expressions—including "now",
  "current", "currently", "right now", "latest", "most recent", and
  unqualified present-tense superlatives—relative to the date in
  `query_temporal_context.audit_as_of`. This is the protocol's modeling
  assumption corresponding to the paper's default current reading for an
  unqualified query.
- Interpret a question with an explicit year or date anchor at that anchored
  time; its `direction` is `historical`.
- Reserve `ambiguous_context` for ambiguity that remains after applying this
  policy, such as an unclear answer-year convention (season year versus
  calendar year) or unclear entity reference. Do not use `ambiguous_context`
  for a disagreement between annotation time and current time; this policy
  resolves that disagreement.

Under this policy, set `direction` to `current` for a deictic or otherwise
unqualified present-tense question, to `historical` for an explicitly
year/date-anchored question, and to `ambiguous` only when the policy cannot
resolve the direction.

## Verdict decision guide

Apply these outcomes symmetrically; do not presume that the frozen answer was
superseded:

- `superseded_revision`: evidence establishes that the frozen answer held at
  or near `query_temporal_context.reference_time`, and a later revision makes
  it invalid at the audit time.
- `superseded_retirement`: evidence establishes that the frozen answer held at
  or near `query_temporal_context.reference_time`, and a later retirement makes
  it invalid at the audit time.
- `still_valid`: the frozen answer remains valid at the audit time.
- `historically_valid`: the question has an explicit year/date anchor and the
  frozen answer is valid at that anchored time.
- `insufficient_evidence`: evidence is missing, lacks usable timestamps, or
  cannot be located well enough to decide.
- `source_conflict`: credible sources contradict one another on the decision.
- `ambiguous_context`: material ambiguity remains after applying the temporal
  reading policy.
- For a question that is not time-sensitive, set `temporal_scope` to
  `non_temporal`, `direction` to `none`, and `verdict` to `null`.

For `change_date.earliest` and `change_date.latest`, prefer complete ISO dates
(`YYYY-MM-DD`). Year precision may use `YYYY`, and month precision may use
`YYYY-MM`; the parser expands an earliest bound to the start of that period and
a latest bound to its end. Use `null` bounds with `precision: "unknown"`.

Blind verifier request:

{{REQUEST_JSON}}

Output JSON schema:

{{OUTPUT_SCHEMA}}
