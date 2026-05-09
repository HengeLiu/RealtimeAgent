# Repository Guidelines

## Project Structure & Module Organization

This repository is organized around the `audio-chat` server-side Python SDK and runnable device examples. Core SDK code lives in `audio-chat-sdk/audio_chat/`, while app-level examples start in `app-examples/for-blind-app/`. Reference device implementations are under `device-examples/` for browser glass, Python glass, Python phone, native iOS phone, and ESP32-S3 glass. Use `tests/` for automated tests, `testdata/` for contract and playback fixtures, `scripts/` for acceptance helpers, and `docs/` for architecture and integration notes. Treat `legacy/` as migration reference only, not the default development entry point.

## Build, Test, and Development Commands

Prepare a local Python 3.11 environment:

```bash
uv sync --python 3.11
uv pip install -e .
```

Run the main example server:

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

Validate a device capability file:

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml
```

Open the browser glass reference client:

```bash
uv run audio-chat.web.open --print-url
```

Run tests:

```bash
uv run python -m pytest
uv run python -m pytest tests/test_text_route_audio_samples.py -q
```

## Coding Style & Naming Conventions

Use Python 3.11+ and follow existing package boundaries. Public SDK modules use the `audio_chat` import namespace; CLI entry points use the `audio-chat.*` naming pattern. Keep Tool and Task logic inside app capability packages and access devices through Context APIs rather than internal WebSocket state. Use Chinese comments/docstrings for new classes, functions, and tests, explaining purpose, key logic, parameters, return values, and important exceptions.

## Testing Guidelines

Tests use `pytest`; `pyproject.toml` sets `tests/` as the default test path and adds `audio-chat-sdk` to `pythonpath`. Name tests `test_*.py` and keep fixtures in `tests/fixtures/` or `testdata/`. For cross-device behavior, include a reproducible local flow: sync or validate config, start the server, start browser/Python phone/glass playback as needed, then inspect logs, `/api/health`, `/api/debug/devices`, and generated files under `runs/`.

## Commit & Pull Request Guidelines

Recent commit history uses concise Chinese commit messages, for example `维护文档一致性` and `补齐端侧supports能力声明`. Keep commits focused and do not push branches directly to remote. PRs should describe the behavior change, list test commands and results, mention affected devices or protocols, and link any updated docs. Include screenshots or logs when UI, playback, iOS, browser, or ESP32 behavior changes.

## Security & Configuration Tips

Do not commit API keys, device tokens, Wi-Fi credentials, local `.env` files, generated logs, build outputs, or real user media. If a new tool creates caches or artifacts, update `.gitignore` in the same change.
