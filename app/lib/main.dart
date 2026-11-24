import 'package:flutter/material.dart';

import 'app_state.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';

void main() {
  final state = AppState()..start();
  runApp(PrintLockerApp(state: state));
}

class PrintLockerApp extends StatelessWidget {
  final AppState state;

  const PrintLockerApp({super.key, required this.state});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFF2F6FD6);
    return MaterialApp(
      title: 'PrintLocker',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: seed, useMaterial3: true, brightness: Brightness.light),
      darkTheme: ThemeData(colorSchemeSeed: seed, useMaterial3: true, brightness: Brightness.dark),
      home: ListenableBuilder(
        listenable: state,
        builder: (context, _) => switch (state.session) {
          SessionState.starting => const Scaffold(body: Center(child: CircularProgressIndicator())),
          SessionState.loggedOut => LoginScreen(state: state),
          SessionState.loggedIn => HomeScreen(state: state),
        },
      ),
    );
  }
}
