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
  "evidence_package_id": "egta_evidence_d60c3ff434f54ecdcfef4305dd007435cd5d302ffabea57f02f0ce7e9285aa8c",
  "evidence_sources": [
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://upstox.com/news/personal-finance/investing/public-provident-fund-ppf-interest-rate-for-july-september-2026/article-196141/",
      "content_sha256": "3a926439721331e9dec5ab23636fd1e6e81be44e512f266694dbf7473de60ebd",
      "evidence_id": "e1",
      "publication_or_update_date": "2026-06-30",
      "publisher": "Upstox",
      "source_tier": "reliable_secondary",
      "spans": [
        {
          "locator": "article body, opening section",
          "span_id": "s1",
          "supports": [
            "current_answer"
          ],
          "text": "you can continue to earn 7.1% tax-free compound interest on your PPF deposits"
        },
        {
          "locator": "article body, rate history paragraph",
          "span_id": "s2",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "There has been no change in the interest rate for PPF deposits since April 1, 2020."
        }
      ],
      "title": "Public Provident Fund (PPF) interest rate July-September 2026: 7.1% compounding to continue",
      "url": "https://upstox.com/news/personal-finance/investing/public-provident-fund-ppf-interest-rate-for-july-september-2026/article-196141/"
    },
    {
      "access_date": "2026-07-22",
      "canonical_url": "https://en.wikipedia.org/wiki/Public_Provident_Fund_(India)",
      "content_sha256": "b2f74420e60739c240a1e7aa55bc77f868eff70e69343bff222e3447f8238eb4",
      "evidence_id": "e2",
      "publication_or_update_date": null,
      "publisher": "Wikipedia",
      "source_tier": "versioned_encyclopedia",
      "spans": [
        {
          "locator": "historical interest rates table, row for Jan-Sep 2018 (cells joined with |)",
          "span_id": "s1",
          "supports": [
            "frozen_answer"
          ],
          "text": "January 2018 – September 2018 | 7.6%"
        },
        {
          "locator": "historical interest rates table, most recent row (cells joined with |)",
          "span_id": "s2",
          "supports": [
            "current_answer",
            "change_date"
          ],
          "text": "April 2020 – September 2026 | 7.1%"
        }
      ],
      "title": "Public Provident Fund (India)",
      "url": "https://en.wikipedia.org/wiki/Public_Provident_Fund_(India)"
    }
  ],
  "frozen_answer": {
    "normalized": "7.6%",
    "raw": "7.6%"
  },
  "item_id": "egta_aef0d688e8e49b4d34953f719f6cd92ea1f91d38e76293319308860cced67c5a",
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
  "question": "nq question: what is the current rate of interest on ppf",
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
