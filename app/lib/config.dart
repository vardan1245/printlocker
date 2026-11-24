/// Build-time settings. Override with:
///   flutter run --dart-define=API_URL=http://10.0.2.2:8000/api/v1
const String apiUrl = String.fromEnvironment(
  'API_URL',
  defaultValue: 'https://printlocker-api-xpqo.onrender.com/api/v1',
);

/// WebSocket URL for live job status, derived from [apiUrl].
String liveUrl(String token) {
  final ws = apiUrl.replaceFirst(RegExp('^http'), 'ws');
  return '$ws/ws/user?token=${Uri.encodeQueryComponent(token)}';
}
