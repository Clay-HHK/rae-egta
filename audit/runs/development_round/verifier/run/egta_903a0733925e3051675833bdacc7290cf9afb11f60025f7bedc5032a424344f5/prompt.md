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
  "evidence_package_id": "egta_evidence_8ccc1cc4eb1797c722f0a402a927b1e42d5f172aa0dd883e43bf2121a73cb542",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://deathpenaltyinfo.org/state-and-federal-info/state-by-state",
      "content_sha256": "d000fde19147b32bc30a15da7cf320f9fc59c2476f456bef8479a4aa9c55bc66",
      "evidence_id": "e1",
      "publication_or_update_date": null,
      "publisher": "Death Penalty Information Center",
      "source_tier": "authoritative_database",
      "spans": [
        {
          "locator": "section heading on state-by-state page",
          "span_id": "s1",
          "supports": [
            "current_answer"
          ],
          "text": "States With the Death Penalty (27)"
        },
        {
          "locator": "section heading on state-by-state page",
          "span_id": "s2",
          "supports": [
            "current_answer"
          ],
          "text": "States Without The Death Penalty (23)"
        },
        {
          "locator": "list entry under states without the death penalty",
          "span_id": "s3",
          "supports": [
            "change_date"
          ],
          "text": "New Hampshire (2019)"
        },
        {
          "locator": "list entry under states without the death penalty",
          "span_id": "s4",
          "supports": [
            "change_date"
          ],
          "text": "Colorado (2020)"
        },
        {
          "locator": "list entry under states without the death penalty",
          "span_id": "s5",
          "supports": [
            "change_date"
          ],
          "text": "Virginia (2021)"
        }
      ],
      "title": "State by State | Death Penalty Information Center",
      "url": "https://deathpenaltyinfo.org/state-and-federal-info/state-by-state"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://worldpopulationreview.com/state-rankings/death-penalty-states",
      "content_sha256": "23a93348dd2d19fd333089c7682a3fc46b6c76ff457b407c4d13d5061d49b3e9",
      "evidence_id": "e2",
      "publication_or_update_date": null,
      "publisher": "World Population Review",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "table column/section heading; 27 states listed with Yes status, table also shows New Hampshire 2019, Colorado 2020, Virginia 2021 abolition years in cells",
          "span_id": "s1",
          "supports": [
            "current_answer"
          ],
          "text": "Death Penalty Permitted"
        }
      ],
      "title": "Death Penalty States 2026",
      "url": "https://worldpopulationreview.com/state-rankings/death-penalty-states"
    }
  ],
  "frozen_answer": {
    "normalized": "31",
    "raw": "31"
  },
  "item_id": "egta_903a0733925e3051675833bdacc7290cf9afb11f60025f7bedc5032a424344f5",
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
  "question": "nq question: how many us states currently use capital punishment",
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
