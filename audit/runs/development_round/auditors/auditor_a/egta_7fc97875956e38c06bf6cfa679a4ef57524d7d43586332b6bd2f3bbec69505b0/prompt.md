You are an isolated Evidence-Grounded Temporal Audit auditor.

Role: auditor_a

Use only the blind request below. Do not use prior annotations, hidden model
memory as evidence, external tools, browsing, or unstated aliases. A cited
evidence ID must occur in `evidence_sources`. Return exactly one JSON object
matching the supplied output schema, with no Markdown fences or prose outside
the object. `manual_override` must be false. Use `verdict: null` only when
`temporal_scope` is `non_temporal`. Abstain conservatively when dates, sources,
or temporal semantics are insufficient.

For `change_date.earliest` and `change_date.latest`, prefer complete ISO dates
(`YYYY-MM-DD`). Year precision may use `YYYY`, and month precision may use
`YYYY-MM`; the parser expands an earliest bound to the start of that period and
a latest bound to its end. Use `null` bounds with `precision: "unknown"`.

Blind request:

{
  "evidence_package_id": "egta_evidence_e5830398ea129b98037f2ccf095924dc461a7273c17f2cf5e415ce2dc64633c7",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/President_pro_tempore_of_the_United_States_Senate",
      "content_sha256": "b786dc50f59bba723af0ec4c562681dd17492e0535f546b0c7bd9e57b7b71297",
      "evidence_id": "e1",
      "publication_or_update_date": null,
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "infobox, under Incumbent Chuck Grassley",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "since January 3, 2025"
        },
        {
          "locator": "historical list of presidents pro tempore",
          "span_id": "s2",
          "supports": [
            "frozen_answer",
            "change_date"
          ],
          "text": "Hatch (2015–2019)"
        }
      ],
      "title": "President pro tempore of the United States Senate - Wikipedia",
      "url": "https://en.wikipedia.org/wiki/President_pro_tempore_of_the_United_States_Senate"
    }
  ],
  "frozen_answer": {
    "normalized": "orrin hatch",
    "raw": "Orrin Hatch"
  },
  "item_id": "egta_7fc97875956e38c06bf6cfa679a4ef57524d7d43586332b6bd2f3bbec69505b0",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "currently"
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
  "question": "nq question: who is currently serving as president of the senate",
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
