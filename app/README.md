# PrintLocker app (Flutter)

User app for Android, iOS and web: log in, choose a kiosk printer, upload a PDF
(copies, page range, two-sided), follow the job live, and show the QR code / PIN at the kiosk.

```bash
cd app
flutter create . --platforms=android,ios,web --org am.printlocker --project-name printlocker_app   # once: generates platform folders
flutter pub get
flutter run                                   # uses the Render API by default
flutter run --dart-define=API_URL=http://10.0.2.2:8000/api/v1   # local backend from the Android emulator
flutter test
```

Android release builds need `<uses-permission android:name="android.permission.INTERNET"/>` in
`android/app/src/main/AndroidManifest.xml` (CI adds it automatically).

GitHub Actions (`.github/workflows/app.yml`) analyzes, tests and builds an Android APK and the
web app on every push to `app/`; download them from the run's *Artifacts*.

| File | What |
| --- | --- |
| `lib/config.dart` | API URL (`--dart-define=API_URL=...`) |
| `lib/api/` | REST client (token refresh, friendly errors) and models |
| `lib/app_state.dart` | session, kiosks, jobs, pickup codes, WebSocket live status + polling fallback |
| `lib/screens/` | login, printers, print, my jobs, job detail with QR + PIN |
