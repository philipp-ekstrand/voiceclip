# Audio Pipeline

VoiceClip has two audio capture modes. The streaming mode is the default; HQ mode is an automatic fallback when whisper-server is not available.

## Mode Selection (Boot)

```
App starts
  │
  ├── whisper-server binary found?
  │     ├── YES → start whisper-server → warmup → mode = "fast" (streaming)
  │     └── NO  → log warning → mode = "hq" (fallback)
  │
  └── Model found at ~/.whisper/ggml-large-v3.bin?
        ├── YES → proceed to IDLE
        └── NO  → DOWNLOADING state → download model → IDLE
```

## Streaming Mode ("fast") — Default

### Audio Capture

```
sounddevice.InputStream (in-process, no subprocess)
  │
  │  callback(indata, frames, time_info, status)
  │    → PCM int16 samples appended to _inprocess_pending (thread-safe lock)
  │    → PCM int16 samples appended to _inprocess_full (complete recording backup)
  │
  ▼
stream_capture_timer (QTimer, every 120ms)
  │
  │  consume_pending_samples()
  │    → lock → copy _inprocess_pending → clear → unlock
  │    → return numpy array of PCM samples
  │
  ▼
StreamingTranscriptionController.add_audio_samples(samples)
  │
  │  Accumulates samples in internal buffer
  │  When buffer >= chunk_samples (5s × 16000 = 80000 samples):
  │    → Extract chunk PCM bytes (with overlap from previous chunk)
  │    → Enqueue (chunk_index, pcm_bytes) to worker queue
  │
  ▼
Worker thread (_worker_loop)
  │
  │  Dequeues chunks sequentially
  │  For each chunk:
  │    1. Build prompt (previous chunk text or base prompt)
  │    2. Convert PCM → WAV bytes (in-memory, pcm16_to_wav_bytes)
  │    3. POST to whisper-server /inference endpoint
  │    4. Parse JSON response → extract text
  │    5. assembler.add(chunk_index, text)
  │    6. Save text as prev_text for next chunk's prompt
  │
  ▼
TranscriptAssembler
  │
  │  Stores (chunk_index, text) pairs
  │  merged_text(): concatenates in order, deduplicates overlapping tokens
  │
  ▼
User clicks stop → finalize()
  │
  │  1. Enqueue final tail chunk (remaining audio < 5s)
  │  2. Send sentinel (None) to worker queue
  │  3. Wait for worker to finish (timeout: STREAM_FLUSH_TIMEOUT_SECONDS)
  │  4. Return assembler.merged_text()
  │
  ▼
Clipboard (pyperclip.copy or pbcopy fallback)
```

### Overlap Handling

```
Chunk N:     [============================]
                                   |--overlap--|
Chunk N+1:                  [============================]

overlap_samples = sample_rate × overlap_ms / 1000
step_samples = chunk_samples - overlap_samples

With 5000ms chunks and 600ms overlap:
  chunk_samples = 80000
  overlap_samples = 9600
  step_samples = 70400 (= 4.4s advance per chunk)
```

The overlap ensures words at chunk boundaries appear in both chunks. The `TranscriptAssembler` deduplicates by comparing token sequences at the join point.

## HQ Mode — Fallback

```
AudioRecorder.start(capture_full_chunks=True)
  │
  │  Subprocess: python3 main.py --audio-worker
  │    --wav-path /tmp/voiceclip-{uuid}.wav
  │    --ready-path /tmp/voiceclip-{uuid}.ready
  │    --status-path /tmp/voiceclip-{uuid}.status
  │
  │  Subprocess uses sounddevice to record 16kHz mono PCM
  │  Writes directly to WAV file
  │  Creates .ready sentinel when recording starts
  │
  ▼
User clicks stop
  │
  │  AudioRecorder.stop()
  │    → SIGINT to subprocess → WAV file finalized
  │    → Read WAV file as numpy array
  │    → Resample to 16kHz if needed
  │
  ▼
FinalizeRecordingThread
  │
  │  Concatenate chunks (HQ mode: typically 1 chunk)
  │  Write final temp WAV: /tmp/voiceclip-{uuid}.wav
  │
  ▼
TranscribeThread
  │
  │  whisper-cli \
  │    -m ~/.whisper/ggml-large-v3.bin \
  │    -f {wav_path} \
  │    -l auto \
  │    -otxt -of {output_base} \
  │    -np -nt -fa \
  │    -t {cpu_count}
  │
  │  Read output .txt file → emit transcript signal
  │
  ▼
Clipboard
```

## Performance Comparison (M3 Pro, Metal GPU)

| Metric | Streaming Mode | HQ Mode |
|--------|---------------|---------|
| Wait after stop (1 min audio) | ~2-3s | ~11s |
| Wait after stop (10 min audio) | ~2-3s | ~120s+ |
| Model loading | Once at boot (~5-10s) | Every transcription (~3s) |
| GPU utilization | Distributed over recording | Spike after stop |
| Fan noise | Minimal (short bursts) | Noticeable (sustained) |

### Raw Benchmarks

| Backend | 3s Audio | 10s Audio | 33.5s Audio | 63s Audio |
|---------|----------|-----------|-------------|-----------|
| whisper-server (warm, beam=5) | ~2s | ~3s | ~6s | ~10s |
| whisper-cli (cold start) | ~5s | ~5s | 7.7s | 11.2s |

## Audio Format

| Parameter | Value |
|-----------|-------|
| Sample rate | 16000 Hz |
| Channels | 1 (mono) |
| Bit depth | 16-bit signed integer (PCM16) |
| Format | WAV (RIFF) |
| Byte order | Little-endian |
