#!/data/data/com.termux/files/usr/bin/bash
# =============================================================
#  termux_setup.sh -- EDUCATIONAL PURPOSES ONLY
#  One-shot setup for Discord Bot Creator on Termux (Android).
#
#  Installs Firefox + geckodriver from x11-repo so that both
#  Method 1 (API) and Method 2 (Browser/Selenium) work on Termux.
# =============================================================
set -euo pipefail

echo "================================================="
echo "  Discord Bot Creator -- Termux Setup"
echo "================================================="

# --- 1. Update package lists ---
echo ""
echo "[1/4] Updating Termux packages ..."
pkg update -y  || { echo "ERROR: pkg update failed. Check your internet connection."; exit 1; }
pkg upgrade -y || { echo "WARN: pkg upgrade had errors; continuing."; true; }

# --- 2. Enable x11-repo and install Python, Firefox, geckodriver ---
# Firefox and geckodriver are only available through the x11-repo repository.
echo ""
echo "[2/4] Enabling x11-repo and installing Python, Firefox, geckodriver ..."
pkg install -y x11-repo || {
    echo "ERROR: Failed to enable x11-repo."
    exit 1
}
pkg install -y python firefox geckodriver || {
    echo "ERROR: Failed to install python, firefox, or geckodriver."
    echo "       Try manually:"
    echo "         pkg install x11-repo"
    echo "         pkg install python firefox geckodriver"
    exit 1
}

# --- 3. Upgrade pip ---
echo ""
echo "[3/4] Upgrading pip ..."
python -m ensurepip --upgrade 2>/dev/null || true
pip install --upgrade pip || { echo "WARN: pip upgrade failed; continuing."; true; }

# --- 4. Install Python dependencies (requests, pyotp, selenium, groq) ---
echo ""
echo "[4/4] Installing Python dependencies ..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# On Termux/Android, pydantic-core (a Rust extension required by pydantic>=2) cannot
# be compiled.  We work around this by:
#   a) Installing pydantic v1 (pure-Python, no Rust build needed).
#   b) Installing groq's non-pydantic runtime dependencies individually.
#   c) Installing the groq package itself with --no-deps so pip does not try to
#      upgrade pydantic to >=2 (which would re-trigger the pydantic-core build).
# All other packages (requests, pyotp, selenium, webdriver-manager) install fine.

# Step 4a: packages that have no pydantic-core dependency
pip install \
    "requests>=2.28.0,<3.0.0" \
    "pyotp>=2.9.0,<3.0.0" \
    "selenium>=4.0.0,<5.0.0" \
    "webdriver-manager>=4.0.0,<5.0.0" || {
    echo "ERROR: Failed to install base Python dependencies."
    exit 1
}

# Step 4b: pydantic v1 (pure-Python wheel -- no Rust/pydantic-core needed)
pip install "pydantic>=1.9.0,<2" || {
    echo "ERROR: Failed to install pydantic v1."
    exit 1
}

# Step 4c: groq's runtime dependencies (excluding pydantic, which we just installed)
pip install \
    "httpx>=0.23.0,<1" \
    "anyio>=3.5.0,<5" \
    "sniffio" \
    "distro>=1.7.0,<2" \
    "typing_extensions>=4.7" || {
    echo "ERROR: Failed to install groq runtime dependencies."
    exit 1
}

# Step 4d: groq itself without deps so pip won't re-require pydantic>=2
pip install "groq>=1.1.0,<2.0.0" --no-deps || {
    echo "ERROR: Failed to install groq."
    exit 1
}

echo ""
echo "================================================="
echo "  Setup complete!"
echo "  Run the bot creator with:"
echo "    python $SCRIPT_DIR/create_discord_bot.py"
echo ""
echo "  Select Method 1 (API) for no-browser automation, or"
echo "  Select Method 2 (Browser) to use Firefox with Selenium."
echo "================================================="
