"""
Discord Bot Creator -- EDUCATIONAL PURPOSES ONLY
=================================================
Supports two automation methods -- user picks at runtime:

  Method 1 -- API  (no browser needed; works everywhere including Termux):
    Uses the Discord REST API directly via `requests`.
    No browser, no driver required.
    Just: pip install requests pyotp

    hCaptcha handling (Method 1):
      Discord sometimes demands an hCaptcha solution when creating an
      application.  Supply a captcha-solver API key at runtime to handle
      this automatically.  Supported services:
        - 2captcha  (https://2captcha.com)  -- service name: "2captcha"
        - CapSolver  (https://capsolver.com)  -- service name: "capsolver"
      Leave the API key blank to skip automatic solving (you will receive
      an informative error instead of a silent failure).

  Method 2 -- Browser  (Selenium; works on desktop AND Termux):
    Uses Selenium to automate the Discord Developer Portal in a real browser.
    On Termux: install Firefox + geckodriver via x11-repo (see termux_setup.sh).
    On desktop: Chrome or Firefox with matching driver.
    Needs: pip install -r requirements.txt  (includes selenium + webdriver-manager)

Per-bot steps (both methods):
  1. Create a new Discord application
  2. Attach a bot user
  3. Reset / generate the bot token  (2FA handled automatically via TOTP)
  4. Enable all three Privileged Gateway Intents
  5. Add the bot to a server / guild
  6. Save the token to tokens.txt  (one per line)

Termux usage:
    bash termux_setup.sh          # one-time setup (installs x11-repo, firefox, geckodriver, selenium)
    python create_discord_bot.py  # select Method 1 (API) or Method 2 (Browser)

Desktop usage:
    pip install -r requirements.txt
    python create_discord_bot.py  # select Method 1 or 2

Dependencies:
    requests>=2.28.0,<3.0.0          # both methods
    pyotp>=2.9.0,<3.0.0              # both methods (2FA TOTP)
    selenium>=4.0.0,<5.0.0           # Method 2 (Browser)
    webdriver-manager>=4.0.0,<5.0.0  # Method 2 (Browser)
"""

import os
import sys
import time
import shutil
import getpass

import pyotp
import requests

# ---------------------------------------------------------------------------
# Optional Selenium imports (Method 2 -- Browser).
# We import lazily so the script still starts on Termux / environments that
# only have `requests` + `pyotp` installed.
# ---------------------------------------------------------------------------
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.service import Service as FirefoxService
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
API_BASE  = "https://discord.com/api/v10"
LOGIN_URL = "https://discord.com/login"
DEVELOPER_PORTAL_URL = "https://discord.com/developers/applications"
TOKEN_PATH = "tokens.txt"

# Privileged gateway intent bit-flags (Application.flags field)
_INTENT_PRESENCE        = 1 << 12   # 4096   -- Presence Intent
_INTENT_GUILD_MEMBERS   = 1 << 14   # 16384  -- Server Members Intent
_INTENT_MESSAGE_CONTENT = 1 << 18   # 262144 -- Message Content Intent
ALL_PRIVILEGED_INTENTS  = _INTENT_PRESENCE | _INTENT_GUILD_MEMBERS | _INTENT_MESSAGE_CONTENT

# Browser method timeout (seconds) -- raised to handle slow mobile/network
WAIT_TIMEOUT = 45

# Captcha solver polling: up to MAX_CAPTCHA_POLL_ATTEMPTS * 5 s ≈ 3 min
MAX_CAPTCHA_POLL_ATTEMPTS = 36
# Maximum number of application-creation attempts (initial + captcha retry)
MAX_CAPTCHA_ATTEMPTS = 2

# Termux prefix
TERMUX_USR = "/data/data/com.termux/files/usr"


# ===========================================================================
# ENVIRONMENT HELPERS
# ===========================================================================

def is_termux() -> bool:
    """Return True when running inside a Termux session on Android."""
    return (
        os.environ.get("TERMUX_VERSION") is not None
        or os.path.isdir("/data/data/com.termux")
    )


def detect_browser() -> str:
    """Return 'firefox' or 'chrome' based on what is installed."""
    if is_termux():
        # On Termux, Firefox is installed via x11-repo + pkg install firefox.
        # geckodriver is also available: pkg install geckodriver.
        if shutil.which("firefox"):
            return "firefox"
        if shutil.which("chromium-browser") or shutil.which("chromium"):
            return "chrome"
        return "firefox"  # default; termux_setup.sh installs Firefox
    if shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium"):
        return "chrome"
    if shutil.which("firefox"):
        return "firefox"
    return "chrome"


# ===========================================================================
# SHARED HELPER
# ===========================================================================

def save_token(token: str) -> None:
    """Append *token* to tokens.txt (one token per line)."""
    with open(TOKEN_PATH, "a", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    print(f"    Token saved to '{TOKEN_PATH}'.")


# ===========================================================================
# METHOD 1 -- API  (requests-based, no browser)
# ===========================================================================

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
    """Raise a descriptive RuntimeError on non-2xx responses; return parsed JSON."""
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.status_code == 429:
        retry = body.get("retry_after", 5)
        raise RuntimeError(
            f"{action}: rate-limited by Discord. "
            f"Retry after {retry}s."
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
    Exchange a TOTP code for a short-lived MFA token.
    Discord returns {"code": 60003, "mfa": {"ticket": "..."}} when a sensitive
    action requires 2FA.  We exchange the ticket + TOTP code for an MFA token
    that is sent as X-Discord-MFA-Authorization on the retry.
    """
    ticket = (error_body.get("mfa") or {}).get("ticket", "")
    if not ticket:
        raise RuntimeError(
            "Discord requires 2FA for this action but did not return an MFA "
            "ticket. Cannot complete automatically."
        )
    if not totp_secret:
        raise RuntimeError(
            "Discord requires a 2FA code but no 2FA secret key was provided. "
            "Re-run and supply your base-32 2FA secret when prompted."
        )
    code = pyotp.TOTP(totp_secret).now()
    print(f"    Generated TOTP code: {code}")
    resp = sess.post(f"{API_BASE}/auth/mfa/totp", json={"code": code, "ticket": ticket})
    data = _raise_for_status(resp, "MFA token exchange")
    mfa_tok = data.get("token", "")
    if not mfa_tok:
        raise RuntimeError("MFA exchange succeeded but returned no token.")
    return mfa_tok


def _solve_hcaptcha(
    sitekey: str,
    pageurl: str,
    rqdata: str,
    solver_key: str,
    solver_service: str = "2captcha",
) -> str:
    """
    Solve an hCaptcha challenge via a third-party solving service.

    Supported *solver_service* values:
      "2captcha"  -- https://2captcha.com  (also aliased as "2cap")
      "capsolver" -- https://capsolver.com (also aliased as "cap")

    Returns the captcha response token (``gRecaptchaResponse`` field) to be
    sent back to Discord in the ``captcha_key`` body parameter.
    """
    svc = solver_service.lower()

    if svc in ("2captcha", "2cap"):
        base = "https://api.2captcha.com"
        task: dict = {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": pageurl,
            "websiteKey": sitekey,
            "isInvisible": False,
        }
        if rqdata:
            task["enterprisePayload"] = {"rqdata": rqdata}
        cr = requests.post(
            f"{base}/createTask",
            json={"clientKey": solver_key, "task": task},
            timeout=30,
        )
        cd = cr.json()
        if cd.get("errorId"):
            raise RuntimeError(
                f"2captcha createTask error: {cd.get('errorDescription')}"
            )
        task_id = cd["taskId"]
        print(f"    Captcha task submitted (id={task_id}). Waiting for solution ...")
        for _ in range(MAX_CAPTCHA_POLL_ATTEMPTS):
            time.sleep(5)
            rr = requests.post(
                f"{base}/getTaskResult",
                json={"clientKey": solver_key, "taskId": task_id},
                timeout=30,
            )
            rd = rr.json()
            if rd.get("errorId"):
                raise RuntimeError(
                    f"2captcha getTaskResult error: {rd.get('errorDescription')}"
                )
            if rd.get("status") == "ready":
                return rd["solution"]["gRecaptchaResponse"]
        raise RuntimeError("2captcha: timed out waiting for captcha solution.")

    if svc in ("capsolver", "cap"):
        base = "https://api.capsolver.com"
        task = {
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": pageurl,
            "websiteKey": sitekey,
        }
        if rqdata:
            task["enterprisePayload"] = {"rqdata": rqdata}
        cr = requests.post(
            f"{base}/createTask",
            json={"clientKey": solver_key, "task": task},
            timeout=30,
        )
        cd = cr.json()
        if cd.get("errorId"):
            raise RuntimeError(
                f"capsolver createTask error: {cd.get('errorDescription')}"
            )
        task_id = cd["taskId"]
        print(f"    Captcha task submitted (id={task_id}). Waiting for solution ...")
        for _ in range(MAX_CAPTCHA_POLL_ATTEMPTS):
            time.sleep(5)
            rr = requests.post(
                f"{base}/getTaskResult",
                json={"clientKey": solver_key, "taskId": task_id},
                timeout=30,
            )
            rd = rr.json()
            if rd.get("errorId"):
                raise RuntimeError(
                    f"capsolver getTaskResult error: {rd.get('errorDescription')}"
                )
            if rd.get("status") == "ready":
                return rd["solution"]["gRecaptchaResponse"]
        raise RuntimeError("capsolver: timed out waiting for captcha solution.")

    raise RuntimeError(
        f"Unknown captcha solver service: {solver_service!r}. "
        "Use '2captcha' or 'capsolver'."
    )


def api_create_application(
    sess: requests.Session,
    name: str,
    solver_key: str = "",
    solver_service: str = "2captcha",
) -> dict:
    """POST /api/v10/applications -- create app, return application object.

    If Discord returns an hCaptcha challenge (HTTP 400 with ``captcha_key``),
    the challenge is solved automatically when *solver_key* is provided, and
    the request is retried with the captcha token included.
    """
    print(f"  [1/4] Creating application '{name}' ...")
    payload: dict = {"name": name}
    for attempt in range(MAX_CAPTCHA_ATTEMPTS):
        resp = sess.post(f"{API_BASE}/applications", json=payload)
        if resp.status_code == 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            if "captcha_key" in body:
                if not solver_key:
                    raise RuntimeError(
                        f"create application: HTTP {resp.status_code} -- {body}\n"
                        "  Discord requires a captcha solution.  Re-run the script\n"
                        "  and supply a captcha solver API key (2captcha or CapSolver)\n"
                        "  when prompted, or use Method 2 (Browser) instead."
                    )
                if attempt > 0:
                    raise RuntimeError(
                        "create application: captcha retry failed -- "
                        "the solved token was rejected by Discord."
                    )
                print(f"    Captcha required. Solving via {solver_service} ...")
                captcha_token = _solve_hcaptcha(
                    sitekey=body["captcha_sitekey"],
                    pageurl="https://discord.com/developers/applications",
                    rqdata=body.get("captcha_rqdata", ""),
                    solver_key=solver_key,
                    solver_service=solver_service,
                )
                payload = {"name": name, "captcha_key": captcha_token}
                if body.get("captcha_rqtoken"):
                    payload["captcha_rqtoken"] = body["captcha_rqtoken"]
                continue  # retry with captcha token
        app = _raise_for_status(resp, "create application")
        print(f"    Client ID: {app['id']}")
        return app
    raise RuntimeError(
        "create application: failed unexpectedly after captcha handling loop."
    )


def api_create_bot_user(sess: requests.Session, app_id: str) -> None:
    """POST /api/v10/applications/{id}/bot -- attach bot user to app."""
    print("  [2/4] Creating bot user ...")
    resp = sess.post(f"{API_BASE}/applications/{app_id}/bot")
    if resp.status_code == 400 and (resp.json() or {}).get("code") == 30007:
        print("    Bot user already exists -- skipping.")
        return
    _raise_for_status(resp, "create bot user")
    print("    Bot user created.")


def api_reset_bot_token(sess: requests.Session, app_id: str, totp_secret: str) -> str:
    """POST /api/v10/applications/{id}/bot/reset -- returns the new token."""
    print("  [3/4] Resetting bot token ...")
    resp = sess.post(f"{API_BASE}/applications/{app_id}/bot/reset")
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
            "Token reset succeeded but the response contained no token. "
            "Copy it manually from the Developer Portal."
        )
    print(f"    Token captured (length={len(token)}).")
    return token


def api_enable_intents(sess: requests.Session, app_id: str) -> None:
    """PATCH /api/v10/applications/{id} -- set privileged intent flag bits."""
    print("  [4/4] Enabling all three Privileged Gateway Intents ...")
    resp = sess.get(f"{API_BASE}/applications/{app_id}")
    app = _raise_for_status(resp, "get application")
    new_flags = app.get("flags", 0) | ALL_PRIVILEGED_INTENTS
    patch = sess.patch(f"{API_BASE}/applications/{app_id}", json={"flags": new_flags})
    _raise_for_status(patch, "patch application flags")
    print(f"    Presence Intent        enabled  (1<<12 = {_INTENT_PRESENCE})")
    print(f"    Server Members Intent  enabled  (1<<14 = {_INTENT_GUILD_MEMBERS})")
    print(f"    Message Content Intent enabled  (1<<18 = {_INTENT_MESSAGE_CONTENT})")


def api_add_to_server(
    sess: requests.Session,
    client_id: str,
    guild_id: str,
    permissions: str,
) -> None:
    """POST /api/v10/oauth2/authorize -- add bot to guild; print URL on failure."""
    print(f"  Adding bot to server (guild {guild_id}) ...")
    if not client_id:
        print("  Client ID missing -- skipping.")
        return
    invite_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}&permissions={permissions}"
        f"&guild_id={guild_id}&scope=bot+applications.commands"
        f"&disable_guild_select=true"
    )
    get_r = sess.get(
        f"{API_BASE}/oauth2/authorize",
        params={"client_id": client_id, "scope": "bot applications.commands",
                "permissions": permissions, "guild_id": guild_id},
    )
    if not get_r.ok:
        print(f"  Validation failed (HTTP {get_r.status_code}). Use URL below:")
        print(f"    {invite_url}")
        return
    post_r = sess.post(
        f"{API_BASE}/oauth2/authorize",
        params={"client_id": client_id, "scope": "bot applications.commands",
                "permissions": permissions, "guild_id": guild_id},
        json={"authorize": True, "permissions": permissions, "guild_id": guild_id},
    )
    if post_r.ok:
        print("  Bot successfully added to the server.")
    else:
        print(f"  Auto-invite failed (HTTP {post_r.status_code}). Use URL below:")
        print(f"    {invite_url}")


def run_api_bot(
    sess: requests.Session,
    bot_name: str,
    totp_secret: str,
    guild_id: str,
    permissions: str,
    solver_key: str = "",
    solver_service: str = "2captcha",
) -> str:
    """Run the full API creation flow for one bot. Returns the token."""
    app    = api_create_application(sess, bot_name, solver_key, solver_service)
    app_id = app["id"]
    api_create_bot_user(sess, app_id)
    token  = api_reset_bot_token(sess, app_id, totp_secret)
    api_enable_intents(sess, app_id)
    save_token(token)
    if guild_id:
        api_add_to_server(sess, app_id, guild_id, permissions)
    else:
        print("  -- No server ID provided; skipping bot invite step.")
    return token


# ===========================================================================
# METHOD 2 -- BROWSER  (Selenium)
# ===========================================================================

def _chrome_user_agent() -> str:
    """Return a Chrome user-agent string using the installed version if detectable."""
    fallback = "136.0.0.0"
    try:
        import subprocess
        for binary in ("google-chrome", "chromium-browser", "chromium",
                       os.path.join(TERMUX_USR, "bin", "chromium-browser")):
            if shutil.which(binary) or os.path.exists(binary):
                r = subprocess.run([binary, "--version"],
                                   capture_output=True, text=True, timeout=5)
                for part in r.stdout.strip().split():
                    if part and part[0].isdigit():
                        fallback = f"{part.split('.')[0]}.0.0.0"
                        break
                break
    except Exception:  # noqa: BLE001
        pass
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{fallback} Safari/537.36"
    )


def _find_chromedriver() -> str:
    """Return chromedriver path: Termux bundled -> system PATH -> webdriver-manager."""
    if is_termux():
        for p in (
            os.path.join(TERMUX_USR, "lib", "chromium", "chromedriver"),
            os.path.join(TERMUX_USR, "bin", "chromedriver"),
        ):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    s = shutil.which("chromedriver")
    if s:
        return s
    return ChromeDriverManager().install()


def _find_geckodriver() -> str:
    """Return geckodriver path: system PATH -> webdriver-manager download."""
    s = shutil.which("geckodriver")
    if s:
        return s
    return GeckoDriverManager().install()


def build_browser_driver(browser: str, headless: bool):
    """Build and return a Chrome or Firefox WebDriver."""
    if browser == "firefox":
        opts = FirefoxOptions()
        if headless:
            opts.add_argument("-headless")
        opts.set_preference("dom.webdriver.enabled", False)
        opts.set_preference("useAutomationExtension", False)
        if is_termux():
            fb = os.path.join(TERMUX_USR, "bin", "firefox")
            if os.path.exists(fb):
                opts.binary_location = fb
        return webdriver.Firefox(service=FirefoxService(_find_geckodriver()), options=opts)
    # Chrome
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(f"user-agent={_chrome_user_agent()}")
    if is_termux():
        for c in (os.path.join(TERMUX_USR, "bin", "chromium-browser"),
                  os.path.join(TERMUX_USR, "bin", "chromium")):
            if os.path.exists(c):
                opts.binary_location = c
                break
    driver = webdriver.Chrome(service=ChromeService(_find_chromedriver()), options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


def _wait_for(driver, by, locator, timeout=WAIT_TIMEOUT):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, locator)))


def _wait_click(driver, by, locator, timeout=WAIT_TIMEOUT):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, locator)))


def _click(driver, by, locator, timeout=WAIT_TIMEOUT):
    el = _wait_click(driver, by, locator, timeout)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.3)
    el.click()
    return el


def _safe_toggle_intent(driver, label_text: str) -> None:
    """Enable the privileged intent toggle whose label contains *label_text*."""
    try:
        xpath = (
            f"//div[contains(normalize-space(.),'{label_text}')]"
            f"//input[@type='checkbox']|"
            f"//h3[contains(normalize-space(.),'{label_text}')]"
            f"/ancestor::div[contains(@class,'intent') or contains(@class,'Intent')]"
            f"//input[@type='checkbox']"
        )
        boxes = driver.find_elements(By.XPATH, xpath)
        for cb in boxes:
            if not cb.is_selected():
                driver.execute_script("arguments[0].click();", cb)
                time.sleep(0.5)
                print(f"    Enabled: {label_text}")
                return
        if boxes:
            print(f"    Already enabled: {label_text}")
        else:
            print(f"    Could not locate toggle for: {label_text}")
    except (TimeoutException, NoSuchElementException) as exc:
        print(f"    Warning toggling '{label_text}': {exc}")


def _extract_token_from_dom(driver) -> str:
    """Scan the DOM for a Discord bot token pattern (headless-safe)."""
    import re
    pat = re.compile(r'[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}')
    candidates = driver.execute_script("""
        var v=[];
        document.querySelectorAll(
            'input[type="text"],input:not([type]),textarea,[class*="token" i],[class*="Token"]'
        ).forEach(function(e){if(e.value)v.push(e.value);if(e.textContent)v.push(e.textContent);});
        return v;
    """) or []
    for text in candidates:
        text = str(text).strip()
        if pat.fullmatch(text):
            return text
    return ""


def browser_login(driver, user_token: str) -> None:
    """Inject user token into localStorage and redirect to the Discord app."""
    print("\n  [1/5] Logging in with user token ...")
    driver.get(LOGIN_URL)
    time.sleep(2)
    driver.execute_script(
        "window.localStorage.setItem('token',JSON.stringify(arguments[0]));"
        "window.location.replace('https://discord.com/channels/@me');",
        user_token,
    )
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda d: "/login" not in d.current_url and "channels" in d.current_url
        )
        print("    Logged in.")
    except TimeoutException:
        raise RuntimeError(
            "Token login timed out. Verify the token is correct and not expired."
        )


def browser_create_application(driver, app_name: str) -> str:
    """Navigate the Developer Portal to create a new application. Returns client_id."""
    print(f"\n  [2/5] Creating application '{app_name}' ...")
    driver.get(DEVELOPER_PORTAL_URL)
    time.sleep(2)
    _click(driver, By.XPATH, "//button[contains(normalize-space(.),'New Application')]")
    time.sleep(1)
    name_input = _wait_for(driver, By.XPATH, "//input[@placeholder or @name]")
    name_input.clear()
    name_input.send_keys(app_name)
    try:
        cb = driver.find_element(By.XPATH,
            "//input[@type='checkbox' and (contains(@id,'tos') or contains(@id,'terms'))]")
        if not cb.is_selected():
            driver.execute_script("arguments[0].click();", cb)
    except NoSuchElementException:
        pass
    _click(driver, By.XPATH, "//button[contains(normalize-space(.),'Create')]")
    time.sleep(3)
    # Extract client ID from URL (.../applications/<CLIENT_ID>/information)
    client_id = ""
    parts = driver.current_url.rstrip("/").split("/")
    for i, p in enumerate(parts):
        if p == "applications" and i + 1 < len(parts) and parts[i + 1].isdigit():
            client_id = parts[i + 1]
            break
    if not client_id:
        try:
            el = WebDriverWait(driver, 10).until(EC.presence_of_element_located((
                By.XPATH,
                "//div[contains(normalize-space(.),'Application ID')]/following-sibling::div|"
                "//input[@aria-label='Application ID' or @id='app-id']"
            )))
            client_id = (el.get_attribute("value") or el.text).strip()
        except TimeoutException:
            pass
    print(f"    Client ID: {client_id or '(not detected)'}")
    return client_id


def browser_enable_intents_and_get_token(driver, totp_secret: str) -> str:
    """On the Bot page: reset token (handle 2FA), enable intents, return token."""
    token = ""

    # --- Reset token ---
    try:
        btn = _wait_click(driver, By.XPATH,
            "//button[contains(normalize-space(.),'Reset Token')]", timeout=10)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        btn.click()
        time.sleep(1)

        # Confirmation modal
        try:
            _wait_click(driver, By.XPATH,
                "//button[contains(normalize-space(.),'Yes, do it!') or "
                "contains(normalize-space(.),'Confirm')]", timeout=10).click()
            time.sleep(1)
        except TimeoutException:
            pass

        # 2FA modal
        try:
            totp_input = WebDriverWait(driver, 8).until(EC.presence_of_element_located((
                By.XPATH,
                "//input[@placeholder='6-digit authentication code' or "
                "@name='code' or @autocomplete='one-time-code' or "
                "contains(@placeholder,'digit')]"
            )))
            if not totp_secret:
                raise RuntimeError(
                    "Discord is asking for a 2FA code but no secret was provided."
                )
            code = pyotp.TOTP(totp_secret).now()
            print(f"    Generated TOTP code: {code}")
            totp_input.clear()
            totp_input.send_keys(code)
            time.sleep(0.3)
            try:
                _wait_click(driver, By.XPATH,
                    "//button[@type='submit' or contains(normalize-space(.),'Verify') "
                    "or contains(normalize-space(.),'Log In')]", timeout=8).click()
            except TimeoutException:
                pass
            time.sleep(2)
        except TimeoutException:
            pass  # no 2FA challenge

        # Revealed token from DOM
        token_el = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//div[contains(@class,'token') or @data-text-as-pseudo-element]"
                "|//span[contains(@class,'token')]"
            ))
        )
        token = token_el.text.strip()
    except TimeoutException:
        pass

    # DOM scan fallback (headless-safe)
    if not token:
        token = _extract_token_from_dom(driver)
        if token:
            print("    Token extracted from DOM.")

    # Clipboard fallback (non-headless only)
    if not token:
        try:
            _wait_click(driver, By.XPATH,
                "//button[contains(normalize-space(.),'Copy')]", timeout=10).click()
            time.sleep(0.5)
            token = driver.execute_async_script(
                "var d=arguments[0];"
                "navigator.clipboard.readText().then(d).catch(function(){d('');});"
            ) or ""
        except (TimeoutException, NoSuchElementException):
            pass

    if token:
        print(f"    Token captured (length={len(token)}).")
    else:
        print("    Could not capture token automatically. Copy it manually from the portal.")

    # --- Enable intents ---
    print("  [4/5] Enabling Privileged Gateway Intents ...")
    for label in ("PRESENCE INTENT", "SERVER MEMBERS INTENT", "MESSAGE CONTENT INTENT"):
        _safe_toggle_intent(driver, label)

    # --- Save changes ---
    try:
        _wait_click(driver, By.XPATH,
            "//button[contains(normalize-space(.),'Save Changes')]", timeout=10).click()
        time.sleep(2)
        print("    Changes saved.")
    except TimeoutException:
        print("    'Save Changes' not found -- may have been auto-saved.")

    return token


def browser_add_to_server(driver, client_id: str, guild_id: str, permissions: str) -> None:
    """Navigate to the OAuth2 authorize page and click Authorise."""
    print(f"\n  [5/5] Adding bot to server (guild {guild_id}) ...")
    if not client_id:
        print("    Client ID missing -- skipping.")
        return
    oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}&permissions={permissions}"
        f"&guild_id={guild_id}&scope=bot+applications.commands"
        f"&disable_guild_select=true"
    )
    driver.get(oauth_url)
    time.sleep(3)
    try:
        el = WebDriverWait(driver, 10).until(EC.presence_of_element_located((
            By.XPATH,
            f"//div[@data-guild-id='{guild_id}']|//option[@value='{guild_id}']"
        )))
        driver.execute_script("arguments[0].click();", el)
        time.sleep(1)
    except TimeoutException:
        pass
    try:
        _wait_click(driver, By.XPATH,
            "//button[contains(normalize-space(.),'Continue')]", timeout=10).click()
        time.sleep(2)
    except TimeoutException:
        pass
    try:
        _wait_click(driver, By.XPATH,
            "//button[contains(normalize-space(.),'Authorise') or "
            "contains(normalize-space(.),'Authorize')]", timeout=15).click()
        time.sleep(3)
        print("    Bot added to server.")
    except TimeoutException:
        print(f"    Could not click Authorise. Open manually:\n      {oauth_url}")


def run_browser_bot(driver, bot_name: str, totp_secret: str, guild_id: str, permissions: str) -> str:
    """Run the full browser creation flow for one bot. Returns the token."""
    client_id = browser_create_application(driver, bot_name)
    print("\n  [3/5] Navigating to Bot settings ...")
    _click(driver, By.XPATH, "//a[normalize-space(.)='Bot']|//div[normalize-space(.)='Bot']")
    time.sleep(2)
    token = browser_enable_intents_and_get_token(driver, totp_secret)
    save_token(token)
    if guild_id:
        browser_add_to_server(driver, client_id, guild_id, permissions)
    else:
        print("  -- No server ID provided; skipping bot invite step.")
    return token


# ===========================================================================
# ENTRY POINT
# ===========================================================================

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

    # ------------------------------------------------------------------
    # Method selection
    # ------------------------------------------------------------------
    print(
        "\nSelect automation method:\n"
        "  1 = API  (no browser needed -- works on Termux and all platforms)\n"
        "  2 = Browser  (Selenium -- requires Chrome or Firefox installed;\n"
        "                does NOT work on Termux)\n"
    )
    while True:
        method_raw = input("Enter 1 or 2 [default: 1]: ").strip()
        if method_raw in ("", "1"):
            method = "api"
            break
        if method_raw == "2":
            method = "browser"
            break
        print("  Please enter 1 or 2.")

    if method == "browser":
        if not SELENIUM_AVAILABLE:
            print(
                "\n[!] Selenium is not installed. Install it with:\n"
                "      pip install selenium webdriver-manager\n"
                "    On Termux, run termux_setup.sh first.\n"
                "    Or switch to Method 1 (API) -- no install needed."
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Common inputs
    # ------------------------------------------------------------------
    user_token = getpass.getpass(
        "\nDiscord user token (input is hidden): "
    ).strip()
    totp_secret = getpass.getpass(
        "2FA secret key (base-32, leave blank if 2FA not enabled): "
    ).strip()
    app_name = input("Application / bot name: ").strip() or "MyDiscordBot"

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
        "Bot permissions integer [default 2048 = Send Messages, ENTER to keep]: "
    ).strip() or "2048"

    # ------------------------------------------------------------------
    # Captcha solver inputs (API method only; ignored for Browser method)
    # ------------------------------------------------------------------
    solver_key = ""
    solver_service = "2captcha"
    if method == "api":
        print(
            "\nCaptcha solver (optional):\n"
            "  Discord may require an hCaptcha solution when creating applications.\n"
            "  Supported services: 2captcha (https://2captcha.com)\n"
            "                      capsolver (https://capsolver.com)\n"
            "  Leave blank to skip -- you will see an error if a captcha is required.\n"
        )
        svc_raw = input(
            "Captcha solver service [2captcha/capsolver, leave blank to skip]: "
        ).strip().lower()
        if svc_raw in ("2captcha", "2cap", "capsolver", "cap"):
            solver_service = svc_raw
            solver_key = getpass.getpass(
                "Captcha solver API key (input is hidden): "
            ).strip()

    # ------------------------------------------------------------------
    # Browser-specific inputs
    # ------------------------------------------------------------------
    driver = None
    if method == "browser":
        default_browser = detect_browser()
        b_raw = input(
            f"Browser [chrome/firefox, default: {default_browser}]: "
        ).strip().lower()
        browser = b_raw if b_raw in ("chrome", "firefox") else default_browser

        h_raw = input("Run headless? [y/N]: ").strip().lower()
        headless = h_raw in ("y", "yes")

        print("\nStarting browser ...")
        driver = build_browser_driver(browser, headless)
        browser_login(driver, user_token)

    # ------------------------------------------------------------------
    # Creation loop
    # ------------------------------------------------------------------
    sess = _make_session(user_token) if method == "api" else None
    results: list = []

    try:
        for i in range(1, bot_count + 1):
            bot_name = app_name  # same name for every bot; no bio or avatar set

            print(f"\n{'=' * 60}")
            print(f"  Bot {i}/{bot_count}: {bot_name}  [{method.upper()} method]")
            print(f"{'=' * 60}")

            try:
                if method == "api":
                    run_api_bot(
                        sess, bot_name, totp_secret, guild_id, permissions,
                        solver_key, solver_service,
                    )
                else:
                    run_browser_bot(driver, bot_name, totp_secret, guild_id, permissions)
                results.append((bot_name, True))
            except (RuntimeError, requests.RequestException) as exc:
                print(f"\n  [FAIL] {exc}", file=sys.stderr)
                results.append((bot_name, False))
            except Exception as exc:  # noqa: BLE001 -- catches Selenium exceptions when available
                # KeyboardInterrupt/SystemExit are BaseException and propagate normally.
                print(f"\n  [FAIL] {exc}", file=sys.stderr)
                results.append((bot_name, False))

            if i < bot_count:
                time.sleep(2)

    finally:
        if driver is not None:
            # Show "press ENTER" pause only when a visible browser window is open.
            if method != "browser" or is_termux() or headless:
                driver.quit()
            else:
                input("\nPress ENTER to close the browser ...")
                driver.quit()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    succeeded = sum(1 for _, ok in results if ok)
    print(f"\n{'=' * 60}")
    print(f"  Summary: {succeeded}/{bot_count} bot(s) created successfully.")
    for name, ok in results:
        print(f"    {'[OK]  ' if ok else '[FAIL]'} {name}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
