import '../base/skill_base.dart';

class SkillRegistry {
  final Map<String, SkillBase> _skills = <String, SkillBase>{};

  void register(SkillBase skill) {
    if (_skills.containsKey(skill.name)) {
      throw StateError('Skill already registered: ${skill.name}');
    }
    _skills[skill.name] = skill;
  }

  SkillBase get(String name) {
    final skill = _skills[name];
    if (skill == null) {
      throw StateError('Unknown skill: $name');
    }
    return skill;
  }

  List<Map<String, dynamic>> listSkills() {
    return _skills.values
        .map(
          (skill) => <String, dynamic>{
            'name': skill.name,
            'description': skill.description,
            'input_schema': skill.inputSchema,
            'output_schema': skill.outputSchema,
            'mode': skill.mode,
          },
        )
        .toList(growable: false);
  }

  Future<Map<String, dynamic>> execute(String name, Map<String, dynamic> input) {
    return get(name).execute(input);
  }
}

