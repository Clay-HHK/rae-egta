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

{
  "evidence_package_id": "egta_evidence_e194cdeb4184b3cd7801d506fe686dbf501e60897606cebdb3d327c448a443da",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://www.hoopsrumors.com/2026/03/dwight-howard-announces-retirement.html",
      "content_sha256": "ed3683c43ad880e1ebbdd4b78672775d3257ea08bd1eb74149ac6520648a82a2",
      "evidence_id": "e1",
      "publication_or_update_date": "2026-03-12",
      "publisher": "Hoops Rumors",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "article lead paragraph",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "Longtime NBA center Dwight Howard officially announced his retirement from basketball on Thursday"
        },
        {
          "locator": "article body, career recap paragraph",
          "span_id": "s2",
          "supports": [
            "frozen_answer",
            "current_answer"
          ],
          "text": "From 2012-22, Howard bounced around the NBA, spending time with the Lakers, Rockets, Hawks, Hornets, Wizards, and Sixers."
        },
        {
          "locator": "article body, post-NBA activity paragraph",
          "span_id": "s3",
          "supports": [
            "current_answer"
          ],
          "text": "He has spent the past few years competing in Taiwan, Puerto Rico, and in the BIG3."
        }
      ],
      "title": "Dwight Howard Announces Retirement | Hoops Rumors",
      "url": "https://www.hoopsrumors.com/2026/03/dwight-howard-announces-retirement.html"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/Dwight_Howard",
      "content_sha256": "2a85c97909a795cccb198aa847b1ede87675b1037d131ce98df500d83096d7ec",
      "evidence_id": "e2",
      "publication_or_update_date": null,
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "article body, Atlanta Hawks section",
          "span_id": "s1",
          "supports": [
            "frozen_answer"
          ],
          "text": "On July 12, 2016, Howard signed a three-year, $70 million contract with his hometown team the Atlanta Hawks."
        },
        {
          "locator": "article body, retirement section",
          "span_id": "s2",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "On March 12, 2026, Howard announced his retirement from basketball."
        }
      ],
      "title": "Dwight Howard",
      "url": "https://en.wikipedia.org/wiki/Dwight_Howard"
    }
  ],
  "frozen_answer": {
    "normalized": "atlanta hawks",
    "raw": "Atlanta Hawks"
  },
  "item_id": "egta_daab0bc1b66530b276d14c88386844e35bc557abd76b84e4eddf691ba6af8e1e",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "last year"
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
  "question": "nq question: who did dwight howard play for last year",
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
