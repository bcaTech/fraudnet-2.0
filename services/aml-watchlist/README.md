# aml-watchlist

Sanctions/PEP/internal watchlist service. Local mirror of:

- **UN Security Council Consolidated List** (XML feed, daily refresh)
- **OFAC SDN List** (CSV feed, daily refresh)
- **GFIC** (Ghana Financial Intelligence Centre) — manual import
- **Internal watchlist** — operator-defined, manual + API

## Matching

- Exact MSISDN / national-ID lookup (GIN index, sub-millisecond).
- Fuzzy name matching: Jaro-Winkler + Soundex + simplified Metaphone,
  composed into a [0, 1] score. Default threshold 0.85;
  `tier1_match_threshold` defaults to 0.90.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET  | `/watchlist/check/{kind}/{value}` | Real-time lookup (kind: `name` \| `msisdn` \| `national_id`) |
| GET  | `/watchlist/stats` | List counts, last refresh, 24h match volume |
| POST | `/watchlist/import` | Bulk import (CSV/XML/JSON) — `SYSTEM_ADMIN` + step-up |
| POST | `/watchlist/internal/add` | Manual internal entry — `FRAUD_LEAD` |

## Pipeline integration

`brain-behavioural` calls `/watchlist/check/msisdn/{value}` after every
score. A hit emits a separate `aml.watchlist_match` SignalEventV1 with
severity scaled by score:
- `>= 0.95` → CRITICAL → Tier 1 `momo.freeze_account` (compliance notification)
- `>= 0.90` → HIGH → Tier 1 `momo.send_with_care`
- otherwise → MEDIUM/LOW → Tier 3 investigation queue

Routing lives in `services/decisions/policies/default.yaml`
(rules `aml-watchlist-tier1-*` and `aml-watchlist-tier3`).

## Refresh

Refresh runs in-process every `AML_REFRESH_INTERVAL_S` seconds (default
24h). The atomic-replace operation deactivates the prior generation
inside a transaction; previously-active rows remain detected until a
successful refresh, so transient feed errors don't regress coverage.

Manual refresh: `POST /watchlist/import` (same parsers as the cron).

## Audit

Every check writes to `watchlist_match_log` with the query *hash*
(SHA-256, 16 hex chars) — never the raw value. Hits surface in
`/watchlist/stats` 24h volume; misses are logged for threshold tuning.

## PII

No raw query value is ever persisted in the audit log. The matcher
itself works on plaintext (it has to — fuzzy matching needs the
strings) but is in-process and the strings do not leak.
