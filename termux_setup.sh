#!/data/data/com.termux/files/usr/bin/bash
# =============================================================
#  termux_setup.sh -- EDUCATIONAL PURPOSES ONLY
#  One-shot setup for Discord Bot Creator on Termux (Android).
#
#  On Termux use Method 1 (API) when prompted -- no browser is
#  available via pkg so Method 2 (Browser/Selenium) will not work.
#  Only `requests` and `pyotp` are installed here (no selenium).
# =============================================================
set -euo pipefail

echo "================================================="
echo "  Discord Bot Creator -- Termux Setup"
echo "================================================="

# --- 1. Update package lists ---
echo ""
echo "[1/3] Updating Termux packages ..."
pkg update -y  || { echo "ERROR: pkg update failed. Check your internet connection."; exit 1; }
pkg upgrade -y || { echo "WARN: pkg upgrade had errors; continuing."; true; }

# --- 2. Install Python ---
echo ""
echo "[2/3] Installing Python ..."
pkg install -y python || {
    echo "ERROR: Failed to install python."
    echo "       Try manually: pkg install python"
    exit 1
}

# --- 3. Install Python dependencies (API method only) ---
echo ""
echo "[3/3] Installing Python dependencies (requests + pyotp) ..."
python -m ensurepip --upgrade 2>/dev/null || true
pip install --upgrade pip || { echo "WARN: pip upgrade failed; continuing."; true; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Install only the API-method deps; skip selenium/webdriver-manager (no browser on Termux)
pip install "requests>=2.28.0,<3.0.0" "pyotp>=2.9.0,<3.0.0" || {
    echo "ERROR: Failed to install Python dependencies."
    exit 1
}

echo ""
echo "================================================="
echo "  Setup complete!"
echo "  Run the bot creator with:"
echo "    python $SCRIPT_DIR/create_discord_bot.py"
echo ""
echo "  When prompted, select Method 1 (API) --"
echo "  Method 2 (Browser) does not work on Termux."
echo "================================================="
