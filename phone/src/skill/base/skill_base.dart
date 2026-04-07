abstract class SkillBase {
  String get name;
  String get description;

  Future<Map<String, dynamic>> execute(Map<String, dynamic> input);
}
