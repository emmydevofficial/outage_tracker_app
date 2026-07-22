"""Simple command-line utility to insert a new user into the
PostgreSQL ``users`` table defined for the Streamlit app.

Passwords are hashed with bcrypt before insertion. Bootstrap tool for
creating the first Super Admin (or any user) before the User Management
page (pages/15_User_Management.py) has anyone able to log in and use it.

Usage (from workspace root, after activating your venv):
    python add_user.py
"""
import getpass
import sys

from utils.auth import hash_password
from utils.db import create_user
from utils.regions import REGIONS


def add_user(username: str, password: str, role: str, region: str | None) -> None:
    # bcrypt only uses the first 72 bytes of a password; enforce that here
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > 72:
        pw_bytes = pw_bytes[:72]
        password = pw_bytes.decode("utf-8", "ignore")

    create_user(username, hash_password(password), role, region)


def main():
    uname = input("Username: ")
    if not uname:
        print("Username cannot be empty", file=sys.stderr)
        sys.exit(1)

    print("Role: 1) Super Admin (all regions, manages users)  2) Regional User")
    role_choice = input("Choose role [1/2]: ").strip()
    if role_choice == "1":
        role, region = "super_admin", None
    elif role_choice == "2":
        print("Regions: " + ", ".join(f"{i+1}) {r}" for i, r in enumerate(REGIONS)))
        region_choice = input("Choose region number: ").strip()
        try:
            region = REGIONS[int(region_choice) - 1]
        except (ValueError, IndexError):
            print("Invalid region choice", file=sys.stderr)
            sys.exit(1)
        role = "regional_user"
    else:
        print("Invalid role choice", file=sys.stderr)
        sys.exit(1)

    pwd = getpass.getpass("Password: ")
    if not pwd:
        print("Password cannot be empty", file=sys.stderr)
        sys.exit(1)
    if len(pwd.encode("utf-8")) > 72:
        print("Password longer than 72 bytes will be truncated by the system.")
    confirm = getpass.getpass("Confirm password: ")
    if pwd != confirm:
        print("Passwords do not match", file=sys.stderr)
        sys.exit(1)
    try:
        add_user(uname, pwd, role, region)
    except Exception as e:
        print(f"Failed to add user: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"User '{uname}' created successfully as {'Super Admin' if role == 'super_admin' else f'Regional User ({region})'}.")


if __name__ == "__main__":
    main()
