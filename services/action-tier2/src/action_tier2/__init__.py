"""action-tier2 — near-real-time actuators per CLAUDE.md §5.4.

Consumes `action.tier2.v1` and dispatches to:

  customer.alert_smishing      → customer SMS / push notification
  customer.do_i_know_you_prompt → app prompt
  momo.review_limit            → MoMo BSS limit-review request
  safeguard.enroll             → SafeGuard auto-enrollment

Tolerates seconds-to-minutes latency; HTTP timeouts are 2 s rather than 100 ms.
"""

__version__ = "0.1.0"
