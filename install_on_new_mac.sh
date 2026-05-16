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
TOKEN_FILE="$INSTALL_DIR/.token"

# ---- 0) Daemon'u durdur (varsa) ----
if [ -f "$LAUNCH_DIR/$PLIST_NAME" ]; then
  launchctl unload "$LAUNCH_DIR/$PLIST_NAME" 2>/dev/null || true
fi

# ---- 1) Tkinter destekli Python bul ----
echo "==> Tkinter destekleyen Python aranıyor"
PY=""
for P in \
  /opt/homebrew/bin/python3.12 \
  /opt/homebrew/bin/python3.13 \
  /opt/homebrew/bin/python3.14 \
  /opt/homebrew/bin/python3 \
  /Library/Developer/CommandLineTools/usr/bin/python3 \
  /usr/bin/python3 ; do
  [ -x "$P" ] || continue
  if "$P" -c "import tkinter" 2>/dev/null; then
    PY="$P"; break
  fi
done
if [ -z "$PY" ]; then
  echo "❌ Tkinter destekli Python yok. Şu komutu çalıştır: brew install python@3.12"
  exit 1
fi
echo "    Python: $PY"

# ---- 2) Repo clone / pull ----
echo "==> Repo clone -> $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  cd "$INSTALL_DIR" && git pull --quiet
else
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

# ---- 3) Pillow + rumps (ZORLA kur) ----
echo "==> Pillow + rumps yükleniyor"
"$PY" -m pip install --user --upgrade --quiet pip 2>/dev/null || true
"$PY" -m pip install --user --quiet pillow rumps
"$PY" -c "import PIL; print('   PIL OK:', PIL.__version__)"

# ---- 4) Font ----
echo "==> Bangers font"
mkdir -p /tmp/fonts
curl -sL -o /tmp/fonts/Bangers-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf"

# ---- 5) config.json ----
echo "==> config.json hazırlanıyor"
if [ ! -f "$INSTALL_DIR/config.json" ]; then
  cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
fi
"$PY" - <<PYEOF
import json
p = "$INSTALL_DIR/config.json"
c = json.load(open(p))
c["strapi_url"] = "https://rundealsmobile.herokuapp.com/urunlers"
c["token_path"] = "$TOKEN_FILE"   # repo içinde, TCC engeli yok
json.dump(c, open(p,"w"), indent=2)
print("   config.json yazıldı:", p)
PYEOF

# ---- 6) Bot token ----
echo "==> Bot token"
# Eski Downloads konumundan migrate
OLD_TOKEN="$HOME/Downloads/untitled text 5.txt"
if [ ! -s "$TOKEN_FILE" ] && [ -s "$OLD_TOKEN" ]; then
  cp "$OLD_TOKEN" "$TOKEN_FILE" 2>/dev/null || true
fi
if [ ! -s "$TOKEN_FILE" ]; then
  if [ -e /dev/tty ]; then
    read -p "    Telegram bot token: " TOKEN < /dev/tty
  fi
  if [ -n "$TOKEN" ]; then
    echo -n "$TOKEN" > "$TOKEN_FILE"
    echo "    Token kaydedildi: $TOKEN_FILE"
  else
    echo "    ⚠️  Token girilmedi. Sonra elle koy:"
    echo "       echo -n 'YOUR_TOKEN' > '$TOKEN_FILE'"
  fi
else
  echo "    Token mevcut: $TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE" 2>/dev/null || true

# ---- 7) LaunchAgent ----
echo "==> LaunchAgent kuruluyor"
mkdir -p "$LAUNCH_DIR"
sed -e "s|__PYTHON__|$PY|g" -e "s|__HOME__|$HOME|g" \
    "$INSTALL_DIR/$PLIST_NAME" > "$LAUNCH_DIR/$PLIST_NAME"
launchctl unload "$LAUNCH_DIR/$PLIST_NAME" 2>/dev/null || true
launchctl load -w "$LAUNCH_DIR/$PLIST_NAME"

# ---- 8) .app to /Applications ----
echo "==> Dashboard .app -> /Applications"
cp -R "$INSTALL_DIR/RunRunDealsBot.app" /Applications/ 2>/dev/null || true
xattr -dr com.apple.quarantine /Applications/RunRunDealsBot.app 2>/dev/null || true

echo ""
echo "✅ Kurulum tamam."
echo "   Python:   $PY"
echo "   Repo:     $INSTALL_DIR"
echo "   Token:    $TOKEN_FILE"
echo "   Plist:    $LAUNCH_DIR/$PLIST_NAME"
echo "   App:      /Applications/RunRunDealsBot.app"
echo ""
echo "   ⚠️  Auto-login: System Settings → Users & Groups → 'Automatically log in as'"
