You are an isolated Evidence-Grounded Temporal Audit auditor.

Role: auditor_a

Use only the blind request below. Do not use prior annotations, hidden model
memory as evidence, external tools, browsing, or unstated aliases. A cited
evidence ID must occur in `evidence_sources`. Return exactly one JSON object
matching the supplied output schema, with no Markdown fences or prose outside
the object. `manual_override` must be false. Use `verdict: null` only when
`temporal_scope` is `non_temporal`. Abstain conservatively when dates, sources,
or temporal semantics are insufficient.

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

Blind request:

{
  "evidence_package_id": "egta_evidence_a482982ba9a8c979ee19b978f6de860808684164eb5363cc0b9654ba9ce3ef2b",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/2017_AFL_Mark_of_the_Year",
      "content_sha256": "e681166d8306b72c78595ceffd510d0114518575465a8f93737828e47a93a46b",
      "evidence_id": "e1",
      "publication_or_update_date": "2026-01-16",
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "article body, winner announcement",
          "span_id": "s1",
          "supports": [
            "frozen_answer",
            "current_answer"
          ],
          "text": "The winner was Essendon's Joe Daniher, which surprised many fans and media personal who expected Jeremy Howe of Collingwood to win the award"
        }
      ],
      "title": "2017 AFL Mark of the Year",
      "url": "https://en.wikipedia.org/wiki/2017_AFL_Mark_of_the_Year"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://www.zerohanger.com/2017-mark-goal-year-winners-announced-15967/",
      "content_sha256": "4b200dce4f9ab2eae3a238637809d651711ee7f0f620ddc7db484a3d299f12c6",
      "evidence_id": "e2",
      "publication_or_update_date": "2017-09-25",
      "publisher": "Zero Hanger",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "article body",
          "span_id": "s2",
          "supports": [
            "frozen_answer"
          ],
          "text": "Essendon big man Joe Daniher took out the Mark of the Year"
        },
        {
          "locator": "article body",
          "span_id": "s3",
          "supports": [
            "frozen_answer"
          ],
          "text": "Richmond youngster Daniel Rioli has been awarded the 2017 Goal of the Year for his remarkable effort from the boundary against West Coast."
        }
      ],
      "title": "2017 Mark and Goal of the Year winners announced",
      "url": "https://www.zerohanger.com/2017-mark-goal-year-winners-announced-15967/"
    }
  ],
  "frozen_answer": {
    "normalized": "joe daniher",
    "raw": "Joe Daniher"
  },
  "item_id": "egta_0dd7d306ea549b8e372c89e7ea6deea09860d8b16d46aa6c1243fe149c64d2bb",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "explicit_year",
        "text": "2017"
      }
    ],
    "audit_as_of": "2026-07-21",
    "direction": "ambiguous",
    "reference_time": {
      "earliest": null,
      "latest": "2019-12-31",
      "precision": "year",
      "provenance": "NQ (TACL 2019) annotation no later than publication year; see audit/evidence/REFERENCE_TIME_PROVENANCE.md"
    },
    "scope": "uncertain"
  },
  "question": "nq question: who won mark and goal of the year 2017",
  "schema_version": "egta.v1"
}

Output JSON schema:

{
  "additionalProperties": false,
  "properties": {
    "change_date": {
      "additionalProperties": false,
      "properties": {
        "earliest": {
          "type": [
            "string",
            "null"
          ]
        },
        "latest": {
          "type": [
            "string",
            "null"
          ]
        },
        "precision": {
          "enum": [
            "day",
            "month",
            "year",
            "interval",
            "unknown"
          ],
          "type": "string"
        }
      },
      "required": [
        "earliest",
        "latest",
        "precision"
      ],
      "type": "object"
    },
    "credible_source_conflict": {
      "type": "boolean"
    },
    "direction": {
      "enum": [
        "current",
        "historical",
        "ambiguous",
        "none"
      ],
      "type": "string"
    },
    "event_type": {
      "enum": [
        "addition",
        "revision",
        "retirement",
        "none",
        "unknown"
      ],
      "type": "string"
    },
    "frozen_answer_identified": {
      "type": "boolean"
    },
    "item_id": {
      "type": "string"
    },
    "manual_override": {
      "type": "boolean"
    },
    "proposed_current_answer": {
      "additionalProperties": false,
      "properties": {
        "canonical": {
          "type": [
            "string",
            "null"
          ]
        },
        "state": {
          "enum": [
            "value",
            "absent_or_retired",
            "unresolved"
          ],
          "type": "string"
        }
      },
      "required": [
        "state",
        "canonical"
      ],
      "type": "object"
    },
    "rationale": {
      "type": "string"
    },
    "schema_version": {
      "enum": [
        "egta_judgment.v1"
      ],
      "type": "string"
    },
    "supporting_evidence_ids": {
      "items": {
        "type": "string"
      },
      "type": "array"
    },
    "temporal_scope": {
      "enum": [
        "temporal",
        "non_temporal",
        "uncertain"
      ],
      "type": "string"
    },
    "verdict": {
      "enum": [
        "still_valid",
        "superseded_revision",
        "superseded_retirement",
        "historically_valid",
        "ambiguous_context",
        "insufficient_evidence",
        "source_conflict",
        null
      ],
      "type": [
        "string",
        "null"
      ]
    }
  },
  "required": [
    "schema_version",
    "item_id",
    "temporal_scope",
    "direction",
    "verdict",
    "event_type",
    "frozen_answer_identified",
    "proposed_current_answer",
    "change_date",
    "supporting_evidence_ids",
    "credible_source_conflict",
    "manual_override",
    "rationale"
  ],
  "type": "object"
}
