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
                      Supports challenge types: image_label_binary,
                      image_label_area_select, image_label_multiple_choice,
                      image_label_text, and unknown types with image tasks
                      (best-effort batch classification).
                      All types use multi-image batch Groq calls for speed
                      and accuracy; reference example images are included
                      automatically when hCaptcha provides them.
                      Supports hsl- and hsw-type proof-of-work in pure
                      Python (no Node.js required; Node.js used as JIT
                      accelerator for hsw when available).
                      Auto-retries up to 2 times on answer rejection.

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
import random
import re
import subprocess
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
# Maximum nonce iterations for hsl/hsw proof-of-work before giving up
_HCAPTCHA_POW_MAX_ITERATIONS = 10_000_000
# Extra re-solve attempts after hCaptcha rejects submitted answers
_HCAPTCHA_SOLVE_RETRIES = 2
# Maximum images sent to Groq in a single multi-image batch call
_HCAPTCHA_BATCH_MAX = 9

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


# Inline Node.js script for solving hsw proof-of-work.
# Uses only the built-in ``crypto`` module -- no npm packages required.
# The algorithm mirrors the hsl solver: find the smallest nonce such that
# SHA-256(seed + nonce) starts with ``difficulty`` zero hex digits, then
# return the base64-encoded hex digest.
# The max iteration limit is passed as process.argv[2] from Python so that
# _HCAPTCHA_POW_MAX_ITERATIONS is the single source of truth.
_HCAPTCHA_HSW_NODE_SCRIPT = r"""
const crypto = require('crypto');
const req = process.argv[1] || '';
const maxIter = parseInt(process.argv[2] || '10000000', 10);
let data;
try {
  const padded = req + '='.repeat((4 - (req.length % 4)) % 4);
  data = JSON.parse(Buffer.from(padded, 'base64').toString('utf8'));
} catch (e) {
  data = { d: 5, s: req };
}
const difficulty = data.d || 5;
const seed = data.s || req;
const prefix = '0'.repeat(difficulty);
for (let nonce = 0; nonce < maxIter; nonce++) {
  const digest = crypto.createHash('sha256').update(seed + String(nonce)).digest('hex');
  if (digest.startsWith(prefix)) {
    process.stdout.write(Buffer.from(digest).toString('base64'));
    process.exit(0);
  }
}
process.stderr.write(`hsw: no solution found within ${maxIter.toLocaleString()} iterations\n`);
process.exit(1);
""".strip()


def _hcaptcha_solve_hsw_pow(req: str) -> str:
    """
    Solve an hCaptcha ``hsw``-type proof-of-work challenge.

    ``hsw`` (Hash SHA-256 Web) uses the same SHA-256 hashcash algorithm as
    ``hsl`` but is intended to run inside a JavaScript worker context.  This
    function first tries to delegate to an inline Node.js script (faster via
    V8 JIT); when Node.js is not available it falls back to the pure-Python
    implementation (same algorithm, no extra dependencies).

    The ``req`` field is decoded from base64 JSON (same format as ``hsl``):
    ``d`` = difficulty (leading zero hex digits), ``s`` = seed string.

    Returns the base64-encoded hex-digest of the winning SHA-256 hash,
    identical in format to the ``hsl`` solver output.
    """
    node_exe = shutil.which("node") or shutil.which("nodejs")
    if node_exe:
        try:
            result = subprocess.run(
                [node_exe, "-e", _HCAPTCHA_HSW_NODE_SCRIPT, req,
                 str(_HCAPTCHA_POW_MAX_ITERATIONS)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "hCaptcha PoW (hsw): Node.js solver timed out after 120 s."
            ) from exc
        if result.returncode == 0:
            solution = result.stdout.strip()
            if solution:
                return solution
        # Node.js failed -- fall through to the Python implementation.
        print(
            "    [hCaptcha/Groq] Node.js hsw solver failed; "
            "falling back to pure-Python ..."
        )

    # Pure-Python fallback: hsw uses the identical SHA-256 hashcash algorithm.
    print("    [hCaptcha/Groq] Solving hsw PoW in pure Python ...")
    return _hcaptcha_solve_hsl_pow(req)


def _generate_motion_data(start_ts: int) -> dict:
    """
    Generate plausible mouse-movement motion data for hCaptcha requests.

    Simulates a human cursor travelling from a random entry point in the upper
    portion of the page into the captcha image-grid area, with smooth
    ease-in/out acceleration, small Gaussian jitter, and a synthetic
    mouse-down/mouse-up click at the destination.

    ``start_ts`` is the Unix timestamp in milliseconds used as the base for
    all event timestamps.  Returns the ``motionData`` dict expected by both
    the ``/getcaptcha`` and ``/checkcaptcha`` endpoints.
    """
    rng = random.SystemRandom()   # OS entropy: different unpredictable path each call

    t = start_ts

    # Origin: somewhere in the upper portion of the page
    ox = rng.randint(60, 250)
    oy = rng.randint(80, 220)

    # Destination: inside the captcha image-grid area
    dx = rng.randint(260, 560)
    dy = rng.randint(260, 520)

    # Control point for a quadratic Bezier curve (adds subtle arc)
    cx_ctrl = rng.randint(min(ox, dx), max(ox, dx))
    cy_ctrl = rng.randint(min(oy, dy) - 60, min(oy, dy))

    mm = []   # mouse-move events: [x, y, timestamp]
    steps = rng.randint(20, 38)
    for i in range(steps):
        # Smoothstep easing parameter
        p = i / steps
        ease = p * p * (3.0 - 2.0 * p)

        # Quadratic Bezier interpolation
        bx = (1 - ease) * (1 - ease) * ox + 2 * (1 - ease) * ease * cx_ctrl + ease * ease * dx
        by = (1 - ease) * (1 - ease) * oy + 2 * (1 - ease) * ease * cy_ctrl + ease * ease * dy

        # Small Gaussian jitter to mimic hand tremor
        px = int(bx + rng.gauss(0, 3.5))
        py = int(by + rng.gauss(0, 3.5))

        t += rng.randint(6, 28)
        mm.append([px, py, t])

    # Arrive and dwell briefly at the destination
    t += rng.randint(35, 110)
    mm.append([dx, dy, t])

    click_down_t   = t + rng.randint(20, 70)
    click_up_t     = click_down_t + rng.randint(55, 160)

    return {
        "st":  start_ts,
        "dct": start_ts + rng.randint(0, 4),
        "mm":  mm,
        "md":  [[dx, dy, click_down_t]],    # mouse-down
        "mu":  [[dx, dy, click_up_t]],      # mouse-up
    }


def _groq_call_content(
    content: list,
    groq_client: "GroqClient",
    model: str,
    max_tokens: int = 5,
) -> str:
    """
    Send a pre-built content list (text + image_url items) to Groq and return
    the raw text response (stripped, lower-cased).

    This is the central call path shared by all Groq vision helpers.
    Retries automatically on ``GroqRateLimitError`` with exponential back-off
    (up to ``_GROQ_RATE_LIMIT_RETRIES`` attempts).  All other Groq errors are
    re-raised as ``RuntimeError``.
    """
    messages = [{"role": "user", "content": content}]
    for attempt in range(_GROQ_RATE_LIMIT_RETRIES):
        try:
            completion = groq_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            return (completion.choices[0].message.content or "").strip().lower()

        except GroqRateLimitError:
            wait = 2 ** (attempt + 1)
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
                f"Groq network error: {exc}"
            ) from exc

        except GroqBadRequestError as exc:
            raise RuntimeError(
                f"Groq rejected the vision request: {exc}"
            ) from exc

        except GroqAPIStatusError as exc:
            raise RuntimeError(
                f"Groq API error (HTTP {exc.status_code}): {exc.message}"
            ) from exc

    # Should not be reached.
    return ""


def _groq_call_vision(
    data_url: str,
    prompt: str,
    groq_client: "GroqClient",
    model: str,
    max_tokens: int = 5,
) -> str:
    """
    Single-image wrapper around ``_groq_call_content``.

    Builds the standard ``[text, image_url]`` content list from *data_url* +
    *prompt* and delegates to ``_groq_call_content`` for retry/error handling.
    """
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    return _groq_call_content(content, groq_client, model, max_tokens)


def _download_image_as_data_url(
    image_url: str,
    img_session: requests.Session,
) -> str:
    """
    Download the image at *image_url* and return a base64 data-URL string.
    Raises ``RuntimeError`` on failure.
    """
    img_resp = img_session.get(image_url, timeout=15)
    img_resp.raise_for_status()
    b64_data = base64.b64encode(img_resp.content).decode()
    mime = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    return f"data:{mime};base64,{b64_data}"


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
    try:
        data_url = _download_image_as_data_url(image_url, img_session)
    except Exception as exc:  # noqa: BLE001
        print(f"      Warning: could not download image {image_url}: {exc}")
        return False

    prompt = (
        f"{question}\n\n"
        "Reply with ONLY the single word 'yes' if the image "
        "matches the description, or 'no' if it does not. "
        "Do not include any other text."
    )
    answer = _groq_call_vision(data_url, prompt, groq_client, model, max_tokens=5)
    return answer.startswith("y")


def _groq_classify_batch(
    image_urls: list,
    question: str,
    groq_client: "GroqClient",
    model: str,
    img_session: requests.Session,
    example_data_urls: "list | None" = None,
) -> list:
    """
    Classify multiple images in a **single** Groq API call for speed and
    accuracy.

    All images are sent together in one message so the model can compare them
    in context.  Optional *example_data_urls* (challenge reference images) are
    prepended to the prompt to give the model a visual anchor for the target.

    Returns a ``list[bool]`` in the same order as *image_urls*:
    ``True`` = image matches the challenge task, ``False`` = does not.

    If the combined call fails or returns an unparseable response, the function
    falls back to individual ``_groq_classify_image`` calls automatically.

    Handles batches larger than ``_HCAPTCHA_BATCH_MAX`` by splitting them.
    """
    n = len(image_urls)
    if n == 0:
        return []
    # Split oversized batches
    if n > _HCAPTCHA_BATCH_MAX:
        results: list = []
        for i in range(0, n, _HCAPTCHA_BATCH_MAX):
            chunk = image_urls[i : i + _HCAPTCHA_BATCH_MAX]
            results.extend(
                _groq_classify_batch(
                    chunk, question, groq_client, model, img_session, example_data_urls
                )
            )
        return results

    # Build content list: optional examples + numbered task images
    content: list = []

    if example_data_urls:
        ex_parts: list = [
            {"type": "text", "text": f"Reference example(s) showing '{question}':"}
        ]
        for edu in example_data_urls:
            ex_parts.append({"type": "image_url", "image_url": {"url": edu}})
        content.extend(ex_parts)

    content.append(
        {
            "type": "text",
            "text": (
                f"Below are {n} images, each labelled with a number.\n"
                f"Task: {question}\n\n"
                "Reply with ONLY the comma-separated numbers of images that match "
                "the task (e.g. '1,3,5'), or the single word 'none' if none match. "
                "Do not include any other text."
            ),
        }
    )

    valid_indices: list = []
    for i, url in enumerate(image_urls):
        try:
            du = _download_image_as_data_url(url, img_session)
        except requests.RequestException as exc:
            print(f"      Warning: could not download batch image {i + 1}: {exc}")
            du = None
        if du:
            content.append({"type": "text", "text": f"[Image {i + 1}]"})
            content.append({"type": "image_url", "image_url": {"url": du}})
            valid_indices.append(i)

    if not valid_indices:
        return [False] * n

    try:
        raw = _groq_call_content(content, groq_client, model, max_tokens=40)
        results_flags = [False] * n
        if raw and "none" not in raw:
            for m in re.finditer(r'\b([0-9]+)\b', raw):
                idx = int(m.group(1)) - 1
                if 0 <= idx < n:
                    results_flags[idx] = True
        return results_flags
    except RuntimeError as exc:
        # Groq call failed -- fall back to per-image calls
        print(f"      Warning: batch Groq call failed ({exc}); falling back to per-image.")
        return [
            _groq_classify_image(url, question, groq_client, model, img_session)
            for url in image_urls
        ]


def _groq_locate_entity(
    image_url: str,
    question: str,
    entity_name: str,
    groq_client: "GroqClient",
    model: str,
    img_session: requests.Session,
) -> list:
    """
    Ask Groq's vision model to locate one or more instances of the entity
    described in *question* within the image at *image_url*.

    Returns a list of ``{"entity_name": entity_name, "x": float, "y": float}``
    dicts for use as the answer to an ``image_label_area_select`` hCaptcha
    task.  Coordinates are normalised to [0.0, 1.0] (0,0 = top-left,
    1,1 = bottom-right).  Multiple instances are returned when the model
    finds more than one on separate lines.

    Falls back to the image centre (0.5, 0.5) on parse or network errors so
    that the pipeline can continue rather than abort.

    Automatically retries on ``GroqRateLimitError`` with exponential back-off.
    """
    label = entity_name or "target"
    _fallback = [{"entity_name": label, "x": 0.5, "y": 0.5}]
    try:
        data_url = _download_image_as_data_url(image_url, img_session)
    except Exception as exc:  # noqa: BLE001
        print(f"      Warning: could not download image {image_url}: {exc}")
        return _fallback

    prompt = (
        f"Locate all instances of the following in the image: {question}\n\n"
        "For EACH instance found, output exactly one line in this format:\n"
        "x=<value>,y=<value>\n"
        "where x and y are decimal numbers between 0.0 and 1.0 "
        "(0.0,0.0 = top-left corner; 1.0,1.0 = bottom-right corner).\n"
        "If nothing is found, output: x=0.5,y=0.5\n"
        "Do not include any other text or explanation."
    )
    raw = _groq_call_vision(data_url, prompt, groq_client, model, max_tokens=80)

    points: list = []
    for line in raw.splitlines():
        m = re.search(
            r'x\s*=\s*([0-9]+\.?[0-9]*).*?y\s*=\s*([0-9]+\.?[0-9]*)',
            line, re.IGNORECASE,
        )
        if m:
            try:
                x = max(0.0, min(1.0, float(m.group(1))))
                y = max(0.0, min(1.0, float(m.group(2))))
                points.append({"entity_name": label, "x": x, "y": y})
            except ValueError:
                continue
    return points if points else _fallback


def _groq_read_text_image(
    image_url: str,
    question: str,
    groq_client: "GroqClient",
    model: str,
    img_session: requests.Session,
) -> str:
    """
    Ask Groq's vision model to read or identify text in *image_url* that is
    relevant to *question*.

    Used for ``image_label_text`` hCaptcha challenges, where the task asks the
    solver to transcribe or identify specific text shown in an image.

    Returns the extracted text string (stripped), or an empty string on failure.
    Automatically retries on ``GroqRateLimitError``.
    """
    try:
        data_url = _download_image_as_data_url(image_url, img_session)
    except Exception as exc:  # noqa: BLE001
        print(f"      Warning: could not download image {image_url}: {exc}")
        return ""

    prompt = (
        f"Task: {question}\n\n"
        "Look at the image and transcribe only the text or characters that "
        "match the task description above. "
        "Reply with ONLY that text, exactly as it appears. "
        "Do not add any explanation or punctuation beyond what is shown."
    )
    return _groq_call_vision(data_url, prompt, groq_client, model, max_tokens=60)


def _groq_analyze_challenge(
    challenge: dict,
    groq_client: "GroqClient",
    model: str,
) -> str:
    """
    Use Groq to explain an unknown or empty hCaptcha challenge.

    Sends the raw challenge JSON (fields: request_type, requester_question,
    tasklist length, and any extra top-level keys) to Groq as a text prompt and
    asks it to:
      1. Identify what type of challenge this appears to be.
      2. Explain what the challenge is asking the solver to do.
      3. Suggest how to handle / solve it programmatically.

    Returns the plain-text explanation from Groq (may be up to 300 tokens).
    Falls back to an empty string on any error so callers are not blocked.
    """
    safe: dict = {
        "request_type":        challenge.get("request_type", ""),
        "requester_question":  challenge.get("requester_question", {}),
        "tasklist_length":     len(challenge.get("tasklist", [])),
        "extra_keys":          [
            k for k in challenge
            if k not in {"tasklist", "request_type", "requester_question",
                         "key", "c", "generated_pass_UUID"}
        ],
    }
    prompt = (
        "You are an expert in hCaptcha challenge analysis.\n\n"
        "I received the following hCaptcha challenge response with an unknown or "
        "empty request_type and no image tasks:\n\n"
        f"{json.dumps(safe, indent=2)}\n\n"
        "Please:\n"
        "1. Identify what type of challenge this appears to be.\n"
        "2. Explain what the challenge is asking the solver to do.\n"
        "3. Suggest how to handle or solve it programmatically.\n\n"
        "Be concise (3-5 sentences)."
    )
    content = [{"type": "text", "text": prompt}]
    messages = [{"role": "user", "content": content}]
    try:
        completion = groq_client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=300,
            temperature=0.2,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 -- never block the main flow
        return f"(Groq analysis unavailable: {exc})"


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
    2. Solve the PoW: ``hsl`` / ``hsw`` / ``enterprise`` in pure Python
       (SHA-256 hashcash); Node.js is used as a JIT accelerator for ``hsw``
       when available, but is no longer required.
    3. POST to ``/getcaptcha`` with realistic mouse-movement motion data to
       retrieve the image challenge.
    4. Dispatch by challenge type:
       - ``image_label_binary``        -- batch-classify all images in one
                                          Groq call; reference example images
                                          are included when provided.
       - ``image_label_area_select``   -- locate entity coordinates per image
                                          (multi-point, normalised 0-1).
       - ``image_label_multiple_choice`` -- batch yes/no per candidate.
       - ``image_label_text``          -- read / transcribe text per image.
       - unknown types with images     -- best-effort binary batch.
       - unknown types with no images  -- Groq analyses the challenge and
                                          attempts submission with empty
                                          answers (may pass for token /
                                          enterprise-only challenges).
    5. POST the labelled answers with realistic motion data to
       ``/checkcaptcha``.
    6. If answers are rejected, re-fetch a fresh challenge and retry up to
       ``_HCAPTCHA_SOLVE_RETRIES`` additional times before raising.
    7. Return the ``generated_pass_UUID`` token for use with Discord.

    Raises ``RuntimeError`` when:
    - The ``groq`` package is not installed.
    - The Groq API key is invalid.
    - hCaptcha rejects the submitted answers on all retry attempts.
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
    def _solve_pow(c: dict) -> str:
        """Solve the PoW described by *c* and return the solution string."""
        ptype = (c.get("type") or "").lower()
        if ptype == "hsl":
            sol = _hcaptcha_solve_hsl_pow(c.get("req", ""))
            print("    [hCaptcha/Groq] PoW (hsl) solved.")
            return sol
        if ptype in ("hsw", "enterprise"):
            print("    [hCaptcha/Groq] Solving hsw PoW ...")
            sol = _hcaptcha_solve_hsw_pow(c.get("req", ""))
            print("    [hCaptcha/Groq] PoW (hsw) solved.")
            return sol
        return ""   # no PoW required

    pow_type_label = (c_obj.get("type") or "(none)").lower()
    print(f"    [hCaptcha/Groq] PoW type: {pow_type_label}")
    pow_solution = _solve_pow(c_obj)

    # -- Outer retry loop: re-fetch challenge if answers are rejected ----------
    for solve_attempt in range(1 + _HCAPTCHA_SOLVE_RETRIES):
        if solve_attempt > 0:
            print(
                f"    [hCaptcha/Groq] Answers rejected; "
                f"re-fetching challenge (attempt {solve_attempt + 1}) ..."
            )
            # Re-solve PoW for the new request
            pow_solution = _solve_pow(c_obj)

        # -- 3. Get challenge -------------------------------------------------
        if solve_attempt == 0:
            print("    [hCaptcha/Groq] Fetching challenge ...")
        ts_ms = int(time.time() * 1000)
        motion_get = _generate_motion_data(ts_ms)
        getcap_form: dict = {
            "v":          version,
            "host":       host,
            "sitekey":    sitekey,
            "sc":         "1",
            "swa":        "1",
            "motionData": json.dumps(motion_get),
            "pdc":        json.dumps({"s": ts_ms, "n": 0, "p": 0, "gcs": 10}),  # gcs=gesture count
            "n":          pow_solution,
            "c":          json.dumps(c_obj),
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
        # Some hCaptcha responses omit 'request_type' even when a binary image
        # tasklist is present.  Fall back to 'image_label_binary' so the solver
        # can proceed rather than aborting with a confusing empty-type error.
        if not req_type and challenge.get("tasklist"):
            req_type = "image_label_binary"

        tasklist: list = challenge.get("tasklist", [])
        question_dict: dict = challenge.get("requester_question", {})
        question: str = (
            question_dict.get("en")
            or next(iter(question_dict.values()), "Does this image match?")
        )
        challenge_key: str = challenge.get("key", "")
        c_next: dict = challenge.get("c") or c_obj

        # Collect reference example images (visual anchor for the model)
        example_data_urls: list = []
        for ex in challenge.get("requester_question_example", []):
            ex_url = ex if isinstance(ex, str) else (ex.get("datapoint_uri") or "")
            if ex_url:
                try:
                    example_data_urls.append(
                        _download_image_as_data_url(ex_url, img_session)
                    )
                except requests.RequestException as exc:
                    print(f"      Warning: could not download example image: {exc}")

        # -- 4. Build answers based on challenge type -------------------------
        answers: dict = {}

        if req_type in ("image_label_binary", "image_label_multiple_choice"):
            type_label = (
                "batch-classifying" if req_type == "image_label_binary"
                else "multiple-choice"
            )
            print(
                f'    [hCaptcha/Groq] {type_label.capitalize()} '
                f'{len(tasklist)} image(s) [{req_type}]: "{question}" ...'
            )
            task_keys: list = []
            image_urls: list = []
            for task in tasklist:
                tk: str = task.get("task_key") or task.get("datapoint_hash", "")
                iu: str = task.get("datapoint_uri") or task.get("datapoint_url", "")
                if tk and iu:
                    task_keys.append(tk)
                    image_urls.append(iu)

            batch_results = _groq_classify_batch(
                image_urls, question, groq_client, groq_model, img_session,
                example_data_urls=example_data_urls or None,
            )
            for tk, matched in zip(task_keys, batch_results):
                answers[tk] = "true" if matched else "false"
                print(f"      Task {tk[:10]}... -> {'yes' if matched else 'no'}")

        elif req_type == "image_label_area_select":
            # Area-select: locate entity coordinates per image.
            # Extract the entity label from the question when possible.
            entity_m = re.search(
                r'(?:click on|find|locate|select|identify)\s+'
                r'(?:all\s+)?(?:the\s+)?([a-z0-9 ]+?)(?:\s*$|\.)',
                question, re.IGNORECASE,
            )
            entity_name: str = entity_m.group(1).strip() if entity_m else "target"
            print(
                f'    [hCaptcha/Groq] Locating "{entity_name}" in '
                f'{len(tasklist)} image(s) [{req_type}]: "{question}" ...'
            )
            for task in tasklist:
                tk = task.get("task_key") or task.get("datapoint_hash", "")
                iu = task.get("datapoint_uri") or task.get("datapoint_url", "")
                if not tk or not iu:
                    continue
                coords = _groq_locate_entity(
                    iu, question, entity_name, groq_client, groq_model, img_session
                )
                answers[tk] = coords
                print(f"      Task {tk[:10]}... -> {coords}")

        elif req_type == "image_label_text":
            # Text challenge: transcribe / identify text per image.
            print(
                f'    [hCaptcha/Groq] Text challenge {len(tasklist)} image(s)'
                f' [{req_type}]: "{question}" ...'
            )
            for task in tasklist:
                tk = task.get("task_key") or task.get("datapoint_hash", "")
                iu = task.get("datapoint_uri") or task.get("datapoint_url", "")
                if not tk or not iu:
                    continue
                text = _groq_read_text_image(
                    iu, question, groq_client, groq_model, img_session
                )
                answers[tk] = text or ""
                print(f"      Task {tk[:10]}... -> {text!r}")

        else:
            # Best-effort for any other type: treat as binary batch when images
            # are present; use Groq to explain the challenge and attempt an
            # empty-answers submission when no image tasks are provided.
            if not tasklist:
                print(
                    f"    [hCaptcha/Groq] Unknown challenge type "
                    f"'{req_type}' with no image tasks."
                )
                print("    [hCaptcha/Groq] Asking Groq to analyse the challenge ...")
                explanation = _groq_analyze_challenge(
                    challenge, groq_client, groq_model
                )
                print(f"    [hCaptcha/Groq] Groq analysis:\n      {explanation}")
                # Attempt to pass by submitting empty answers -- some
                # challenge types (e.g. enterprise token challenges) require
                # only the PoW solution and no image answers.  We proceed to
                # the submit step below with an empty dict; if the server
                # rejects the submission the outer retry loop will handle it,
                # and after all retries are exhausted a clear error is raised.
                print(
                    "    [hCaptcha/Groq] Attempting submission with empty "
                    "answers (no image tasks present) ..."
                )
                # answers stays {} -- fall through to step 5
            else:
                print(
                    f'    [hCaptcha/Groq] Unknown type "{req_type}" -- attempting '
                    f'batch classification on {len(tasklist)} image(s): '
                    f'"{question}" ...'
                )
                task_keys = []
                image_urls = []
                for task in tasklist:
                    tk = task.get("task_key") or task.get("datapoint_hash", "")
                    iu = task.get("datapoint_uri") or task.get("datapoint_url", "")
                    if tk and iu:
                        task_keys.append(tk)
                        image_urls.append(iu)
                batch_results = _groq_classify_batch(
                    image_urls, question, groq_client, groq_model, img_session,
                    example_data_urls=example_data_urls or None,
                )
                for tk, matched in zip(task_keys, batch_results):
                    answers[tk] = "true" if matched else "false"
                    print(f"      Task {tk[:10]}... -> {'yes' if matched else 'no'}")

        # -- 5. Submit answers ------------------------------------------------
        print("    [hCaptcha/Groq] Submitting answers ...")

        # A second PoW descriptor may arrive inside the challenge body.
        second_pow = _solve_pow(c_next) if isinstance(c_next, dict) else ""

        ts_ms2 = int(time.time() * 1000)
        motion_check = _generate_motion_data(ts_ms2)
        submit_payload = {
            "v":          version,
            "job_mode":   req_type,
            "answers":    answers,
            "serverdomain": host,
            "sitekey":    sitekey,
            "n":          second_pow or pow_solution,
            "c": (
                json.dumps(c_next)
                if isinstance(c_next, dict)
                else (c_next or json.dumps(c_obj))
            ),
            "motionData": json.dumps(motion_check),
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
        if token:
            print("    [hCaptcha/Groq] Challenge solved successfully.")
            return token

        # Answers were rejected; loop will retry if attempts remain.

    raise RuntimeError(
        f"hCaptcha: challenge not accepted after "
        f"{1 + _HCAPTCHA_SOLVE_RETRIES} attempt(s) -- "
        f"last response: {result}"
    )


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
