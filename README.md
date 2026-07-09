# The Cabinet 🗄️

**AI-native operations platform for The Crafters Hub woodworking school — Egypt.**

Built for the **Build with Gemini XPRIZE** hackathon. Uses Gemini 3.5 Flash and Gemini Embedding-2 to automate WhatsApp customer service, semantic knowledge retrieval, and traditional woodworking heritage archiving for a real Egyptian small business.

---

## What It Does

The Cabinet is a self-hosted AI operations brain that runs entirely on-premise at our workshop:

| Feature | Technology |
|---|---|
| **WhatsApp AI Concierge** | Gemini 3.5 Flash + RAG (answers pricing, schedule, booking questions in Egyptian Arabic / Franco-Arabic) |
| **Semantic Knowledge Base** | Gemini Embedding-2 → PostgreSQL pgvector (553 entries, all vectorized during hackathon) |
| **Heritage Archive** | Gemini 3.5 Flash structured extraction (extracts JSON metadata from unstructured Arabic text about traditional woodworking techniques) |
| **Business Intelligence** | React Control Panel + FastAPI + live PostgreSQL (students, finance, inventory) |
| **WhatsApp Pipeline** | n8n → Meta Cloud API webhook → HMAC-SHA256 validation → Query Rewriter → Gemini → reply |
| **Edge Inference** | Qwen 2.5 7B (Ollama, local GPU) for lightweight intent classification |

**AI cost per WhatsApp message: $0.00** (Gemini free tier + local Ollama)

---

## Architecture

```
WhatsApp Customer
      │
      ▼
Meta Cloud API Webhook (HMAC-SHA256 validated)
      │
      ▼
n8n Pipeline (local Docker)
  ├── Query Rewriter: Franco-Arabic → clean Arabic
  ├── Intent Classifier: Qwen 2.5 7B @ localhost:11434 (edge)
  ├── RAG Search: gemini-embedding-2 → pgvector → top-3 KB entries
  ├── Response Generator: gemini-3.5-flash (grounded, no hallucination)
  └── WhatsApp Reply via Meta Graph API
      │
      ▼
Student gets answer in <30 seconds
Cost per message: $0.00
```

---

## Stack

- **AI:** `gemini-3.5-flash`, `gemini-embedding-2` (Google AI Studio), Qwen 2.5 7B via Ollama
- **Orchestration:** n8n (self-hosted Docker)
- **Database:** PostgreSQL 15 + pgvector extension
- **API:** Python FastAPI
- **Frontend:** React (Control Panel)
- **Infrastructure:** Docker Compose, Cloudflare Tunnel
- **WhatsApp:** Meta Business Cloud API

---

## Repository Structure

```
the-cabinet/
├── api/                        # FastAPI Python server (Cabinet API)
│   └── main.py                 # All endpoints: /concierge, /archive, /health, etc.
├── db_init/                    # PostgreSQL schema SQL files (run on first boot)
│   ├── 01-init.sql             # Core tables: students, finance, inventory
│   ├── 04_concierge_schema.sql # AI concierge tables and views
│   ├── 04_ai_infrastructure.sql# Knowledge base + embeddings tables
│   └── 05_artisan_techniques.sql # Heritage Archive table (pgvector)
├── n8n_workflows/              # Importable n8n workflow JSON files
│   ├── customer_service_agent.json  # Main WhatsApp AI pipeline
│   ├── n8n_wix_cache_sync.json      # Wix booking data sync
│   └── pdpl_data_rights_handler.json # GDPR/PDPL compliance workflow
├── docker-compose.yml          # Full production stack (sanitized)
├── .env.example                # Environment variable template
├── requirements.txt            # Python dependencies
└── README.md
```

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- An NVIDIA GPU (optional, for local Ollama inference)
- A Gemini API key (free at [aistudio.google.com](https://aistudio.google.com/apikey))
- A Meta Business WhatsApp Cloud API account

### 1. Clone and configure
```bash
git clone https://github.com/The-Crafters-Hub/the-cabinet.git
cd the-cabinet
cp .env.example .env
# Fill in your values in .env
```

### 2. Start the stack
```bash
docker compose up -d
```

### 3. Import n8n workflows
- Open n8n at `http://localhost:5678`
- Import each JSON file from `n8n_workflows/`
- Configure your Gemini API key and Meta credentials in n8n

### 4. Verify
```bash
curl http://localhost:8000/health
# {"status": "healthy", "db": "connected"}
```

---

## Live Demo

- **WhatsApp Bot:** `+20 1113776666` — send any question in Arabic
- **Website:** [the-crafters-hub.com](https://the-crafters-hub.com)
- **Live demo request:** Email us for a screen share session

---

## Evidence of Real Business

- **121 students** in the database (31 enrolled during hackathon period)
- **EGP 142,400 (~$2,848 USD)** in revenue during May–June 2026
- **553 knowledge base entries** — all vectorized with `gemini-embedding-2`
- **Heritage Archive live** — traditional Egyptian woodworking techniques archived via Gemini structured extraction (Naqsh Al-Lotus carving, Aswan region)

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

*Built by Hosam Elshanawany — The Crafters Hub, Cairo, Egypt*
*Gemini XPRIZE submission — Category: Small Business Services*
