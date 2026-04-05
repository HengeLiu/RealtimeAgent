import 'package:flutter_test/flutter_test.dart';

import 'package:nextgen_phone_flutter/main.dart';

void main() {
  testWidgets('phone runtime app renders smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const NextgenPhoneApp());

    expect(find.text('Nextgen Phone Runtime'), findsOneWidget);
    expect(find.text('启动手机端通信壳'), findsOneWidget);
  });
}
