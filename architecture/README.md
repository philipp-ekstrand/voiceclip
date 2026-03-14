# VoiceClip Architecture Documentation

## Overview

VoiceClip is a macOS menubar app for local speech-to-text transcription. It uses whisper.cpp with the `large-v3` model running entirely on-device (Apple Silicon, Metal GPU). The app supports streaming transcription during recording for near-instant results.

## Documentation Index

| Document | Description |
|----------|-------------|
| [STATE_MACHINE.md](STATE_MACHINE.md) | All UI states, transitions, session tracking, error recovery |
| [AUDIO_PIPELINE.md](AUDIO_PIPELINE.md) | Audio capture, two modes (streaming + HQ), data flow |
| [STREAMING.md](STREAMING.md) | Streaming architecture, chunk processing, quality tuning, chain prompting |
| [BUILD_RELEASE.md](BUILD_RELEASE.md) | Build with PyInstaller, code signing, notarization, deployment |
| [CONFIGURATION.md](CONFIGURATION.md) | Environment variables, runtime files, QSettings, defaults |

## Class Map

```
main.py (~3300 lines, single-file app)
├── VoiceClipApp              Tray icon, menubar, system events, app lifecycle
├── VoiceClipWidget           Main widget, state machine, all UI logic
├── AudioRecorder             Audio capture (in-process streaming or subprocess HQ)
├── StreamingTranscriptionController   Chunk queue, worker thread, chain prompting
├── TranscriptAssembler       Merges overlapping chunk transcripts (token-level dedup)
├── WhisperServerProcessManager        Lifecycle: spawn, health check, warmup, cleanup
├── SingleInstanceGuard       Qt local socket, prevents duplicate app instances
├── TranscribeThread          QThread: whisper-cli subprocess (HQ fallback)
├── RemoteTranscribeThread    QThread: HTTP POST to remote server (optional)
├── ModelDownloadThread       QThread: downloads missing model files
├── ServerWarmupThread        QThread: warms up whisper-server after start
├── FinalizeRecordingThread   QThread: assembles audio chunks into WAV
└── StreamFinalizeThread      QThread: finalizes streaming mode after stop
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| GUI | Python 3 + PyQt6 |
| Audio capture | sounddevice (PortAudio) |
| Transcription | whisper.cpp (whisper-server + whisper-cli) |
| Model | ggml-large-v3.bin (2.9 GB, full precision) |
| GPU | Apple Metal (flash attention) |
| Packaging | PyInstaller (.app bundle) |
| Clipboard | pyperclip / pbcopy |
| Platform | macOS only (Apple Silicon) |

## High-Level Data Flow

```
         Streaming Mode (default)                    HQ Mode (fallback)
         ========================                    ==================

User clicks tray                              User clicks tray
         │                                            │
         ▼                                            ▼
   In-process InputStream                     Subprocess → WAV file
   (sounddevice callback)                     (direct write)
         │                                            │
    ┌────┴────┐                                       │
    │ 120ms   │ timer                                 │
    │ poll    │                                       │
    ▼         │                               User clicks stop
  Samples → StreamingController                       │
    │         │                                       ▼
    ▼         │                               FinalizeRecordingThread
  5s chunks   │                                       │
    │         │                                       ▼
    ▼         │                               TranscribeThread
  whisper-server (/inference)                 (whisper-cli subprocess)
    │         │                                       │
    ▼         │                                       ▼
  TranscriptAssembler                          Full transcript
    │         │                                       │
    └────┬────┘                                       │
         │                                            │
User clicks stop                                      │
         │                                            │
    Last chunk → ~2-3s                                │
         │                                            │
         ▼                                            ▼
   Merged transcript                           Transcript
         │                                            │
         ▼                                            ▼
      Clipboard                                  Clipboard
```
