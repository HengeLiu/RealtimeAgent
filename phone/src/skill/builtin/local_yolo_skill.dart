import '../base/skill_base.dart';

class LocalYoloSkill extends SkillBase {
  @override
  String get name => 'local_yolo_skill';

  @override
  String get description => 'Run local YOLO detection on phone.';

  @override
  Map<String, dynamic> get inputSchema => <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'frame_ref': <String, dynamic>{'type': 'string'},
        },
        'required': <String>['frame_ref'],
      };

  @override
  Map<String, dynamic> get outputSchema => <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'detections': <String, dynamic>{'type': 'array'},
        },
      };

  @override
  String get mode => 'sync';

  @override
  Future<Map<String, dynamic>> execute(Map<String, dynamic> input) async {
    return <String, dynamic>{
      'status': 'completed',
      'detections': <Map<String, dynamic>>[],
    };
  }
}

