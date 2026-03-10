#!/data/data/com.termux/files/usr/bin/bash
# =============================================================
#  termux_setup.sh  –  EDUCATIONAL PURPOSES ONLY
#  One-shot setup for Discord Bot Creator on Termux (Android).
# =============================================================
set -euo pipefail

echo "================================================="
echo "  Discord Bot Creator – Termux Setup"
echo "================================================="

# --- 1. Update package lists and upgrade installed packages ---
echo ""
echo "[1/4] Updating Termux packages …"
pkg update -y
pkg upgrade -y

# --- 2. Install Python and Firefox ---
# Firefox ships with GeckoDriver support via webdriver-manager (ARM64).
# If you prefer Chromium, replace 'firefox' with 'chromium' below, but
# be aware that a matching chromedriver must be compiled manually for ARM.
echo ""
echo "[2/4] Installing Python and Firefox …"
pkg install -y python firefox

# --- 3. Ensure pip is up to date ---
echo ""
echo "[3/4] Upgrading pip …"
python -m ensurepip --upgrade 2>/dev/null || true
pip install --upgrade pip

# --- 4. Install Python dependencies ---
echo ""
echo "[4/4] Installing Python dependencies …"
# Navigate to the directory containing this script (the repo root).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
pip install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "================================================="
echo "  Setup complete!"
echo "  Run the bot creator with:"
echo "    python $SCRIPT_DIR/create_discord_bot.py"
echo "================================================="
