#!/bin/bash
# RunRunDeals Bot — yeni Mac'e kurulum
# Kullanım:
#   curl -fsSL https://raw.githubusercontent.com/runrundealss/tg_dealbot/main/install_on_new_mac.sh \
#     | REPO_URL=https://github.com/runrundealss/tg_dealbot.git bash
set -e

REPO_URL="${REPO_URL:-https://github.com/runrundealss/tg_dealbot.git}"
INSTALL_DIR="$HOME/tg_dealbot"
PLIST_NAME="com.runrundeals.dealbot.plist"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

echo "==> Python & pip kontrol"
command -v python3 >/dev/null || { echo "Python3 yok"; exit 1; }
python3 -m pip install -q --user pillow rumps 2>/dev/null || true

echo "==> Repo clone -> $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  cd "$INSTALL_DIR" && git pull --quiet
else
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

echo "==> Font indir (Bangers)"
mkdir -p /tmp/fonts
curl -sL -o /tmp/fonts/Bangers-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf"

echo "==> config.json oluştur"
if [ ! -f "$INSTALL_DIR/config.json" ]; then
  cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
  # Otomatik defaultları doldur
  python3 - <<'PYEOF'
import json, os
p = os.path.expanduser("~/tg_dealbot/config.json")
c = json.load(open(p))
c["strapi_url"] = "https://rundealsmobile.herokuapp.com/urunlers"
json.dump(c, open(p,"w"), indent=2)
PYEOF
  echo "    config.json yazıldı"
fi

echo "==> Bot token"
TOKEN_FILE="$HOME/Downloads/untitled text 5.txt"
if [ ! -s "$TOKEN_FILE" ]; then
  if [ -t 0 ]; then
    read -p "    Telegram bot token: " TOKEN
  else
    # piped run — read from tty if available
    if [ -e /dev/tty ]; then
      read -p "    Telegram bot token: " TOKEN < /dev/tty
    fi
  fi
  if [ -n "$TOKEN" ]; then
    echo -n "$TOKEN" > "$TOKEN_FILE"
    echo "    Token kaydedildi: $TOKEN_FILE"
  else
    echo "    ⚠️  Token girilmedi! Kurulum sonra şu komutla tamamlanır:"
    echo "       echo -n 'YOUR_TOKEN' > '$TOKEN_FILE'"
  fi
fi

echo "==> LaunchAgent kur"
mkdir -p "$LAUNCH_DIR"
sed "s|/Users/kaan|$HOME|g" "$INSTALL_DIR/$PLIST_NAME" > "$LAUNCH_DIR/$PLIST_NAME"
launchctl unload "$LAUNCH_DIR/$PLIST_NAME" 2>/dev/null || true
launchctl load -w "$LAUNCH_DIR/$PLIST_NAME"

echo "==> Dashboard .app -> /Applications"
cp -R "$INSTALL_DIR/RunRunDealsBot.app" /Applications/ 2>/dev/null || true
# Strip quarantine flag (Gatekeeper engellesin diye değil)
xattr -dr com.apple.quarantine /Applications/RunRunDealsBot.app 2>/dev/null || true

echo ""
echo "✅ Kurulum tamam."
echo "   • Daemon login'de otomatik başlar"
echo "   • Applications → RunRunDeals Bot (çift tıkla)"
echo ""
echo "   ⚠️  Auto-login açmak için:"
echo "       System Settings → Users & Groups → 'Automatically log in as'"
