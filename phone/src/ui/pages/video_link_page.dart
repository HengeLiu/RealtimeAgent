import 'package:flutter/material.dart';

class VideoLinkPage extends StatelessWidget {
  const VideoLinkPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Video Link Debug')),
      body: const Center(
        child: Text('等待眼镜视频流...'),
      ),
    );
  }
}
