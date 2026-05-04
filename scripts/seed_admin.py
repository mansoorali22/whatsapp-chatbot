"""
Seed the first admin user into the admin_users table.

Usage:
    python -m scripts.seed_admin

Or from the project root:
    python scripts/seed_admin.py

You'll be prompted for email, display name, and password.
The password is hashed with bcrypt before storing.
"""

import sys
import os
import getpass

# Ensure project root is on the path so 'app' imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from app.db.connection import init_db, SessionLocal
from app.db.models import AdminUser


def main():
    print("=" * 50)
    print("  Atleet Buddy — Seed First Admin User")
    print("=" * 50)

    # Ensure tables exist
    init_db()

    db = SessionLocal()

    try:
        # Check if any admin already exists
        existing = db.query(AdminUser).count()
        if existing > 0:
            print(f"\n⚠️  {existing} admin user(s) already exist.")
            proceed = input("Add another? (y/N): ").strip().lower()
            if proceed != "y":
                print("Aborted.")
                return

        # Collect info
        email = input("\nEmail: ").strip()
        if not email:
            print("Email is required.")
            return

        # Check for duplicate
        if db.query(AdminUser).filter(AdminUser.email == email).first():
            print(f"❌ Admin with email '{email}' already exists.")
            return

        display_name = input("Display name (optional): ").strip() or None

        password = getpass.getpass("Password: ")
        if len(password) < 8:
            print("❌ Password must be at least 8 characters.")
            return

        password_confirm = getpass.getpass("Confirm password: ")
        if password != password_confirm:
            print("❌ Passwords don't match.")
            return

        # Hash password
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        # Insert
        admin = AdminUser(
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            role="admin",  # First user is always admin
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(f"\n✅ Admin user created successfully!")
        print(f"   ID:    {admin.id}")
        print(f"   Email: {admin.email}")
        print(f"   Role:  {admin.role}")
        print(f"\nYou can now log in at POST /admin/auth/login")

    finally:
        db.close()


if __name__ == "__main__":
    main()
