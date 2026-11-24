import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'api/api_client.dart';
import 'api/models.dart';
import 'config.dart';

enum SessionState { starting, loggedOut, loggedIn }

/// App-wide state: session, kiosks, jobs, pickup codes, live updates.
class AppState extends ChangeNotifier {
  final ApiClient api;
  final Future<SharedPreferences> Function() _prefs;

  SessionState session = SessionState.starting;
  String? email;
  List<Kiosk> kiosks = const [];
  List<Job> jobs = const [];
  final Map<int, Pickup> pickups = {};
  String? kiosksError;
  bool liveConnected = false;

  WebSocketChannel? _socket;
  StreamSubscription<dynamic>? _socketSub;
  Timer? _poll;
  Timer? _reconnect;

  AppState({ApiClient? api, Future<SharedPreferences> Function()? prefs})
      : api = api ?? ApiClient(apiUrl),
        _prefs = prefs ?? SharedPreferences.getInstance {
    this.api.onTokens = _saveTokens;
  }

  // ---------------------------------------------------------------- session

  Future<void> start() async {
    final p = await _prefs();
    api.accessToken = p.getString('access_token');
    api.refreshToken = p.getString('refresh_token');
    if (api.accessToken == null) {
      _set(SessionState.loggedOut);
      return;
    }
    try {
      email = await api.me();
      await _afterLogin();
    } on ApiException catch (e) {
      if (e.unauthorized) api.logout();
      _set(SessionState.loggedOut);
    }
  }

  Future<void> _saveTokens(String? access, String? refresh) async {
    final p = await _prefs();
    if (access == null) {
      await p.remove('access_token');
      await p.remove('refresh_token');
    } else {
      await p.setString('access_token', access);
      if (refresh != null) await p.setString('refresh_token', refresh);
    }
  }

  Future<void> login(String email, String password) async {
    await api.login(email, password);
    this.email = await api.me();
    await _afterLogin();
  }

  Future<void> register(String email, String password) async {
    await api.register(email, password);
    await login(email, password);
  }

  Future<void> _afterLogin() async {
    _set(SessionState.loggedIn);
    await Future.wait([refreshKiosks(), refreshJobs()]);
    _connectLive();
    _schedulePoll();
  }

  void logout() {
    api.logout();
    _closeLive();
    _poll?.cancel();
    email = null;
    kiosks = const [];
    jobs = const [];
    pickups.clear();
    _set(SessionState.loggedOut);
  }

  void _set(SessionState s) {
    session = s;
    notifyListeners();
  }

  // ---------------------------------------------------------------- data

  Future<void> refreshKiosks() async {
    try {
      kiosks = await api.kiosks();
      kiosksError = null;
    } on ApiException catch (e) {
      kiosksError = e.message;
      if (e.unauthorized) {
        logout();
        return;
      }
    }
    notifyListeners();
  }

  Future<void> refreshJobs() async {
    try {
      jobs = await api.jobs();
      for (final j in jobs.where((j) => j.status == JobStatus.ready && !pickups.containsKey(j.id))) {
        try {
          pickups[j.id] = await api.pickup(j.id);
        } on ApiException {
          // not ready any more; the next refresh will show the new status
        }
      }
      pickups.removeWhere((id, _) => !jobs.any((j) => j.id == id && j.status == JobStatus.ready));
    } on ApiException catch (e) {
      if (e.unauthorized) {
        logout();
        return;
      }
    }
    notifyListeners();
  }

  Job? jobById(int id) {
    for (final j in jobs) {
      if (j.id == id) return j;
    }
    return null;
  }

  Future<Job> submit(Printer printer, List<int> pdf, String filename, PrintOptions o) async {
    final job = await api.createJob(printer.id, pdf, filename, o);
    jobs = [job, ...jobs.where((j) => j.id != job.id)];
    notifyListeners();
    unawaited(refreshKiosks());
    _schedulePoll();
    return job;
  }

  Future<void> cancel(int jobId) async {
    await api.cancel(jobId);
    await Future.wait([refreshJobs(), refreshKiosks()]);
  }

  // ---------------------------------------------------------------- live updates

  void _connectLive() {
    _closeLive();
    final token = api.accessToken;
    if (token == null) return;
    try {
      _socket = WebSocketChannel.connect(Uri.parse(liveUrl(token)));
      _socketSub = _socket!.stream.listen(
        (raw) {
          liveConnected = true;
          final msg = raw is String ? jsonDecode(raw) : null;
          if (msg is Map && msg['type'] == 'job') {
            refreshJobs();
            refreshKiosks();
          } else {
            notifyListeners();
          }
        },
        onDone: _liveLost,
        onError: (_) => _liveLost(),
        cancelOnError: true,
      );
    } catch (_) {
      _liveLost();
    }
  }

  void _liveLost() {
    liveConnected = false;
    notifyListeners();
    _reconnect?.cancel();
    if (session == SessionState.loggedIn) {
      _reconnect = Timer(const Duration(seconds: 5), _connectLive);
    }
  }

  void _closeLive() {
    _reconnect?.cancel();
    _socketSub?.cancel();
    _socket?.sink.close();
    _socket = null;
    liveConnected = false;
  }

  /// Polling is the fallback when the WebSocket is down: fast while a job is moving.
  void _schedulePoll() {
    _poll?.cancel();
    if (session != SessionState.loggedIn) return;
    final busy = jobs.any((j) => j.status.active);
    _poll = Timer(Duration(seconds: busy ? 4 : 20), () async {
      await refreshJobs();
      if (!busy) await refreshKiosks();
      _schedulePoll();
    });
  }

  @override
  void dispose() {
    _closeLive();
    _poll?.cancel();
    super.dispose();
  }
}
