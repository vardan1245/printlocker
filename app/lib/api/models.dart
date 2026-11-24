/// Data classes matching the backend's JSON (see backend/app/schemas.py).
library;

DateTime? _date(Object? v) {
  if (v is! String || v.isEmpty) return null;
  // The API sends UTC; SQLite-backed servers may omit the zone.
  final s = (v.endsWith('Z') || v.contains('+')) ? v : '${v}Z';
  return DateTime.tryParse(s)?.toLocal();
}

class Printer {
  final int id;
  final String name;
  final bool duplex;
  final String status;
  final bool paperOk;
  final int compartmentsTotal;
  final int compartmentsFree;

  const Printer({
    required this.id,
    required this.name,
    required this.duplex,
    required this.status,
    required this.paperOk,
    required this.compartmentsTotal,
    required this.compartmentsFree,
  });

  bool get available => status == 'ok' && paperOk && compartmentsFree > 0;

  factory Printer.fromJson(Map<String, dynamic> j) => Printer(
        id: j['id'] as int,
        name: j['name'] as String,
        duplex: j['duplex'] as bool? ?? false,
        status: j['status'] as String? ?? 'ok',
        paperOk: j['paper_ok'] as bool? ?? true,
        compartmentsTotal: j['compartments_total'] as int? ?? 0,
        compartmentsFree: j['compartments_free'] as int? ?? 0,
      );
}

class Kiosk {
  final int id;
  final String name;
  final String location;
  final String status;
  final List<Printer> printers;

  const Kiosk({
    required this.id,
    required this.name,
    required this.location,
    required this.status,
    required this.printers,
  });

  bool get online => status == 'online';

  factory Kiosk.fromJson(Map<String, dynamic> j) => Kiosk(
        id: j['id'] as int,
        name: j['name'] as String,
        location: j['location'] as String? ?? '',
        status: j['status'] as String? ?? 'offline',
        printers: [
          for (final p in (j['printers'] as List? ?? const [])) Printer.fromJson(p as Map<String, dynamic>),
        ],
      );
}

enum JobStatus {
  uploaded,
  queued,
  printing,
  delivering,
  ready,
  collected,
  failed,
  expired,
  cancelled,
  unknown;

  static JobStatus parse(String? s) =>
      JobStatus.values.firstWhere((v) => v.name == s, orElse: () => JobStatus.unknown);

  bool get active => this == queued || this == printing || this == delivering || this == uploaded;
  bool get finishedBadly => this == failed || this == expired || this == cancelled;

  String get label => switch (this) {
        JobStatus.uploaded || JobStatus.queued => 'Waiting for printer',
        JobStatus.printing => 'Printing',
        JobStatus.delivering => 'Moving to compartment',
        JobStatus.ready => 'Ready for pickup',
        JobStatus.collected => 'Collected',
        JobStatus.failed => 'Failed',
        JobStatus.expired => 'Expired',
        JobStatus.cancelled => 'Cancelled',
        JobStatus.unknown => 'Unknown',
      };

  /// Position in the happy path, for the progress bar (0..4), or -1.
  int get step => switch (this) {
        JobStatus.uploaded || JobStatus.queued => 0,
        JobStatus.printing => 1,
        JobStatus.delivering => 2,
        JobStatus.ready => 3,
        JobStatus.collected => 4,
        _ => -1,
      };
}

class Job {
  final int id;
  final int kioskId;
  final int printerId;
  final String filename;
  final int pages;
  final String? pageRange;
  final int selectedPages;
  final int copies;
  final bool duplex;
  final int sheets;
  final JobStatus status;
  final String? error;
  final DateTime? createdAt;
  final DateTime? expiresAt;
  final List<int> slots;

  const Job({
    required this.id,
    required this.kioskId,
    required this.printerId,
    required this.filename,
    required this.pages,
    required this.pageRange,
    required this.selectedPages,
    required this.copies,
    required this.duplex,
    required this.sheets,
    required this.status,
    required this.error,
    required this.createdAt,
    required this.expiresAt,
    required this.slots,
  });

  factory Job.fromJson(Map<String, dynamic> j) => Job(
        id: j['id'] as int,
        kioskId: j['kiosk_id'] as int,
        printerId: j['printer_id'] as int,
        filename: j['original_filename'] as String? ?? 'document.pdf',
        pages: j['pages'] as int? ?? 0,
        pageRange: j['page_range'] as String?,
        selectedPages: j['selected_pages'] as int? ?? 0,
        copies: j['copies'] as int? ?? 1,
        duplex: j['duplex'] as bool? ?? false,
        sheets: j['sheets'] as int? ?? 0,
        status: JobStatus.parse(j['status'] as String?),
        error: j['error'] as String?,
        createdAt: _date(j['created_at']),
        expiresAt: _date(j['expires_at']),
        slots: [
          for (final c in (j['compartments'] as List? ?? const []))
            (c as Map<String, dynamic>)['compartment_slot'] as int,
        ],
      );
}

class Pickup {
  final String qr;
  final String pin;
  final List<int> compartments;
  final DateTime? expiresAt;

  const Pickup({required this.qr, required this.pin, required this.compartments, this.expiresAt});

  factory Pickup.fromJson(Map<String, dynamic> j) => Pickup(
        qr: j['qr'] as String,
        pin: j['pin'] as String,
        compartments: [for (final c in (j['compartments'] as List? ?? const [])) c as int],
        expiresAt: _date(j['expires_at']),
      );
}

class Preview {
  final int pages;
  final int selectedPages;
  final int sheets;
  final int compartmentsNeeded;
  final int compartmentsFree;
  final bool canPrint;

  const Preview({
    required this.pages,
    required this.selectedPages,
    required this.sheets,
    required this.compartmentsNeeded,
    required this.compartmentsFree,
    required this.canPrint,
  });

  factory Preview.fromJson(Map<String, dynamic> j) => Preview(
        pages: j['pages'] as int,
        selectedPages: j['selected_pages'] as int,
        sheets: j['sheets'] as int,
        compartmentsNeeded: j['compartments_needed'] as int,
        compartmentsFree: j['compartments_free'] as int,
        canPrint: j['can_print'] as bool,
      );
}

/// What the user chose on the print screen.
class PrintOptions {
  final int copies;
  final String pageRange;
  final bool duplex;

  const PrintOptions({this.copies = 1, this.pageRange = '', this.duplex = false});

  PrintOptions copyWith({int? copies, String? pageRange, bool? duplex}) => PrintOptions(
        copies: copies ?? this.copies,
        pageRange: pageRange ?? this.pageRange,
        duplex: duplex ?? this.duplex,
      );
}
