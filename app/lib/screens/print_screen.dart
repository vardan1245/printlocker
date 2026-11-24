import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../app_state.dart';
import 'job_detail_screen.dart';

class PrintScreen extends StatefulWidget {
  final AppState state;
  final Kiosk kiosk;
  final Printer printer;

  const PrintScreen({super.key, required this.state, required this.kiosk, required this.printer});

  @override
  State<PrintScreen> createState() => _PrintScreenState();
}

class _PrintScreenState extends State<PrintScreen> {
  static const maxBytes = 50 * 1024 * 1024;

  List<int>? _pdf;
  String _filename = '';
  PrintOptions _opts = const PrintOptions();
  final _range = TextEditingController();
  Preview? _preview;
  String? _error;
  bool _checking = false;
  bool _sending = false;
  Timer? _debounce;

  @override
  void dispose() {
    _range.dispose();
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _pick() async {
    final res = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf'],
      withData: true,
    );
    final f = res?.files.single;
    if (f == null) return;
    if (f.bytes == null) {
      setState(() => _error = 'Could not read this file.');
      return;
    }
    if (f.size > maxBytes) {
      setState(() => _error = 'The file is larger than 50 MB.');
      return;
    }
    setState(() {
      _pdf = f.bytes;
      _filename = f.name;
      _preview = null;
      _error = null;
    });
    _check();
  }

  void _changed(PrintOptions o) {
    setState(() => _opts = o);
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 600), _check);
  }

  Future<void> _check() async {
    if (_pdf == null) return;
    setState(() {
      _checking = true;
      _error = null;
    });
    try {
      final p = await widget.state.api.preview(widget.printer.id, _pdf!, _filename, _opts);
      if (mounted) setState(() => _preview = p);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _preview = null;
          _error = e.message;
        });
      }
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  Future<void> _print() async {
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final job = await widget.state.submit(widget.printer, _pdf!, _filename, _opts);
      if (!mounted) return;
      final nav = Navigator.of(context);
      nav.pop(true);
      unawaited(nav.push(MaterialPageRoute(
        builder: (_) => JobDetailScreen(state: widget.state, jobId: job.id),
      )));
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final p = _preview;
    final canPrint = _pdf != null && p != null && p.canPrint && !_checking && !_sending;
    return Scaffold(
      appBar: AppBar(title: Text('${widget.kiosk.name} · ${widget.printer.name}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          OutlinedButton.icon(
            onPressed: _sending ? null : _pick,
            icon: const Icon(Icons.upload_file),
            label: Text(_pdf == null ? 'Choose a PDF' : 'Choose another PDF'),
            style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
          ),
          if (_pdf != null)
            ListTile(
              leading: const Icon(Icons.picture_as_pdf_outlined),
              title: Text(_filename, overflow: TextOverflow.ellipsis),
              subtitle: Text('${(_pdf!.length / 1024).ceil()} KB${p != null ? ' · ${p.pages} pages' : ''}'),
            ),
          const SizedBox(height: 16),
          Text('Options', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          Row(children: [
            const Expanded(child: Text('Copies')),
            IconButton.outlined(
              onPressed: _opts.copies > 1 ? () => _changed(_opts.copyWith(copies: _opts.copies - 1)) : null,
              icon: const Icon(Icons.remove),
            ),
            SizedBox(
              width: 40,
              child: Text('${_opts.copies}', textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
            ),
            IconButton.outlined(
              onPressed: _opts.copies < 50 ? () => _changed(_opts.copyWith(copies: _opts.copies + 1)) : null,
              icon: const Icon(Icons.add),
            ),
          ]),
          const SizedBox(height: 12),
          TextField(
            controller: _range,
            decoration: const InputDecoration(
              labelText: 'Pages (optional)',
              hintText: 'e.g. 1-3, 5',
              helperText: 'Leave empty to print all pages',
              border: OutlineInputBorder(),
            ),
            onChanged: (v) => _changed(_opts.copyWith(pageRange: v)),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Two-sided'),
            subtitle: widget.printer.duplex ? null : const Text('Not available on this printer'),
            value: _opts.duplex,
            onChanged: widget.printer.duplex ? (v) => _changed(_opts.copyWith(duplex: v)) : null,
          ),
          const SizedBox(height: 8),
          if (_checking) const LinearProgressIndicator(),
          if (p != null && !_checking) _Summary(p),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
            ),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: canPrint ? _print : null,
            icon: _sending
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.print),
            label: const Text('Print'),
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(52)),
          ),
        ],
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  final Preview p;

  const _Summary(this.p);

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final ok = p.canPrint;
    return Card(
      color: ok ? theme.colorScheme.secondaryContainer : theme.colorScheme.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${p.sheets} sheet${p.sheets == 1 ? '' : 's'} of paper', style: theme.textTheme.titleMedium),
          const SizedBox(height: 4),
          Text('${p.selectedPages} of ${p.pages} pages selected · '
              '${p.compartmentsNeeded} compartment${p.compartmentsNeeded == 1 ? '' : 's'} needed'),
          if (!ok)
            Text('Only ${p.compartmentsFree} compartment(s) free at this printer right now.',
                style: TextStyle(color: theme.colorScheme.onErrorContainer)),
        ]),
      ),
    );
  }
}
