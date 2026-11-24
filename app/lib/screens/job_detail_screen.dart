import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../app_state.dart';
import '../widgets/status.dart';

class JobDetailScreen extends StatelessWidget {
  final AppState state;
  final int jobId;

  const JobDetailScreen({super.key, required this.state, required this.jobId});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final job = state.jobById(jobId);
        return Scaffold(
          appBar: AppBar(title: Text('Job $jobId')),
          body: job == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: state.refreshJobs,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      _Header(job: job),
                      const SizedBox(height: 20),
                      if (job.status == JobStatus.ready) _PickupCard(job: job, pickup: state.pickups[job.id]),
                      if (job.status.active) _Waiting(job: job),
                      if (job.status == JobStatus.collected)
                        const _Message(icon: Icons.check_circle, text: 'Collected. Thank you!'),
                      if (job.status.finishedBadly)
                        _Message(
                          icon: Icons.error_outline,
                          text: switch (job.status) {
                            JobStatus.failed => 'Printing failed${job.error != null ? ': ${job.error}' : '.'} '
                                'Nothing is waiting for you; please print again.',
                            JobStatus.expired => 'Not collected in time. Ask the operator if you still need it.',
                            _ => 'Cancelled.',
                          },
                          error: true,
                        ),
                      if (job.status == JobStatus.queued) ...[
                        const SizedBox(height: 24),
                        OutlinedButton(
                          onPressed: () async {
                            try {
                              await state.cancel(job.id);
                            } on ApiException catch (e) {
                              if (context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
                              }
                            }
                          },
                          child: const Text('Cancel this job'),
                        ),
                      ],
                    ],
                  ),
                ),
        );
      },
    );
  }
}

class _Header extends StatelessWidget {
  final Job job;

  const _Header({required this.job});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final details = [
      '${job.sheets} sheet${job.sheets == 1 ? '' : 's'}',
      '${job.copies} cop${job.copies == 1 ? 'y' : 'ies'}',
      if (job.duplex) 'two-sided',
      if (job.pageRange != null) 'pages ${job.pageRange}',
    ].join(' · ');
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(job.filename, style: theme.textTheme.titleLarge, overflow: TextOverflow.ellipsis),
      const SizedBox(height: 4),
      Text(details, style: theme.textTheme.bodyMedium),
      const SizedBox(height: 16),
      Row(children: [
        StatusIcon(job.status),
        const SizedBox(width: 8),
        Text(job.status.label, style: theme.textTheme.titleMedium),
      ]),
      if (job.status.step >= 0 && job.status.step <= 3) ...[
        const SizedBox(height: 12),
        StatusSteps(job.status),
      ],
    ]);
  }
}

class _Waiting extends StatelessWidget {
  final Job job;

  const _Waiting({required this.job});

  @override
  Widget build(BuildContext context) {
    return const _Message(
      icon: Icons.hourglass_top,
      text: 'You can leave this screen. The QR code and PIN appear here when your documents are '
          'in their compartment.',
    );
  }
}

class _PickupCard extends StatelessWidget {
  final Job job;
  final Pickup? pickup;

  const _PickupCard({required this.job, required this.pickup});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final p = pickup;
    if (p == null) return const Center(child: CircularProgressIndicator());
    final slots = p.compartments.map((s) => '#$s').join(', ');
    final until = p.expiresAt;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(children: [
          Text('Show this at the kiosk', style: theme.textTheme.titleMedium),
          const SizedBox(height: 16),
          Container(
            color: Colors.white, // QR codes need a light background, also in dark mode
            padding: const EdgeInsets.all(12),
            child: QrImageView(data: p.qr, size: 240, backgroundColor: Colors.white),
          ),
          const SizedBox(height: 16),
          Text('or type this PIN', style: theme.textTheme.bodySmall),
          SelectableText(
            p.pin.split('').join(' '),
            style: theme.textTheme.displaySmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: 2),
          ),
          const SizedBox(height: 12),
          Text('Compartment $slots', style: theme.textTheme.titleMedium),
          if (until != null)
            Text('Collect before ${_fmt(until)}', style: theme.textTheme.bodySmall),
          const SizedBox(height: 8),
          ExpansionTile(
            title: Text('QR text (for the simulator)', style: theme.textTheme.bodySmall),
            children: [SelectableText(p.qr, style: theme.textTheme.bodySmall)],
          ),
        ]),
      ),
    );
  }

  static String _fmt(DateTime d) {
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(d.day)}.${two(d.month)}.${d.year} ${two(d.hour)}:${two(d.minute)}';
  }
}

class _Message extends StatelessWidget {
  final IconData icon;
  final String text;
  final bool error;

  const _Message({required this.icon, required this.text, this.error = false});

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).colorScheme;
    return Card(
      color: error ? c.errorContainer : c.surfaceContainerHigh,
      child: ListTile(
        leading: Icon(icon, color: error ? c.onErrorContainer : c.primary),
        title: Text(text),
      ),
    );
  }
}
