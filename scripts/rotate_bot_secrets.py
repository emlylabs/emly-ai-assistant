"""Re-encrypt every stored secret under the current BOT_SECRETS_KEY (C8).

Rotation procedure:

1. Generate a new key:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. Set BOT_SECRETS_KEY=<new key> and BOT_SECRETS_KEY_PREVIOUS=<old key>, then
   restart. Everything keeps working: old ciphertext still decrypts.
3. Run:  python -m scripts.rotate_bot_secrets          (or --dry-run first)
4. Keep BOT_SECRETS_KEY_PREVIOUS for at least WIDGET_TOKEN_RENEWAL_WINDOW_SECONDS
   (30 days by default) so returning widget visitors keep their identity and
   in-flight admin refresh cookies stay valid; then remove it.

Covers ``bots.api_key_encrypted`` and ``bot_channel.credentials_encrypted``.
Exit status is 1 if any value could not be decrypted with the configured
keys (those rows are left untouched and listed).
"""

from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger("rotate_bot_secrets")


def rotate_all(*, dry_run: bool = False) -> dict:
    from cryptography.fernet import InvalidToken

    from models.bot_channels import BotChannel
    from models.bots import Bot
    from services.secrets import reset_for_tests, rotate, validate_configuration

    validate_configuration()
    reset_for_tests()  # pick up the keys from the environment as they are now

    targets = (
        ("bots.api_key_encrypted", Bot, "api_key_encrypted"),
        ("bot_channel.credentials_encrypted", BotChannel, "credentials_encrypted"),
    )
    report: dict = {"rotated": 0, "failed": []}
    for label, model, column in targets:
        field = getattr(model, column)
        for row in model.select(model.id, field).where(field.is_null(False) & (field != "")):
            old = getattr(row, column)
            try:
                new = rotate(old)
            except InvalidToken:
                report["failed"].append(f"{label} id={row.id}")
                continue
            if not dry_run:
                model.update({field: new}).where(model.id == row.id).execute()
            report["rotated"] += 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Check every value decrypts; write nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = rotate_all(dry_run=args.dry_run)
    verb = "would re-encrypt" if args.dry_run else "re-encrypted"
    print(f"{verb} {report['rotated']} value(s)")
    for item in report["failed"]:
        print(f"could not decrypt with any configured key: {item}", file=sys.stderr)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
