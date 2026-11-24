import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

class ApiException implements Exception {
  final int status;
  final String message;

  const ApiException(this.status, this.message);

  bool get unauthorized => status == 401;

  @override
  String toString() => message;
}

/// Thin wrapper around the PrintLocker REST API.
///
/// Keeps the access/refresh tokens; on a 401 it refreshes once and retries.
class ApiClient {
  final String baseUrl;
  final http.Client _http;
  String? accessToken;
  String? refreshToken;

  /// Called whenever tokens change (so they can be saved), with nulls on logout.
  void Function(String? access, String? refresh)? onTokens;

  ApiClient(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Map<String, String> get _auth => accessToken == null ? {} : {'Authorization': 'Bearer $accessToken'};

  static String _detail(http.Response r) {
    try {
      final body = jsonDecode(utf8.decode(r.bodyBytes));
      final d = body is Map ? body['detail'] : null;
      if (d is String) return d;
      if (d is List && d.isNotEmpty) {
        return d.map((e) => e is Map ? (e['msg'] ?? e.toString()) : e.toString()).join('; ');
      }
    } catch (_) {
      // not JSON
    }
    return 'Server error (${r.statusCode})';
  }

  Future<dynamic> _send(Future<http.Response> Function() call, {bool retried = false}) async {
    final http.Response r;
    try {
      r = await call();
    } catch (e) {
      throw const ApiException(0, 'Cannot reach the server. Check your connection and try again.');
    }
    if (r.statusCode == 401 && !retried && refreshToken != null && await _refresh()) {
      return _send(call, retried: true);
    }
    if (r.statusCode >= 400) throw ApiException(r.statusCode, _detail(r));
    if (r.bodyBytes.isEmpty) return null;
    return jsonDecode(utf8.decode(r.bodyBytes));
  }

  Future<bool> _refresh() async {
    try {
      final r = await _http.post(
        _uri('/auth/refresh'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'refresh_token': refreshToken}),
      );
      if (r.statusCode != 200) return false;
      _setTokens(jsonDecode(r.body) as Map<String, dynamic>);
      return true;
    } catch (_) {
      return false;
    }
  }

  void _setTokens(Map<String, dynamic> t) {
    accessToken = t['access_token'] as String?;
    refreshToken = t['refresh_token'] as String?;
    onTokens?.call(accessToken, refreshToken);
  }

  void logout() {
    accessToken = null;
    refreshToken = null;
    onTokens?.call(null, null);
  }

  Future<dynamic> _getJson(String path) => _send(() => _http.get(_uri(path), headers: _auth));

  Future<dynamic> _postJson(String path, Object body) => _send(() => _http.post(
        _uri(path),
        headers: {..._auth, 'Content-Type': 'application/json'},
        body: jsonEncode(body),
      ));

  // ---- auth

  Future<void> login(String email, String password) async {
    final t = await _postJson('/auth/login', {'email': email.trim(), 'password': password});
    _setTokens(t as Map<String, dynamic>);
  }

  Future<void> register(String email, String password) =>
      _postJson('/auth/register', {'email': email.trim(), 'password': password});

  Future<String> me() async => ((await _getJson('/auth/me')) as Map<String, dynamic>)['email'] as String;

  // ---- kiosks and jobs

  Future<List<Kiosk>> kiosks() async =>
      [for (final k in (await _getJson('/kiosks')) as List) Kiosk.fromJson(k as Map<String, dynamic>)];

  Future<List<Job>> jobs() async =>
      [for (final j in (await _getJson('/jobs')) as List) Job.fromJson(j as Map<String, dynamic>)];

  Future<Job> job(int id) async => Job.fromJson((await _getJson('/jobs/$id')) as Map<String, dynamic>);

  Future<Pickup> pickup(int id) async =>
      Pickup.fromJson((await _getJson('/jobs/$id/pickup')) as Map<String, dynamic>);

  Future<Job> cancel(int id) async =>
      Job.fromJson((await _send(() => _http.delete(_uri('/jobs/$id'), headers: _auth))) as Map<String, dynamic>);

  http.MultipartRequest _multipart(String path, int printerId, List<int> pdf, String filename, PrintOptions o) {
    final req = http.MultipartRequest('POST', _uri(path))
      ..headers.addAll(_auth)
      ..fields['printer_id'] = '$printerId'
      ..fields['copies'] = '${o.copies}'
      ..fields['duplex'] = o.duplex ? 'true' : 'false'
      ..files.add(http.MultipartFile.fromBytes('file', pdf, filename: filename));
    if (o.pageRange.trim().isNotEmpty) req.fields['page_range'] = o.pageRange.trim();
    return req;
  }

  Future<dynamic> _sendMultipart(http.MultipartRequest Function() build) =>
      _send(() async => http.Response.fromStream(await _http.send(build())));

  Future<Preview> preview(int printerId, List<int> pdf, String filename, PrintOptions o) async =>
      Preview.fromJson((await _sendMultipart(() => _multipart('/jobs/preview', printerId, pdf, filename, o)))
          as Map<String, dynamic>);

  Future<Job> createJob(int printerId, List<int> pdf, String filename, PrintOptions o) async =>
      Job.fromJson(
          (await _sendMultipart(() => _multipart('/jobs', printerId, pdf, filename, o))) as Map<String, dynamic>);
}
