#!/usr/bin/env python3
"""
Recovery script for the "add tag as alias" merge bug (fixed in app/web/auth.py
connect_alias, 2026-08-31).

Bug summary
-----------
_do_transfer(old, new) keeps its FIRST argument's own columns (oidc_sub, name,
pin_hash, created_at) and merges the SECOND argument's balance/history into it,
then renames the surviving row to the second argument's id. The old
connect_alias code called it as _do_transfer(new_id, old_id) to keep the main
account's numeric id — which worked for the id, but the surviving row's own
columns ended up being the (usually blank) new tag's, silently wiping the
account's oidc_sub / name / pin_hash / created_at.

Balance, transactions, sessions, machine_authorizations and rentals were NOT
lost — those are re-attributed via UPDATE + ON UPDATE CASCADE regardless of
the bug. Only the four plain columns on the surviving `users` row are at risk.

What this script does
----------------------
Every row currently in `user_card_aliases` marks an account that went through
the buggy merge at least once. For each such account id, this script compares
the CURRENT ("live") `users` row against the SAME id in a RESTORED BACKUP
("backup") — a MariaDB instance started from a pre-incident data directory
copy — and reports/restores oidc_sub, name, pin_hash and created_at, but only
where the live value is currently NULL/blank and the backup has a non-blank
value. It never touches balance, transactions, or any other table, and it
never overwrites a live value that isn't blank (so anything fixed manually
since, or genuinely never set, is left alone).

Restoring the backup
---------------------
Do NOT point this script at the live docker-compose stack for the "backup"
side. Restore the file-backup into an isolated, throwaway container first:

    mkdir -p ~/restore/mariadb-data
    tar -xzf /path/to/backup.tar.gz -C ~/restore/mariadb-data   # adjust to your archive layout
    docker run -d --name makerspace-restore \\
        -p 13306:3306 \\
        -e MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1 \\
        -v ~/restore/mariadb-data:/var/lib/mysql \\
        mariadb:10.11
    docker logs -f makerspace-restore   # wait for "ready for connections", then Ctrl+C

Then run this script with --backup-url pointing at that throwaway container
(e.g. mysql+pymysql://root@127.0.0.1:13306/makerspaceapi) and --live-url
pointing at production. Default is a dry run — nothing is written until you
pass --apply. Take a fresh backup of the LIVE database before using --apply.

When you're done:

    docker rm -f makerspace-restore
    rm -rf ~/restore/mariadb-data

Usage
-----
    # Dry run — report only, no writes:
    python scripts/recover_alias_merge_data.py \\
        --live-url   "mysql+pymysql://user:pass@prod-host:3306/makerspaceapi" \\
        --backup-url "mysql+pymysql://root@127.0.0.1:13306/makerspaceapi"

    # Apply the safe restores:
    python scripts/recover_alias_merge_data.py ... --apply
"""

from __future__ import annotations

import argparse
import sys

import pymysql
import pymysql.cursors

FIELDS = ("oidc_sub", "name", "pin_hash", "created_at")


def _parse_dsn(dsn: str) -> dict:
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


def affected_ids(live: pymysql.Connection) -> list[int]:
    with live.cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM user_card_aliases ORDER BY user_id")
        return [row["user_id"] for row in cur.fetchall()]


def fetch_user(conn: pymysql.Connection, user_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, oidc_sub, name, pin_hash, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def oidc_sub_conflict(live: pymysql.Connection, oidc_sub: str, own_id: int) -> int | None:
    """Return the id of another live user already holding this oidc_sub, if any."""
    with live.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE oidc_sub = %s AND id != %s",
            (oidc_sub, own_id),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def plan_restore(live_row: dict, backup_row: dict, live: pymysql.Connection) -> dict:
    """Return {field: new_value} for fields safe to restore, plus any warnings."""
    restores: dict = {}
    warnings: list[str] = []

    for field in FIELDS:
        live_val = live_row.get(field)
        backup_val = backup_row.get(field)
        live_blank = live_val is None or (isinstance(live_val, str) and live_val.strip() == "")
        backup_blank = backup_val is None or (isinstance(backup_val, str) and backup_val.strip() == "")

        if not live_blank or backup_blank:
            continue  # nothing to do: live already has a value, or backup has nothing better

        if field == "oidc_sub":
            conflict = oidc_sub_conflict(live, backup_val, live_row["id"])
            if conflict is not None:
                warnings.append(
                    f"oidc_sub {backup_val!r} from backup already used by live user id={conflict} "
                    f"— NOT restoring, needs manual review"
                )
                continue

        restores[field] = backup_val

    return {"restores": restores, "warnings": warnings}


def apply_restore(live: pymysql.Connection, user_id: int, restores: dict) -> None:
    if not restores:
        return
    set_clause = ", ".join(f"{field} = %s" for field in restores)
    with live.cursor() as cur:
        cur.execute(
            f"UPDATE users SET {set_clause} WHERE id = %s",
            (*restores.values(), user_id),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--live-url", required=True,
                         help="mysql+pymysql://user:pass@host:3306/makerspaceapi — the production DB")
    parser.add_argument("--backup-url", required=True,
                         help="mysql+pymysql://user:pass@host:3306/makerspaceapi — the RESTORED backup DB")
    parser.add_argument("--apply", action="store_true",
                         help="Actually write the restores. Default is a dry run (report only).")
    args = parser.parse_args()

    live = connect(args.live_url)
    backup = connect(args.backup_url)

    if not args.apply:
        print("=== DRY RUN — no data will be written. Pass --apply to write. ===\n")

    ids = affected_ids(live)
    if not ids:
        print("No rows in user_card_aliases — nothing to check.")
        return

    print(f"{len(ids)} account(s) currently have at least one alias tag registered:\n")

    total_restorable = 0
    total_no_backup_row = 0
    total_nothing_to_do = 0
    total_warnings = 0

    for user_id in ids:
        live_row = fetch_user(live, user_id)
        if not live_row:
            print(f"  id={user_id}: MISSING from live `users` table entirely — investigate manually")
            continue

        backup_row = fetch_user(backup, user_id)
        if not backup_row:
            total_no_backup_row += 1
            print(f"  id={user_id}: no matching row in backup (account created after backup was taken?) — skipped")
            continue

        plan = plan_restore(live_row, backup_row, live)
        restores = plan["restores"]
        warnings = plan["warnings"]

        if not restores and not warnings:
            total_nothing_to_do += 1
            continue

        print(f"  id={user_id}:")
        for field, value in restores.items():
            print(f"    {field}: {live_row.get(field)!r}  ->  {value!r}")
        for w in warnings:
            print(f"    WARNING: {w}")
            total_warnings += 1

        if restores:
            total_restorable += 1
            if args.apply:
                apply_restore(live, user_id, restores)

    if args.apply:
        live.commit()
        print(f"\nApplied restores for {total_restorable} account(s).")
    else:
        print(
            f"\n{total_restorable} account(s) have a safe restore available "
            f"(re-run with --apply to write them)."
        )
    if total_no_backup_row:
        print(f"{total_no_backup_row} account(s) had no matching row in the backup.")
    if total_warnings:
        print(f"{total_warnings} warning(s) need manual review (see above) — not auto-restored.")

    live.close()
    backup.close()


if __name__ == "__main__":
    sys.exit(main())
