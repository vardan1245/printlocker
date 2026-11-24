import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:printlocker_app/api/api_client.dart';
import 'package:printlocker_app/api/models.dart';
import 'package:printlocker_app/app_state.dart';
import 'package:printlocker_app/main.dart';
import 'package:shared_preferences/shared_preferences.dart';

const jobJson = {
  'id': 7,
  'kiosk_id': 1,
  'printer_id': 2,
  'original_filename': 'thesis.pdf',
  'pages': 10,
  'page_range': '1-3',
  'selected_pages': 3,
  'copies': 2,
  'duplex': true,
  'sheets': 4,
  'status': 'ready',
  'error': null,
  'created_at': '2026-11-24T10:00:00',
  'ready_at': '2026-11-24T10:01:00',
  'expires_at': '2026-11-25T10:01:00',
  'collected_at': null,
  'compartments': [
    {'part_no': 1, 'sheets': 4, 'compartment_slot': 3}
  ],
};

http.Response json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

void main() {
  group('models', () {
    test('job parses and maps status', () {
      final j = Job.fromJson(jobJson);
      expect(j.status, JobStatus.ready);
      expect(j.slots, [3]);
      expect(j.expiresAt!.isUtc, isFalse); // shown in local time
      expect(JobStatus.parse('delivering').label, 'Moving to compartment');
      expect(JobStatus.parse('weird'), JobStatus.unknown);
      expect(JobStatus.queued.active, isTrue);
      expect(JobStatus.collected.step, 4);
    });

    test('kiosk and printer availability', () {
      final k = Kiosk.fromJson({
        'id': 1,
        'name': 'Lobby',
        'location': 'Main',
        'status': 'online',
        'last_seen_at': null,
        'printers': [
          {'id': 1, 'name': 'A', 'duplex': true, 'status': 'ok', 'paper_ok': true, 'toner_ok': true,
           'compartments_total': 5, 'compartments_free': 0},
          {'id': 2, 'name': 'B', 'duplex': true, 'status': 'ok', 'paper_ok': true, 'toner_ok': true,
           'compartments_total': 5, 'compartments_free': 2},
        ],
      });
      expect(k.online, isTrue);
      expect(k.printers.map((p) => p.available), [false, true]);
    });
  });

  group('api client', () {
    test('login stores tokens and sends bearer', () async {
      final seen = <String>[];
      final api = ApiClient('http://api', client: MockClient((r) async {
        seen.add('${r.method} ${r.url.path} ${r.headers['authorization'] ?? ''}');
        if (r.url.path == '/auth/login') return json({'access_token': 'A1', 'refresh_token': 'R1'});
        return json([jobJson]);
      }));
      String? saved;
      api.onTokens = (a, _) => saved = a;
      await api.login(' me@x.am ', 'pw');
      final jobs = await api.jobs();
      expect(saved, 'A1');
      expect(jobs.single.id, 7);
      expect(seen.last, 'GET /jobs Bearer A1');
    });

    test('expired token is refreshed once', () async {
      var calls = 0;
      final api = ApiClient('http://api', client: MockClient((r) async {
        if (r.url.path == '/auth/refresh') return json({'access_token': 'A2', 'refresh_token': 'R2'});
        calls++;
        return r.headers['authorization'] == 'Bearer A2' ? json([]) : json({'detail': 'expired'}, 401);
      }))
        ..accessToken = 'A1'
        ..refreshToken = 'R1';
      expect(await api.jobs(), isEmpty);
      expect(calls, 2);
      expect(api.accessToken, 'A2');
    });

    test('server error messages are shown as-is', () async {
      final api = ApiClient('http://api', client: MockClient((r) async {
        if (r.url.path == '/jobs') return json({'detail': 'not enough free compartments'}, 409);
        return json({'detail': [{'msg': 'field required'}]}, 422);
      }));
      await expectLater(
        () => api.createJob(1, [1, 2, 3], 'a.pdf', const PrintOptions()),
        throwsA(isA<ApiException>().having((e) => e.message, 'message', 'not enough free compartments')),
      );
      await expectLater(
        () => api.kiosks(),
        throwsA(isA<ApiException>().having((e) => e.message, 'message', 'field required')),
      );
    });

    test('upload is multipart with options', () async {
      late String body;
      final api = ApiClient('http://api', client: MockClient((r) async {
        body = r.body;
        return json(jobJson, 201);
      }));
      await api.createJob(2, utf8.encode('%PDF-1.4'), 'a.pdf',
          const PrintOptions(copies: 2, pageRange: '1-3', duplex: true));
      expect(body, contains('name="printer_id"'));
      expect(body, contains('name="page_range"'));
      expect(body, contains('1-3'));
      expect(body, contains('filename="a.pdf"'));
    });

    test('unreachable server gives a friendly message', () async {
      final api = ApiClient('http://api', client: MockClient((r) async => throw Exception('offline')));
      await expectLater(() => api.kiosks(), throwsA(isA<ApiException>().having((e) => e.status, 'status', 0)));
    });
  });

  testWidgets('logged-out app shows the login screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final state = AppState(api: ApiClient('http://api', client: MockClient((r) async => json({}))));
    await state.start();
    await tester.pumpWidget(PrintLockerApp(state: state));
    expect(find.text('PrintLocker'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Log in'), findsOneWidget);
  });
}
