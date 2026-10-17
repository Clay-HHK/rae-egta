You are the isolated blind verifier for an Evidence-Grounded Temporal Audit.

Use only the request below. It intentionally contains no auditor identity,
auditor verdict, proposed auditor answer, disagreement reason, or legacy
annotation. Do not infer or request those fields. Do not browse or call tools.
Return exactly one JSON object matching the supplied output schema, with no
Markdown fences or prose outside the object. `manual_override` must be false.
Resolve the item from the frozen evidence only; otherwise abstain.

For `change_date.earliest` and `change_date.latest`, prefer complete ISO dates
(`YYYY-MM-DD`). Year precision may use `YYYY`, and month precision may use
`YYYY-MM`; the parser expands an earliest bound to the start of that period and
a latest bound to its end. Use `null` bounds with `precision: "unknown"`.

Blind verifier request:

{
  "evidence_package_id": "egta_evidence_8c5ba8b23532c78cc0fccff34e597e7c91959d7e1d84df72bbc62215baa12ffa",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://www.adobe.com/products/flashplayer/end-of-life-alternative.html",
      "content_sha256": "3d81b9eca1c6ed99e7b6822550360c538b007d867aa22c097902dbd2e076e294",
      "evidence_id": "e1",
      "publication_or_update_date": "2021-01-13",
      "publisher": "Adobe",
      "source_tier": "official_primary",
      "spans": [
        {
          "locator": "Adobe Flash Player EOL General Information 段",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "Adobe stopped supporting Flash Player beginning December 31, 2020"
        },
        {
          "locator": "FAQ 段（页面多处出现）",
          "span_id": "s2",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "Adobe blocked Flash content from running in Flash Player beginning January 12, 2021"
        },
        {
          "locator": "EOL General Information 开头段落",
          "span_id": "s3",
          "supports": [
            "current_answer"
          ],
          "text": "Adobe strongly recommends all users immediately uninstall Flash Player to help protect their systems."
        }
      ],
      "title": "Adobe Flash Player End of Life",
      "url": "https://www.adobe.com/products/flashplayer/end-of-life-alternative.html"
    }
  ],
  "frozen_answer": {
    "normalized": "28.0.0.137",
    "raw": "28.0.0.137"
  },
  "item_id": "egta_3d23c5df19abf36a79484571fb27b063049d79fe4c616f289fa6f77bad41e5c0",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "most current"
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
  "question": "nq question: what is the most current adobe flash player version",
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
