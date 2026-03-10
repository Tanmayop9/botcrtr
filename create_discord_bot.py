"""
Discord Bot Creator -- EDUCATIONAL PURPOSES ONLY
=================================================
Uses the Discord REST API directly -- NO browser, NO Selenium,
NO chromedriver, NO geckodriver required.  Works on any platform
that has Python + pip, including Termux on Android.

What it does (per bot):
  1. Create a new Discord application via POST /api/v10/applications
  2. Attach a bot user via POST /api/v10/applications/{id}/bot
  3. Reset / generate the bot token via POST /api/v10/applications/{id}/bot/reset
     -- If your account has 2FA enabled the TOTP code is generated automatically
        from your 2FA secret key and exchanged for an MFA ticket.
  4. Enable all three Privileged Gateway Intents by patching the application
     flags field (PATCH /api/v10/applications/{id}).
  5. Add the bot to a server (guild) via POST /api/v10/oauth2/authorize.
     Falls back to printing an invite URL if the API call is refused.
  6. Save the bot token to tokens.txt (one token per line).

Termux usage:
    bash termux_setup.sh   # one-time setup
    python create_discord_bot.py

Desktop usage:
    pip install -r requirements.txt
    python create_discord_bot.py

Dependencies:
    requests>=2.28.0,<3.0.0
    pyotp>=2.9.0,<3.0.0
"""

import sys
import time
import getpass

import pyotp
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE = "https://discord.com/api/v10"

# Privileged gateway intent bit-flags (set on the Application object's `flags`).
# These correspond to the three toggles on the Developer Portal Bot settings page.
_INTENT_PRESENCE        = 1 << 12   # 4096   -- Presence Intent
_INTENT_GUILD_MEMBERS   = 1 << 14   # 16384  -- Server Members Intent
_INTENT_MESSAGE_CONTENT = 1 << 18   # 262144 -- Message Content Intent
ALL_PRIVILEGED_INTENTS  = _INTENT_PRESENCE | _INTENT_GUILD_MEMBERS | _INTENT_MESSAGE_CONTENT

TOKEN_PATH = "tokens.txt"


# ---------------------------------------------------------------------------
# Session + low-level helpers
# ---------------------------------------------------------------------------

def _make_session(user_token: str) -> requests.Session:
    """Return a requests.Session pre-configured with Discord API headers."""
    sess = requests.Session()
    sess.headers.update({
        "Authorization": user_token,
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "X-Discord-Locale": "en-US",
    })
    return sess


def _raise_for_status(resp: requests.Response, action: str) -> dict:
    """
    Parse a Discord API response, raising a descriptive RuntimeError on failure.
    Returns the parsed JSON body on success.
    """
    try:
        body = resp.json()
    except Exception:
        body = {}

    if resp.status_code == 429:
        retry = body.get("retry_after", 5)
        raise RuntimeError(
            f"{action}: rate-limited by Discord. "
            f"Retry after {retry}s. Wait a moment and try again."
        )
    if resp.status_code == 401:
        raise RuntimeError(
            f"{action}: HTTP 401 Unauthorized. "
            "Your user token may be invalid or expired."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            f"{action}: HTTP 403 Forbidden. "
            "Your account may lack permission for this action."
        )
    if not resp.ok:
        raise RuntimeError(f"{action}: HTTP {resp.status_code} -- {body}")
    return body


def _exchange_mfa(sess: requests.Session, error_body: dict, totp_secret: str) -> str:
    """
    Exchange a TOTP code for a short-lived MFA authorization token.

    When a sensitive Discord API action requires two-factor verification the
    API returns HTTP 401 with {"code": 60003, "mfa": {"ticket": "<ticket>"}}.
    We POST the ticket + current TOTP code to /auth/mfa/totp to receive an
    MFA token that is then sent as the X-Discord-MFA-Authorization header on
    the retried request.
    """
    ticket = (error_body.get("mfa") or {}).get("ticket", "")
    if not ticket:
        raise RuntimeError(
            "Discord requires 2FA for this action but did not return an MFA "
            "ticket in its response. Cannot complete automatically."
        )
    if not totp_secret:
        raise RuntimeError(
            "Discord requires a 2FA code but no 2FA secret key was provided. "
            "Re-run the script and supply your base-32 2FA secret when prompted."
        )
    code = pyotp.TOTP(totp_secret).now()
    print(f"    Generated TOTP code: {code}")
    resp = sess.post(f"{API_BASE}/auth/mfa/totp", json={"code": code, "ticket": ticket})
    data = _raise_for_status(resp, "MFA token exchange")
    mfa_token = data.get("token", "")
    if not mfa_token:
        raise RuntimeError("MFA exchange succeeded but returned no token.")
    return mfa_token


# ---------------------------------------------------------------------------
# API workflow steps
# ---------------------------------------------------------------------------

def create_application(sess: requests.Session, name: str) -> dict:
    """
    Create a new Discord application.
    Returns the full application object (contains 'id' == client_id).
    """
    print(f"  [1/4] Creating application '{name}' ...")
    resp = sess.post(f"{API_BASE}/applications", json={"name": name})
    app = _raise_for_status(resp, "create application")
    print(f"    Application ID (Client ID): {app['id']}")
    return app


def create_bot_user(sess: requests.Session, app_id: str) -> None:
    """Attach a bot user to the application (required before resetting a token)."""
    print("  [2/4] Creating bot user ...")
    resp = sess.post(f"{API_BASE}/applications/{app_id}/bot")
    if resp.status_code == 400:
        code = (resp.json() or {}).get("code", 0)
        if code == 30007:
            # "Maximum number of webhooks reached" -- bot already exists
            print("    Bot user already exists -- skipping.")
            return
    _raise_for_status(resp, "create bot user")
    print("    Bot user created.")


def reset_bot_token(sess: requests.Session, app_id: str, totp_secret: str) -> str:
    """
    Reset (generate) the bot token.
    Handles the 2FA challenge automatically when *totp_secret* is supplied.
    Returns the new token string.
    """
    print("  [3/4] Resetting bot token ...")
    resp = sess.post(f"{API_BASE}/applications/{app_id}/bot/reset")

    # Handle 2FA challenge
    if resp.status_code == 401:
        body = resp.json() or {}
        if body.get("code") == 60003:
            print("    2FA verification required ...")
            mfa_tok = _exchange_mfa(sess, body, totp_secret)
            resp = sess.post(
                f"{API_BASE}/applications/{app_id}/bot/reset",
                headers={"X-Discord-MFA-Authorization": mfa_tok},
            )

    data = _raise_for_status(resp, "reset bot token")
    token = data.get("token", "")
    if not token:
        raise RuntimeError(
            "Token reset call succeeded (HTTP 200) but the response "
            "contained no token. Try again or copy the token manually "
            "from the Developer Portal."
        )
    print(f"    Token captured (length={len(token)}).")
    return token


def enable_privileged_intents(sess: requests.Session, app_id: str) -> None:
    """
    Enable all three Privileged Gateway Intents by patching the application's
    `flags` field.  Existing flags are preserved (bitwise OR).
    """
    print("  [4/4] Enabling all three Privileged Gateway Intents ...")

    # Read current flags so unrelated bits are not cleared.
    resp = sess.get(f"{API_BASE}/applications/{app_id}")
    app = _raise_for_status(resp, "get application")
    current_flags = app.get("flags", 0)
    new_flags = current_flags | ALL_PRIVILEGED_INTENTS

    patch_resp = sess.patch(
        f"{API_BASE}/applications/{app_id}",
        json={"flags": new_flags},
    )
    _raise_for_status(patch_resp, "patch application flags")

    print(f"    Presence Intent        enabled  (bit 1<<12 = {_INTENT_PRESENCE})")
    print(f"    Server Members Intent  enabled  (bit 1<<14 = {_INTENT_GUILD_MEMBERS})")
    print(f"    Message Content Intent enabled  (bit 1<<18 = {_INTENT_MESSAGE_CONTENT})")


def add_bot_to_server(
    sess: requests.Session,
    client_id: str,
    guild_id: str,
    permissions: str = "2048",
) -> None:
    """
    Add the bot to the Discord server identified by *guild_id*.

    Uses the internal OAuth2 authorize endpoint (POST /api/v10/oauth2/authorize)
    which is what the Discord web client calls when you click 'Authorise'.
    If the API call fails (e.g. missing Manage Server permission), the invite
    URL is printed so the user can open it manually.
    """
    print(f"  Adding bot to server (guild ID: {guild_id}) ...")

    if not client_id:
        print("  Client ID is missing -- skipping server invite step.")
        return

    invite_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions={permissions}"
        f"&guild_id={guild_id}"
        f"&scope=bot+applications.commands"
        f"&disable_guild_select=true"
    )

    # Query the authorize endpoint first (validates the parameters).
    get_resp = sess.get(
        f"{API_BASE}/oauth2/authorize",
        params={
            "client_id": client_id,
            "scope": "bot applications.commands",
            "permissions": permissions,
            "guild_id": guild_id,
        },
    )
    if not get_resp.ok:
        print(f"  Could not validate invite (HTTP {get_resp.status_code}).")
        print(f"  Open this URL manually to add the bot:\n    {invite_url}")
        return

    # Perform the actual authorization.
    post_resp = sess.post(
        f"{API_BASE}/oauth2/authorize",
        params={
            "client_id": client_id,
            "scope": "bot applications.commands",
            "permissions": permissions,
            "guild_id": guild_id,
        },
        json={
            "authorize": True,
            "permissions": permissions,
            "guild_id": guild_id,
        },
    )
    if post_resp.ok:
        print("  Bot successfully added to the server.")
    else:
        print(f"  Auto-invite failed (HTTP {post_resp.status_code}).")
        print(f"  Open this URL manually to add the bot:\n    {invite_url}")


def save_token(token: str) -> None:
    """Append *token* to tokens.txt (one token per line)."""
    with open(TOKEN_PATH, "a", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    print(f"    Token saved to '{TOKEN_PATH}'.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  Discord Bot Creator -- EDUCATIONAL PURPOSES ONLY")
    print("=" * 60)
    print(
        "\n[!] WARNING\n"
        "   Automating a Discord *user* account may violate Discord's\n"
        "   Terms of Service and can result in account suspension.\n"
        "   This script is for educational purposes only.\n"
        "   Use it responsibly and only with accounts you own.\n"
        "\n[!] SECURITY NOTE\n"
        "   Bot tokens will be stored in plain text in tokens.txt.\n"
        "   Keep that file private; never commit it to version control.\n"
    )
    confirm = input("Type 'yes' to acknowledge and continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    user_token = getpass.getpass(
        "\nDiscord user token (input is hidden): "
    ).strip()
    totp_secret = getpass.getpass(
        "2FA secret key (base-32, leave blank if 2FA is not enabled): "
    ).strip()
    app_name = input("Application / bot name: ").strip() or "MyDiscordBot"

    # How many bots?
    while True:
        raw = input("How many bots to create? [default: 1]: ").strip()
        if raw == "":
            bot_count = 1
            break
        try:
            bot_count = int(raw)
            if bot_count >= 1:
                break
            print("  Please enter a number >= 1.")
        except ValueError:
            print("  Please enter a whole number (e.g. 3).")

    guild_id = input(
        "Server (Guild) ID to add bot(s) to (leave blank to skip): "
    ).strip()
    permissions = input(
        "Bot permissions integer [default 2048 = Send Messages, "
        "press ENTER to keep default]: "
    ).strip() or "2048"

    sess = _make_session(user_token)
    results: list = []

    for i in range(1, bot_count + 1):
        # All bots get the exact same name; no bio or avatar is set.
        bot_name = app_name

        print(f"\n{'=' * 60}")
        print(f"  Bot {i}/{bot_count}: {bot_name}")
        print(f"{'=' * 60}")

        try:
            app     = create_application(sess, bot_name)
            app_id  = app["id"]
            create_bot_user(sess, app_id)
            token   = reset_bot_token(sess, app_id, totp_secret)
            enable_privileged_intents(sess, app_id)
            save_token(token)

            if guild_id:
                add_bot_to_server(sess, app_id, guild_id, permissions)
            else:
                print("  -- No server ID provided; skipping bot invite step.")

            results.append((bot_name, True))

        except (RuntimeError, requests.RequestException) as exc:
            # Per-bot failure: report and continue to the next bot.
            # KeyboardInterrupt / SystemExit are BaseException and propagate normally.
            print(f"\n  [FAIL] Bot '{bot_name}': {exc}", file=sys.stderr)
            results.append((bot_name, False))

        # Small pause between creations to respect Discord rate limits.
        if i < bot_count:
            time.sleep(2)

    # Summary
    succeeded = sum(1 for _, ok in results if ok)
    print(f"\n{'=' * 60}")
    print(f"  Summary: {succeeded}/{bot_count} bot(s) created successfully.")
    for name, ok in results:
        print(f"    {'[OK]  ' if ok else '[FAIL]'} {name}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
