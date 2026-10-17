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
  "evidence_package_id": "egta_evidence_0703e8774227bd5b3c331255b1b5124729c7982083491be2e5575fe29d0106bc",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://www.espn.com/nfl/recap/_/gameId/401671889",
      "content_sha256": "0eb055c3aa3f19756bade77878bbe76625acf807594a3f27c2d7ac7b0a226735",
      "evidence_id": "e1",
      "publication_or_update_date": "2025-02-09",
      "publisher": "ESPN",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "game recap body (Super Bowl LIX, Feb 9, 2025)",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "the Eagles routed the Chiefs 40-22 on Sunday to secure the franchise's second Super Bowl championship."
        },
        {
          "locator": "game recap lead",
          "span_id": "s2",
          "supports": [
            "current_answer"
          ],
          "text": "the Philadelphia Eagles' ferocious defense denied Patrick Mahomes, Travis Kelce and the Kansas City Chiefs a Super Bowl three-peat"
        }
      ],
      "title": "Eagles 40-22 Chiefs (Feb 9, 2025) Game Recap - ESPN",
      "url": "https://www.espn.com/nfl/recap/_/gameId/401671889"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/Super_Bowl_LII",
      "content_sha256": "5735a3e87188f7095355d49689754f93ff5775d941dadc6e3af09a897a1ba758",
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
          "text": "The underdog Eagles defeated the Patriots with a score of 41–33 to win their first Super Bowl and their first NFL title since 1960."
        },
        {
          "locator": "infobox, Date field (Super Bowl LII, i.e. the 2017-season championship game)",
          "span_id": "s2",
          "supports": [
            "frozen_answer"
          ],
          "text": "February 4, 2018"
        }
      ],
      "title": "Super Bowl LII",
      "url": "https://en.wikipedia.org/wiki/Super_Bowl_LII"
    }
  ],
  "frozen_answer": {
    "normalized": "2017",
    "raw": "2017"
  },
  "item_id": "egta_703b072fd3c02387d80c035d491e2dc519b599cfb7e582af259254b6a0b0f6e1",
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
  "question": "nq question: when was the last year the eagles went to the superbowl",
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
