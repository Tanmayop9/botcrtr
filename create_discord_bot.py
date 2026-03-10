"""
Discord Bot Creator -- EDUCATIONAL PURPOSES ONLY
================================================
Automates the Discord Developer Portal to:
  1. Log in by injecting your Discord account token into the browser session.
  2. Create a new application (bot).
  3. Enable all three Privileged Gateway Intents:
       • Presence Intent
       • Server Members Intent
       • Message Content Intent
  4. Reset / reveal the bot token; if your account has 2FA enabled, the
     6-digit TOTP code is generated automatically from your 2FA secret key.
     The bot token is saved to tokens.txt.
  5. Add the bot to a server (guild) whose ID the user provides.

Termux (Android) usage:
    Run termux_setup.sh first, then:
        python create_discord_bot.py
    The script auto-detects Termux, uses Chromium (the only browser
    available via pkg on Termux) with its bundled chromedriver, and
    forces headless mode (no display server required).

Usage (desktop):
    python create_discord_bot.py

Dependencies (install with pip install -r requirements.txt):
    selenium>=4.0.0,<5.0.0
    webdriver-manager>=4.0.0,<5.0.0
    pyotp>=2.9.0,<3.0.0
"""

import os
import sys
import time
import shutil
import getpass

import pyotp
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEVELOPER_PORTAL_URL = "https://discord.com/developers/applications"
LOGIN_URL = "https://discord.com/login"
WAIT_TIMEOUT = 45  # seconds -- raised to 45 to tolerate mobile CPU/network on Termux

# Termux installs everything under this prefix on Android.
TERMUX_USR = "/data/data/com.termux/files/usr"


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def is_termux() -> bool:
    """Return True when running inside a Termux session on Android."""
    return (
        os.environ.get("TERMUX_VERSION") is not None
        or os.path.isdir("/data/data/com.termux")
    )


def detect_browser() -> str:
    """
    Return 'firefox' or 'chrome' based on what is installed.

    On Termux, Chromium is the only available browser via pkg:
        pkg install chromium
    Firefox is NOT available in the Termux main repository.
    On desktop, Chrome/Chromium is preferred; Firefox is the fallback.
    """
    if is_termux():
        for candidate in (
            os.path.join(TERMUX_USR, "bin", "chromium-browser"),
            os.path.join(TERMUX_USR, "bin", "chromium"),
        ):
            if os.path.exists(candidate) or shutil.which(os.path.basename(candidate)):
                return "chrome"
        return "chrome"  # default for Termux -- termux_setup.sh installs chromium
    # Desktop: prefer Chrome/Chromium, fall back to Firefox
    if shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium"):
        return "chrome"
    if shutil.which("firefox"):
        return "firefox"
    return "chrome"  # let webdriver-manager download it


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chrome_user_agent() -> str:
    """
    Build a realistic Chrome user-agent string.
    Uses the installed Chrome/Chromium major version when detectable,
    otherwise falls back to a recent hard-coded version.
    """
    fallback_version = "124.0.0.0"
    try:
        import subprocess
        for binary in ("google-chrome", "chromium-browser", "chromium",
                       os.path.join(TERMUX_USR, "bin", "chromium-browser")):
            if shutil.which(binary) or os.path.exists(binary):
                result = subprocess.run(
                    [binary, "--version"],
                    capture_output=True, text=True, timeout=5
                )
                parts = result.stdout.strip().split()
                # Output is typically: "Google Chrome 124.0.6367.82" or "Chromium 124.0.6367.82"
                for part in parts:
                    if part[0].isdigit():
                        major = part.split(".")[0]
                        fallback_version = f"{major}.0.0.0"
                        break
                break
    except Exception:  # noqa: BLE001 -- non-critical, gracefully degrade
        pass
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{fallback_version} Safari/537.36"
    )



def _find_chromedriver() -> str:
    """
    Return a path to chromedriver, checking in this order:
      1. Paths where Termux's `pkg install chromium` places the driver
      2. System PATH (e.g. a manually installed chromedriver)
      3. webdriver-manager download (desktop fallback)
    """
    if is_termux():
        termux_candidates = (
            # Termux chromium package bundles the driver here:
            os.path.join(TERMUX_USR, "lib", "chromium", "chromedriver"),
            os.path.join(TERMUX_USR, "bin", "chromedriver"),
        )
        for path in termux_candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    system = shutil.which("chromedriver")
    if system:
        return system
    return ChromeDriverManager().install()


def _find_geckodriver() -> str:
    """
    Return a path to geckodriver, checking system PATH before downloading.
    On Termux, `pkg install geckodriver` places it in PATH.
    """
    system = shutil.which("geckodriver")
    if system:
        return system
    return GeckoDriverManager().install()


def build_driver(browser: str = "chrome", headless: bool = False) -> webdriver.Remote:
    """
    Return a configured WebDriver for *browser* ('chrome' or 'firefox').

    On Termux, only Chromium is available via pkg.  The script uses
    Termux's bundled chromedriver so no internet download is needed.
    """
    if browser == "firefox":
        return _build_firefox_driver(headless)
    return _build_chrome_driver(headless)


def _build_chrome_driver(headless: bool) -> webdriver.Chrome:
    """Build and return a Chrome WebDriver, Termux-aware."""
    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"user-agent={_chrome_user_agent()}")
    # On Termux, point to the Chromium binary installed via pkg.
    if is_termux():
        for candidate in (
            os.path.join(TERMUX_USR, "bin", "chromium-browser"),
            os.path.join(TERMUX_USR, "bin", "chromium"),
        ):
            if os.path.exists(candidate):
                options.binary_location = candidate
                break
    service = ChromeService(_find_chromedriver())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def _build_firefox_driver(headless: bool) -> webdriver.Firefox:
    """Build and return a Firefox WebDriver, Termux-aware."""
    options = FirefoxOptions()
    if headless:
        options.add_argument("-headless")  # Firefox uses single-dash flag
    # Suppress webdriver detection in Firefox
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    # On Termux, point to the Firefox binary installed via pkg.
    if is_termux():
        firefox_bin = os.path.join(TERMUX_USR, "bin", "firefox")
        if os.path.exists(firefox_bin):
            options.binary_location = firefox_bin
    service = FirefoxService(_find_geckodriver())
    return webdriver.Firefox(service=service, options=options)


def wait_for(driver: webdriver.Remote, by: str, locator: str, timeout: int = WAIT_TIMEOUT):
    """Wait until an element is present and return it."""
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, locator))
    )


def wait_clickable(driver: webdriver.Remote, by: str, locator: str, timeout: int = WAIT_TIMEOUT):
    """Wait until an element is clickable and return it."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, locator))
    )


def click(driver: webdriver.Remote, by: str, locator: str, timeout: int = WAIT_TIMEOUT):
    """Wait for an element to be clickable, then click it."""
    element = wait_clickable(driver, by, locator, timeout)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    time.sleep(0.3)
    element.click()
    return element


def safe_toggle_intent(driver: webdriver.Remote, label_text: str) -> None:
    """
    Find the intent toggle whose visible label contains *label_text* and
    enable it if it is not already checked/enabled.
    """
    try:
        # The privileged intents section uses <input type="checkbox"> elements
        # whose associated label text is nearby.  We use XPath to find them.
        xpath = (
            f"//div[contains(normalize-space(.), '{label_text}')]"
            f"//input[@type='checkbox'] | "
            f"//h3[contains(normalize-space(.), '{label_text}')]"
            f"/ancestor::div[contains(@class,'intent') or contains(@class,'Intent')]"
            f"//input[@type='checkbox']"
        )
        checkboxes = driver.find_elements(By.XPATH, xpath)
        for cb in checkboxes:
            if not cb.is_selected():
                driver.execute_script("arguments[0].click();", cb)
                time.sleep(0.5)
                print(f"  ✓ Enabled intent: {label_text}")
                return
        if checkboxes:
            print(f"  -- Intent already enabled: {label_text}")
        else:
            print(f"  ⚠ Could not locate toggle for intent: {label_text}")
    except (TimeoutException, NoSuchElementException) as exc:
        print(f"  ⚠ Error toggling intent '{label_text}': {exc}")


def save_token(token: str, path: str = "tokens.txt") -> None:
    """Append *token* to *path* (one token per line)."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    print(f"\n✓ Token saved to '{path}'.")


def _extract_token_from_dom(driver: webdriver.Remote) -> str:
    """
    Scan visible input/text elements for a Discord bot token.

    Discord bot tokens have the form: <base64url>.<base64url>.<base64url>
    This works in headless mode (no clipboard permission required).
    """
    import re
    token_re = re.compile(
        r'[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}'
    )
    candidates: list = driver.execute_script("""
        var vals = [];
        document.querySelectorAll(
            'input[type="text"], input:not([type]), textarea, '
            + '[class*="token" i], [class*="Token"]'
        ).forEach(function(el) {
            if (el.value)       vals.push(el.value);
            if (el.textContent) vals.push(el.textContent);
        });
        return vals;
    """) or []
    for text in candidates:
        text = str(text).strip()
        if token_re.fullmatch(text):
            return text
    return ""


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def login_with_token(driver: webdriver.Remote, user_token: str) -> None:
    """
    Authenticate by injecting *user_token* directly into the browser's
    localStorage, then navigating to the Discord app.

    This avoids the email/password form and any login-page CAPTCHA.
    The token is the value found in Discord's localStorage under the key
    'token' (Settings → Advanced → copy from DevTools, or a third-party
    token grabber — use only your own account).
    """
    print("\n[1/5] Logging in with user token …")

    # Land on a Discord page first so we can write to its localStorage.
    driver.get(LOGIN_URL)
    time.sleep(2)

    # Inject the token and redirect to the app.
    driver.execute_script(
        "window.localStorage.setItem('token', JSON.stringify(arguments[0]));"
        "window.location.replace('https://discord.com/channels/@me');",
        user_token,
    )

    # Wait until the URL leaves the login page (token was accepted).
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda d: "/login" not in d.current_url and "channels" in d.current_url
        )
        print("  ✓ Logged in successfully via token.")
    except TimeoutException:
        raise RuntimeError(
            "Token login failed or took too long. "
            "Verify that your user token is correct and not expired."
        )


def create_application(driver: webdriver.Remote, app_name: str) -> tuple:
    """
    Create a new Discord application.

    Returns a (client_id, app_url) tuple.  The client_id is extracted from
    the developer-portal URL which takes the form:
        https://discord.com/developers/applications/<CLIENT_ID>/information
    """
    print("\n[2/5] Creating new application …")
    driver.get(DEVELOPER_PORTAL_URL)
    time.sleep(2)

    # Click "New Application"
    click(driver, By.XPATH, "//button[contains(normalize-space(.), 'New Application')]")
    time.sleep(1)

    # Fill in the application name
    name_input = wait_for(driver, By.XPATH, "//input[@placeholder or @name]")
    name_input.clear()
    name_input.send_keys(app_name)

    # Agree to ToS checkbox if present
    try:
        tos_checkbox = driver.find_element(
            By.XPATH, "//input[@type='checkbox' and (contains(@id,'tos') or contains(@id,'terms'))]"
        )
        if not tos_checkbox.is_selected():
            driver.execute_script("arguments[0].click();", tos_checkbox)
    except NoSuchElementException:
        pass

    # Click "Create"
    click(driver, By.XPATH, "//button[contains(normalize-space(.), 'Create')]")
    time.sleep(3)

    app_url = driver.current_url
    # Extract client ID from URL, e.g. /applications/123456789/information
    client_id = ""
    url_parts = app_url.rstrip("/").split("/")
    for i, part in enumerate(url_parts):
        if part == "applications" and i + 1 < len(url_parts):
            candidate = url_parts[i + 1]
            if candidate.isdigit():
                client_id = candidate
            break

    # Fallback: read it from the "Application ID" field on the page
    if not client_id:
        try:
            app_id_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH,
                     "//div[contains(normalize-space(.), 'Application ID')]"
                     "/following-sibling::div | "
                     "//input[@aria-label='Application ID' or @id='app-id']")
                )
            )
            client_id = (app_id_el.get_attribute("value") or app_id_el.text).strip()
        except TimeoutException:
            pass

    print(f"  ✓ Application '{app_name}' created.")
    print(f"    Client ID : {client_id or '(could not detect -- check the portal)'}")
    print(f"    URL       : {app_url}")
    return client_id, app_url


def navigate_to_bot_tab(driver: webdriver.Remote) -> None:
    """Click the 'Bot' tab in the left-hand sidebar."""
    print("\n[3/5] Navigating to Bot settings …")
    click(driver, By.XPATH, "//a[normalize-space(.)='Bot'] | //div[normalize-space(.)='Bot']")
    time.sleep(2)


def enable_intents_and_get_token(driver: webdriver.Remote, totp_secret: str = "") -> str:
    """
    On the Bot settings page:
      • Reset (or reveal) the bot token and capture it.
        If the account has 2FA enabled, the TOTP code is generated from
        *totp_secret* and entered automatically.
      • Enable all three Privileged Gateway Intents.
    Returns the bot token string.
    """
    # ---- Token ----
    token = ""

    # Try "Reset Token" button first (most common flow for new bots)
    try:
        reset_btn = wait_clickable(
            driver,
            By.XPATH,
            "//button[contains(normalize-space(.), 'Reset Token')]",
            timeout=10,
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", reset_btn)
        reset_btn.click()
        time.sleep(1)

        # --- Confirmation modal ("Yes, do it!" / "Confirm") ---
        try:
            confirm_btn = wait_clickable(
                driver,
                By.XPATH,
                "//button[contains(normalize-space(.), 'Yes, do it!') or "
                "contains(normalize-space(.), 'Confirm')]",
                timeout=10,
            )
            confirm_btn.click()
            time.sleep(1)
        except TimeoutException:
            pass  # no confirmation modal

        # --- 2FA modal ---
        # Discord shows a 6-digit TOTP input when the account has 2FA enabled.
        try:
            totp_input = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located(
                    (By.XPATH,
                     "//input[@placeholder='6-digit authentication code' or "
                     "@name='code' or @autocomplete='one-time-code' or "
                     "contains(@placeholder,'digit')]")
                )
            )
            if not totp_secret:
                raise RuntimeError(
                    "Discord is asking for a 2FA code but no --totp-secret was provided.\n"
                    "Re-run the script and supply your 2FA secret key when prompted."
                )
            code = pyotp.TOTP(totp_secret).now()
            print(f"  ✓ Generated TOTP code: {code}")
            totp_input.clear()
            totp_input.send_keys(code)
            time.sleep(0.3)

            # Submit the 2FA form
            try:
                submit_btn = wait_clickable(
                    driver,
                    By.XPATH,
                    "//button[@type='submit' or "
                    "contains(normalize-space(.), 'Log In') or "
                    "contains(normalize-space(.), 'Verify')]",
                    timeout=8,
                )
                submit_btn.click()
            except TimeoutException:
                # Some flows auto-submit on 6 digits — just wait
                pass
            time.sleep(2)
        except TimeoutException:
            pass  # no 2FA challenge

        # The token is now displayed
        token_el = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[contains(@class,'token') or @data-text-as-pseudo-element]"
                           " | //span[contains(@class,'token')]")
            )
        )
        token = token_el.text.strip()
    except TimeoutException:
        pass

    # Fallback 1: scan the DOM for a token-shaped string (works in headless mode).
    if not token:
        token = _extract_token_from_dom(driver)
        if token:
            print("  ✓ Token extracted from DOM.")

    # Fallback 2: clipboard (non-headless desktop only -- requires user gesture).
    if not token:
        try:
            copy_btn = wait_clickable(
                driver,
                By.XPATH,
                "//button[contains(normalize-space(.), 'Copy')]",
                timeout=10,
            )
            copy_btn.click()
            time.sleep(0.5)
            # clipboard.readText() returns a Promise; use execute_async_script.
            token = driver.execute_async_script(
                "var done = arguments[0];"
                "navigator.clipboard.readText().then(done).catch(function(){done('')});"
            ) or ""
        except (TimeoutException, NoSuchElementException):
            pass

    if token:
        print(f"  ✓ Token captured (length={len(token)}).")
    else:
        print(
            "  ⚠ Could not automatically capture the token.\n"
            "    Please copy it manually from the page and add it to tokens.txt."
        )

    # ---- Intents ----
    print("\n[4/5] Enabling Privileged Gateway Intents …")
    for intent_label in ("PRESENCE INTENT", "SERVER MEMBERS INTENT", "MESSAGE CONTENT INTENT"):
        safe_toggle_intent(driver, intent_label)

    # Save changes
    try:
        save_btn = wait_clickable(
            driver,
            By.XPATH,
            "//button[contains(normalize-space(.), 'Save Changes')]",
            timeout=10,
        )
        save_btn.click()
        time.sleep(2)
        print("  ✓ Changes saved.")
    except TimeoutException:
        print("  ⚠ 'Save Changes' button not found -- changes may have been auto-saved.")

    return token


def add_bot_to_server(
    driver: webdriver.Remote, client_id: str, guild_id: str, permissions: str = "2048"
) -> None:
    """
    Add the bot to the Discord server identified by *guild_id*.

    Builds the standard OAuth2 bot-invite URL with:
      • scope       = bot + applications.commands
      • permissions = value supplied by the caller (default: 2048 = Send Messages)
      • guild_id    pre-selected so the user isn't asked to pick a server
      • disable_guild_select=true  to lock the dropdown to that guild

    The browser session is already logged in, so Discord will show the
    authorisation screen and we click "Authorise".
    """
    print(f"\n[5/5] Adding bot to server (guild ID: {guild_id}) …")

    if not client_id:
        print(
            "  ⚠ Client ID is missing -- cannot build invite URL automatically.\n"
            "    Go to the OAuth2 → URL Generator tab in the Developer Portal,\n"
            "    select 'bot' scope, copy the URL, and open it manually."
        )
        return

    oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions={permissions}"
        f"&guild_id={guild_id}"
        f"&scope=bot+applications.commands"
        f"&disable_guild_select=true"
    )
    print(f"  OAuth2 URL: {oauth_url}")
    driver.get(oauth_url)
    time.sleep(3)

    # Discord may show a "Select a server" page even with disable_guild_select.
    # If so, click the correct server entry first.
    try:
        server_option = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, f"//div[@data-guild-id='{guild_id}']"
                           f" | //option[@value='{guild_id}']")
            )
        )
        driver.execute_script("arguments[0].click();", server_option)
        time.sleep(1)
    except TimeoutException:
        pass  # guild was already pre-selected or the dropdown is not shown

    # Click "Continue" if a permissions-review step is shown
    try:
        continue_btn = wait_clickable(
            driver,
            By.XPATH,
            "//button[contains(normalize-space(.), 'Continue')]",
            timeout=10,
        )
        continue_btn.click()
        time.sleep(2)
    except TimeoutException:
        pass  # no "Continue" step

    # Click the "Authorise" / "Authorize" button
    try:
        auth_btn = wait_clickable(
            driver,
            By.XPATH,
            "//button[contains(normalize-space(.), 'Authorise') or "
            "contains(normalize-space(.), 'Authorize')]",
            timeout=15,
        )
        auth_btn.click()
        time.sleep(3)
        print("  ✓ Bot successfully added to the server!")
    except TimeoutException:
        print(
            "  ⚠ 'Authorise' button not found.\n"
            "    You may need to complete the authorisation manually in the browser.\n"
            f"    Use this URL: {oauth_url}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  Discord Bot Creator  --  EDUCATIONAL PURPOSES ONLY")
    print("=" * 60)

    on_termux = is_termux()
    if on_termux:
        print("\n  ✦ Termux environment detected.")
        print("    • Headless mode will be used (no display server).")
        print("    • Chromium (pkg install chromium) will be used as the browser.")
        print("    • Run termux_setup.sh first if you haven't already.\n")

    print(
        "\n⚠  WARNING\n"
        "   Automating a Discord *user* account may violate Discord's\n"
        "   Terms of Service and can result in account suspension.\n"
        "   This script is provided for educational purposes only.\n"
        "   Use it responsibly and only with accounts you own.\n"
        "\n⚠  SECURITY NOTE\n"
        "   The bot token will be stored in plain text in tokens.txt.\n"
        "   Keep that file private and never commit it to version control.\n"
    )
    confirm = input("Type 'yes' to acknowledge and continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    user_token = getpass.getpass(
        "\nDiscord user token (input hidden): "
    ).strip()
    totp_secret = getpass.getpass(
        "2FA secret key (base-32, leave blank if 2FA is not enabled): "
    ).strip()
    app_name = input("Application / bot name: ").strip() or "MyDiscordBot"
    guild_id = input("Server (Guild) ID to add the bot to: ").strip()
    permissions = input(
        "Bot permissions integer [default 2048 = Send Messages, "
        "press ENTER to keep default]: "
    ).strip() or "2048"

    # --- Browser selection ---
    default_browser = detect_browser()
    browser_input = input(
        f"Browser to use [chrome/firefox, default: {default_browser}]: "
    ).strip().lower()
    browser = browser_input if browser_input in ("chrome", "firefox") else default_browser

    # --- Headless selection (forced on Termux) ---
    if on_termux:
        headless = True
        print("  (Headless mode forced on Termux.)")
    else:
        headless_input = input("Run headless? [y/N]: ").strip().lower()
        headless = headless_input in ("y", "yes")

    driver = build_driver(browser=browser, headless=headless)
    try:
        login_with_token(driver, user_token)
        client_id, _ = create_application(driver, app_name)
        navigate_to_bot_tab(driver)
        token = enable_intents_and_get_token(driver, totp_secret)

        if token:
            save_token(token)
        else:
            print(
                "\n  ⚠ Token was not captured automatically.\n"
                "    Copy the token from the browser and add it to tokens.txt manually."
            )

        if guild_id:
            add_bot_to_server(driver, client_id, guild_id, permissions)
        else:
            print("\n  -- No server ID provided; skipping bot invite step.")

        print("\n✓ All done!")
        # In headless / Termux mode there is no browser window to keep open.
        if not headless:
            input("Press ENTER to close the browser …")
    except RuntimeError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
