"""
Discord Bot Creator – EDUCATIONAL PURPOSES ONLY
================================================
Automates the Discord Developer Portal to:
  1. Log in with your Discord account credentials.
  2. Create a new application (bot).
  3. Enable all three Privileged Gateway Intents:
       • Presence Intent
       • Server Members Intent
       • Message Content Intent
  4. Reset / reveal the bot token and save it to tokens.txt.
  5. Add the bot to a server (guild) whose ID the user provides.

Usage:
    python create_discord_bot.py

Dependencies (install with pip install -r requirements.txt):
    selenium>=4.0.0
    webdriver-manager>=4.0.0
"""

import os
import sys
import time
import getpass

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEVELOPER_PORTAL_URL = "https://discord.com/developers/applications"
LOGIN_URL = "https://discord.com/login"
WAIT_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_driver(headless: bool = False) -> webdriver.Chrome:
    """Return a configured Chrome WebDriver instance."""
    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def wait_for(driver: webdriver.Chrome, by: str, locator: str, timeout: int = WAIT_TIMEOUT):
    """Wait until an element is present and return it."""
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, locator))
    )


def wait_clickable(driver: webdriver.Chrome, by: str, locator: str, timeout: int = WAIT_TIMEOUT):
    """Wait until an element is clickable and return it."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, locator))
    )


def click(driver: webdriver.Chrome, by: str, locator: str, timeout: int = WAIT_TIMEOUT):
    """Wait for an element to be clickable, then click it."""
    element = wait_clickable(driver, by, locator, timeout)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    time.sleep(0.3)
    element.click()
    return element


def safe_toggle_intent(driver: webdriver.Chrome, label_text: str) -> None:
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
            print(f"  – Intent already enabled: {label_text}")
        else:
            print(f"  ⚠ Could not locate toggle for intent: {label_text}")
    except (TimeoutException, NoSuchElementException, Exception) as exc:  # noqa: BLE001
        print(f"  ⚠ Error toggling intent '{label_text}': {exc}")


def save_token(token: str, path: str = "tokens.txt") -> None:
    """Append *token* to *path* (one token per line)."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    print(f"\n✓ Token saved to '{path}'.")


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def login(driver: webdriver.Chrome, email: str, password: str) -> None:
    """Navigate to Discord login page and authenticate."""
    print("\n[1/5] Logging in to Discord …")
    driver.get(LOGIN_URL)
    time.sleep(2)

    email_field = wait_for(driver, By.NAME, "email")
    email_field.clear()
    email_field.send_keys(email)

    password_field = driver.find_element(By.NAME, "password")
    password_field.clear()
    password_field.send_keys(password)

    click(driver, By.XPATH, "//button[@type='submit']")

    # Wait for the main Discord app to load (URL changes away from /login)
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda d: "/login" not in d.current_url
        )
        print("  ✓ Logged in successfully.")
    except TimeoutException:
        raise RuntimeError(
            "Login failed or took too long. "
            "Check your credentials, or you may need to pass a CAPTCHA manually."
        )


def create_application(driver: webdriver.Chrome, app_name: str) -> tuple:
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
    print(f"    Client ID : {client_id or '(could not detect – check the portal)'}")
    print(f"    URL       : {app_url}")
    return client_id, app_url


def navigate_to_bot_tab(driver: webdriver.Chrome) -> None:
    """Click the 'Bot' tab in the left-hand sidebar."""
    print("\n[3/5] Navigating to Bot settings …")
    click(driver, By.XPATH, "//a[normalize-space(.)='Bot'] | //div[normalize-space(.)='Bot']")
    time.sleep(2)


def enable_intents_and_get_token(driver: webdriver.Chrome) -> str:
    """
    On the Bot settings page:
      • Reset (or reveal) the bot token and capture it.
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

        # Confirm the reset in the modal that appears
        try:
            confirm_btn = wait_clickable(
                driver,
                By.XPATH,
                "//button[contains(normalize-space(.), 'Yes, do it!') or "
                "contains(normalize-space(.), 'Confirm')]",
                timeout=10,
            )
            confirm_btn.click()
            time.sleep(2)
        except TimeoutException:
            pass  # no confirmation modal

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

    # Fallback: look for a "Copy" button then read the token asynchronously
    # from the clipboard (works in non-headless mode only).
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
            # clipboard.readText() returns a Promise; use execute_async_script
            # to wait for the resolved value.
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
        print("  ⚠ 'Save Changes' button not found – changes may have been auto-saved.")

    return token


def add_bot_to_server(driver: webdriver.Chrome, client_id: str, guild_id: str) -> None:
    """
    Add the bot to the Discord server identified by *guild_id*.

    Builds the standard OAuth2 bot-invite URL with:
      • scope  = bot + applications.commands
      • permissions = 8  (Administrator – adjust as needed)
      • guild_id pre-selected so the user isn't asked to pick a server
      • disable_guild_select=true  to lock the dropdown to that guild

    The browser session is already logged in, so Discord will show the
    authorisation screen and we click "Authorise".
    """
    print(f"\n[5/5] Adding bot to server (guild ID: {guild_id}) …")

    if not client_id:
        print(
            "  ⚠ Client ID is missing – cannot build invite URL automatically.\n"
            "    Go to the OAuth2 → URL Generator tab in the Developer Portal,\n"
            "    select 'bot' scope, copy the URL, and open it manually."
        )
        return

    oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions=8"
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
    print("  Discord Bot Creator  –  EDUCATIONAL PURPOSES ONLY")
    print("=" * 60)
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

    email = input("\nDiscord email: ").strip()
    password = getpass.getpass("Discord password: ")
    app_name = input("Application / bot name: ").strip() or "MyDiscordBot"
    guild_id = input("Server (Guild) ID to add the bot to: ").strip()
    permissions = input(
        "Bot permissions integer [default 2048 = Send Messages, "
        "press ENTER to keep default]: "
    ).strip() or "2048"
    headless_input = input("Run headless? [y/N]: ").strip().lower()
    headless = headless_input in ("y", "yes")

    driver = build_driver(headless=headless)
    try:
        login(driver, email, password)
        client_id, _ = create_application(driver, app_name)
        navigate_to_bot_tab(driver)
        token = enable_intents_and_get_token(driver)

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
            print("\n  – No server ID provided; skipping bot invite step.")

        input("\nPress ENTER to close the browser …")
    except RuntimeError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
