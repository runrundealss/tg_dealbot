#!/bin/bash
# RunRunDeals Bot — yeni Mac'e tek-seferlik kurulum
# Kullanım: bash install_on_new_mac.sh
set -e

REPO_URL="${REPO_URL:-https://github.com/runrundealss/tg_dealbot.git}"
INSTALL_DIR="$HOME/tg_dealbot"
PLIST_NAME="com.runrundeals.dealbot.plist"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

echo "==> Python & pip kontrol"
command -v python3 >/dev/null || { echo "Python3 yok"; exit 1; }
python3 -m pip install -q --user pillow rumps

echo "==> Repo clone -> $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  cd "$INSTALL_DIR" && git pull
else
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

echo "==> Font indir (Bangers)"
mkdir -p /tmp/fonts
curl -sL -o /tmp/fonts/Bangers-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf"

echo "==> Token yerleştir"
if [ ! -f "$HOME/Downloads/untitled text 5.txt" ]; then
  echo "⚠️  Bot token bekleniyor."
  echo "    Telegram BotFather → /mybots → API Token kopyala"
  read -p "    Token'ı buraya yapıştır: " TOKEN
  echo -n "$TOKEN" > "$HOME/Downloads/untitled text 5.txt"
fi

echo "==> LaunchAgent kur (login'de otomatik başla)"
mkdir -p "$LAUNCH_DIR"
# Path'i bu Mac'in user'ına göre güncelle
sed "s|/Users/kaan|$HOME|g" "$INSTALL_DIR/$PLIST_NAME" > "$LAUNCH_DIR/$PLIST_NAME"
launchctl unload "$LAUNCH_DIR/$PLIST_NAME" 2>/dev/null || true
launchctl load -w "$LAUNCH_DIR/$PLIST_NAME"

echo "==> Dashboard .app -> /Applications"
cp -R "$INSTALL_DIR/RunRunDealsBot.app" /Applications/ 2>/dev/null || true

echo ""
echo "✅ Kurulum tamam."
echo "   Daemon login'de otomatik başlar (caffeinate ile uyumaz)."
echo "   Dashboard'u açmak için: Applications → RunRunDeals Bot"
echo ""
echo "   ⚠️  Auto-login (elektrik gidip geldiğinde Mac kullanıcı şifresi sormadan açılsın):"
echo "       System Settings → Users & Groups → \"Automatically log in as\" seç"
