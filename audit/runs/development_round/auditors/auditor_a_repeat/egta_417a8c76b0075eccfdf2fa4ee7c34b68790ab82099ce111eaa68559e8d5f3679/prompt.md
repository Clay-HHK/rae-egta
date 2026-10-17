You are an isolated Evidence-Grounded Temporal Audit auditor.

Role: auditor_a_repeat

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
  "evidence_package_id": "egta_evidence_a085ada41c23b1972aaee7910e1b9cd70f727b64fab6a5b8a13d95eea4a402c4",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://learn.microsoft.com/en-us/officeupdates/update-history-office-2010-click-to-run",
      "content_sha256": "1bef332dbb3f4b3810ab3406c41e336a2a5e159007e28bfadf9de9a581d6f8ac",
      "evidence_id": "e1",
      "publication_or_update_date": null,
      "publisher": "Microsoft Learn",
      "source_tier": "official_primary",
      "spans": [
        {
          "locator": "note box above the update table (bold markers removed)",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "The most current version of Office 2010 is 14.0.7261.5000, which was released on October 13, 2020."
        },
        {
          "locator": "update history table, 2017 release-year block, row cells joined with |",
          "span_id": "s2",
          "supports": [
            "frozen_answer"
          ],
          "text": "June 13 | 14.0.7182.5000 | KB 4023935"
        },
        {
          "locator": "Important banner at top of page",
          "span_id": "s3",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "Support for Office 2010 ended on October 13, 2020."
        }
      ],
      "title": "Update history for Office 2010 Click-to-Run products - Office release notes | Microsoft Learn",
      "url": "https://learn.microsoft.com/en-us/officeupdates/update-history-office-2010-click-to-run"
    }
  ],
  "frozen_answer": {
    "normalized": "14.0.7182.5000",
    "raw": "14.0.7182.5000"
  },
  "item_id": "egta_417a8c76b0075eccfdf2fa4ee7c34b68790ab82099ce111eaa68559e8d5f3679",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "latest"
      },
      {
        "kind": "explicit_year",
        "text": "2010"
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
  "question": "nq question: what is the latest version of microsoft office 2010",
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
