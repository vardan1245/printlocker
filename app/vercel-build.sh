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
flutter pub get
flutter build web --release ${API_URL:+--dart-define=API_URL=$API_URL}
