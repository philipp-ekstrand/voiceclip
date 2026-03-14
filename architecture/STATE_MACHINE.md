# VoiceClip State Machine

## States

| State | UI | Interaktion |
|-------|-----|------------|
| `BOOT` | Mic-Icon (grau) | Kein Input moeglich |
| `DOWNLOADING` | Progress-Bar + Status | Kein Input moeglich |
| `IDLE` | Mic-Icon (orange) | Click → Start Recording |
| `STARTING` | Spinner-Animation | Kein Input moeglich |
| `RECORDING` | Puls-Animation (rot) + Stop-Icon | Click → Stop Recording |
| `STOPPING` | Spinner-Animation | Kein Input moeglich |
| `PROCESSING` | Spinner-Animation | Kein Input moeglich |
| `CHECK` | Checkmark (gruen, 500ms Flash) | Kein Input moeglich |
| `COPY_READY` | Copy-Icon + "Kopieren" Button | Click → Copy to Clipboard |
| `ERROR` | Notification | Auto-Transition zu IDLE |

## Uebergaenge

```
BOOT ──────────────────────────────────────────────────────────────────────
  │
  ├── Modell gefunden ──────────────────────────────────────────→ IDLE
  └── Modell fehlt ─────────────────────────────────────────────→ DOWNLOADING
                                                                    │
                                                                    ├── Download fertig → IDLE
                                                                    └── Download fehlgeschlagen → ERROR → IDLE

IDLE ──────────────────────────────────────────────────────────────────────
  │
  └── User klickt Mic ──────────────────────────────────────────→ STARTING
                                                                    │
                                                                    ├── Mic bereit → RECORDING
                                                                    ├── Timeout (Retry 1) → STARTING
                                                                    ├── Timeout (Retry 2) → STARTING
                                                                    └── Alle Retries fehlgeschlagen → ERROR → IDLE

RECORDING ─────────────────────────────────────────────────────────────────
  │
  └── User klickt Stop ─────────────────────────────────────────→ STOPPING
      │                                                             │
      │                                                             ├── WAV erstellt → PROCESSING
      │                                                             └── Fehler → ERROR → IDLE
      │
      └── (Audio wird in WAV geschrieben via Subprocess)

PROCESSING ────────────────────────────────────────────────────────────────
  │
  ├── Remote-Server verfuegbar ──→ RemoteTranscribeThread
  │   ├── Erfolg ───────────────────────────────────────────────→ CHECK
  │   └── Fehler → Fallback auf lokales whisper-cli
  │
  └── Lokal (whisper-cli) ──────→ TranscribeThread
      ├── Erfolg ───────────────────────────────────────────────→ CHECK
      └── Fehler ───────────────────────────────────────────────→ ERROR → IDLE

CHECK ─────────────────────────────────────────────────────────────────────
  │
  └── 500ms Timer ──────────────────────────────────────────────→ COPY_READY

COPY_READY ────────────────────────────────────────────────────────────────
  │
  ├── User klickt Copy ─────────→ Text → Clipboard ────────────→ IDLE
  └── User klickt Mic ──────────→ Neue Aufnahme ───────────────→ STARTING
```

## Session-Tracking

Jeder Recording-Zyklus hat eine `session_id` (UUID). Alle Callbacks pruefen ob die Session noch aktiv ist, um veraltete Signale zu ignorieren:

```python
if not self._is_session_active(session_id):
    return  # Veralteter Callback, ignorieren
```
