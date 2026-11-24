import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../app_state.dart';

class LoginScreen extends StatefulWidget {
  final AppState state;

  const LoginScreen({super.key, required this.state});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _register = false;
  bool _busy = false;
  bool _slow = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _slow = false;
      _error = null;
    });
    // A sleeping free server takes up to a minute to answer the first request.
    Future.delayed(const Duration(seconds: 4), () {
      if (mounted && _busy) setState(() => _slow = true);
    });
    try {
      if (_register) {
        await widget.state.register(_email.text, _password.text);
      } else {
        await widget.state.login(_email.text, _password.text);
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Form(
                key: _form,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Icon(Icons.print_outlined, size: 56, color: theme.colorScheme.primary),
                    const SizedBox(height: 12),
                    Text('PrintLocker', textAlign: TextAlign.center, style: theme.textTheme.headlineMedium),
                    const SizedBox(height: 4),
                    Text(
                      'Print here, pick up at the kiosk with your QR code.',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                    ),
                    const SizedBox(height: 32),
                    TextFormField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      decoration: const InputDecoration(labelText: 'Email', border: OutlineInputBorder()),
                      validator: (v) => (v == null || !v.contains('@')) ? 'Enter your email' : null,
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _password,
                      obscureText: true,
                      autofillHints: const [AutofillHints.password],
                      decoration: const InputDecoration(labelText: 'Password', border: OutlineInputBorder()),
                      validator: (v) {
                        if (v == null || v.isEmpty) return 'Enter your password';
                        if (_register && v.length < 8) return 'At least 8 characters';
                        return null;
                      },
                      onFieldSubmitted: (_) => _submit(),
                    ),
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                      child: _busy
                          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                          : Text(_register ? 'Create account' : 'Log in'),
                    ),
                    if (_slow) ...[
                      const SizedBox(height: 8),
                      Text('Waking up the server, this can take up to a minute…',
                          textAlign: TextAlign.center, style: theme.textTheme.bodySmall),
                    ],
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: _busy ? null : () => setState(() => _register = !_register),
                      child: Text(_register ? 'I already have an account' : 'Create a new account'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
