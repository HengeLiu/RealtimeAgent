import 'package:flutter_test/flutter_test.dart';

import 'package:ai_glasses_test_app/main.dart';

void main() {
  testWidgets('app starts with test title', (WidgetTester tester) async {
    await tester.pumpWidget(const AiGlassesTestApp());
    expect(find.text('AI眼镜三端直连测试'), findsOneWidget);
  });
}
