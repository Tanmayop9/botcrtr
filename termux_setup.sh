#!/data/data/com.termux/files/usr/bin/bash
# =============================================================
#  termux_setup.sh -- EDUCATIONAL PURPOSES ONLY
#  One-shot setup for Discord Bot Creator on Termux (Android).
#
#  No browser or driver is needed -- the script uses the Discord
#  REST API directly via Python requests.
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

# --- 3. Install Python dependencies ---
echo ""
echo "[3/3] Installing Python dependencies (requests + pyotp) ..."
python -m ensurepip --upgrade 2>/dev/null || true
pip install --upgrade pip || { echo "WARN: pip upgrade failed; continuing."; true; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
pip install -r "$SCRIPT_DIR/requirements.txt" || {
    echo "ERROR: Failed to install Python dependencies."
    echo "       Check that requirements.txt exists at: $SCRIPT_DIR"
    exit 1
}

echo ""
echo "================================================="
echo "  Setup complete! No browser install needed."
echo "  Run the bot creator with:"
echo "    python $SCRIPT_DIR/create_discord_bot.py"
echo "================================================="
