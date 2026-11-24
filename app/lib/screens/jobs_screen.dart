import 'package:flutter/material.dart';

import '../api/models.dart';
import '../app_state.dart';
import '../widgets/status.dart';
import 'job_detail_screen.dart';

class JobsScreen extends StatelessWidget {
  final AppState state;

  const JobsScreen({super.key, required this.state});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final jobs = state.jobs;
        return RefreshIndicator(
          onRefresh: state.refreshJobs,
          child: jobs.isEmpty
              ? ListView(children: const [
                  SizedBox(height: 120),
                  Center(child: Text('No print jobs yet.')),
                ])
              : ListView.separated(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  itemCount: jobs.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, i) {
                    final j = jobs[i];
                    final slots = j.slots.map((s) => '#$s').join(', ');
                    return ListTile(
                      leading: StatusIcon(j.status),
                      title: Text(j.filename, overflow: TextOverflow.ellipsis),
                      subtitle: Text('${j.status.label}'
                          '${j.status == JobStatus.ready && slots.isNotEmpty ? ' · compartment $slots' : ''}'
                          ' · ${j.sheets} sheet${j.sheets == 1 ? '' : 's'}'),
                      trailing: j.status == JobStatus.ready
                          ? Icon(Icons.qr_code_2, color: theme.colorScheme.primary)
                          : const Icon(Icons.chevron_right),
                      onTap: () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => JobDetailScreen(state: state, jobId: j.id),
                      )),
                    );
                  },
                ),
        );
      },
    );
  }
}
