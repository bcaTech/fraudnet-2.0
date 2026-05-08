"""Populate the local stack with a realistic demo dataset.

Targets:
  Memgraph    500 Numbers, 200 Devices, 100 Wallets
              + CALLED / SMSED / SENT / OWNS / USED edges (a small fraction of
              the cartesian — denser around three injected fraud rings so the
              moat detection path has something to find).

  Aerospike   feature snapshots (numbers set + wallets set) for every seeded
              MSISDN and wallet — call-velocity, fan-out, IMEI churn, MoMo
              velocity. Inflated for nodes that belong to a ring.

  Postgres
    fraudnet              7 users (NOC roles), 10 rings, ring members,
                          50 alerts, 5 takedowns referencing rings.
    fraudnet_audit        100 audit events spread across actors and actions.

Idempotent. Re-running upserts (Postgres rows by id; Memgraph by MERGE).
Safe to point at the dev compose stack.

Geographic anchors (used as device location stamps and ring footprints):
  Accra (GA), Kumasi (KU), Tamale (TM), Cape Coast (CC), Takoradi (TK).

Phone numbers: Ghanaian E.164 (+233...). MCC-620 IMSIs. Luhn-valid 15-digit
IMEIs.
"""

from __future__ import annotations

import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

# -- Optional deps loaded at top so we fail fast with a useful message ------
try:
    import psycopg  # type: ignore[import-not-found]
except ImportError:
    try:
        import psycopg2 as psycopg  # type: ignore[import-not-found,no-redef]
    except ImportError:
        psycopg = None  # type: ignore[assignment]

try:
    from neo4j import GraphDatabase  # type: ignore[import-not-found]
except ImportError:
    GraphDatabase = None  # type: ignore[assignment, misc]

try:
    import aerospike  # type: ignore[import-not-found]
except ImportError:
    aerospike = None  # type: ignore[assignment]


# ---- Config -----------------------------------------------------------------

PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_USER = os.environ.get("POSTGRES_USER", "fraudnet")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "fraudnet_dev")
PG_DB = os.environ.get("POSTGRES_DB", "fraudnet")
AUDIT_DB = os.environ.get("AUDIT_DB", "fraudnet_audit")

MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687")
MEMGRAPH_USER = os.environ.get("MEMGRAPH_USER", "")
MEMGRAPH_PASS = os.environ.get("MEMGRAPH_PASSWORD", "")

AS_HOSTS_RAW = os.environ.get("AEROSPIKE_HOSTS", "localhost:3010")
AS_NAMESPACE = os.environ.get("AEROSPIKE_NAMESPACE", "fraudnet")

NUM_NUMBERS = int(os.environ.get("DEMO_NUMBERS", "500"))
NUM_DEVICES = int(os.environ.get("DEMO_DEVICES", "200"))
NUM_WALLETS = int(os.environ.get("DEMO_WALLETS", "100"))
NUM_USERS = 7
NUM_RINGS = 10
NUM_ALERTS = 50
NUM_TAKEDOWNS = 5
NUM_AUDIT = 100

# Stable RNG so re-running this seeder produces the same dataset (handy for
# regression tests that diff against a known-good fixture).
SEED = int(os.environ.get("DEMO_SEED", "42"))
rng = random.Random(SEED)


# ---- Ghana data ------------------------------------------------------------

GH_PREFIXES = (
    "024", "025", "053", "054", "055", "059",   # MTN
    "020", "050",                                # Vodafone (Telecel)
    "026", "027", "056", "057",                  # AirtelTigo
)

GH_REGIONS = (
    # name, lat, lon, location_code
    ("Accra",       5.6037,  -0.1870, "GA"),
    ("Kumasi",      6.6885,  -1.6244, "KU"),
    ("Tamale",      9.4034,  -0.8424, "TM"),
    ("Cape Coast",  5.1053,  -1.2466, "CC"),
    ("Takoradi",    4.8845,  -1.7554, "TK"),
)

NOC_ROLES = ("FRAUD_LEAD", "FRAUD_LEAD", "FRAUD_ANALYST", "FRAUD_ANALYST",
             "FRAUD_ANALYST", "SOC_OPERATOR", "DPO_LIAISON")

NOC_NAMES = (
    ("kofi.boateng",   "Kofi Boateng"),
    ("ama.mensah",     "Ama Mensah"),
    ("yaw.asante",     "Yaw Asante"),
    ("akua.owusu",     "Akua Owusu"),
    ("kwesi.darko",    "Kwesi Darko"),
    ("efua.appiah",    "Efua Appiah"),
    ("esi.ofori",      "Esi Ofori"),
)

RING_TYPES = ("voice_scam", "voice_scam", "smishing", "smishing", "mule",
              "mule", "mixed", "mixed", "smishing", "voice_scam")

ALERT_TYPES = ("voice", "sms", "momo", "ott")
ALERT_SEVERITIES = ("critical", "high", "high", "medium", "medium", "low")
ALERT_STATUSES = ("new", "new", "claimed", "reviewing", "closed", "fp")


# ---- Helpers --------------------------------------------------------------


def fake_msisdn() -> str:
    """Ghana E.164 MSISDN. +233<9 digits>, dropping the leading 0 of the local form."""
    prefix = rng.choice(GH_PREFIXES)
    suffix = "".join(str(rng.randrange(10)) for _ in range(10 - len(prefix)))
    local = prefix + suffix
    return "+233" + local[1:]   # drop leading 0


def fake_imsi() -> str:
    """MCC=620 (Ghana). MNC=01 (MTN). 15 digits total."""
    msin = "".join(str(rng.randrange(10)) for _ in range(10))
    return "62001" + msin


def luhn_check_digit(payload: str) -> str:
    s = 0
    parity = (len(payload) + 1) % 2
    for i, ch in enumerate(payload):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        s += n
    return str((10 - s % 10) % 10)


def fake_imei() -> str:
    """Luhn-valid 15-digit IMEI. Reserved Type Allocation Code 35 prefix."""
    body = "35" + "".join(str(rng.randrange(10)) for _ in range(12))
    return body + luhn_check_digit(body)


def now_iso() -> datetime:
    return datetime.now(tz=timezone.utc)


def ts_back(min_minutes: int, max_minutes: int) -> datetime:
    return now_iso() - timedelta(minutes=rng.randint(min_minutes, max_minutes))


# ---- Domain objects --------------------------------------------------------


@dataclass
class Number:
    id: UUID
    msisdn: str
    imsi: str
    region: str
    risk_score: float


@dataclass
class Device:
    imei: str
    region: str
    first_seen: datetime


@dataclass
class Wallet:
    id: UUID
    wallet_id: str
    msisdn: str
    risk_score: float


@dataclass
class User:
    id: UUID
    sub: str
    email: str
    display_name: str
    role: str


def build_dataset() -> tuple[list[Number], list[Device], list[Wallet], list[User]]:
    numbers = []
    for _ in range(NUM_NUMBERS):
        region = rng.choice(GH_REGIONS)[3]
        numbers.append(Number(
            id=uuid4(),
            msisdn=fake_msisdn(),
            imsi=fake_imsi(),
            region=region,
            risk_score=round(rng.betavariate(2, 8), 3),  # skew low; risky tail
        ))

    devices = [
        Device(
            imei=fake_imei(),
            region=rng.choice(GH_REGIONS)[3],
            first_seen=ts_back(60, 60 * 24 * 365),
        )
        for _ in range(NUM_DEVICES)
    ]

    wallets = []
    for n in rng.sample(numbers, NUM_WALLETS):
        digits = n.msisdn.replace("+", "")
        wallets.append(Wallet(
            id=uuid4(),
            wallet_id=f"W:{digits}",
            msisdn=n.msisdn,
            risk_score=round(rng.betavariate(2, 8), 3),
        ))

    users = []
    for (sub, name), role in zip(NOC_NAMES, NOC_ROLES):
        users.append(User(
            id=uuid4(),
            sub=f"keycloak|{sub}",
            email=f"{sub}@mtn.com.gh",
            display_name=name,
            role=role,
        ))
    return numbers, devices, wallets, users


# ---- Memgraph -------------------------------------------------------------


def seed_memgraph(numbers: list[Number], devices: list[Device], wallets: list[Wallet],
                  ring_members: dict[int, list[Number]]) -> None:
    if GraphDatabase is None:
        print("  ! neo4j driver not installed; skipping Memgraph (pip install neo4j)")
        return

    auth = (MEMGRAPH_USER, MEMGRAPH_PASS) if MEMGRAPH_USER else None
    print(f"==> Memgraph at {MEMGRAPH_URL}")
    with GraphDatabase.driver(MEMGRAPH_URL, auth=auth) as driver, driver.session() as s:
        s.run("CREATE INDEX ON :Number(msisdn);")
        s.run("CREATE INDEX ON :Device(imei);")
        s.run("CREATE INDEX ON :Wallet(wallet_id);")

        for n in numbers:
            s.run(
                "MERGE (x:Number {msisdn: $msisdn}) "
                "SET x.imsi=$imsi, x.region=$region, x.risk_score=$score",
                msisdn=n.msisdn, imsi=n.imsi, region=n.region, score=n.risk_score,
            )

        for d in devices:
            s.run(
                "MERGE (x:Device {imei: $imei}) "
                "SET x.region=$region, x.first_seen=datetime($first_seen)",
                imei=d.imei, region=d.region, first_seen=d.first_seen.isoformat(),
            )

        for w in wallets:
            s.run(
                "MERGE (x:Wallet {wallet_id: $wid}) "
                "SET x.risk_score=$score",
                wid=w.wallet_id, score=w.risk_score,
            )
            s.run(
                "MATCH (n:Number {msisdn:$msisdn}), (w:Wallet {wallet_id:$wid}) "
                "MERGE (n)-[:OWNS]->(w)",
                msisdn=w.msisdn, wid=w.wallet_id,
            )

        # Sparse random call/SMS edges across the population (~3 per number).
        called = 0
        smsed = 0
        for n in numbers:
            for _ in range(rng.randint(1, 5)):
                m = rng.choice(numbers)
                if m.msisdn == n.msisdn:
                    continue
                ts = ts_back(1, 60 * 24 * 14).isoformat()
                if rng.random() < 0.7:
                    s.run(
                        "MATCH (a:Number {msisdn:$a}), (b:Number {msisdn:$b}) "
                        "CREATE (a)-[:CALLED {ts: datetime($ts), duration: $dur}]->(b)",
                        a=n.msisdn, b=m.msisdn, ts=ts, dur=rng.randint(5, 600),
                    )
                    called += 1
                else:
                    s.run(
                        "MATCH (a:Number {msisdn:$a}), (b:Number {msisdn:$b}) "
                        "CREATE (a)-[:SMSED {ts: datetime($ts), template_hash:$h}]->(b)",
                        a=n.msisdn, b=m.msisdn, ts=ts,
                        h=f"th_{rng.randrange(1 << 24):06x}",
                    )
                    smsed += 1

        # Device USE edges: each number used 1–2 devices.
        for n in numbers:
            for d in rng.sample(devices, k=min(rng.randint(1, 2), len(devices))):
                s.run(
                    "MATCH (a:Number {msisdn:$msisdn}), (d:Device {imei:$imei}) "
                    "MERGE (a)-[r:USED]->(d) SET r.since=datetime($ts)",
                    msisdn=n.msisdn, imei=d.imei,
                    ts=ts_back(60, 60 * 24 * 90).isoformat(),
                )

        # Wallet→Wallet SENT edges (mostly small, with a fat-tailed handful).
        sent = 0
        for w in wallets:
            for _ in range(rng.randint(0, 4)):
                target = rng.choice(wallets)
                if target.wallet_id == w.wallet_id:
                    continue
                amount = rng.choices(
                    [rng.uniform(5, 50), rng.uniform(50, 500), rng.uniform(500, 5000)],
                    weights=[6, 3, 1],
                )[0]
                s.run(
                    "MATCH (a:Wallet {wallet_id:$a}), (b:Wallet {wallet_id:$b}) "
                    "CREATE (a)-[:SENT {ts: datetime($ts), amount: $amt}]->(b)",
                    a=w.wallet_id, b=target.wallet_id,
                    ts=ts_back(1, 60 * 24 * 7).isoformat(), amt=round(amount, 2),
                )
                sent += 1

        # Inject denser edges inside each ring so motif detection has a target.
        ring_edges = 0
        for members in ring_members.values():
            for i, src in enumerate(members):
                for tgt in members[i + 1:]:
                    s.run(
                        "MATCH (a:Number {msisdn:$a}), (b:Number {msisdn:$b}) "
                        "CREATE (a)-[:CALLED {ts: datetime($ts), duration: $dur}]->(b) "
                        "CREATE (a)-[:SMSED {ts: datetime($ts2), template_hash:$h}]->(b)",
                        a=src.msisdn, b=tgt.msisdn,
                        ts=ts_back(5, 60).isoformat(), dur=rng.randint(20, 120),
                        ts2=ts_back(1, 30).isoformat(),
                        h=f"th_ring_{rng.randrange(1 << 16):04x}",
                    )
                    ring_edges += 2

        print(f"  ✓ Memgraph: numbers={len(numbers)} devices={len(devices)} "
              f"wallets={len(wallets)} called={called} smsed={smsed} sent={sent} "
              f"ring_edges={ring_edges}")


# ---- Aerospike ------------------------------------------------------------


def seed_aerospike(numbers: list[Number], wallets: list[Wallet],
                   ring_members: dict[int, list[Number]]) -> None:
    if aerospike is None:
        print("  ! aerospike client not installed; skipping (pip install aerospike)")
        return

    hosts = []
    for chunk in AS_HOSTS_RAW.split(","):
        host, _, port = chunk.strip().partition(":")
        hosts.append((host, int(port or "3000")))
    print(f"==> Aerospike at {hosts}, namespace={AS_NAMESPACE}")

    client = aerospike.client({"hosts": hosts}).connect()
    try:
        in_ring = {n.msisdn for ms in ring_members.values() for n in ms}

        for n in numbers:
            risky = n.msisdn in in_ring
            mult = 8 if risky else 1
            bins = {
                "vel_1m": rng.randint(0, 5) * mult,
                "vel_5m": rng.randint(0, 20) * mult,
                "vel_1h": rng.randint(1, 80) * mult,
                "fanout_1h": rng.randint(1, 30) * mult,
                "imei_count": rng.randint(1, 3) + (3 if risky else 0),
                "geo_entropy": round(rng.uniform(0.1, 1.0), 3),
                "sms_freq_1h": rng.randint(0, 40) * mult,
                "smshash_top": f"th_{rng.randrange(1 << 24):06x}",
                "last_score": n.risk_score if not risky else max(n.risk_score, 0.85),
                "last_score_at": int(time.time()),
                "region": n.region,
            }
            client.put((AS_NAMESPACE, "numbers", n.msisdn), bins,
                       meta={"ttl": 86400})

        for w in wallets:
            bins = {
                "momo_vel_1h": rng.randint(0, 25),
                "momo_vel_24h": rng.randint(1, 120),
                "counterparty_div_24h": rng.randint(1, 18),
                "value_p95_24h": round(rng.uniform(20, 4000), 2),
                "last_score": w.risk_score,
                "last_score_at": int(time.time()),
            }
            client.put((AS_NAMESPACE, "wallets", w.wallet_id), bins,
                       meta={"ttl": 86400})

        print(f"  ✓ Aerospike: numbers={len(numbers)} wallets={len(wallets)}")
    finally:
        client.close()


# ---- Postgres -------------------------------------------------------------


def _pg_connect(db: str):
    if psycopg is None:
        raise RuntimeError(
            "psycopg / psycopg2 not installed (pip install 'psycopg[binary]')"
        )
    return psycopg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=db,
    )


def seed_postgres_app(numbers: list[Number], wallets: list[Wallet], users: list[User],
                      ring_members: dict[int, list[Number]]) -> list[tuple[UUID, str]]:
    """Returns [(ring_id, type), …] for use by the audit seeder."""
    print(f"==> Postgres ({PG_DB}) — users / rings / alerts / takedowns")
    conn = _pg_connect(PG_DB)
    conn.autocommit = False
    rings_out: list[tuple[UUID, str]] = []
    try:
        with conn.cursor() as cur:
            # Users
            for u in users:
                cur.execute(
                    "INSERT INTO users(id, sub, email, display_name, role) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (sub) DO UPDATE SET email=EXCLUDED.email, "
                    "display_name=EXCLUDED.display_name, role=EXCLUDED.role",
                    (str(u.id), u.sub, u.email, u.display_name, u.role),
                )

            # Rings + members
            ring_ids: list[UUID] = []
            for i in range(NUM_RINGS):
                rid = uuid4()
                ring_ids.append(rid)
                rtype = RING_TYPES[i % len(RING_TYPES)]
                rings_out.append((rid, rtype))
                composite = round(rng.uniform(0.62, 0.96), 3)
                active_since = ts_back(60 * 24, 60 * 24 * 30)
                last_activity = ts_back(5, 60 * 24)
                members = ring_members[i]
                cur.execute(
                    "INSERT INTO rings(id, type, status, composite_score, "
                    "active_since, last_activity, member_count, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (id) DO NOTHING",
                    (str(rid), rtype,
                     rng.choice(("monitoring", "monitoring", "takedown")),
                     composite, active_since, last_activity, len(members),
                     '{"region":"' + members[0].region + '","seeded":true}'),
                )
                for m in members:
                    cur.execute(
                        "INSERT INTO ring_members(ring_id, member_kind, member_id, "
                        "role, confidence, first_seen, last_seen) "
                        "VALUES (%s, 'number', %s, %s, %s, %s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (str(rid), m.msisdn,
                         rng.choice(("originator", "mule", "recipient", "coordinator")),
                         round(rng.uniform(0.55, 0.95), 3),
                         ts_back(60 * 24, 60 * 24 * 30),
                         ts_back(1, 60 * 24)),
                    )

            # Alerts: distribute across ring members + lone numbers.
            risky_numbers = [n for ms in ring_members.values() for n in ms]
            for i in range(NUM_ALERTS):
                if i % 3 == 0 and risky_numbers:
                    subj = rng.choice(risky_numbers)
                    ring_id = ring_ids[i % NUM_RINGS]
                else:
                    subj = rng.choice(numbers)
                    ring_id = None
                kind = rng.choice(ALERT_TYPES)
                severity = rng.choice(ALERT_SEVERITIES)
                status = rng.choice(ALERT_STATUSES)
                assignee = rng.choice(users).id if status in ("claimed", "reviewing", "closed") else None
                cur.execute(
                    "INSERT INTO alerts(id, type, severity, subject_kind, subject_id, "
                    "score, ring_id, status, assignee_id, details) "
                    "VALUES (%s, %s, %s, 'number', %s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (id) DO NOTHING",
                    (str(uuid4()), kind, severity, subj.msisdn,
                     round(rng.uniform(0.55, 0.99), 3),
                     str(ring_id) if ring_id else None,
                     status, str(assignee) if assignee else None,
                     '{"reason":"seeded","model":"heuristic-v1"}'),
                )

            # Takedowns: 5 against a sample of rings.
            for rid in rng.sample(ring_ids, NUM_TAKEDOWNS):
                creator = rng.choice([u for u in users if u.role == "FRAUD_LEAD"])
                cur.execute(
                    "INSERT INTO takedowns(id, ring_id, status, filed_with, "
                    "filed_at, evidence_hash, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (str(uuid4()), str(rid),
                     rng.choice(("drafted", "filed", "executed", "approved")),
                     rng.choice(("nca", "police", "bog")),
                     ts_back(60 * 24, 60 * 24 * 14),
                     f"sha256:{uuid4().hex}{uuid4().hex}",
                     str(creator.id)),
                )

        conn.commit()
        print(f"  ✓ users={len(users)} rings={NUM_RINGS} alerts={NUM_ALERTS} takedowns={NUM_TAKEDOWNS}")
        return rings_out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_postgres_audit(users: list[User], rings: list[tuple[UUID, str]]) -> None:
    print(f"==> Postgres ({AUDIT_DB}) — audit_events")
    conn = _pg_connect(AUDIT_DB)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            actions = (
                ("alerts.list", "alert"),
                ("alerts.claim", "alert"),
                ("alerts.close", "alert"),
                ("rings.view", "ring"),
                ("rings.takedown.file", "ring"),
                ("graph.query", "graph"),
                ("models.promote", "model"),
                ("customer.profile.view", "customer"),
                ("export.regulator", "audit_export"),
                ("user.role.change", "user"),
            )
            ring_ids = [str(r[0]) for r in rings] or [str(uuid4())]
            for _ in range(NUM_AUDIT):
                actor = rng.choice(users)
                action, resource_kind = rng.choice(actions)
                # Pick a partition that exists per the migration (2026-05..08).
                event_ts = datetime(2026, rng.choice((5, 6, 7, 8)),
                                    rng.randint(1, 28),
                                    rng.randint(0, 23), rng.randint(0, 59),
                                    tzinfo=timezone.utc)
                cur.execute(
                    "INSERT INTO audit_events(id, actor_id, actor_kind, action, "
                    "resource_kind, resource_id, purpose, request_id, event_ts, metadata) "
                    "VALUES (%s, %s, 'user', %s, %s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (id) DO NOTHING",
                    (str(uuid4()), str(actor.id), action, resource_kind,
                     rng.choice(ring_ids),
                     "fraud_prevention",
                     f"req_{uuid4().hex[:16]}",
                     event_ts,
                     '{"seeded":true,"actor_role":"' + actor.role + '"}'),
                )
        conn.commit()
        print(f"  ✓ audit_events={NUM_AUDIT}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- Entry point ----------------------------------------------------------


def main() -> int:
    print(f"seed_demo: SEED={SEED} numbers={NUM_NUMBERS} devices={NUM_DEVICES} "
          f"wallets={NUM_WALLETS}")
    numbers, devices, wallets, users = build_dataset()

    # Ten injected rings, sized 4–8 members each, picked from `numbers`.
    chosen: set[str] = set()
    ring_members: dict[int, list[Number]] = {}
    for i in range(NUM_RINGS):
        size = rng.randint(4, 8)
        candidates = [n for n in numbers if n.msisdn not in chosen]
        members = rng.sample(candidates, k=min(size, len(candidates)))
        chosen.update(n.msisdn for n in members)
        ring_members[i] = members

    seed_memgraph(numbers, devices, wallets, ring_members)
    seed_aerospike(numbers, wallets, ring_members)
    rings = seed_postgres_app(numbers, wallets, users, ring_members)
    seed_postgres_audit(users, rings)

    print("OK: seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
