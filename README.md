# Worker Grievance Case Wallet

Voice-first grievance documentation, evidence organization, and verified routing wallet for delivery and ride-hailing workers in India (India Runs on Voice Hackathon — Round 2).

## Quick Start (Zero `.env` / Zero API Keys Required)

```bash
docker compose up --build
```

Then open **http://localhost:3000** in your browser.

- No `.env` file or external API keys are needed to boot the application and load the main screen.
- Missing ASR/LLM API keys are displayed transparently (`"API key not set"`).

## Optional Cloud Provider Setup

Copy `.env.example` to `.env` and fill in keys only if enabling cloud Speech-to-Text or Structured Extraction:

1. **Speech-to-Text (`ASR_PROVIDER=sarvam`):**
   - Model: `saaras:v4` (22 Indian languages + English; supports `webm`, `wav`, `mp3`, `ogg`, `opus`, `mp4`, `m4a`, `flac`, `aac`).
   - Free credits / pricing: ₹100 non-expiring signup credits (~2 hrs audio), ₹30/hr pay-as-you-go.
2. **Structured Extraction (`LLM_PROVIDER=gemini`):**
   - Model: `gemini-3.6-flash` (native JSON Schema / Pydantic output).
   - Free tier / privacy note: Free tier has RPM/RPD rate limits and inputs may be used by Google to improve products; paid tier does not train on customer data.
