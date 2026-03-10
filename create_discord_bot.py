"""
Discord Bot Creator -- EDUCATIONAL PURPOSES ONLY
=================================================
Supports two automation methods -- user picks at runtime:

  Method 1 -- API  (no browser needed; works everywhere including Termux):
    Uses the Discord REST API directly via `requests`.
    No browser, no driver required.
    Just: pip install requests pyotp groq

    hCaptcha handling (Method 1):
      Discord sometimes demands an hCaptcha solution when creating an
      application.  Three solver options are available:

        "groq"     -- Built-in solver powered by the official Groq Python SDK
                      and the Llama 4 Maverick vision model.  FREE tier
                      available; no third-party captcha service needed.
                      Fetches the hCaptcha challenge images, classifies each
                      one with Groq's ultra-fast LLM inference, and submits
                      the answers -- all inside this script.
                      Requires: pip install groq>=1.1.0
                      Get a free API key: https://console.groq.com
                      Default model : meta-llama/llama-4-maverick-17b-128e-instruct
                      Fast  model   : meta-llama/llama-4-scout-17b-16e-instruct
                      Supports hCaptcha challenges with hsl-type proof-of-work.

        "2captcha" -- Delegate to 2captcha.com (paid service).
        "capsolver" -- Delegate to capsolver.com (paid service).

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
    groq>=1.1.0,<2.0.0               # Method 1 Groq hCaptcha solver (optional)
    selenium>=4.0.0,<5.0.0           # Method 2 (Browser)
    webdriver-manager>=4.0.0,<5.0.0  # Method 2 (Browser)
"""

import os
import sys
import time
import shutil
import base64
import hashlib
import json
import re
from urllib.parse import urlparse

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
# Optional Groq SDK import (Method 1 -- own hCaptcha solver).
# Install with: pip install groq>=1.1.0
# ---------------------------------------------------------------------------
try:
    from groq import Groq as GroqClient                          # type: ignore[import]
    from groq import (                                           # type: ignore[import]
        AuthenticationError  as GroqAuthError,
        RateLimitError       as GroqRateLimitError,
        BadRequestError      as GroqBadRequestError,
        APIStatusError       as GroqAPIStatusError,
        APIConnectionError   as GroqConnectionError,
        APITimeoutError      as GroqTimeoutError,
    )
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

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

# ---------------------------------------------------------------------------
# Own hCaptcha solver -- Groq-powered
# ---------------------------------------------------------------------------
# hCaptcha public API endpoints
_HCAPTCHA_API_JS_URL       = "https://hcaptcha.com/1/api.js"
_HCAPTCHA_SITE_CONFIG_URL  = "https://hcaptcha.com/checksiteconfig"
_HCAPTCHA_GET_CHALLENGE_URL = "https://hcaptcha.com/getcaptcha"
_HCAPTCHA_CHECK_URL        = "https://hcaptcha.com/checkcaptcha"

# Llama 4 Maverick: 17 B params / 128 experts -- best Groq vision model
GROQ_DEFAULT_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
# Llama 4 Scout: 17 B params / 16 experts -- faster / lower quota usage
GROQ_FAST_MODEL    = "meta-llama/llama-4-scout-17b-16e-instruct"
# Max retries when Groq rate-limits an image classification call
_GROQ_RATE_LIMIT_RETRIES = 3
# Maximum nonce iterations for hsl proof-of-work before giving up
_HCAPTCHA_POW_MAX_ITERATIONS = 10_000_000

# Shared browser-like request headers for hCaptcha API calls
_HCAPTCHA_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://discord.com",
    "Referer": "https://discord.com/",
}

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


# ===========================================================================
# OWN hCaptcha SOLVER -- Groq Vision AI
# ===========================================================================

def _hcaptcha_get_version() -> str:
    """
    Fetch the current hCaptcha JS bundle version string from the api.js URL.
    Falls back to a known-good version string if the request fails.
    """
    try:
        resp = requests.get(
            _HCAPTCHA_API_JS_URL,
            headers={"User-Agent": _HCAPTCHA_HEADERS["User-Agent"]},
            timeout=10,
            allow_redirects=True,
        )
        # Version appears as a path segment: /VERSION/api.js
        m = re.search(r'/([a-f0-9]{6,})/api\.js', resp.url)
        if m:
            return m.group(1)
        m = re.search(r'v=([a-f0-9]{6,})', resp.url)
        if m:
            return m.group(1)
        # Also try to parse from the JS source
        m = re.search(r'"v"\s*:\s*"([a-f0-9]{6,})"', resp.text)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "4e53e8c"  # safe fallback version


def _hcaptcha_solve_hsl_pow(req: str) -> str:
    """
    Solve an hCaptcha ``hsl``-type proof-of-work challenge in pure Python.

    The ``req`` field (from checksiteconfig ``c.req``) is a base64-encoded
    JSON with at least a ``d`` (difficulty = number of leading zero hex digits
    required) and an ``s`` (seed string) field.

    Returns the base64-encoded hex-digest of the winning SHA-256 hash.
    """
    padded = req + "=" * ((4 - len(req) % 4) % 4)
    try:
        raw = base64.b64decode(padded).decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(
            f"hCaptcha PoW (hsl): could not decode challenge -- {exc}"
        ) from exc

    difficulty: int = data.get("d", 5)
    seed: str = data.get("s", req)
    prefix = "0" * difficulty

    for nonce in range(_HCAPTCHA_POW_MAX_ITERATIONS):
        digest = hashlib.sha256(f"{seed}{nonce}".encode()).hexdigest()
        if digest.startswith(prefix):
            return base64.b64encode(digest.encode()).decode()

    raise RuntimeError(
        f"hCaptcha PoW (hsl): could not find a solution within "
        f"{_HCAPTCHA_POW_MAX_ITERATIONS:,} iterations."
    )


def _groq_classify_image(
    image_url: str,
    question: str,
    groq_client: "GroqClient",
    model: str,
    img_session: requests.Session,
) -> bool:
    """
    Ask Groq's vision model whether the image at *image_url* matches *question*.

    Downloads the image, encodes it as a base64 data-URL, and calls
    ``groq_client.chat.completions.create()`` with the official Groq Python SDK.
    Returns ``True`` when the model answers "yes", ``False`` otherwise.

    Automatically retries on ``GroqRateLimitError`` with exponential back-off
    (up to ``_GROQ_RATE_LIMIT_RETRIES`` attempts).
    """
    # --- download & encode image -----------------------------------------
    try:
        img_resp = img_session.get(image_url, timeout=15)
        img_resp.raise_for_status()
        b64_data = base64.b64encode(img_resp.content).decode()
        mime = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        data_url = f"data:{mime};base64,{b64_data}"
    except Exception as exc:  # noqa: BLE001
        print(f"      Warning: could not download image {image_url}: {exc}")
        return False

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"{question}\n\n"
                        "Reply with ONLY the single word 'yes' if the image "
                        "contains it, or 'no' if it does not. "
                        "Do not include any other text."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                },
            ],
        }
    ]

    # --- call Groq SDK with retry on rate-limit --------------------------
    for attempt in range(_GROQ_RATE_LIMIT_RETRIES):
        try:
            completion = groq_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=5,
                temperature=0.0,
            )
            answer = (
                completion.choices[0].message.content or ""
            ).strip().lower()
            return answer.startswith("y")

        except GroqRateLimitError:
            wait = 2 ** (attempt + 1)          # 2 s, 4 s, 8 s
            if attempt < _GROQ_RATE_LIMIT_RETRIES - 1:
                print(f"      Groq rate-limited; retrying in {wait}s ...")
                time.sleep(wait)
            else:
                raise

        except GroqAuthError as exc:
            raise RuntimeError(
                f"Groq authentication failed -- check your API key: {exc}"
            ) from exc

        except (GroqConnectionError, GroqTimeoutError) as exc:
            raise RuntimeError(
                f"Groq network error while classifying image: {exc}"
            ) from exc

        except GroqBadRequestError as exc:
            raise RuntimeError(
                f"Groq rejected the image classification request: {exc}"
            ) from exc

        except GroqAPIStatusError as exc:
            raise RuntimeError(
                f"Groq API error (HTTP {exc.status_code}): {exc.message}"
            ) from exc

    # Should not be reached; satisfies type checkers.
    return False


def _solve_hcaptcha_groq(
    sitekey: str,
    pageurl: str,
    rqdata: str,
    groq_api_key: str,
    groq_model: str = GROQ_DEFAULT_MODEL,
) -> str:
    """
    Solve an hCaptcha challenge using the official Groq Python SDK.

    The Groq ``GroqClient`` is instantiated once and reused for every image
    classification call, which is the recommended SDK usage pattern.

    Pipeline
    --------
    1. Fetch hCaptcha site config to obtain the proof-of-work descriptor and
       the current JS bundle version.
    2. Solve the PoW in Python (``hsl`` type; SHA-256 leading-zero search).
    3. POST to ``/getcaptcha`` to retrieve the image classification challenge.
    4. For each task image, ask Groq's vision model (Llama 4 Maverick by default)
       whether it matches the challenge label (yes / no per image).
    5. POST the labelled answers to ``/checkcaptcha``.
    6. Return the ``generated_pass_UUID`` token for use with Discord.

    Raises ``RuntimeError`` when:
    - The ``groq`` package is not installed.
    - The Groq API key is invalid.
    - The hCaptcha PoW type is ``hsw`` / ``enterprise`` (requires JS runtime).
    - The challenge type is not ``image_label_binary``.
    - hCaptcha rejects the submitted answers.
    """
    if not GROQ_AVAILABLE:
        raise RuntimeError(
            "The 'groq' package is required for the Groq hCaptcha solver.\n"
            "Install it with:  pip install groq>=1.1.0\n"
            "Then re-run the script."
        )

    # Create the Groq client once; it is thread-safe and reusable.
    groq_client = GroqClient(api_key=groq_api_key)

    host = urlparse(pageurl).hostname or "discord.com"
    img_session = requests.Session()
    img_session.headers.update({"User-Agent": _HCAPTCHA_HEADERS["User-Agent"]})

    # -- 1. Site config -------------------------------------------------------
    print(f"    [hCaptcha/Groq] Fetching site config (model: {groq_model}) ...")
    version = _hcaptcha_get_version()
    cfg_resp = requests.get(
        _HCAPTCHA_SITE_CONFIG_URL,
        params={"v": version, "host": host, "sitekey": sitekey, "sc": "1", "swa": "1"},
        headers=_HCAPTCHA_HEADERS,
        timeout=15,
    )
    cfg_resp.raise_for_status()
    config = cfg_resp.json()
    c_obj: dict = config.get("c") or {}

    # -- 2. Proof-of-work -----------------------------------------------------
    pow_type = (c_obj.get("type") or "").lower()
    print(f"    [hCaptcha/Groq] PoW type: {pow_type or '(none)'}")
    pow_solution = ""
    if pow_type == "hsl":
        pow_solution = _hcaptcha_solve_hsl_pow(c_obj.get("req", ""))
        print("    [hCaptcha/Groq] PoW solved.")
    elif pow_type in ("hsw", "enterprise"):
        raise RuntimeError(
            "hCaptcha PoW type 'hsw'/'enterprise' requires a JavaScript runtime and "
            "cannot be solved by the built-in Python solver. "
            "Use a third-party solver (2captcha / capsolver) for this challenge."
        )
    # No PoW when type is absent -- proceed without it.

    # -- 3. Get challenge -----------------------------------------------------
    print("    [hCaptcha/Groq] Fetching challenge ...")
    ts_ms = int(time.time() * 1000)
    getcap_form: dict = {
        "v": version,
        "host": host,
        "sitekey": sitekey,
        "sc": "1",
        "swa": "1",
        "motionData": json.dumps({"st": ts_ms, "dct": ts_ms}),
        "pdc": json.dumps({"s": ts_ms, "n": 0, "p": 0}),
        "n": pow_solution,
        "c": json.dumps(c_obj),
    }
    if rqdata:
        getcap_form["rqdata"] = rqdata

    cap_resp = requests.post(
        f"{_HCAPTCHA_GET_CHALLENGE_URL}/{sitekey}",
        data=getcap_form,
        headers=_HCAPTCHA_HEADERS,
        timeout=20,
    )
    cap_resp.raise_for_status()
    challenge: dict = cap_resp.json()

    # Some easy challenges pass immediately without image tasks.
    if challenge.get("generated_pass_UUID"):
        print("    [hCaptcha/Groq] Challenge passed automatically (no images).")
        return challenge["generated_pass_UUID"]

    req_type: str = challenge.get("request_type", "")
    if req_type != "image_label_binary":
        raise RuntimeError(
            f"hCaptcha: unsupported challenge type '{req_type}'. "
            "Only 'image_label_binary' is supported by the built-in Groq solver."
        )

    # -- 4. Classify images with Groq SDK ------------------------------------
    tasklist: list = challenge.get("tasklist", [])
    question_dict: dict = challenge.get("requester_question", {})
    question: str = (
        question_dict.get("en")
        or next(iter(question_dict.values()), "Does this image match?")
    )
    challenge_key: str = challenge.get("key", "")
    c_next: dict = challenge.get("c") or c_obj

    print(
        f'    [hCaptcha/Groq] Classifying {len(tasklist)} image(s): "{question}" ...'
    )
    answers: dict = {}
    for task in tasklist:
        task_key: str = task.get("task_key") or task.get("datapoint_hash", "")
        image_url: str = task.get("datapoint_uri") or task.get("datapoint_url", "")
        if not task_key or not image_url:
            continue
        matched = _groq_classify_image(
            image_url, question, groq_client, groq_model, img_session
        )
        answers[task_key] = "true" if matched else "false"
        print(f"      Task {task_key[:10]}... -> {'yes' if matched else 'no'}")

    # -- 5. Submit answers ----------------------------------------------------
    print("    [hCaptcha/Groq] Submitting answers ...")

    # A second PoW descriptor may arrive inside the challenge body.
    pow_type2 = (c_next.get("type") or "").lower() if isinstance(c_next, dict) else ""
    second_pow = ""
    if pow_type2 == "hsl":
        second_pow = _hcaptcha_solve_hsl_pow(c_next.get("req", ""))

    ts_ms2 = int(time.time() * 1000)
    submit_payload = {
        "v": version,
        "job_mode": req_type,
        "answers": answers,
        "serverdomain": host,
        "sitekey": sitekey,
        "n": second_pow or pow_solution,
        "c": (
            json.dumps(c_next)
            if isinstance(c_next, dict)
            else (c_next or json.dumps(c_obj))
        ),
        "motionData": json.dumps({
            "st": ts_ms2,
            "dct": ts_ms2,
            "mm": [[100, 200, ts_ms2]],
        }),
    }

    submit_resp = requests.post(
        f"{_HCAPTCHA_CHECK_URL}/{sitekey}/{challenge_key}",
        json=submit_payload,
        headers={**_HCAPTCHA_HEADERS, "Content-Type": "application/json"},
        timeout=20,
    )
    submit_resp.raise_for_status()
    result: dict = submit_resp.json()

    token: str = result.get("generated_pass_UUID", "")
    if not token:
        raise RuntimeError(
            f"hCaptcha: challenge not accepted -- response: {result}"
        )

    print("    [hCaptcha/Groq] Challenge solved successfully.")
    return token


def _solve_hcaptcha(
    sitekey: str,
    pageurl: str,
    rqdata: str,
    solver_key: str,
    solver_service: str = "2captcha",
    groq_model: str = GROQ_DEFAULT_MODEL,
) -> str:
    """
    Solve an hCaptcha challenge and return the response token.

    *solver_service* routing
    ------------------------
    "groq"               -- Built-in Groq-SDK-powered solver using
                            Llama 4 Maverick vision (recommended, free tier).
                            *solver_key* must be a Groq API key.
                            Install: pip install groq>=1.1.0
    "2captcha" / "2cap"  -- Delegate to https://2captcha.com (paid).
    "capsolver" / "cap"  -- Delegate to https://capsolver.com (paid).

    Returns the token string to embed in ``captcha_key`` when retrying
    the Discord application creation request.
    """
    svc = solver_service.lower()

    if svc == "groq":
        return _solve_hcaptcha_groq(
            sitekey=sitekey,
            pageurl=pageurl,
            rqdata=rqdata,
            groq_api_key=solver_key,
            groq_model=groq_model,
        )

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
        "Use 'groq', '2captcha', or 'capsolver'."
    )


def api_create_application(
    sess: requests.Session,
    name: str,
    solver_key: str = "",
    solver_service: str = "2captcha",
    groq_model: str = GROQ_DEFAULT_MODEL,
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
                        "  and supply a captcha solver API key (groq, 2captcha, or\n"
                        "  capsolver) when prompted, or use Method 2 (Browser) instead."
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
                    groq_model=groq_model,
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
    groq_model: str = GROQ_DEFAULT_MODEL,
) -> str:
    """Run the full API creation flow for one bot. Returns the token."""
    app    = api_create_application(sess, bot_name, solver_key, solver_service, groq_model)
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
    user_token = input(
        "\nDiscord user token: "
    ).strip()
    totp_secret = input(
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
    solver_service = "groq"     # default to the built-in free solver
    groq_model = GROQ_DEFAULT_MODEL
    if method == "api":
        groq_note = (
            " [RECOMMENDED, free]" if GROQ_AVAILABLE
            else " [install: pip install groq>=1.1.0]"
        )
        print(
            "\nCaptcha solver (optional):\n"
            "  Discord may require an hCaptcha solution when creating applications.\n"
            f"  groq{groq_note}\n"
            "      Built-in Llama 4 vision solver via Groq API.\n"
            "      Free API key: https://console.groq.com\n"
            "  2captcha  -- paid service: https://2captcha.com\n"
            "  capsolver -- paid service: https://capsolver.com\n"
            "  Leave blank to skip (an error will appear if a captcha is triggered).\n"
        )
        svc_raw = input(
            "Captcha solver service [groq/2captcha/capsolver, leave blank to skip]: "
        ).strip().lower()
        if svc_raw in ("groq",):
            solver_service = "groq"
            solver_key = input(
                "Groq API key (get one free at console.groq.com): "
            ).strip()
            model_raw = input(
                f"Groq model [ENTER for default '{GROQ_DEFAULT_MODEL}',\n"
                f"  or type '{GROQ_FAST_MODEL}' for faster/cheaper]: "
            ).strip()
            if model_raw:
                groq_model = model_raw
        elif svc_raw in ("2captcha", "2cap", "capsolver", "cap"):
            solver_service = svc_raw
            solver_key = input(
                "Captcha solver API key: "
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
                        solver_key, solver_service, groq_model,
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
