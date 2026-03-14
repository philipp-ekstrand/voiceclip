# Streaming Transcription

Deep dive into the streaming architecture, quality settings, and tuning decisions.

## Why Streaming?

Without streaming, the entire audio must be transcribed after the user stops recording. For a 10-minute memo, this means ~2 minutes of waiting. With streaming, audio is transcribed in parallel during recording. By the time the user stops, most of the audio is already processed — only the final chunk needs to be transcribed (~2-3 seconds regardless of recording length).

```
Without streaming:
  Record ██████████████████ Stop ████████████████████████ Done
  (10 min recording)           (2 min processing)

With streaming:
  Record ██████████████████ Stop ██ Done
  Chunks: ██ ██ ██ ██ ██ ██ ██    (~2-3s for last chunk)
          (processed in parallel)
```

## Architecture

### Components

```
AudioRecorder (in-process)
       │
       │ sounddevice.InputStream callback → PCM samples
       │
       ▼
stream_capture_timer (120ms interval)
       │
       │ consume_pending_samples() → numpy array
       │
       ▼
StreamingTranscriptionController
       │
       ├── Audio buffer (accumulates samples)
       ├── Chunk slicer (5s windows, 600ms overlap)
       ├── Queue (chunk_index, pcm_bytes)
       │
       ▼
Worker thread (sequential processing)
       │
       ├── Chain prompting (prev chunk text → current prompt)
       ├── PCM → WAV conversion (in-memory)
       ├── HTTP POST → whisper-server /inference
       │
       ▼
TranscriptAssembler
       │
       ├── Ordered storage (chunk_index → text)
       └── Token-level overlap deduplication
```

### WhisperServerProcessManager

The local whisper-server is a long-running process managed by VoiceClip:

```
App boot
  │
  ├── find_whisper_server() → /opt/homebrew/bin/whisper-server
  │
  ├── Spawn process with quality flags:
  │     whisper-server -m ~/.whisper/ggml-large-v3.bin \
  │       --host 127.0.0.1 --port {dynamic} \
  │       -l de -fa -t 6 -bo 5 -bs 5 -sns
  │
  ├── Health check loop (poll /health every 0.5s, 60s timeout)
  │
  ├── Warmup request (1s silence WAV → /inference)
  │     Primes Metal GPU, loads compute graph into memory
  │
  └── Register PID in ~/Library/Application Support/voiceClip/whisper_servers.json
```

**Lifecycle:**
- Server stays running for the entire app session
- Model (~3.4 GB) loaded once into RAM, stays resident
- On app quit: server process killed, PID deregistered
- On app crash: next startup cleans up orphaned servers (owned mode)

## Quality Settings

### Server-Level Flags (set at startup)

| Flag | Value | Default | Impact |
|------|-------|---------|--------|
| `-l de` | German | `en` | No per-chunk language detection overhead. Handles English loanwords within German speech correctly. |
| `-bo 5` | best-of 5 | 2 | Generates 5 candidate transcriptions, picks the best. Higher = better quality, more compute. |
| `-bs 5` | beam-size 5 | -1 (off) | **Biggest quality impact.** Beam search explores multiple token paths simultaneously instead of greedy decoding. |
| `-sns` | suppress NST | off | Suppresses non-speech tokens (music notes, special characters, hallucinated artifacts). |
| `-fa` | flash attention | on | Metal GPU acceleration. No quality impact, only speed. |
| `-t 6` | 6 threads | 4 | Half of CPU cores. Balances transcription speed vs system responsiveness. |

### Per-Request Parameters (sent with each chunk)

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `response_format` | `json` | Structured response with text field |
| `temperature` | `0.0` | Greedy decoding — highest confidence output |
| `temperature_inc` | `0.2` | If entropy exceeds threshold, retry at temp 0.2, then 0.4, etc. Catches uncertain segments. |
| `language` | `de` | Redundant with server flag, ensures consistency |
| `prompt` | dynamic | **Chain prompting** — see below |

### Chain Prompting

Whisper's `prompt` parameter conditions the decoder on what text came before. This is the key mechanism for cross-chunk quality:

```
Chunk 0:  prompt = BASE_PROMPT (natural German/English example text)
Chunk 1:  prompt = last 224 chars of Chunk 0's transcript
Chunk 2:  prompt = last 224 chars of Chunk 1's transcript
...
```

**Why this helps:**
- Consistent terminology across chunks ("API" stays "API", not "a p i")
- Better handling of sentence continuations at chunk boundaries
- Language style continuity (formal vs. informal)
- Proper noun consistency

**Base prompt (first chunk only):**
```
Also, ich habe jetzt den Code reviewed und das Deployment gemacht.
Der API-Endpoint funktioniert, aber die Performance beim Streaming
ist noch nicht optimal. Ich muss noch den Frontend-Teil refactoren
und die Pipeline-Configuration anpassen.
```

This prompt is written to match the user's actual speech style: German with casual English tech vocabulary mixed in. It primes whisper for exactly this pattern.

**224 character limit:** Whisper truncates the prompt internally at `n_text_ctx / 2` tokens. 224 characters keeps us well within this limit while providing ~2 sentences of context.

### Chunk Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Chunk size | 5000ms | Enough context for whisper to understand sentences. Smaller chunks (2.2s) caused poor quality due to insufficient context. |
| Overlap | 600ms | Prevents word splits at chunk boundaries. Overlap region appears in both adjacent chunks; `TranscriptAssembler` deduplicates. |
| Chunk timeout | 10s | With beam-size=5, a 5s chunk takes ~2-3s to process. 10s timeout provides safe margin. |
| Min tail | 220ms | Minimum audio length for the final tail chunk to be worth transcribing. |
| Max queue | 120 chunks | Safety limit. At 5s/chunk = 10 minutes of backlog. If exceeded, streaming aborts. |

## TranscriptAssembler

Merges chunk transcripts while deduplicating overlap regions:

```python
# Simplified logic:
for each new chunk text:
    tokens = tokenize(text)  # split into words
    overlap = find_longest_matching_suffix(merged_tokens, tokens)
    merged_tokens.extend(tokens[overlap:])
```

The deduplication works at the token (word) level, comparing the end of the existing transcript with the beginning of the new chunk. This handles the 600ms audio overlap where the same words appear in consecutive chunks.

## Tuning Guide

### If quality is too low
1. Increase chunk size (5000 → 8000ms) — more context per inference
2. Increase overlap (600 → 1000ms) — safer word boundaries
3. Verify beam-size is 5 in server logs
4. Check that chain prompting is working (prev_text not empty after first chunk)

### If transcription is too slow
1. Decrease chunk size (5000 → 3000ms) — faster per-chunk processing
2. Lower beam-size (5 → 3) — less compute per inference
3. Lower best-of (5 → 2) — fewer candidates
4. Increase thread count in server startup

### If fan noise is bothering
1. Lower thread count (6 → 4)
2. Increase chunk size (longer intervals between GPU bursts)

## Comparison: Streaming vs Cloud APIs

| Aspect | VoiceClip Streaming | Cloud API (e.g. OpenAI Whisper) |
|--------|--------------------|---------------------------------|
| Latency after stop | ~2-3s | ~1-2s |
| During recording | Transcribes in parallel | Sends after stop |
| Privacy | 100% local | Audio sent to server |
| Cost | Free (local GPU) | $0.006/min |
| Internet required | No | Yes |
| Model | large-v3 (2.9 GB) | Server-side (unknown) |
| Quality | Very good (beam=5 + chain prompting) | Excellent |
