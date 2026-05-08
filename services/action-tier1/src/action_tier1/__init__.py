"""action-tier1 — inline (sub-200ms) actuators per CLAUDE.md §5.4.

Consumes `action.tier1.v1` and dispatches to the appropriate backend:

  volte.tag_suspected_spam → IMS core SIP-header rewrite
  url.block               → DNS sinkhole push
  sms.block               → SMSC outbound block list
  momo.send_with_care     → MoMo BSS friction prompt

All actuator calls are HTTP-based with strict timeouts. Outcome emitted to
`actions.taken.v1` for the feedback loop.
"""

__version__ = "0.1.0"
