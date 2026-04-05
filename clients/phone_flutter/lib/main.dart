import 'package:flutter/material.dart';

import 'screens/phone_runtime_screen.dart';

void main() {
  runApp(const NextgenPhoneApp());
}

class NextgenPhoneApp extends StatelessWidget {
  const NextgenPhoneApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Nextgen Phone Runtime',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF0F766E)),
        useMaterial3: true,
      ),
      home: const PhoneRuntimeScreen(),
    );
  }
}
