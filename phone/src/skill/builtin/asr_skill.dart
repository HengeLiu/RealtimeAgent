import '../base/skill_base.dart';

class AsrSkill extends SkillBase {
  @override
  String get name => 'asr_skill';

  @override
  String get description => 'Transcribe audio to text on phone.';

  @override
  Map<String, dynamic> get inputSchema => <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'audio_ref': <String, dynamic>{'type': 'string'},
        },
        'required': <String>['audio_ref'],
      };

  @override
  Map<String, dynamic> get outputSchema => <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'text': <String, dynamic>{'type': 'string'},
        },
      };

  @override
  String get mode => 'sync';

  @override
  Future<Map<String, dynamic>> execute(Map<String, dynamic> input) async {
    return <String, dynamic>{'status': 'completed', 'text': ''};
  }
}

