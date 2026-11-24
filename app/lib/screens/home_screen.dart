import 'package:flutter/material.dart';

import '../app_state.dart';
import 'jobs_screen.dart';
import 'kiosks_screen.dart';

class HomeScreen extends StatefulWidget {
  final AppState state;

  const HomeScreen({super.key, required this.state});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _tab = 0;

  void showJobs() => setState(() => _tab = 1);

  @override
  Widget build(BuildContext context) {
    final s = widget.state;
    final ready = s.jobs.where((j) => s.pickups.containsKey(j.id)).length;
    return Scaffold(
      appBar: AppBar(
        title: Text(_tab == 0 ? 'Print' : 'My jobs'),
        actions: [
          PopupMenuButton<String>(
            icon: const Icon(Icons.account_circle_outlined),
            onSelected: (v) {
              if (v == 'logout') s.logout();
            },
            itemBuilder: (_) => [
              PopupMenuItem(enabled: false, child: Text(s.email ?? '')),
              const PopupMenuItem(value: 'logout', child: Text('Log out')),
            ],
          ),
        ],
      ),
      body: IndexedStack(
        index: _tab,
        children: [
          KiosksScreen(state: s, onJobCreated: showJobs),
          JobsScreen(state: s),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: [
          const NavigationDestination(icon: Icon(Icons.print_outlined), selectedIcon: Icon(Icons.print), label: 'Print'),
          NavigationDestination(
            icon: Badge(isLabelVisible: ready > 0, label: Text('$ready'), child: const Icon(Icons.inbox_outlined)),
            selectedIcon: const Icon(Icons.inbox),
            label: 'My jobs',
          ),
        ],
      ),
    );
  }
}
