import 'package:flutter/material.dart';

import '../api/models.dart';

class StatusIcon extends StatelessWidget {
  final JobStatus status;

  const StatusIcon(this.status, {super.key});

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).colorScheme;
    final (IconData icon, Color color) = switch (status) {
      JobStatus.ready => (Icons.qr_code_2, Colors.green),
      JobStatus.collected => (Icons.check_circle_outline, c.outline),
      JobStatus.failed || JobStatus.expired || JobStatus.cancelled => (Icons.error_outline, c.error),
      _ => (Icons.hourglass_top, c.primary),
    };
    return Icon(icon, color: color);
  }
}

/// Four-step progress: waiting · printing · moving · ready.
class StatusSteps extends StatelessWidget {
  final JobStatus status;

  const StatusSteps(this.status, {super.key});

  static const labels = ['Waiting', 'Printing', 'Moving', 'Ready'];

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final step = status.step;
    return Row(
      children: [
        for (var i = 0; i < labels.length; i++)
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(right: i < labels.length - 1 ? 6 : 0),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 300),
                  height: 6,
                  decoration: BoxDecoration(
                    color: i <= step ? theme.colorScheme.primary : theme.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(3),
                  ),
                ),
                const SizedBox(height: 4),
                Text(labels[i],
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: i == step ? theme.colorScheme.primary : theme.colorScheme.onSurfaceVariant,
                      fontWeight: i == step ? FontWeight.w600 : null,
                    )),
              ]),
            ),
          ),
      ],
    );
  }
}
