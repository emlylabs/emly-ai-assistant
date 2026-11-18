"""Clear a built-in sign-in lockout for one admin (S13).

After AUTH_LOCAL_LOCKOUT_THRESHOLD wrong passwords, built-in sign-in locks
the account for AUTH_LOCAL_LOCKOUT_DURATION_SECONDS. To let the admin in
before that:

    python -m scripts.unlock_local_admin admin@example.com
    (Docker: docker compose exec app python -m scripts.unlock_local_admin admin@example.com)

Writes an ``auth.local.unlocked`` audit row. Exits 1 if the address has
no built-in sign-in credential.
"""

from __future__ import annotations

import argparse
import sys


def unlock(email: str) -> bool:
    from models.admin_users import AdminUsers
    from models.local_credentials import LocalCredentials
    from services.audit import audit

    address = email.strip().lower()
    admin = AdminUsers.get_by_email(address)
    key = admin.id if admin is not None else f"bootstrap:{address}"
    if LocalCredentials.get(key) is None:
        return False
    LocalCredentials.record_success(key)
    audit("auth.local.unlocked", admin_id=admin.id if admin else None,
          target_type="admin", target_id=admin.id if admin else None,
          payload={"via": "scripts.unlock_local_admin"})
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    args = parser.parse_args(argv)
    if not unlock(args.email):
        print(f"No built-in sign-in credential for {args.email}", file=sys.stderr)
        return 1
    print(f"Unlocked {args.email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
