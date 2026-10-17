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
  "evidence_package_id": "egta_evidence_a4c18a8ebaa9e55658fc7515aa2a24272d8a370bf4a05b6ec86a0261f1e7fa0b",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://www.macworld.com/article/2644146/macos-26-features-latest-update-release-date-beta.html",
      "content_sha256": "ef6c5e8af743e0c6025bdc9d9763e2078ab85c373dd00ae580a08bf033ae6320",
      "evidence_id": "e1",
      "publication_or_update_date": "2026-06-29",
      "publisher": "Macworld",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "article summary/lead",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "The latest version of macOS is macOS Tahoe 26.5.2, which arrived on June 29, 2026."
        },
        {
          "locator": "article body, latest update section",
          "span_id": "s2",
          "supports": [
            "current_answer"
          ],
          "text": "This update has no new features and contains security fixes."
        }
      ],
      "title": "macOS 26 Tahoe Guide: All the new features in macOS Tahoe, including the latest update",
      "url": "https://www.macworld.com/article/2644146/macos-26-features-latest-update-release-date-beta.html"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/MacOS_High_Sierra",
      "content_sha256": "3e00d90e470b6bfb76a68cced4aafcec6c2269935c98bea4ba02d3b2603195c8",
      "evidence_id": "e2",
      "publication_or_update_date": null,
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "article lead section",
          "span_id": "s1",
          "supports": [
            "frozen_answer"
          ],
          "text": "macOS High Sierra (version 10.13) is the fourteenth major release of macOS, Apple's desktop operating system for Macintosh computers."
        },
        {
          "locator": "release history table row (version | build | release date cells joined with |)",
          "span_id": "s2",
          "supports": [
            "frozen_answer"
          ],
          "text": "10.13.4 | 17E199 | March 29, 2018"
        }
      ],
      "title": "macOS High Sierra",
      "url": "https://en.wikipedia.org/wiki/MacOS_High_Sierra"
    }
  ],
  "frozen_answer": {
    "normalized": "10.13.4",
    "raw": "10.13.4"
  },
  "item_id": "egta_e6aa2995af8c94faac2af949ec52db19626235fbe360cca400f0de0d724d4bb2",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "current"
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
  "question": "nq question: what is the current mac os operating system",
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
