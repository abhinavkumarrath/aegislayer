# AegisLayer 🛡️
### Enterprise Privacy Middleware for LLMs

> **Zero-trust, reversible tokenization middleware** — intercepts LLM prompts, redacts sensitive entities, forwards sanitised text to external AI APIs, and seamlessly restores original values in the response.

---

## Architecture

```
[Raw User Input]
       │
       ▼
[AegisLayer FastAPI App]
       │
       ├──► Module A: CPU Regex Engine
       │      Emails · API Keys · IPs · Phones · Credit Cards
       │
       ├──► Module B: AMD GPU NER Engine (ROCm + dslim/bert-base-NER)
       │      Persons · Organisations · Locations
       │
       ▼
[Session Vault]  →  Token ↔ Original Map (encrypted in-memory)
       │
       ▼
[Sanitised Prompt]  ──────────►  [External Cloud LLM API]
                                          │
[De-Sanitised Response]  ◄────────────────┘
       │
       ▼
[Final Output + Audit Ledger]
```

---

## 🏆 AMD Hackathon: Track 3 (Unicorn) Compliance

This project is submitted for **Track 3: Unicorn Pre-Screening**. Below is the judging checklist mapping:

- ✅ **README explains what you built:** AegisLayer is a Zero-Trust Privacy Middleware that redacts sensitive PII from LLM prompts and seamlessly restores it in the response.
- ✅ **README explains AMD resource usage:** Our Deep Learning NER engine (`ner_engine.py`) explicitly utilizes **AMD ROCm** via PyTorch (`torch.cuda.is_available()` mapped to AMD Instinct hardware) to achieve sub-100ms inference latency for Named Entity Recognition on a 400MB HuggingFace BERT model.
- ✅ **Setup instructions are complete:** See the **Quick Start** section below for both 1-click `run.bat` deployment and manual environment configuration.
- ✅ **Main code path is easy to find:** The core pipeline is orchestrated in `backend/main.py`. The CPU Regex logic is in `backend/rule_engine.py` and the AMD GPU inference logic is in `backend/ner_engine.py`.
- ✅ **External services are documented:** We use an external LLM (configurable via `.env` like OpenAI or Fireworks AI) to process the sanitized prompt. The `backend/llm_client.py` handles this HTTP bridge.

---

## Quick Start

### 1. Clone / navigate to the project

```bash
cd "AMD HACKATHON"
```

### Option A: 1-Click Launch (Windows)

Simply double-click the `run.bat` file in the root directory!
It will automatically create a virtual environment, install dependencies, start the backend, and open your browser to the application.

### Option B: Manual Setup

### 2. Install PyTorch

**For AMD GPU (ROCm 6.0):**
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.0
```

**For CPU / development:**
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```

### 3. Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Configure environment

```bash
# Copy example config
cp ../.env.example .env

# Edit .env — add your LLM API key (optional, mock mode works without one)
# LLM_API_KEY=sk-...
# LLM_BASE_URL=https://api.openai.com/v1
```

### 5. Start the backend

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be live at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

### 6. Open the frontend

```bash
# Option A: FastAPI serves it automatically at http://localhost:8000/
# Option B: Serve frontend separately
cd ../frontend
python -m http.server 3000
# Then open http://localhost:3000
```

### Option C: Docker (Hackathon Judging Ready)

We provide a `Dockerfile` and `docker-compose.yml` for seamless, containerized deployment without any local setup. Perfect for platforms like Render, DigitalOcean, or Railway.

```bash
docker-compose up --build -d
```
The app will be instantly available at `http://localhost:8000`.

---

## API Reference

### `GET /health`

Returns system health and GPU status.

```json
{
  "status": "ok",
  "uptime_s": 42.5,
  "ner_device": "rocm:AMD Instinct MI300X",
  "ner_model": "dslim/bert-base-NER",
  "ner_ready": true,
  "llm_endpoint": "https://api.openai.com/v1",
  "version": "1.0.0"
}
```

### `POST /api/process`

Full privacy pipeline.

**Request:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "prompt": "Contact john.doe@corp.com at 192.168.1.1. His key is sk-abc123..."
}
```

**Response:**
```json
{
  "session_id": "550e8400-...",
  "original_prompt": "Contact john.doe@corp.com at 192.168.1.1...",
  "sanitized_prompt": "Contact [EMAIL_A] at [IP_A]. His key is [APIKEY_A]...",
  "raw_llm_response": "I'll reach out to [EMAIL_A] about the server at [IP_A]...",
  "final_de_sanitized_response": "I'll reach out to john.doe@corp.com about the server at 192.168.1.1...",
  "audit_logs": [
    { "action": "REDACTED", "type": "EMAIL",   "token": "[EMAIL_A]",  "original": "john.doe@corp.com" },
    { "action": "REDACTED", "type": "IPV4",    "token": "[IP_A]",     "original": "192.168.1.1" },
    { "action": "REDACTED", "type": "API_KEY", "token": "[APIKEY_A]", "original": "sk-abc123..." }
  ],
  "latency_ms": 87.4,
  "ner_device": "rocm:AMD Instinct MI300X"
}
```

---

## Project Structure

```
AMD HACKATHON/
├── backend/
│   ├── main.py           # FastAPI app — pipeline orchestrator
│   ├── rule_engine.py    # Module A: CPU regex PII detection
│   ├── ner_engine.py     # Module B: AMD ROCm NER inference
│   ├── vault.py          # Module C: Ephemeral session token vault
│   ├── llm_client.py     # Async HTTP bridge to external LLM
│   ├── schemas.py        # Pydantic v2 request/response models
│   └── requirements.txt  # Python dependencies
├── frontend/
│   ├── index.html        # Single-page admin dashboard
│   ├── style.css         # Premium dark AMD theme (glassmorphism)
│   └── app.js            # Vanilla JS fetch orchestration
├── .env.example          # Environment variable template
└── README.md             # This file
```

---

## Entity Types Detected

| Source | Type | Example |
|--------|------|---------|
| Regex CPU | `EMAIL` | `john@corp.com` |
| Regex CPU | `API_KEY` | `sk-proj-abc...`, `ghp_...`, `AIza...` |
| Regex CPU | `IPV4` | `192.168.1.105` |
| Regex CPU | `PHONE` | `+1 (555) 234-5678` |
| Regex CPU | `CREDIT_CARD` | `4111 1111 1111 1111` |
| AMD GPU NER | `PERSON` | `John Smith` |
| AMD GPU NER | `ORG` | `Acme Corporation` |
| AMD GPU NER | `LOCATION` | `San Francisco` |

---

## AMD ROCm Notes

AegisLayer uses PyTorch's ROCm compatibility layer. `torch.cuda.is_available()` returns `True` on ROCm builds — the NER engine targets `device=0` (first GPU) automatically.

```python
# Verify ROCm is detected
python -c "import torch; print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

---

## Security Notes

- **Vault is ephemeral**: Session state is cleared after every round-trip — no persistent PII storage.
- **Tokens are opaque**: Tokens like `[PERSON_1]` carry no plaintext information.
- **CORS**: Currently open (`*`) for development. Lock down to your frontend origin in production.
- **API Keys**: Never log or persist the `original` field from audit entries in production.

---

*Built for the AMD Developer Cloud Hackathon — leveraging ROCm-accelerated local NER inference for sub-100ms entity detection.*
