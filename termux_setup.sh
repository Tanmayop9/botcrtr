#!/data/data/com.termux/files/usr/bin/bash
# =============================================================
#  termux_setup.sh  --  EDUCATIONAL PURPOSES ONLY
#  One-shot setup for Discord Bot Creator on Termux (Android).
# =============================================================
set -euo pipefail

echo "================================================="
echo "  Discord Bot Creator -- Termux Setup"
echo "================================================="

# --- 1. Update package lists and upgrade installed packages ---
echo ""
echo "[1/4] Updating Termux packages …"
pkg update -y  || { echo "ERROR: pkg update failed. Check your internet connection."; exit 1; }
pkg upgrade -y || { echo "WARN: pkg upgrade had errors; continuing anyway."; true; }

# --- 2. Install Python and Chromium ---
# NOTE: 'firefox' is NOT available in the Termux main repository.
#       Chromium is the supported browser on Termux.
#       Its bundled chromedriver (in /usr/lib/chromium/) is used automatically.
echo ""
echo "[2/4] Installing Python and Chromium …"
pkg install -y python chromium || {
    echo "ERROR: Failed to install python or chromium."
    echo "       Try: pkg install python  then  pkg install chromium"
    exit 1
}

# geckodriver is also available if you ever want desktop Firefox support:
#   pkg install geckodriver

# --- 3. Ensure pip is up to date ---
echo ""
echo "[3/4] Upgrading pip …"
python -m ensurepip --upgrade 2>/dev/null || true
pip install --upgrade pip || { echo "WARN: pip upgrade failed; continuing with existing pip."; true; }

# --- 4. Install Python dependencies ---
echo ""
echo "[4/4] Installing Python dependencies …"
# Navigate to the directory containing this script (the repo root).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
pip install -r "$SCRIPT_DIR/requirements.txt" || {
    echo "ERROR: Failed to install Python dependencies."
    echo "       Check that requirements.txt exists at: $SCRIPT_DIR"
    exit 1
}

echo ""
echo "================================================="
echo "  Setup complete!"
echo "  Run the bot creator with:"
echo "    python $SCRIPT_DIR/create_discord_bot.py"
echo "================================================="
