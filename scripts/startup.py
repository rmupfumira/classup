#!/usr/bin/env python3
"""
Startup script for Railway deployment.
Runs migrations and creates super admin if configured.
"""

import os
import subprocess
import sys


def run_command(cmd: list[str], description: str) -> bool:
    """Run a command and return success status."""
    print(f"\n=== {description} ===")
    try:
        result = subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Warning: {description} failed with code {e.returncode}")
        return False


def main():
    # Run Alembic migrations
    print("\n" + "=" * 50)
    print("ClassUp Startup Script")
    print("=" * 50)

    run_command(["alembic", "upgrade", "head"], "Running database migrations")

    # Create super admin if environment variables are set
    email = os.environ.get("SUPER_ADMIN_EMAIL", "").strip()
    password = os.environ.get("SUPER_ADMIN_PASSWORD", "").strip()

    if email and password:
        first_name = os.environ.get("SUPER_ADMIN_FIRST_NAME", "Super").strip()
        last_name = os.environ.get("SUPER_ADMIN_LAST_NAME", "Admin").strip()

        print("\n=== Creating Super Admin ===")
        result = subprocess.run([
            "python", "scripts/create_super_admin.py",
            "--email", email,
            "--password", password,
            "--first-name", first_name,
            "--last-name", last_name
        ])
        # Don't fail if super admin already exists
        if result.returncode != 0:
            print("Note: Super admin creation returned non-zero (may already exist)")
    else:
        print("\nSkipping super admin creation (SUPER_ADMIN_EMAIL/PASSWORD not set)")

    # Start uvicorn
    port = os.environ.get("PORT", "8000")
    print(f"\n=== Starting uvicorn on port {port} ===\n")

    os.execvp("uvicorn", [
        "uvicorn",
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", port
    ])


if __name__ == "__main__":
    main()
