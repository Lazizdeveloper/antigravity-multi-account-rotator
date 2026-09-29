#!/usr/bin/env bash
# ==============================================================================
# 🚀 Antigravity Multi-Account Rotator Installer
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
ACCOUNTS_DIR="$HOME/.gemini/accounts"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "========================================================================"
echo "🤖 Google Antigravity (AGY) Multi-Account Rotator o'rnatish boshlandi..."
echo "========================================================================"

# 1. Katalogni yaratish
mkdir -p "$BIN_DIR" "$ACCOUNTS_DIR" "$SYSTEMD_USER_DIR"

# 2. Skriptlarni ko'chirish
echo "📦 Skriptlar o'rnatilmoqda..."
cp "$DIR/src/account_manager.py" "$ACCOUNTS_DIR/account_manager.py"
cp "$DIR/src/auto_rotator.py" "$ACCOUNTS_DIR/auto_rotator.py"
chmod +x "$ACCOUNTS_DIR/account_manager.py" "$ACCOUNTS_DIR/auto_rotator.py"

# 3. switch-acc buyrug'ini o'rnatish
cp "$DIR/bin/switch-acc" "$BIN_DIR/switch-acc"
chmod +x "$BIN_DIR/switch-acc"

# 4. agy aqlli qobig'ini (wrapper) sozlash
if [ -f "$BIN_DIR/agy" ] && [ ! -f "$BIN_DIR/agy-real" ]; then
    echo "🔄 Mavjud agy binar fayli agy-real nomiga o'tkazilmoqda..."
    mv "$BIN_DIR/agy" "$BIN_DIR/agy-real"
fi

if [ -f "$BIN_DIR/agy-real" ]; then
    echo "🛡️ Smart TTY Wrapper (~/.local/bin/agy) o'rnatilmoqda..."
    cp "$DIR/bin/agy-wrapper" "$BIN_DIR/agy"
    chmod +x "$BIN_DIR/agy"
fi

# 5. Systemd xizmatini o'rnatish va ishga tushirish
echo "⚙️ Systemd fon xizmati (gemini-auto-rotator.service) sozlanmoqda..."
cp "$DIR/systemd/gemini-auto-rotator.service" "$SYSTEMD_USER_DIR/gemini-auto-rotator.service"
systemctl --user daemon-reload
systemctl --user enable --now gemini-auto-rotator.service

echo ""
echo "========================================================================"
echo "✅ Muvaffaqiyatli o'rnatildi!"
echo "========================================================================"
echo "Buyruqlar:"
echo "  switch-acc usage     - Barcha hisoblar kvotasini ko'rish"
echo "  switch-acc usage -r  - Jonli kvotani yangilash"
echo "  switch-acc next      - Eng ko'p kvotasi bor hisobga o'tish"
echo "  switch-acc <raqam>   - Aniq hisobni faollashtirish"
echo "========================================================================"
