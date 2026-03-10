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

# --- 4. Install Python dependencies (requests, pyotp, selenium) ---
echo ""
echo "[4/4] Installing Python dependencies ..."
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
echo ""
echo "  Select Method 1 (API) for no-browser automation, or"
echo "  Select Method 2 (Browser) to use Firefox with Selenium."
echo "================================================="
