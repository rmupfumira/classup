"""Generate a VAPID keypair for Web Push and store it in system_settings.

VAPID = Voluntary Application Server Identification. The browser uses the
public key as `applicationServerKey` when subscribing; the server signs the
JWT with the private key when sending. Once generated this is permanent
(reuse across all push sends for the tenant).

Key format gotcha (lessons learned from prior projects):
  py_vapid and pywebpush want SEC1 PEM, NOT PKCS8. SEC1 starts with
  `-----BEGIN EC PRIVATE KEY-----`. PKCS8 starts with
  `-----BEGIN PRIVATE KEY-----` and py_vapid's from_pem() blows up on it
  with "Could not deserialize key data (e.g. EC curves with explicit
  parameters)". Use TraditionalOpenSSL format, NOT PKCS8.

Usage:
    python scripts/generate_vapid_keys.py [--subject mailto:admin@you.com] [--force]

The keys + subject get stored under SystemSettings.key='vapid_config':
  {
    "public_key_b64url": "BNc...",
    "private_pem":       "-----BEGIN EC PRIVATE KEY-----\\n...",
    "subject":           "mailto:admin@classup.co.za",
    "generated_at":      "2026-05-13T...",
  }

Pass --force to overwrite an existing keypair. Anyone subscribed against
the old public key will get rejected (NotAllowedError on resubscribe) and
will need to re-enable notifications in the UI.
"""

import argparse
import asyncio
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `app` importable when running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.database import async_session_factory
from app.models import SystemSettings


VAPID_SETTINGS_KEY = "vapid_config"


def generate_keypair(subject: str) -> dict:
    """Produce a fresh P-256 keypair in the exact formats pywebpush / browsers expect."""
    priv = ec.generate_private_key(ec.SECP256R1(), default_backend())

    # Public key: uncompressed point (X9.62), base64url-encoded (no padding).
    # This is what the browser uses as `applicationServerKey`.
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64url = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("ascii")

    # Private key: SEC1 PEM (TraditionalOpenSSL). Starts with
    # "-----BEGIN EC PRIVATE KEY-----". py_vapid/pywebpush understand this.
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    return {
        "public_key_b64url": public_b64url,
        "private_pem": private_pem,
        "subject": subject,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def store(config: dict, force: bool) -> None:
    """Insert or update the vapid_config row in system_settings."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(SystemSettings).where(SystemSettings.key == VAPID_SETTINGS_KEY)
        )
        row = result.scalar_one_or_none()

        if row and not force:
            print("VAPID keypair already exists in system_settings.")
            print("Use --force to overwrite (will invalidate all existing push subscriptions).")
            return

        if row:
            row.value = config
            print("Overwrote existing VAPID keypair (existing subscriptions are now stale).")
        else:
            db.add(SystemSettings(key=VAPID_SETTINGS_KEY, value=config))
            print("Created VAPID keypair.")

        await db.commit()


def main() -> None:
    p = argparse.ArgumentParser(description="Generate VAPID keys for Web Push.")
    p.add_argument(
        "--subject",
        default="mailto:admin@classup.co.za",
        help="VAPID `sub` claim. Push services use this to reach you about delivery issues.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing keypair (invalidates all subscriptions).",
    )
    p.add_argument(
        "--print-only",
        action="store_true",
        help="Print the keypair to stdout instead of writing to the DB.",
    )
    args = p.parse_args()

    config = generate_keypair(args.subject)

    if args.print_only:
        print("=" * 60)
        print("Public key (base64url, give to the browser):")
        print(config["public_key_b64url"])
        print()
        print("Private key (SEC1 PEM, keep secret on the server):")
        print(config["private_pem"])
        print(f"Subject: {config['subject']}")
        print("=" * 60)
        return

    asyncio.run(store(config, args.force))
    print(f"Public key: {config['public_key_b64url'][:32]}...")
    print(f"Subject:    {config['subject']}")
    print("Done.")


if __name__ == "__main__":
    main()
