# audio-chat

`audio-chat` is the new server-side Python SDK for stream-based voice sessions.

This first phase implements the minimal playback loop described in
`docs/audio-chat-sdk-architecture.md`:

1. device registration and event subscriptions;
2. `sensor.mic` input stream ingestion;
3. mock `TextAgentCore`;
4. mock Streaming TTS;
5. `actuator.speaker` output stream delivery through Playback Arbiter.
