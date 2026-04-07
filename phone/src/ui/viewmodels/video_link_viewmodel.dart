class VideoLinkViewModel {
  VideoLinkViewModel({
    required this.linkId,
    required this.status,
  });

  final String linkId;
  String status;

  void markReady() {
    status = 'ready';
  }

  void markBroken() {
    status = 'broken';
  }
}

