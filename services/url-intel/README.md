# url-intel

Real-time URL threat-intelligence service. Maintains a Redis-backed
blocklist fed by:

1. **fraud.signals.v1** — brain-content publishes URL-related signals
   (e.g. `sms.malicious_url`); url-intel ingests with a 30-day TTL.
2. **External threat feeds** — `POST /feeds/import` accepts batches
   from VirusTotal / PhishTank / GSMA / peer-telco shares, etc.
3. **Manual analyst additions** — `POST /blocklist/add` for SOC.

The DNS sinkhole pulls the full list via `GET /blocklist/export`.
The Tier-1 `DnsSinkholeActuator` registers each `url.block` decision
here before pushing to the resolver — that gives us central
allow-listing of legitimate domains (mtn.com.gh, google.com, etc.)
which the actuator path alone would miss.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/blocklist/check?url=...` | Sub-5ms verdict |
| `GET`  | `/blocklist/export` | Full domain list (DNS sinkhole pull) |
| `POST` | `/blocklist/add` | Manual analyst add |
| `POST` | `/blocklist/remove` | Manual analyst remove |
| `POST` | `/feeds/import` | Bulk import |
| `GET`  | `/health/{live,ready}` | k8s probes |
| `GET`  | `/metrics` | Prometheus scrape |

## Allow-list

Configurable via `URL_INTEL_ALLOW_LIST` (CSV). Hard-coded defaults:
google.com, facebook.com, whatsapp.com, mtn.com.gh, bog.gov.gh,
ecobank.com, x.com, microsoft.com, apple.com, amazon.com (plus more).
A domain in the allow-list — or a subdomain of one — is **never**
blocked, even if a feed or signal says otherwise. `add` returns
`{added: false, reason: "allow_listed"}`. `check` returns
`{blocked: false, allow_listed: true}`.

## Storage

Redis DB 4 by convention (`URL_INTEL_REDIS_URL`).

| Key | Type | Notes |
|---|---|---|
| `urlintel:domains` | SET | Blocked domains, what `/export` returns |
| `urlintel:meta:<domain>` | HASH | source, category, confidence, added_at_ms |

## Operational

- **Inputs:** `fraud.signals.v1` (URL-related)
- **Output:** REST API
- **State:** Redis
- **Suppression:** Per-domain TTL on signal-driven entries (default 30d)

## Configuration

| Env | Default | Notes |
|---|---|---|
| `URL_INTEL_REDIS_URL` | `redis://localhost:6379/4` | DB 4 |
| `URL_INTEL_SIGNAL_TTL_S` | 2592000 (30d) | TTL for signal-fed entries |
| `URL_INTEL_ALLOW_LIST` | (CSV) | Critical service domains |
| `URL_INTEL_LISTEN_SIGNALS` | `1` | Set `0` to disable Kafka listener |

## Runbook

[`docs/runbooks/url-intel.md`](../../docs/runbooks/url-intel.md)
