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
  "evidence_package_id": "egta_evidence_91ba16170d2d28d8f3f24ab1b6a8f2a8bd0075fa9edc948722e5f18f2f06ec04",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/2025_World_Open_(snooker)",
      "content_sha256": "c3159b84db539e45831ad3db5bdb14aa9e6096f72dcb4cfed51ab38fd618872a",
      "evidence_id": "e1",
      "publication_or_update_date": null,
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "lead section",
          "span_id": "s1",
          "supports": [
            "current_answer"
          ],
          "text": "The tournament took place from 23 February to 1 March 2025 at the Yushan Sport Centre in Yushan, China."
        },
        {
          "locator": "background/history section",
          "span_id": "s2",
          "supports": [
            "frozen_answer",
            "current_answer",
            "change_date"
          ],
          "text": "China hosted the event from 2012 to 2014 in Haikou, and in Yushan since 2016."
        }
      ],
      "title": "2025 World Open (snooker) - Wikipedia",
      "url": "https://en.wikipedia.org/wiki/2025_World_Open_(snooker)"
    }
  ],
  "frozen_answer": {
    "normalized": "haikou",
    "raw": "Haikou"
  },
  "item_id": "egta_1da06ca032c2414b5e396089200bb9a4df6942ac65404f98456dfb3def960b1c",
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
  "question": "nq question: where is the snooker world open currently held",
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
