"""Standalone script to purge uploaded files older than 90 days.

The app itself already does this lazily whenever a Super Admin opens the
Activity Log page (pages/17_Activity_Log.py), which needs no setup. This
script exists only for anyone who wants exact-day enforcement regardless of
whether that page gets visited -- wire it into a real OS cron / Task
Scheduler, e.g.:

    0 3 * * * /path/to/venv/bin/python /path/to/purge_expired_uploads.py

Usage (from workspace root, after activating your venv):
    python purge_expired_uploads.py
"""
from utils.file_storage import purge_expired_files


def main():
    purged = purge_expired_files()
    print(f"Purged {purged} expired file(s).")


if __name__ == "__main__":
    main()
