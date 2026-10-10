"""Account first aid on this laptop, for when nobody can sign in to the app::

    python -m cashcow_studio.auth list
    python -m cashcow_studio.auth reset-password <username>
    python -m cashcow_studio.auth enable <username>
    python -m cashcow_studio.auth unlock <username>

``reset-password`` prints a new one-time password (the person must choose their own at the
next sign-in); ``enable`` turns an account that was switched off back on; ``unlock`` lets an
account that was locked to one laptop sign in on any laptop again (for an admin locked to a
lost or renamed laptop). All of them work on the team's accounts in the shared folder chosen
in Settings > Folders, need that folder to be reachable, and are written to the activity log
as done from the command line.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_settings
from . import audit, machine_name
from .users import UserStore, UserStoreError

COMMAND_LINE_USER = "command-line"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cashcow_studio.auth",
        description="CashCow Studio account first aid for this laptop's team account list.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Show the team's accounts.")
    reset = commands.add_parser("reset-password", help="Give an account a new password.")
    reset.add_argument("username")
    enable = commands.add_parser("enable", help="Turn a switched-off account back on.")
    enable.add_argument("username")
    unlock = commands.add_parser("unlock", help="Let an account sign in on any laptop again.")
    unlock.add_argument("username")
    args = parser.parse_args(argv)

    settings = load_settings()
    store = UserStore(settings.resolved_shared_dir, settings.app_data_dir)
    try:
        if args.command == "list":
            roster = store.roster()
            if roster.notice:
                print(roster.notice)
            if not roster.users:
                print(f"No accounts in {store.users_dir}. Open the app to create the first admin.")
            for user in store.list():
                state = "turned off" if user.disabled else "active"
                laptop = f", only on {user.machine}" if user.machine else ""
                print(f"{user.username:<32} {user.role:<7} {state}{laptop}  ({user.display_name})")
            return 0
        if args.command == "reset-password":
            user, password = store.reset_password(args.username)
            audit.record(
                settings.app_data_dir, "user.reset", COMMAND_LINE_USER, machine=machine_name(),
                detail={"username": user.username, "via": "command line"},
            )
            print(f"New one-time password for {user.username}: {password}")
            print("Hand it over in person; they choose their own password at the next sign-in.")
            return 0
        if args.command == "unlock":
            user, changes = store.update(args.username, machine=None, fields={"machine"})
            if not changes:
                print(f"The account {user.username} is not locked to a laptop.")
                return 0
            audit.record(
                settings.app_data_dir, "user.update", COMMAND_LINE_USER, machine=machine_name(),
                detail={"username": user.username, "changes": changes, "via": "command line"},
            )
            print(f"The account {user.username} can sign in on any laptop again.")
            return 0
        user, changes = store.update(args.username, disabled=False, fields={"disabled"})
        if not changes:
            print(f"The account {user.username} is already turned on.")
            return 0
        audit.record(
            settings.app_data_dir, "user.update", COMMAND_LINE_USER, machine=machine_name(),
            detail={"username": user.username, "changes": changes, "via": "command line"},
        )
        print(f"The account {user.username} is turned on again.")
        return 0
    except UserStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"The team's account list could not be saved: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through main() in the tests
    raise SystemExit(main())
