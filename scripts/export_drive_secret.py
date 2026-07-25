#!/usr/bin/env python3
"""Prints the JSON blob to paste into Colab Secrets / Kaggle Secrets.

RUN THIS YOURSELF. The output contains a Drive refresh token, i.e. a live
credential for one of your Google accounts. Nothing here sends it anywhere --
it goes to your terminal and you paste it into the platform's own secret store,
where the value stays hidden from the notebook body.

    python3 scripts/export_drive_secret.py acc-01

Prerequisites, in order:

  1. backend/data/credentials.json exists (OAuth client of type "Web
     application", with this server's callback URL as an authorised redirect
     URI). See docs/云盘接入清单.md.
  2. The account has been connected once through the dashboard, so a refresh
     token is stored for it.

Then add the printed value to:

  Colab   the key icon in the left sidebar, name it DRIVE_OAUTH_JSON, and turn
          on notebook access
  Kaggle  notebook editor -> Add-ons -> Secrets -> Add secret, same name

Why a refresh token rather than a service account: consumer Google accounts
(the 5 TB Google One / AI Pro kind) cannot have service accounts attached to
their Drive, so a user-consented OAuth refresh token is the only credential
that reaches that storage.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.google_account_manager import GoogleAccountManager, CREDENTIALS_FILE  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        print("error: exactly one account index is required", file=sys.stderr)
        return 2
    wanted = sys.argv[1]

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"error: {CREDENTIALS_FILE} not found -- create the OAuth client first "
              f"(see docs/云盘接入清单.md)", file=sys.stderr)
        return 1

    with open(CREDENTIALS_FILE) as f:
        client = json.load(f)
    block = client.get("web") or client.get("installed")
    if not block:
        print("error: credentials.json has neither a 'web' nor an 'installed' section",
              file=sys.stderr)
        return 1
    # GoogleAccountManager writes a placeholder credentials.json on first run so
    # the dashboard can start. Exporting from it would print a secret that looks
    # valid and fails only later, inside a notebook, with an opaque 401.
    if "dummy" in block.get("client_id", "") or "dummy" in block.get("client_secret", ""):
        print("error: credentials.json is still the placeholder created on first run "
              "(client_id is 'dummy_client_id'). Create a real OAuth client and replace "
              "it -- see docs/云盘接入清单.md", file=sys.stderr)
        return 1

    mgr = GoogleAccountManager()
    account = next((a for a in mgr.accounts if a.get("index") == wanted), None)
    if account is None:
        have = ", ".join(a.get("index", "?") for a in mgr.accounts) or "(none connected)"
        print(f"error: no account {wanted!r}. Connected accounts: {have}", file=sys.stderr)
        return 1
    if not account.get("refresh_token"):
        print(f"error: account {wanted!r} has no refresh token -- reconnect it through "
              f"the dashboard", file=sys.stderr)
        return 1

    payload = {
        "client_id": block["client_id"],
        "client_secret": block["client_secret"],
        # Decrypted here only to be printed to your own terminal.
        "refresh_token": mgr._decrypt(account["refresh_token"]),
    }

    print(f"--- paste the line below as secret DRIVE_OAUTH_JSON (account {wanted}) ---",
          file=sys.stderr)
    print(json.dumps(payload, separators=(",", ":")))
    print("--- treat it like a password: it grants write access to that account's "
          "app-created Drive files ---", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
