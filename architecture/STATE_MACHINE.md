# State Machine

VoiceClip uses a strict linear state machine with 10 states. Every state transition is logged with reason and session ID.

## States

| State | Constant | UI | User Interaction |
|-------|----------|-----|-----------------|
| **BOOT** | `boot` | Mic icon (gray) | None |
| **DOWNLOADING** | `downloading` | Progress bar + status text | None |
| **IDLE** | `idle` | Mic icon (orange) | Click → start recording |
| **STARTING** | `starting` | Spinner animation | None (mic initializing) |
| **RECORDING** | `recording` | Pulse animation (red) + stop icon | Click → stop recording |
| **STOPPING** | `stopping` | Spinner animation | None |
| **PROCESSING** | `processing` | Spinner animation | None |
| **CHECK** | `check` | Checkmark (green, 500ms flash) | None |
| **COPY_READY** | `copy` | Copy icon + "Kopieren" button | Click → copy to clipboard |
| **ERROR** | `error` | System notification | Auto-transitions to IDLE |

## Transition Diagram

```
BOOT
  ├── Model found ──────────────────────────────────────────────→ IDLE
  ├── Model found + whisper-server available ───→ (server start) → IDLE
  └── Model missing ────────────────────────────────────────────→ DOWNLOADING
                                                                    ├── Success → IDLE
                                                                    └── Failure → ERROR → IDLE

IDLE
  └── User clicks tray ────────────────────────────────────────→ STARTING
                                                                    ├── Mic ready → RECORDING
                                                                    ├── Timeout (retry 1-3) → STARTING
                                                                    └── All retries failed → ERROR → IDLE

RECORDING (streaming: chunks transcribed in parallel)
  └── User clicks tray ────────────────────────────────────────→ STOPPING
                                                                    ├── Streaming: finalize last chunk → PROCESSING
                                                                    ├── HQ: WAV created → PROCESSING
                                                                    └── Failure → ERROR → IDLE

PROCESSING
  ├── Streaming mode ──→ StreamFinalizeThread (last chunk)
  │   ├── Success ─────────────────────────────────────────────→ CHECK
  │   └── Failure ─────────────────────────────────────────────→ ERROR → IDLE
  │
  └── HQ mode ─────────→ TranscribeThread (whisper-cli)
      ├── Success ─────────────────────────────────────────────→ CHECK
      └── Failure ─────────────────────────────────────────────→ ERROR → IDLE

CHECK
  └── 500ms timer ─────────────────────────────────────────────→ COPY_READY

COPY_READY
  ├── User clicks Copy ──→ text → clipboard ───────────────────→ IDLE
  └── User clicks Mic ───→ new recording ──────────────────────→ STARTING
```

## Session Tracking

Every recording cycle gets a unique `session_id` (UUID4). All async callbacks validate the session before processing:

```python
if not self._is_session_active(session_id):
    return  # Stale callback from previous session, ignore
```

Sessions are created in `start_recording()` and closed in:
- `_close_session(reason="copied")` — normal completion
- `_close_session(reason="transcript_failed")` — error path
- `_close_session(reason="record_start_failed")` — mic failure

## Error Recovery

| Error Type | Recovery | Code |
|-----------|----------|------|
| Mic not available | Auto-retry (3x, 500ms delay) | `MIC_UNAVAILABLE` |
| Mic start timeout | Error notification → IDLE | `MIC_START_TIMEOUT` |
| Empty transcript | Error notification → IDLE | `TRANSCRIBE_FAILED` |
| Server health check fails | Fallback to HQ mode | (logged as warning) |
| Chunk queue overflow | Abort streaming → error | (worker_error set) |

## Logging

All transitions are logged in structured format:

```
INFO state_transition from=idle to=starting reason=record_arm session=7310e52e...
INFO state_transition from=starting to=recording reason=record_start session=7310e52e...
INFO state_transition from=recording to=stopping reason=record_stop session=7310e52e...
```

Log location: `~/Library/Logs/voiceClip/voiceclip.log`
