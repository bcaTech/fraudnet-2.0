"""AML watchlist service.

Maintains a local mirror of:
  - UN Security Council Consolidated List (XML feed, daily refresh)
  - OFAC SDN List (CSV feed, daily refresh)
  - Ghana Financial Intelligence Centre (GFIC) domestic list (manual import)
  - Operator-defined internal watchlist (manual + API)

Matching: exact MSISDN / national ID + fuzzy name (Jaro-Winkler + Soundex
+ simplified Metaphone). Default match threshold 0.85.

Integration:
  - brain-behavioural calls `/watchlist/check/{kind}/{value}` before
    publishing every scored entity.
  - A hit ≥ 0.9 → Tier-1 immediate freeze + compliance notification
    (configured in services/decisions/policies/default.yaml).
"""
