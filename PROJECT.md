# Magenta - LLM Agent Framework

Magenta is a FastAPI-based skeleton project for building agentic LLM applications. It provides core infrastructure for LLM integration, tool use, RAG (Retrieval-Augmented Generation), and multi-tenant chat management. Designed to be added as a git subtree to existing projects.

## Architecture

```
magenta/
├── core/                   # Core infrastructure
│   ├── config.py          # Configuration, database connections
│   ├── models.py          # Pydantic data models
│   ├── security.py        # JWT authentication, user management
│   ├── tools.py           # Tool definitions and execution
│   └── utils.py           # PDF processing, embeddings, utilities
├── routes/                 # API endpoints
│   ├── chats.py           # Chat creation and messaging
│   ├── documents.py       # Document upload and search
│   ├── prompts.py         # System prompt management
│   ├── tenants.py         # Multi-tenant management
│   └── tools.py           # Tool function CRUD
├── services/               # Business logic
│   ├── chat_service.py    # LLM integration, tool orchestration
│   ├── document_service.py # Document vectorization, RAG search
│   └── data_import.py     # Batch loading utilities
├── data/                   # Sample data and configurations
├── tests/                  # Integration tests
├── main.py                 # Application entry point
├── Dockerfile             # Container image
└── docker-compose.yml     # Multi-container orchestration
```

### Component Relationships

- **Routes** handle HTTP requests and delegate to **Services**
- **Services** contain business logic and use **Core** infrastructure
- **Core** provides database connections, models, authentication, and tools
- **MongoDB** stores chats, prompts, tools, documents, and tenants
- **PostgreSQL with pgVector** stores document embeddings for RAG

## Technology Stack

| Component | Technology |
|-----------|------------|
| Web Framework | FastAPI 0.115.6 |
| ASGI Server | Uvicorn 0.34.0 |
| Document Store | MongoDB 4.10.1 |
| Vector Database | PostgreSQL + pgVector 0.3.6 |
| ORM | SQLAlchemy 2.0.36 |
| LLM Provider | OpenAI 1.57.4 (GPT-4o) |
| PDF Processing | PyMuPDF 1.25.1 |
| Authentication | PyJWT 2.10.1, passlib, bcrypt |
| Logging | Loguru 0.7.3 |

## Core Features

### Chat Management
- Multi-tenant chat sessions with message history
- Asynchronous message processing with status tracking
- Image upload support in messages

### LLM Integration
- OpenAI API integration with function calling
- Tool chaining with iteration limits (max 10)
- JSON mode support for structured outputs

### Tool System
- **Function tools**: Python callables executed locally
- **External tools**: HTTP endpoints (GET/POST/PUT/DELETE)
- Context parameter injection for dynamic values
- Tool validation against function signatures

### RAG Capabilities
- PDF document ingestion with text extraction
- Paragraph-aware text chunking
- Vector embeddings stored in PostgreSQL with pgVector
- Two document modes:
  - **RAG documents**: Retrieved via semantic similarity search
  - **Context documents**: Full text injected into system prompt

### Multi-Tenancy
- Per-tenant MongoDB collections
- Per-tenant PostgreSQL vector tables
- Dynamic tenant registration

## API Overview

### Authentication
| Endpoint | Description |
|----------|-------------|
| `POST /token` | Obtain JWT access token |
| `GET /users/me/` | Get current user info |

### Chats (`/chats`)
| Endpoint | Description |
|----------|-------------|
| `POST /chats/create` | Create new chat session |
| `POST /chats/{chat_id}/send` | Send message (async processing) |
| `GET /chats/` | List chats with filters |
| `GET /chats/{chat_id}/messages` | Get chat messages |
| `GET /chats/{chat_id}/status` | Get processing status |

### Prompts (`/prompts`)
| Endpoint | Description |
|----------|-------------|
| `POST /prompts/create` | Create system prompt with toolset and RAG config |
| `GET /prompts/` | List prompts |
| `PUT /prompts/{prompt_id}` | Update prompt |

### Tools (`/tools`)
| Endpoint | Description |
|----------|-------------|
| `POST /tools/create` | Register function or external tool |
| `GET /tools/` | List tools |
| `PUT /tools/{tool_id}` | Update tool definition |

### Documents (`/documents`)
| Endpoint | Description |
|----------|-------------|
| `POST /documents/upload` | Upload PDF for vectorization |
| `POST /documents/search` | Semantic similarity search |
| `GET /documents/{document_id}/text` | Get document text and chunks |

### Tenants (`/tenants`)
| Endpoint | Description |
|----------|-------------|
| `POST /tenants/create` | Create new tenant |
| `GET /tenants/` | List tenants |

### Health
| Endpoint | Description |
|----------|-------------|
| `GET /` | Service info |
| `GET /healthcheck` | Health status |
| `GET /postgres_status` | PostgreSQL connectivity |
| `GET /mongo_status` | MongoDB connectivity |

## Configuration

### Required Environment Variables
```
OPENAI_API_KEY=sk-...          # OpenAI API key
SECRET_KEY=...                  # JWT signing key
```

### Database Configuration
```
POSTGRES_HOST=localhost         # PostgreSQL host
POSTGRES_PORT=5432
POSTGRES_DB=magenta
POSTGRES_USER=magenta_user
POSTGRES_PASSWORD=magenta_password

MONGO_HOST=localhost            # MongoDB host
MONGO_PORT=27017
MONGO_DB=magenta
```

### Optional Configuration
```
ENV=DEV                         # Set to DEV to disable Slack notifications
SLACK_WEBHOOK_URL=...           # Slack webhook for notifications
LOG_FILE=logs/app.log
LOG_ROTATION=500 MB
LOG_RETENTION=10 days
LOG_LEVEL=INFO
```

## Getting Started

### Using Docker (Recommended)
```bash
# Start all services
docker-compose up -d

# Application runs on http://localhost:8000
```

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Start MongoDB and PostgreSQL separately
# Then run the application
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Running Tests
```bash
# Using Docker
docker-compose run tests

# Or locally
pytest tests/
```

### Initial Data
On startup, the application automatically:
1. Creates PostgreSQL extensions (pgVector)
2. Loads prompts from `data/prompts/`
3. Creates users from `data/users/`
4. Loads tool definitions into MongoDB
5. Processes documents from `data/documents/`

## Additional Docs
The `docs/` directory may contain more detailed documentation.