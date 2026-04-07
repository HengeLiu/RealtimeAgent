import '../base/task_base.dart';

class VideoLinkReceiverTask extends TaskBase {
  final Map<String, dynamic> _result = <String, dynamic>{};

  @override
  String get taskType => 'phone_video_link';

  @override
  void validateInput(Map<String, dynamic> input) {
    if (input['link_id'] == null) {
      throw ArgumentError('link_id is required');
    }
  }

  @override
  Future<void> prepare() async {}

  @override
  Future<Map<String, dynamic>> run() async {
    _result
      ..clear()
      ..addAll(<String, dynamic>{'status': 'running'});
    return _result;
  }

  @override
  Future<void> pause() async {}

  @override
  Future<void> resume() async {}

  @override
  Map<String, dynamic> buildResult() {
    return Map<String, dynamic>.from(_result);
  }

  @override
  Future<void> cancel() async {}
}
