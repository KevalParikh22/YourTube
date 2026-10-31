# Cloud media services: models, limits and prices (R1, 2026-10-31)

Used by docs/plan/cloud-and-parallel.md. Prices are USD and change; check before relying on them.

## Transcription
| Service | Model | Line timestamps | Upload limit | Formats | Price |
|---|---|---|---|---|---|
| Groq | `whisper-large-v3-turbo` (also `whisper-large-v3`, $0.111/h) | `verbose_json` + `timestamp_granularities[]=segment` | 25 MB free tier, 100 MB dev tier | flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm | **$0.04 per audio hour** |
| OpenAI | `whisper-1` | yes (the only OpenAI model with timestamp granularities) | 25 MB | mp3, mp4, mpeg, mpga, m4a, wav, webm (**no ogg**) | $0.006/min = **$0.36/h** (third-party sources; estimate) |
| Gemini | `gemini-3.8-flash` | asked for in the prompt (MM:SS references); approximate | 20 MB per request inline | ogg, mp3, opus, wav, flac, ... | 32 tokens/s of audio = 115,200 tokens/h; $0.75 per 1M input through 2026-12-31 ($1.50 from 2027) → **≈ $0.09/h** plus output (estimate) |

- **OpenAI `whisper-1` is deprecated and shuts down on 2027-02-26.** Its replacement `gpt-transcribe` has no segment timestamps, so after that date OpenAI transcription would give text without times. Groq stays the recommended engine.
- **Upload format:** mono 16 kHz MP3 at 32 kbps (≈ 14 MB per hour; 600 s chunks ≈ 2.4 MB), accepted by all three. OGG would fail on OpenAI.

## Slide reading (one image + prompt)
| Service | Default vision model | Image cost |
|---|---|---|
| Claude | `claude-haiku-5-5` | ≈ width×height/750 tokens (768×432 ≈ 440); Haiku 5.5 input $0.10 per 1M up to 100K-token prompts (third-party sources) → ≈ $0.00005 per frame (estimate) |
| Gemini | the saved Gemini model (`gemini-2.5-flash` default; `gemini-3.8-flash` current) | ≈ 258 tokens per 768-px tile; $0.30 (2.5 flash) / $0.75 (3.8 flash) per 1M → ≈ $0.0001–0.0002 per frame (estimate) |
| OpenAI / OpenAI-compatible | the saved model (must accept images; not verified for current minis) | estimate only |

A 30-minute talk keeps about 40 frames → well under $0.01 per video with Claude Haiku or Gemini Flash.

## Embeddings
| Service | Model | Price per 1M tokens |
|---|---|---|
| Gemini | `gemini-embedding-001` (text; `taskType` RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY) | $0.15 |
| Gemini | `gemini-embedding-2` (multimodal) | $0.20 text |
| OpenAI | `text-embedding-3-small` | $0.02 (third-party sources) |

This library has about 9,000 pieces × ~80 tokens ≈ 0.7M tokens → about $0.11 (Gemini) or $0.015 (OpenAI) for a full index.

## Chat models for the new providers
- Groq: `llama-3.3-70b-versatile` (unchanged default; check the Groq model list at setup).
- OpenAI: `gpt-5.4-mini` ($0.75 in / $4.50 out per 1M; third-party, verified 2026-10-06 by mem0). Vision support of current minis not confirmed; the user can pick another model.

## Sources
- Groq speech-to-text: https://console.groq.com/docs/speech-to-text
- OpenAI speech-to-text: https://developers.openai.com/api/docs/guides/speech-to-text
- Gemini audio: https://ai.google.dev/gemini-api/docs/audio
- Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
- Claude Haiku 5.5 pricing (third-party): https://www.aipricing.guru/models/claude-haiku-5-5/
- OpenAI prices (third-party): https://diyai.io/ai-tools/speech-to-text/openai-whisper-api-pricing-2026/ , https://mem0.ai/library/llms-and-models/openai-api-pricing , https://www.costbench.com/software/embedding-apis/openai-embeddings/
