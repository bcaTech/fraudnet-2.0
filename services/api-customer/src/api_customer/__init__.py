"""api-customer — customer self-service API.

Authentication: customer's MSISDN via OTP. The OTP is delivered out-of-band
(SMS) by an MTN OTP service; api-customer integrates via a thin Adapter
interface (DECISIONS.md D-005). Phase 1 ships a stub adapter; the real one
lands when the security team's OTP service rollout completes.

A successful OTP exchange returns a session JWT signed by api-customer.
The session JWT carries `msisdn` and `tenant_id`; the customer is their
own tenant ('mtn-ghana' for Phase 1).

Endpoints:
  POST /auth/request_otp { msisdn } → 202 (OTP sent OOB)
  POST /auth/verify_otp { msisdn, code } → { session_token, expires_in }
  GET  /me/alerts → list of alerts where the subject is the customer's MSISDN
  POST /me/report { kind, indicator, notes } → forwards to intel.events.v1
  POST /me/block { msisdn } → tier-2 self-service block (writes to action.tier2.v1)
  GET  /me/status → MSISDN status + recent activity summary
"""

__version__ = "0.1.0"
