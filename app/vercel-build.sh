#!/bin/sh
# Vercel build: install Flutter (not available on Vercel), generate the web platform folder, build.
set -e
FLUTTER_DIR="${FLUTTER_DIR:-/tmp/flutter}"
if [ ! -x "$FLUTTER_DIR/bin/flutter" ]; then
  git clone https://github.com/flutter/flutter.git -b stable --depth 1 "$FLUTTER_DIR"
fi
export PATH="$FLUTTER_DIR/bin:$PATH"
flutter --version
flutter config --enable-web --no-analytics
flutter create . --platforms=web --org am.printlocker --project-name printlocker_app
# App name and description instead of the Flutter template's
sed -i 's|<title>.*</title>|<title>PrintLocker</title>|; s|A new Flutter project.|Print here, pick up at the kiosk with your QR code.|; s|content="printlocker_app"|content="PrintLocker"|' web/index.html
sed -i 's|"name": "printlocker_app"|"name": "PrintLocker"|; s|"short_name": "printlocker_app"|"short_name": "PrintLocker"|; s|A new Flutter project.|Print here, pick up at the kiosk.|' web/manifest.json
flutter pub get
flutter build web --release ${API_URL:+--dart-define=API_URL=$API_URL}
