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
  "evidence_package_id": "egta_evidence_6c5ad9bdb7af188e2cc7bc9d974c967f6cd13716519fde2378b87cd5b9190b2f",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://chromereleases.googleblog.com/2026/07/",
      "content_sha256": "2f872953f97e38af67f268680c33feec095002f4c3968e7022347969a72673c1",
      "evidence_id": "e1",
      "publication_or_update_date": null,
      "publisher": "Google (Chrome Releases blog)",
      "source_tier": "official_primary",
      "spans": [
        {
          "locator": "post title in July 2026 archive listing",
          "span_id": "s1",
          "supports": [
            "current_answer"
          ],
          "text": "Stable Channel Update for Desktop"
        },
        {
          "locator": "date header of most recent Stable Channel Update for Desktop post in July 2026 archive",
          "span_id": "s2",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "Tuesday, July 21, 2026"
        },
        {
          "locator": "date header of earlier Stable Channel Update for Desktop post in July 2026 archive",
          "span_id": "s3",
          "supports": [
            "current_answer"
          ],
          "text": "Thursday, July 16, 2026"
        }
      ],
      "title": "Chrome Releases",
      "url": "https://chromereleases.googleblog.com/2026/07/"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://chromereleases.googleblog.com/2018/01/",
      "content_sha256": "58451f7b7873ecc049656392f70428dae869a336a7c89cb85d42b9e31ab74f31",
      "evidence_id": "e2",
      "publication_or_update_date": null,
      "publisher": "Google (Chrome Releases blog)",
      "source_tier": "official_primary",
      "spans": [
        {
          "locator": "date header of Stable Channel Update for Desktop post in January 2018 archive (stable desktop posts that month: Jan 4 and Jan 24)",
          "span_id": "s1",
          "supports": [
            "frozen_answer"
          ],
          "text": "Wednesday, January 24, 2018"
        }
      ],
      "title": "Chrome Releases",
      "url": "https://chromereleases.googleblog.com/2018/01/"
    }
  ],
  "frozen_answer": {
    "normalized": "2018-01-22",
    "raw": "2018-01-22"
  },
  "item_id": "egta_48dd90728455b25e92cd213dbbf35bf75266c5e9039bcffc688c4b0fd88da493",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "latest"
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
  "question": "nq question: when was the latest version of chrome released",
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
