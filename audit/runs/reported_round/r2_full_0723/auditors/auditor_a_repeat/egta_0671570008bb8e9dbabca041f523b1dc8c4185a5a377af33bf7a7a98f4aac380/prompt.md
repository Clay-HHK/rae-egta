You are an isolated Evidence-Grounded Temporal Audit auditor.

Role: auditor_a_repeat

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
  "evidence_package_id": "egta_evidence_e34d06ff10706617d21921281ba2d662400c95ac751719c62a772e82b77c72dd",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/New_Birth_Missionary_Baptist_Church",
      "content_sha256": "3a5fb8d66507d05c23e3777417dd65f4754f42d8783653d2764eafef11778d95",
      "evidence_id": "e1",
      "publication_or_update_date": "2026-05-18",
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "History 段倒数第二段落",
          "span_id": "s1",
          "supports": [
            "frozen_answer",
            "change_date"
          ],
          "text": "In 2017, Stephen A. Davis, pastor of New Birth Birmingham in Birmingham, Alabama became senior pastor while remaining pastor of the Birmingham. However in June, 2018 Bishop Davis resigned."
        },
        {
          "locator": "History 段末句",
          "span_id": "s2",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "In December 2018, Jamal Bryant became senior pastor."
        },
        {
          "locator": "首段",
          "span_id": "s3",
          "supports": [
            "current_answer"
          ],
          "text": "Celebrity pastor Jamal Bryant is the senior pastor since 2018."
        }
      ],
      "title": "New Birth Missionary Baptist Church - Wikipedia",
      "url": "https://en.wikipedia.org/wiki/New_Birth_Missionary_Baptist_Church"
    }
  ],
  "frozen_answer": {
    "normalized": "stephen a. davis",
    "raw": "Stephen A. Davis"
  },
  "item_id": "egta_0671570008bb8e9dbabca041f523b1dc8c4185a5a377af33bf7a7a98f4aac380",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "new"
      },
      {
        "kind": "deictic",
        "text": "now"
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
  "question": "nq question: who is the pastor of new birth missionary baptist church now",
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
