abstract class SkillBase {
  String get name;
  String get description;
  Map<String, dynamic> get inputSchema;
  Map<String, dynamic> get outputSchema;
  String get mode;

  Future<Map<String, dynamic>> execute(Map<String, dynamic> input);
}
