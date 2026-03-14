# Configuration Reference

## Environment Variables

All environment variables are optional. VoiceClip works out of the box with sensible defaults.

### Model & Binary Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICECLIP_HQ_MODEL_PATH` | `~/.whisper/ggml-large-v3.bin` | Path to the whisper model file |
| `VOICECLIP_WHISPER_CLI` | auto-detect | Explicit path to `whisper-cli` binary |
| `VOICECLIP_WHISPER_SERVER` | auto-detect | Explicit path to `whisper-server` binary |
| `VOICECLIP_MODEL_PATH` | — | Legacy alias for `VOICECLIP_HQ_MODEL_PATH` |

Auto-detection searches: PATH, `/opt/homebrew/bin/`, `/usr/local/bin/`

Model search paths (in order):
1. `$VOICECLIP_HQ_MODEL_PATH` (if set)
2. `~/.whisper/ggml-large-v3.bin`
3. `~/.cache/whisper/ggml-large-v3.bin`

### Streaming Parameters

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `VOICECLIP_CHUNK_MS` | `5000` | 600+ | Audio chunk size in milliseconds |
| `VOICECLIP_OVERLAP_MS` | `600` | 100+ | Overlap between consecutive chunks in ms |
| `VOICECLIP_MAX_QUEUE_CHUNKS` | `120` | 8+ | Max pending chunks in worker queue |

### Server Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICECLIP_SERVER_PORT` | dynamic (random free port) | Fixed port for whisper-server |
| `VOICECLIP_SERVER_CLEANUP_MODE` | `owned` | Server cleanup on app start: `owned` (only own PIDs), `global` (all whisper-servers), `off` (no cleanup) |

### Remote Server (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICECLIP_REMOTE_SERVER_URL` | — | URL of remote transcription server (e.g. `http://server:8787`) |
| `VOICECLIP_REMOTE_API_KEY` | — | Bearer token for remote server authentication |

### UI & Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICECLIP_ACTION_DEBOUNCE_MS` | `200` | Button click debounce interval |
| `VOICECLIP_STOPPING_TIMEOUT_SECONDS` | `45` | Timeout for stop/finalize operations |
| `VOICECLIP_ENABLE_VIBRANCY` | `0` | Set to `1` to enable native macOS blur effect on widget |

## QSettings

Persistent settings stored via macOS QSettings framework at `com.voiceclip.voiceClip`.

| Key | Type | Description |
|-----|------|-------------|
| `mode.default` | string | `fast` (streaming) or `hq` (fallback) |
| `stream.chunk_ms` | int | Chunk size (mirrors env var, persisted) |
| `stream.overlap_ms` | int | Overlap (mirrors env var, persisted) |

Settings can be inspected via:
```bash
defaults read com.voiceclip.voiceClip
```

## Runtime Files

### Logs

| Path | Description |
|------|-------------|
| `~/Library/Logs/voiceClip/voiceclip.log` | Main application log (structured, INFO level) |

Log format:
```
2026-03-14 20:00:15,005 INFO primary_action source=tray state=idle session=-
2026-03-14 20:00:15,006 INFO session_start id=7310e52e... mode=hq
2026-03-14 20:00:15,006 INFO state_transition from=idle to=starting reason=record_arm session=7310e52e...
```

### Application Support

| Path | Description |
|------|-------------|
| `~/Library/Application Support/voiceClip/whisper_servers.json` | Registry of owned whisper-server PIDs, ports, and model paths |
| `~/Library/Application Support/voiceClip/voiceclip.pid` | Current app PID (single-instance guard) |

Server registry format:
```json
[
  {
    "pid": 12345,
    "port": 64121,
    "model": "/Users/name/.whisper/ggml-large-v3.bin"
  }
]
```

### Temporary Files

| Pattern | Description |
|---------|-------------|
| `/tmp/voiceclip-{uuid}.wav` | Audio recording (HQ mode) |
| `/tmp/voiceclip-transcript-{uuid}.txt` | Transcription output (whisper-cli) |

Temporary files are cleaned up after use.

### Model Storage

| Path | Description |
|------|-------------|
| `~/.whisper/ggml-large-v3.bin` | Whisper large-v3 model (2.9 GB) |

Downloaded automatically on first launch if not present.

## Port Allocation

The whisper-server uses a dynamically allocated free port by default. The port is:
1. Reserved via `socket.bind(("127.0.0.1", 0))` → OS assigns free port
2. Socket closed immediately
3. whisper-server started on that port
4. Port registered in `whisper_servers.json`

To use a fixed port (e.g., for firewall rules):
```bash
export VOICECLIP_SERVER_PORT=8765
```

## Single Instance Guard

VoiceClip enforces a single running instance using a Qt local socket (`QLocalServer`). If a second instance starts, it sends a signal to the first instance and exits.

Socket name: `voiceClip-single-instance`

## Cleanup Modes

When VoiceClip starts, it cleans up whisper-server processes from previous runs:

| Mode | Behavior |
|------|----------|
| `owned` (default) | Only kills servers registered in `whisper_servers.json` with matching PID |
| `global` | Kills all `whisper-server` processes on the system |
| `off` | No cleanup, leaves orphaned servers running |
