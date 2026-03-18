# AutoPilot AI 🤖

> An autonomous browser agent that takes a natural language goal and executes it — navigating, clicking, typing, and extracting information from any website.

Powered by **Gemini 2.0 Flash** multimodal vision · Built with **FastAPI + Playwright** · Deployable on **Google Cloud Run**

---

## What It Does

Give AutoPilot AI a plain-English instruction, and it will:

1. **Parse your goal** — Gemini picks the right website and start URL automatically
2. **Open a browser** — launches a real Chromium instance via Playwright
3. **Handle popups** — dismisses login modals on Amazon, Myntra, Flipkart automatically
4. **See the screen** — takes a screenshot of the current page
5. **Think** — sends the screenshot + goal to Gemini multimodal vision
6. **Act** — clicks, types, scrolls, or navigates based on Gemini's decision
7. **Repeat** — loops until goal is achieved, max steps reached, or error detected

### Example Tasks

```
"Find the cheapest kurta on Myntra"
"Find the cheapest book on Amazon and return its title and price"
"Search for Python laptops on Flipkart under ₹50,000"
"Find mystery books under £10 on books.toscrape.com"
```

---

## Architecture

```
User / Frontend  (React · port 5500)
        │
        ▼  POST /run
FastAPI Server   (api/server.py · port 8080)
        │
        ▼
AgentController  (agents/agent_controller.py)
   ├── GoalParser    → Gemini picks start URL + task type   (agents/goal_parser.py)
   ├── VisionAgent   → screenshot → Gemini → next action    (agents/vision_agent.py)
   └── BrowserTool   → Playwright: navigate, click, scroll  (tools/browser_tool.py)
                              │
                              ▼
                  Amazon / Myntra / Flipkart / Any Site
```

See `docs/AutoPilot_AI_Architecture.pdf` for detailed system diagrams.

---

## Project Structure

```
autopilot-ai/
├── backend/
│   ├── main.py                        # Entry point — starts uvicorn
│   ├── requirements.txt
│   ├── agents/
│   │   ├── goal_parser.py             # NL goal → start URL + task config
│   │   ├── vision_agent.py            # Screenshot → Gemini → action JSON
│   │   └── agent_controller.py        # Main orchestration loop
│   ├── tools/
│   │   └── browser_tool.py            # Playwright wrapper + DOM extractors
│   └── api/
│       └── server.py                  # FastAPI routes + session store
├── frontend/
│   └── index.html                     # Single-file React UI
├── docs/
│   └── AutoPilot_AI_Architecture.pdf  # System architecture diagrams
├── infra/
│   ├── Dockerfile
│   └── deploy.sh                      # Google Cloud Run deploy script
└── README.md
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js (for frontend only)
- A [Google Gemini API key](https://aistudio.google.com)

### 1. Install dependencies

```bash
cd backend
pip install -r requirements.txt
playwright install chromium
```

### 2. Set your API key

```bash
export GEMINI_API_KEY="your_api_key_here"
```

### 3. Start the backend

```bash
cd backend
python main.py
```

Backend runs at `http://localhost:8080`  
Interactive API docs at `http://localhost:8080/docs`

### 4. Open the frontend

```bash
# Option A: open directly
open frontend/index.html

# Option B: serve with Python
cd frontend && python -m http.server 5500
# open http://localhost:5500
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `POST` | `/run` | Start async agent session |
| `POST` | `/run/sync` | Run agent synchronously (testing only) |
| `GET` | `/status/{session_id}` | Poll live step updates |
| `GET` | `/sessions` | List all sessions |
| `DELETE` | `/sessions` | Clear all sessions |
| `POST` | `/sessions/{id}/cancel` | Cancel a running session |

### POST /run — Request Body

```json
{
  "instruction": "Find the cheapest kurta on Myntra",
  "max_steps": 15,
  "headless": true
}
```

### GET /status/{session_id} — Response

```json
{
  "session_id": "a3f9b1c2",
  "goal": "Find the cheapest kurta on Myntra",
  "status": "done",
  "result": "Cheapest on Myntra: 'Roadster Men Kurta' at ₹399",
  "step_count": 6,
  "steps": [
    {
      "step": 1,
      "action": "navigate",
      "reason": "Opening Myntra kurtas page",
      "confidence": 0.95,
      "goal_progress": "Navigating to start URL",
      "success": true,
      "url": "https://www.myntra.com/kurtas"
    }
  ],
  "final_url": "https://www.myntra.com/kurtas",
  "final_title": "Kurtas - Buy Kurtas Online"
}
```

### Run with curl

```bash
# Start async task
curl -X POST http://localhost:8080/run \
  -H "Content-Type: application/json" \
  -d '{"instruction": "find the cheapest kurta on Myntra"}'

# Poll for result (replace SESSION_ID)
curl http://localhost:8080/status/SESSION_ID

# Run synchronously (blocks until done)
curl -X POST http://localhost:8080/run/sync \
  -H "Content-Type: application/json" \
  -d '{"instruction": "find the cheapest book on books.toscrape.com"}'
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| AI Vision & Reasoning | Gemini 2.0 Flash (multimodal) |
| Backend API | FastAPI + Uvicorn |
| Browser Automation | Playwright (Chromium) |
| Frontend | React (single-file) |
| Cloud Deployment | Google Cloud Run |
| Container Registry | Google Container Registry |
| Language | Python 3.11 |

---

## Cloud Deployment (Google Cloud Run)

### Prerequisites

- `gcloud` CLI installed and authenticated
- Docker installed
- A GCP project created

### Deploy

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GEMINI_API_KEY="your-api-key"

chmod +x infra/deploy.sh
./infra/deploy.sh
```

This will:
1. Enable required GCP APIs (Cloud Run, Container Registry)
2. Build and push the Docker image
3. Deploy to Cloud Run in `us-central1`
4. Print your live service URL

---

## Supported Sites

| Site | URL Strategy | Popup Handling | DOM Extraction |
|------|-------------|----------------|----------------|
| Amazon India | `/s?k=QUERY&s=price-asc-rank` | ✅ Auto-dismissed | ✅ Direct price scrape |
| Myntra | `/myntra.com/CATEGORY` | ✅ Auto-dismissed | ✅ Direct price scrape |
| Flipkart | `/search?q=QUERY&sort=price_asc` | ✅ Auto-dismissed | ✅ Direct price scrape |
| books.toscrape.com | `/catalogue/page-1.html` | N/A | ✅ Full catalogue scan |
| Any other site | Google search fallback | Generic selectors | Vision-based |

---

## Key Design Decisions

**Direct DOM extraction over vision** — For e-commerce price tasks (cheapest/most expensive), the agent scrapes the DOM directly instead of relying on Gemini vision. This is faster, more reliable, and uses fewer API calls.

**Login popup handler** — `handle_login_popups()` runs automatically after every navigation and click. It checks 20+ CSS selectors covering Amazon, Myntra, Flipkart, and generic modal patterns so Gemini always sees clean product pages.

**Site-aware recovery** — When the agent gets stuck (stall detector fires), `_recovery_url()` builds the correct recovery URL for the current site instead of always falling back to a hardcoded URL.

**Gemini gets site context** — Every vision call includes a site-specific hint (e.g. "Myntra shows brand name above product name in `.product-brand`") so Gemini can identify prices and products accurately.

---

## Roadmap

- [ ] Firestore — persistent session storage across restarts
- [ ] Pub/Sub — async task queue for high-throughput deployments
- [ ] Multi-agent — parallel agents for price comparison across sites
- [ ] Memory — cross-session learning of UI patterns per site
- [ ] Tool plugins — email, calendar, form-filling
- [ ] Frontend v2 — live browser preview embedded in UI

---

## License

MIT