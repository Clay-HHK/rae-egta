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
  "evidence_package_id": "egta_evidence_63519d58ae1e0676014d391de065555f25886d236534c5b214f12cceb0c6b134",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://www.espn.com/womens-college-basketball/story/_/id/39445812/ncaa-womens-basketball-championship-all-winners-list",
      "content_sha256": "5205b3c94a6f76cd4d6365523448c653e85e45cc183bdeec098e4041341f3c6e",
      "evidence_id": "e1",
      "publication_or_update_date": "2026-04-05",
      "publisher": "ESPN",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "all-time winners list, 2017 entry",
          "span_id": "s1",
          "supports": [
            "frozen_answer"
          ],
          "text": "2017 South Carolina"
        },
        {
          "locator": "all-time winners list, 2025 entry",
          "span_id": "s2",
          "supports": [
            "current_answer"
          ],
          "text": "2025 UConn"
        },
        {
          "locator": "all-time winners list, 2026 entry",
          "span_id": "s3",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "2026 UCLA"
        }
      ],
      "title": "NCAA women's basketball championship: All-time winners list",
      "url": "https://www.espn.com/womens-college-basketball/story/_/id/39445812/ncaa-womens-basketball-championship-all-winners-list"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/2026_NCAA_Division_I_women%27s_basketball_championship_game",
      "content_sha256": "e59ab93e9d890aa2b1a69e94666973e00a1892b87a1bbe89f95e0e72071b7a47",
      "evidence_id": "e2",
      "publication_or_update_date": "2026-04-29",
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "lead section; game played April 5, 2026, UCLA vs South Carolina",
          "span_id": "s1",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "The Bruins won the game 79–51 for the program's first NCAA title"
        },
        {
          "locator": "South Carolina Gamecocks section",
          "span_id": "s2",
          "supports": [
            "current_answer"
          ],
          "text": "Coming off their 82–59 loss in the 2025 national championship game to UConn"
        }
      ],
      "title": "2026 NCAA Division I women's basketball championship game",
      "url": "https://en.wikipedia.org/wiki/2026_NCAA_Division_I_women%27s_basketball_championship_game"
    }
  ],
  "frozen_answer": {
    "normalized": "south carolina",
    "raw": "South Carolina"
  },
  "item_id": "egta_27cc3be577dfa199a8288d6fb2564e669caa22e78287396813f898c77fe7f1f4",
  "protocol_version": "egta_47pair_v1",
  "query_temporal_context": {
    "anchors": [
      {
        "kind": "deictic",
        "text": "last year's"
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
  "question": "nq question: who won last year's ncaa women's basketball",
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
