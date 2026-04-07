import '../base/task_base.dart';

class VideoLinkReceiverTask extends TaskBase {
  @override
  String get taskType => 'phone_video_link';

  @override
  Future<void> prepare() async {}

  @override
  Future<Map<String, dynamic>> run() async {
    return {'status': 'running'};
  }

  @override
  Future<void> cancel() async {}
}
