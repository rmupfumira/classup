#!/usr/bin/env python3
"""
CLI script to create the initial super admin user.

Usage (interactive):
    python scripts/create_super_admin.py

Usage (non-interactive, for Railway):
    python scripts/create_super_admin.py --email admin@example.com --password yourpassword --first-name John --last-name Doe
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from getpass import getpass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, engine
from app.models import User
from app.models.user import Role
from app.utils.security import hash_password


async def create_super_admin(
    email: str | None = None,
    password: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    interactive: bool = True
):
    """Create a super admin user."""
    print("\n" + "=" * 50)
    print("ClassUp v2 - Super Admin Setup")
    print("=" * 50 + "\n")

    # Get email
    if not email:
        while True:
            email = input("Enter email address: ").strip().lower()
            if "@" in email and "." in email:
                break
            print("Please enter a valid email address.")
    else:
        email = email.strip().lower()
        if "@" not in email or "." not in email:
            print("Invalid email address.")
            return False

    # Get password
    if not password:
        while True:
            password = getpass("Enter password (min 8 characters): ")
            if len(password) >= 8:
                break
            print("Password must be at least 8 characters.")

        password_confirm = getpass("Confirm password: ")
        if password != password_confirm:
            print("\nPasswords do not match. Aborting.")
            return False
    else:
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return False

    # Get name
    if not first_name:
        first_name = input("Enter first name: ").strip()
        if not first_name:
            first_name = "Super"

    if not last_name:
        last_name = input("Enter last name: ").strip()
        if not last_name:
            last_name = "Admin"

    # Create the user
    async with async_session_factory() as session:
        # Check if super admin already exists
        result = await session.execute(
            select(User).where(
                User.role == Role.SUPER_ADMIN,
                User.deleted_at.is_(None)
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"\nA super admin already exists: {existing.email}")
            if interactive:
                confirm = input("Create another super admin? (y/n): ").strip().lower()
                if confirm != "y":
                    print("Aborting.")
                    return False
            else:
                print("Use --force to create another super admin.")
                return False

        # Check if email already exists
        result = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        if result.scalar_one_or_none():
            print(f"\nUser with email {email} already exists.")
            return False

        # Create the super admin
        user = User(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            role=Role.SUPER_ADMIN,
            tenant_id=None,  # Super admins don't belong to a tenant
            is_active=True,
            language="en"
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)

        print("\n" + "=" * 50)
        print("Super Admin Created Successfully!")
        print("=" * 50)
        print(f"  Email: {user.email}")
        print(f"  Name: {user.first_name} {user.last_name}")
        print(f"  ID: {user.id}")
        print("=" * 50 + "\n")

        return True


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Create a ClassUp super admin user")
    parser.add_argument("--email", "-e", help="Admin email address")
    parser.add_argument("--password", "-p", help="Admin password (min 8 chars)")
    parser.add_argument("--first-name", "-f", help="First name", default="Super")
    parser.add_argument("--last-name", "-l", help="Last name", default="Admin")
    parser.add_argument("--force", action="store_true", help="Force creation even if super admin exists")

    args = parser.parse_args()

    # Determine if running interactively
    interactive = not (args.email and args.password)

    try:
        success = await create_super_admin(
            email=args.email,
            password=args.password,
            first_name=args.first_name,
            last_name=args.last_name,
            interactive=interactive
        )
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
