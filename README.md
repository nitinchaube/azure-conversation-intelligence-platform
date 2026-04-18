# Azure Conversation Intelligence Platform

An end-to-end conversation analytics platform built on Azure that ingests customer service call transcripts, extracts structured insights using Azure OpenAI, monitors agent compliance with enterprise policies, and provides a human-in-the-loop QA review workflow — all deployed via Infrastructure as Code.

Built by extending [Microsoft's Conversation Knowledge Mining Solution Accelerator](https://github.com/microsoft/Conversation-Knowledge-Mining-Solution-Accelerator) with custom enterprise features.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Azure Resources (deployed via Bicep / azd)                              │
│                                                                          │
│  Azure OpenAI ──► Azure AI Search ──► Azure Content Understanding        │
│  (GPT-4o-mini)    (Vector Index)       (Document Intelligence)           │
│       │                │                        │                        │
│       ▼                ▼                        ▼                        │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  Processing Pipeline                                            │     │
│  │  01 Ingest & Transcribe  →  02 Summarize & Extract              │     │
│  │  03 Sentiment & Scoring  →  04 Compliance Check ★               │     │
│  │  05 Feedback Analytics ★                                        │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│       │                                                                  │
│       ▼                                                                  │
│  Azure SQL / Cosmos DB ──► Web App (Container Apps) ──► Power BI         │
│                                                                          │
│  ★ = Custom extensions                                                   │
└──────────────────────────────────────────────────────────────────────────┘
```

## Custom Extensions

These are the features I built on top of the Microsoft accelerator:

### 1. Automated Compliance Monitoring (`custom_extensions/04_compliance_check.py`)

An AI-powered compliance auditing pipeline that evaluates every call transcript against configurable policy rules:

- **6 compliance rules** covering PII disclosure, professional greeting, escalation handling, resolution confirmation, empathy, and inappropriate upselling
- **Structured analysis extraction** — sentiment, topic classification, customer intent, resolution status, handle time estimates, and customer effort scores
- **Severity-based scoring** (critical / major / minor) with per-call and aggregate compliance scores
- Results persisted to SQLite for downstream dashboards

### 2. Human Feedback / QA Review App (`custom_extensions/feedback_app.py`)

A Streamlit-based interface where QA reviewers can rate AI-generated outputs:

- Side-by-side view of original transcript vs. AI analysis
- Reviewers rate accuracy across 6 dimensions (summary, sentiment, topic, resolution, compliance, agent tone)
- Supports sentiment correction and manual issue categorization
- Tracks reviewer identity and prevents duplicate reviews

### 3. Operations Analytics Dashboard (`custom_extensions/05_feedback_analytics.py`)

An interactive Streamlit dashboard aggregating all pipeline outputs:

- KPI cards: total calls, resolution rate, avg customer effort, avg handle time
- Call topic distribution, sentiment breakdown, customer intent analysis
- Compliance scorecard with per-rule pass rates and violation drilldowns
- Agent performance leaderboard
- AI accuracy metrics from human QA feedback

## Tech Stack

| Technology | Usage |
|---|---|
| **Azure OpenAI** (GPT-4o-mini) | Summarization, entity extraction, compliance evaluation, RAG |
| **Azure AI Search** | Vector-based semantic search over call transcripts |
| **Azure Content Understanding** | Document and entity extraction |
| **Azure Cosmos DB** | Conversation history and application state |
| **Azure SQL Database** | Structured operational data |
| **Azure Container Apps** | Hosting the web application |
| **Bicep / ARM Templates** | Infrastructure as Code — full deployment automated |
| **Azure Developer CLI (azd)** | One-command deployment orchestration |
| **Python** | All pipeline logic and custom extensions |
| **Streamlit** | QA review app and analytics dashboard |

## Getting Started

### Prerequisites

- Azure subscription with Contributor + RBAC Admin roles
- [Azure Developer CLI (azd)](https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/install-azd)
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- Python 3.10+
- Access to Azure OpenAI Service

### Deploy

```bash
# Clone the repo
git clone https://github.com/your-username/azure-conversation-intelligence-platform.git
cd azure-conversation-intelligence-platform

# Authenticate
azd auth login
az login

# Deploy all Azure resources
azd up
```

### Run Custom Extensions

```bash
# Install dependencies
pip install -r custom_extensions/requirements.txt

# Run compliance analysis pipeline
python custom_extensions/04_compliance_check.py

# Launch the QA review app
streamlit run custom_extensions/feedback_app.py

# Launch the analytics dashboard
streamlit run custom_extensions/05_feedback_analytics.py
```

## Project Structure

```
├── infra/                    # Bicep IaC templates for Azure resources
├── src/
│   ├── api/                  # Backend API (Python)
│   └── App/                  # Frontend web application
├── custom_extensions/        # ★ My custom additions
│   ├── 04_compliance_check.py
│   ├── 05_feedback_analytics.py
│   ├── feedback_app.py
│   └── requirements.txt
├── call_transcripts/         # Sample call transcript data
├── audio_data/               # Sample audio files
├── tests/                    # Test suite
├── azure.yaml                # Azure Developer CLI configuration
└── docs/                     # Documentation and deployment guides
```

## License

This project extends Microsoft's [Conversation Knowledge Mining Solution Accelerator](https://github.com/microsoft/Conversation-Knowledge-Mining-Solution-Accelerator), which is provided under the MIT License. See [LICENSE](./LICENSE) for details.
