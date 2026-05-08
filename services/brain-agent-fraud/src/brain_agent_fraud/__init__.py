"""MoMo agent/merchant fraud detection.

Five dedicated detectors over momo.events.v1:

  - commission_farming  agent + customer pair generating excessive
                        cash-in/cash-out cycles to inflate commission
  - split_txn           large amount broken into N transactions just
                        below a monitoring threshold
  - phantom_customer    transactions against wallets with no prior
                        history (synthetic / dormant counterparty)
  - collusion           multiple agents sharing devices or moving funds
                        in coordinated patterns
  - float_manipulation  agents moving float between accounts to mask
                        activity, or holding excessive float

Each detector publishes a SignalEventV1 with a dedicated `signal_kind`:
  agent.commission_farming, agent.split_txn, agent.phantom_customer,
  agent.collusion, agent.float_manipulation.

XAI: every emitted signal carries feature contributions and a
human-readable explanation via fraudnet.xai. See `signal_builder.py`.
"""
