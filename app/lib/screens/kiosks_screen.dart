import 'package:flutter/material.dart';

import '../api/models.dart';
import '../app_state.dart';
import 'print_screen.dart';

class KiosksScreen extends StatelessWidget {
  final AppState state;
  final VoidCallback onJobCreated;

  const KiosksScreen({super.key, required this.state, required this.onJobCreated});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final kiosks = state.kiosks;
        return RefreshIndicator(
          onRefresh: state.refreshKiosks,
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Text('Choose a printer', style: theme.textTheme.titleMedium),
              const SizedBox(height: 4),
              Text('Your document is printed there and waits in a locked compartment.',
                  style: theme.textTheme.bodySmall),
              const SizedBox(height: 12),
              if (state.kiosksError != null)
                Card(
                  child: ListTile(
                    leading: Icon(Icons.wifi_off, color: theme.colorScheme.error),
                    title: Text(state.kiosksError!),
                    trailing: TextButton(onPressed: state.refreshKiosks, child: const Text('Retry')),
                  ),
                ),
              if (kiosks.isEmpty && state.kiosksError == null)
                const Padding(
                  padding: EdgeInsets.all(32),
                  child: Center(child: Text('No kiosks yet.')),
                ),
              for (final k in kiosks) _KioskCard(kiosk: k, state: state, onJobCreated: onJobCreated),
            ],
          ),
        );
      },
    );
  }
}

class _KioskCard extends StatelessWidget {
  final Kiosk kiosk;
  final AppState state;
  final VoidCallback onJobCreated;

  const _KioskCard({required this.kiosk, required this.state, required this.onJobCreated});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final online = kiosk.online;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(Icons.circle, size: 10, color: online ? Colors.green : theme.colorScheme.error),
              const SizedBox(width: 8),
              Expanded(child: Text(kiosk.name, style: theme.textTheme.titleMedium)),
              Text(online ? 'online' : 'offline', style: theme.textTheme.bodySmall),
            ]),
            if (kiosk.location.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 18, top: 2),
                child: Text(kiosk.location, style: theme.textTheme.bodySmall),
              ),
            const SizedBox(height: 6),
            for (final p in kiosk.printers)
              ListTile(
                contentPadding: const EdgeInsets.only(left: 18),
                leading: const Icon(Icons.print_outlined),
                title: Text(p.name),
                subtitle: Text(_printerLine(p)),
                trailing: const Icon(Icons.chevron_right),
                enabled: p.available,
                onTap: () async {
                  final created = await Navigator.of(context).push<bool>(MaterialPageRoute(
                    builder: (_) => PrintScreen(state: state, kiosk: kiosk, printer: p),
                  ));
                  if (created == true) onJobCreated();
                },
              ),
          ],
        ),
      ),
    );
  }

  static String _printerLine(Printer p) {
    if (p.status != 'ok') return 'Out of order right now';
    if (!p.paperOk) return 'Out of paper';
    final duplex = p.duplex ? ' · two-sided' : '';
    return '${p.compartmentsFree} of ${p.compartmentsTotal} compartments free$duplex';
  }
}
