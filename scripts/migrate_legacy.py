#!/usr/bin/env python3
"""
Legacy → MakerSpaceAPI migration script.

Migrates data from three legacy LeineLab systems into the new unified database.

Sources
-------
  MachineUserManager  --mum-url     cards → users
                                    machines → machines (new API tokens generated)
                                    rates + authorization → machine_authorizations
                                    sessions → machine_sessions + transactions

  NFCKasse            --nfc-url     product_categories → product_categories
                                    products → products
                                    product_alias → product_aliases

  Bankomat            --bankomat-url targets → booking_targets
                                    admins.pin → users.pin_hash (matching UIDs)

NOT migrated (with reasons)
---------------------------
  NFCKasse cards        — UIDs are MD5 hashes; cannot reverse to raw BIGINT
  NFCKasse transactions — linked to MD5 UIDs; no user mapping possible
  NFCKasse admins       — replaced by OIDC login
  NFCKasse topups       — internal voucher codes not used in new system
  MachineUserManager alias — multiple-card-per-user; use card-switch feature instead
  Bankomat transactions — type is ambiguous (no explicit topup/payout field)

Usage
-----
    # Full migration (all sources):
    python scripts/migrate_legacy.py \\
        --target-url   "mysql+pymysql://user:pass@host:3306/makerspaceapi" \\
        --mum-url      "mysql+pymysql://user:pass@host:3306/machineusermanager" \\
        --nfc-url      "mysql+pymysql://user:pass@host:3306/nfckasse" \\
        --bankomat-url "mysql+pymysql://user:pass@host:3306/bankomat"

    # Dry run — preview without writing:
    python scripts/migrate_legacy.py ... --dry-run

    # Selective sections only:
    python scripts/migrate_legacy.py ... --only users machines products

Available sections: users  machines  authorizations  sessions  products  targets  pins
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pymysql
import pymysql.cursors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dsn(dsn: str) -> dict:
    """Parse a mysql+pymysql://user:pass@host:port/db DSN into connect kwargs."""
    dsn = dsn.replace("mysql+pymysql://", "")
    credentials, rest = dsn.split("@", 1)
    user, password = credentials.split(":", 1) if ":" in credentials else (credentials, "")
    host_port, db = rest.split("/", 1)
    host, port = host_port.split(":") if ":" in host_port else (host_port, "3306")
    return dict(host=host, port=int(port), user=user, password=password, db=db)


def connect(dsn: str) -> pymysql.Connection:
    return pymysql.connect(
        **_parse_dsn(dsn),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def generate_api_token() -> tuple[str, str]:
    """Return (plaintext_token, bcrypt_hash)."""
    from passlib.hash import bcrypt as _bcrypt
    token = secrets.token_hex(32)
    return token, _bcrypt.hash(token)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def unix_to_dt(ts) -> datetime | None:
    if ts is None or int(ts) == 0:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Section: users  (MachineUserManager.cards → users)
# ---------------------------------------------------------------------------

def migrate_users(src: pymysql.Connection, dst: pymysql.Connection, dry: bool) -> int:
    """
    Migrate MachineUserManager card holders as users.
    UIDs are raw BIGINTs — directly usable as primary keys.
    """
    with src.cursor() as c:
        c.execute("SELECT uid, name, value, registered_on FROM cards")
        rows = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT id FROM users")
        existing = {r["id"] for r in c.fetchall()}

    inserted = 0
    with dst.cursor() as c:
        for row in rows:
            uid = int(row["uid"])
            if uid in existing:
                continue
            if not dry:
                c.execute(
                    "INSERT INTO users (id, name, balance, created_at)"
                    " VALUES (%s, %s, %s, %s)",
                    (uid, row["name"], row["value"], row["registered_on"] or now_utc()),
                )
            inserted += 1
        if not dry:
            dst.commit()
    return inserted


# ---------------------------------------------------------------------------
# Section: machines  (MachineUserManager.machines → machines)
# ---------------------------------------------------------------------------

def migrate_machines(
    src: pymysql.Connection, dst: pymysql.Connection, dry: bool
) -> tuple[int, list[dict]]:
    """
    Migrate machines and generate new API tokens.
    Returns (count, token_list) — token_list contains plaintext tokens that
    must be configured on the physical devices immediately.
    """
    with src.cursor() as c:
        c.execute("SELECT name FROM machines")
        rows = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT slug FROM machines")
        existing_slugs = {r["slug"] for r in c.fetchall()}

    tokens: list[dict] = []
    inserted = 0
    with dst.cursor() as c:
        for row in rows:
            slug = slugify(row["name"])
            if slug in existing_slugs:
                continue
            token, token_hash = generate_api_token()
            tokens.append({"name": row["name"], "slug": slug, "token": token})
            if not dry:
                c.execute(
                    "INSERT INTO machines"
                    "  (name, slug, machine_type, api_token_hash, created_by, created_at, active)"
                    " VALUES (%s, %s, 'machine', %s, 'legacy_migration', %s, 1)",
                    (row["name"], slug, token_hash, now_utc()),
                )
            inserted += 1
        if not dry:
            dst.commit()
    return inserted, tokens


# ---------------------------------------------------------------------------
# Section: authorizations  (MachineUserManager.authorization + rates → machine_authorizations)
# ---------------------------------------------------------------------------

def migrate_authorizations(
    src: pymysql.Connection, dst: pymysql.Connection, dry: bool
) -> int:
    with src.cursor() as c:
        c.execute(
            """
            SELECT a.uid, a.machine, a.issued,
                   COALESCE(r.per_login,  0.00) AS per_login,
                   COALESCE(r.per_minute, 0.00) AS per_minute
            FROM authorization a
            LEFT JOIN rates r ON a.rate = r.rid
            """
        )
        rows = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT id, name FROM machines")
        machine_by_name = {r["name"]: r["id"] for r in c.fetchall()}
        c.execute("SELECT id FROM users")
        user_ids = {r["id"] for r in c.fetchall()}
        c.execute("SELECT machine_id, user_id FROM machine_authorizations")
        existing = {(r["machine_id"], r["user_id"]) for r in c.fetchall()}

    inserted = 0
    with dst.cursor() as c:
        for row in rows:
            uid = int(row["uid"])
            machine_id = machine_by_name.get(row["machine"])
            if machine_id is None:
                print(
                    f"  WARNING [authorizations]: unknown machine '{row['machine']}' — skipped",
                    file=sys.stderr,
                )
                continue
            if uid not in user_ids:
                print(
                    f"  WARNING [authorizations]: user {uid} not in new DB — skipped",
                    file=sys.stderr,
                )
                continue
            if (machine_id, uid) in existing:
                continue
            if not dry:
                c.execute(
                    "INSERT INTO machine_authorizations"
                    "  (machine_id, user_id, price_per_login, price_per_minute,"
                    "   booking_interval, granted_by, granted_at)"
                    " VALUES (%s, %s, %s, %s, 60, 'legacy_migration', %s)",
                    (machine_id, uid, row["per_login"], row["per_minute"],
                     row["issued"] or now_utc()),
                )
            inserted += 1
        if not dry:
            dst.commit()
    return inserted


# ---------------------------------------------------------------------------
# Section: sessions  (MachineUserManager.sessions → machine_sessions + transactions)
# ---------------------------------------------------------------------------

def migrate_sessions(
    src: pymysql.Connection, dst: pymysql.Connection, dry: bool
) -> tuple[int, int]:
    """
    Migrate completed sessions (end_time IS NOT NULL).
    Each session becomes one machine_session row plus one 'machine_usage'
    transaction for the billed amount (if price > 0).
    Returns (inserted, skipped).
    """
    with src.cursor() as c:
        # start_time / end_time are stored as UNIX timestamps (INT)
        c.execute(
            "SELECT bid, uid, machine, start_time, end_time, price, comment"
            " FROM sessions"
            " WHERE end_time IS NOT NULL AND end_time > 0"
        )
        rows = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT id, name FROM machines")
        machine_by_name = {r["name"]: r["id"] for r in c.fetchall()}
        c.execute("SELECT id FROM users")
        user_ids = {r["id"] for r in c.fetchall()}

    inserted = skipped = 0
    with dst.cursor() as c:
        for row in rows:
            uid = row["uid"]
            if uid is None or int(uid) not in user_ids:
                skipped += 1
                continue
            machine_id = machine_by_name.get(row["machine"])
            if machine_id is None:
                skipped += 1
                continue
            uid = int(uid)
            start_dt = unix_to_dt(row["start_time"])
            end_dt = unix_to_dt(row["end_time"])
            if start_dt is None:
                skipped += 1
                continue

            if not dry:
                c.execute(
                    "INSERT INTO machine_sessions"
                    "  (machine_id, user_id, start_time, end_time, paid_until)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (machine_id, uid, start_dt, end_dt, end_dt),
                )
                session_id = c.lastrowid
                price = Decimal(str(row["price"] or "0.00"))
                if price > 0:
                    c.execute(
                        "INSERT INTO transactions"
                        "  (user_id, amount, type, machine_id, session_id, note, created_at)"
                        " VALUES (%s, %s, 'machine_usage', %s, %s, %s, %s)",
                        (uid, -price, machine_id, session_id,
                         row["comment"] or "Migrated session", start_dt),
                    )
            inserted += 1
        if not dry:
            dst.commit()

    if skipped:
        print(
            f"  WARNING [sessions]: {skipped} session(s) skipped"
            " (uid or machine not found in new DB)",
            file=sys.stderr,
        )
    return inserted, skipped


# ---------------------------------------------------------------------------
# Section: products  (NFCKasse → product_categories + products + product_aliases)
# ---------------------------------------------------------------------------

def migrate_products(
    src: pymysql.Connection, dst: pymysql.Connection, dry: bool
) -> dict[str, int]:
    with src.cursor() as c:
        c.execute("SELECT name FROM product_categories")
        categories = [r["name"] for r in c.fetchall()]

        c.execute("SELECT ean, name, price, stock, category FROM products")
        products = c.fetchall()

        # product_alias.target is the primary product EAN
        c.execute("SELECT ean, target FROM product_alias")
        aliases = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT name FROM product_categories")
        existing_cats = {r["name"] for r in c.fetchall()}
        c.execute("SELECT ean FROM products")
        existing_eans = {r["ean"] for r in c.fetchall()}
        c.execute("SELECT ean FROM product_aliases")
        existing_alias_eans = {r["ean"] for r in c.fetchall()}

    counts = {"categories": 0, "products": 0, "aliases": 0, "aliases_skipped": 0}

    with dst.cursor() as c:
        for name in categories:
            if name in existing_cats:
                continue
            if not dry:
                c.execute("INSERT INTO product_categories (name) VALUES (%s)", (name,))
            counts["categories"] += 1

        for p in products:
            if p["ean"] in existing_eans:
                continue
            if not dry:
                c.execute(
                    "INSERT INTO products (ean, name, price, stock, category, active)"
                    " VALUES (%s, %s, %s, %s, %s, 1)",
                    (p["ean"], p["name"], p["price"], p["stock"], p["category"]),
                )
            counts["products"] += 1

        if not dry:
            dst.commit()
            # Refresh EAN→ID map now that products are inserted
            c.execute("SELECT ean, id FROM products")
            ean_to_id = {r["ean"]: r["id"] for r in c.fetchall()}
        else:
            # In dry-run we don't have real IDs, but we can still count
            ean_to_id = {p["ean"]: 0 for p in products}

        for a in aliases:
            if a["ean"] in existing_alias_eans:
                continue
            product_id = ean_to_id.get(a["target"])
            if product_id is None:
                counts["aliases_skipped"] += 1
                print(
                    f"  WARNING [products]: alias '{a['ean']}' → '{a['target']}'"
                    " — target product not found, skipped",
                    file=sys.stderr,
                )
                continue
            if not dry:
                c.execute(
                    "INSERT INTO product_aliases (ean, product_id) VALUES (%s, %s)",
                    (a["ean"], product_id),
                )
            counts["aliases"] += 1

        if not dry:
            dst.commit()

    return counts


# ---------------------------------------------------------------------------
# Section: targets  (Bankomat.targets → booking_targets)
# ---------------------------------------------------------------------------

def migrate_targets(
    src: pymysql.Connection, dst: pymysql.Connection, dry: bool
) -> int:
    with src.cursor() as c:
        c.execute("SELECT tname, value FROM targets")
        rows = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT slug FROM booking_targets")
        existing = {r["slug"] for r in c.fetchall()}

    inserted = 0
    with dst.cursor() as c:
        for row in rows:
            # tname values are already lowercase slugs (cards, donation, machines, nfckasse)
            slug = slugify(row["tname"])
            if slug in existing:
                continue
            # Capitalise first letter for the human-readable name
            name = row["tname"][0].upper() + row["tname"][1:]
            if not dry:
                c.execute(
                    "INSERT INTO booking_targets (name, slug, balance, created_at)"
                    " VALUES (%s, %s, %s, %s)",
                    (name, slug, row["value"], now_utc()),
                )
            inserted += 1
        if not dry:
            dst.commit()
    return inserted


# ---------------------------------------------------------------------------
# Section: pins  (Bankomat.admins → users.pin_hash)
# ---------------------------------------------------------------------------

def migrate_pins(
    src: pymysql.Connection, dst: pymysql.Connection, dry: bool
) -> tuple[int, int]:
    """
    Copy bcrypt PIN hashes from Bankomat admins to users.pin_hash.
    Only updates rows where the UID already exists in the new users table.
    The hash format is bcrypt (VARCHAR 64 → fits in String(60)).
    """
    with src.cursor() as c:
        c.execute("SELECT uid, pin FROM admins WHERE pin IS NOT NULL AND pin != ''")
        rows = c.fetchall()

    with dst.cursor() as c:
        c.execute("SELECT id FROM users")
        user_ids = {r["id"] for r in c.fetchall()}

    updated = not_found = 0
    with dst.cursor() as c:
        for row in rows:
            uid = int(row["uid"])
            if uid not in user_ids:
                not_found += 1
                continue
            if not dry:
                c.execute(
                    "UPDATE users SET pin_hash = %s WHERE id = %s",
                    (row["pin"], uid),
                )
            updated += 1
        if not dry:
            dst.commit()
    return updated, not_found


# ---------------------------------------------------------------------------
# Alias report helper  (MachineUserManager.alias — reported, not migrated)
# ---------------------------------------------------------------------------

def report_aliases(src: pymysql.Connection) -> list[dict]:
    with src.cursor() as c:
        c.execute("SELECT card_id, uid, comment FROM alias")
        return c.fetchall()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ALL_SECTIONS = ["users", "machines", "authorizations", "sessions",
                "products", "targets", "pins"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy LeineLab databases to MakerSpaceAPI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--target-url", required=True,
                        help="New MakerSpaceAPI DB  (mysql+pymysql://user:pass@host:port/db)")
    parser.add_argument("--mum-url",
                        help="MachineUserManager DB (mysql+pymysql://...)")
    parser.add_argument("--nfc-url",
                        help="NFCKasse DB           (mysql+pymysql://...)")
    parser.add_argument("--bankomat-url",
                        help="Bankomat DB           (mysql+pymysql://...)")
    parser.add_argument("--only", nargs="+", choices=ALL_SECTIONS, metavar="SECTION",
                        help=f"Run only these sections. Choices: {', '.join(ALL_SECTIONS)}")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be written without touching the DB")
    args = parser.parse_args()

    sections = set(args.only) if args.only else set(ALL_SECTIONS)
    dry = args.dry_run

    if dry:
        print("=== DRY RUN — no data will be written ===\n")

    dst = connect(args.target_url)
    src_mum      = connect(args.mum_url)      if args.mum_url      else None
    src_nfc      = connect(args.nfc_url)      if args.nfc_url      else None
    src_bankomat = connect(args.bankomat_url) if args.bankomat_url else None

    # -----------------------------------------------------------------------
    # MachineUserManager sections  (must run in order: users → machines → auth → sessions)
    # -----------------------------------------------------------------------
    if src_mum:
        if "users" in sections:
            n = migrate_users(src_mum, dst, dry)
            print(f"[users]          {n} user(s) imported from MachineUserManager.cards")

        # Always report card aliases regardless of --only, when mum is connected
        aliases = report_aliases(src_mum)
        if aliases:
            print(f"\n[alias_report]   {len(aliases)} card alias(es) found — "
                  "cannot be auto-migrated.")
            print("  Use the card-switch feature (PUT /users/{id}/card) once users log in via OIDC.")
            for a in aliases:
                print(f"  card_id={a['card_id']:>12}  →  primary uid={a['uid']:>12}"
                      f"  ({a['comment']})")
            print()

        if "machines" in sections:
            n, tokens = migrate_machines(src_mum, dst, dry)
            print(f"[machines]       {n} machine(s) imported from MachineUserManager.machines")
            if tokens and not dry:
                print()
                print("  *** MACHINE API TOKENS — shown once, configure devices now ***")
                for t in tokens:
                    print(f"  {t['name']:<30}  slug={t['slug']:<20}  token={t['token']}")
                print()
            elif tokens and dry:
                print(f"  (dry-run: {len(tokens)} token(s) would be generated)")

        if "authorizations" in sections:
            n = migrate_authorizations(src_mum, dst, dry)
            print(f"[authorizations] {n} authorization(s) imported")

        if "sessions" in sections:
            inserted, skipped = migrate_sessions(src_mum, dst, dry)
            print(f"[sessions]       {inserted} session(s) imported"
                  + (f", {skipped} skipped" if skipped else ""))
    else:
        for s in ["users", "machines", "authorizations", "sessions"]:
            if s in sections:
                print(f"[{s}] skipped — no --mum-url provided")

    # -----------------------------------------------------------------------
    # NFCKasse sections
    # -----------------------------------------------------------------------
    if src_nfc:
        if "products" in sections:
            counts = migrate_products(src_nfc, dst, dry)
            print(
                f"[products]       {counts['categories']} categor(y/ies), "
                f"{counts['products']} product(s), "
                f"{counts['aliases']} alias(es) imported"
                + (f" — {counts['aliases_skipped']} alias(es) skipped"
                   if counts["aliases_skipped"] else "")
            )
    else:
        if "products" in sections:
            print("[products] skipped — no --nfc-url provided")

    # -----------------------------------------------------------------------
    # Bankomat sections
    # -----------------------------------------------------------------------
    if src_bankomat:
        if "targets" in sections:
            n = migrate_targets(src_bankomat, dst, dry)
            print(f"[targets]        {n} booking target(s) imported from Bankomat.targets")

        if "pins" in sections:
            updated, not_found = migrate_pins(src_bankomat, dst, dry)
            print(f"[pins]           {updated} PIN hash(es) copied to users"
                  + (f" — {not_found} UID(s) not found in new DB" if not_found else ""))
    else:
        for s in ["targets", "pins"]:
            if s in sections:
                print(f"[{s}] skipped — no --bankomat-url provided")

    # -----------------------------------------------------------------------
    # Close connections
    # -----------------------------------------------------------------------
    dst.close()
    for conn in [src_mum, src_nfc, src_bankomat]:
        if conn:
            conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
