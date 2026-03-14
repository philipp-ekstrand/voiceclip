# Audio-Pipeline

## HQ-Modus (aktiv, funktionierend)

```
Mikrofon
  ↓
AudioRecorder.start()
  → Subprocess: python3 main.py --audio-worker --wav-path /tmp/voiceclip-{uuid}.wav
  → sounddevice Aufnahme: 16kHz, mono, PCM16
  → Schreibt direkt in WAV-Datei
  ↓
AudioRecorder.stop()
  → SIGINT an Subprocess → WAV-Datei finalisiert
  → WAV-Datei gelesen als numpy.ndarray
  → Resampling auf 16kHz falls noetig
  ↓
FinalizeRecordingThread
  → Chunks zusammenfuegen (im HQ-Modus nur 1 Chunk)
  → Neue temp WAV-Datei: /tmp/voiceclip-{uuid}.wav
  ↓
TranscribeThread
  → whisper-cli -m ~/.whisper/ggml-large-v3.bin -f {wav} -l auto -otxt -np -nt -fa -t {cpus}
  → Liest Output-TXT-Datei
  → Emittiert Text als Signal
  ↓
Clipboard
  → pyperclip.copy(text) oder pbcopy Fallback
```

## Streaming-Modus (NICHT FUNKTIONAL)

```
Mikrofon
  ↓
AudioRecorder.start(capture_full_chunks=False)
  → Gleicher Subprocess wie HQ
  ↓
stream_capture_timer (alle 120ms)
  → recorder.consume_pending_samples()
  → PROBLEM: Gibt IMMER leeres Array zurueck (Stub-Implementierung!)
  → StreamingTranscriptionController bekommt NIE Audio-Daten
  ↓
Stop
  → Finalize mit leerem Transkript
  → FEHLER: "Transkription war leer"
```

### Was fuer echtes Streaming noetig waere

Der AudioRecorder muesste umgebaut werden:

1. **Option A: IPC-Pipe** - Subprocess schreibt PCM-Samples in eine Pipe, Hauptprozess liest live
2. **Option B: Shared Memory** - Subprocess schreibt in mmap, Hauptprozess liest mit Offset
3. **Option C: Thread statt Subprocess** - Audio-Aufnahme im gleichen Prozess (einfacher, aber weniger stabil)

Jede Option braucht eine komplett neue `consume_pending_samples()` Implementierung die tatsaechlich live Audio-Daten liefert.

## Performance-Benchmarks (M3 Pro, Metal GPU)

| Backend | 3s Audio | 10s Audio | 33.5s Audio | Modell-Laden |
|---------|----------|-----------|-------------|-------------|
| whisper-cli (cold) | ~5s | ~5s | 7.7s | Jedes Mal neu (~3s) |
| whisper-server (warm) | 1.7s | 2.3s | 4.6s | Einmalig beim Start |
| Hetzner CPU (int8) | 12.6s | ~18s | 28s | Einmalig beim Start |
