# Magenta — Pip Packaging TODO

Changes required to make magenta installable as a proper Python package via `pip install`.
The approach is a **src layout**, which setuptools discovers cleanly without any hints.

---

## 1. Directory Restructure

Move all Python source into `src/magenta/`. Tests move to the repo root (outside `src/`).
Data stays at the repo root as external runtime data (see §5).

**Before:**
```
magenta/
├── pyproject.toml
├── core/
├── routes/
├── services/
├── data/
├── tests/
├── docs/
└── main.py
```

**After:**
```
magenta/
├── pyproject.toml
├── src/
│   └── magenta/
│       ├── __init__.py
│       ├── main.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── models.py
│       │   ├── security.py
│       │   ├── tools.py
│       │   └── utils.py
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── chats.py
│       │   ├── documents.py
│       │   ├── prompts.py
│       │   ├── tenants.py
│       │   └── tools.py
│       └── services/
│           ├── __init__.py
│           ├── chat_service.py
│           ├── data_import.py
│           └── document_service.py
├── tests/
│   ├── __init__.py
│   └── tests.py
├── data/
│   ├── prompts/
│   ├── users/
│   └── documents/
└── docs/
```

---

## 2. pyproject.toml

Replace the current `pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "magenta"
version = "0.1.0"
description = "FastAPI skeleton for agentic LLM applications"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.115.6",
    "uvicorn>=0.34.0",
    "openai>=1.57.4",
    "pymongo>=4.10.1",
    "pydantic>=2.10.3",
    "sqlalchemy>=2.0.36",
    "pgvector>=0.3.6",
    "psycopg2-binary>=2.9.10",
    "PyMuPDF>=1.25.1",
    "PyJWT>=2.10.1",
    "passlib>=1.7.4",
    "bcrypt>=4.2.1",
    "loguru>=0.7.3",
    "python-multipart>=0.0.20",
    "httpx",
    "pytz",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.4",
    "httpx>=0.28.1",
    "fpdf>=1.7.2",
    "requests>=2.32.3",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Drop the `[tool.setuptools.packages.find] where = [".."]` hack used in the interim.

---

## 3. Fix All Absolute Imports

Every file in `routes/`, `services/`, and `main.py` uses bare absolute imports
(`from core.xxx`, `from services.xxx`, `from routes.xxx`) that only worked because
the Dockerfile's symlink put those directories directly on `sys.path`. These must
become fully-qualified package imports.

### `main.py`
| Before | After |
|--------|-------|
| `from core import (...)` | `from magenta.core import (...)` |
| `from routes import (...)` | `from magenta.routes import (...)` |
| `from services import load_prompts_from_files, load_documents_from_files` | `from magenta.services import load_prompts_from_files, load_documents_from_files` |

### `routes/chats.py`
| Before | After |
|--------|-------|
| `from core.config import logger, tenant_collections, get_db` | `from magenta.core.config import logger, tenant_collections, get_db` |
| `from core.models import Task, Chat, ...` | `from magenta.core.models import Task, Chat, ...` |
| `from services.chat_service import process_chat, call_gpt, stream_chat` | `from magenta.services.chat_service import process_chat, call_gpt, stream_chat` |
| `from services.document_service import perform_postgre_search` | `from magenta.services.document_service import perform_postgre_search` |

### `routes/documents.py`
| Before | After |
|--------|-------|
| `from core.config import logger, tenant_collections, get_db` | `from magenta.core.config import ...` |
| `from core.models import Document, Task` | `from magenta.core.models import Document, Task` |
| `from services.document_service import process_document, ...` | `from magenta.services.document_service import process_document, ...` |

### `routes/prompts.py`
| Before | After |
|--------|-------|
| `from core.config import tenant_collections, logger` | `from magenta.core.config import tenant_collections, logger` |
| `from core.models import Prompt, RagSpec, Task` | `from magenta.core.models import Prompt, RagSpec, Task` |

### `routes/tenants.py`
| Before | After |
|--------|-------|
| `from core.config import logger, tenant_collections` | `from magenta.core.config import logger, tenant_collections` |
| `from core.models import Tenant` | `from magenta.core.models import Tenant` |

### `routes/tools.py`
| Before | After |
|--------|-------|
| `from core.config import logger, tenant_collections` | `from magenta.core.config import logger, tenant_collections` |
| `from core.models import ToolWithContext, ...` | `from magenta.core.models import ToolWithContext, ...` |

### `services/chat_service.py`
| Before | After |
|--------|-------|
| `from core.config import logger, openai_client, spacy_model, get_db` | `from magenta.core.config import ...` |
| `from core.models import ToolWithContext` | `from magenta.core.models import ToolWithContext` |
| `from core.tools import tool_handler, default_function_dictionary` | `from magenta.core.tools import tool_handler, default_function_dictionary` |

### `services/data_import.py`
| Before | After |
|--------|-------|
| `from core import logger` | `from magenta.core import logger` |
| `from core.models import Prompt` | `from magenta.core.models import Prompt` |
| `from core.config import spacy_model, get_db` | `from magenta.core.config import spacy_model, get_db` |
| `from core.utils import create_postgres_table, ...` | `from magenta.core.utils import create_postgres_table, ...` |

### `services/document_service.py`
| Before | After |
|--------|-------|
| `from core.config import logger, get_db` | `from magenta.core.config import logger, get_db` |
| `from core.utils import embed_text_spacy, ...` | `from magenta.core.utils import embed_text_spacy, ...` |

### `tests/tests.py`
| Before | After |
|--------|-------|
| `from core.security import create_access_token` | `from magenta.core.security import create_access_token` |
| `from core.utils import send_slack_message_sync` | `from magenta.core.utils import send_slack_message_sync` |
| `from core.config import SLACK_WEBHOOK_URL` | `from magenta.core.config import SLACK_WEBHOOK_URL` |

---

## 4. Move `logger.add()` out of `core/config.py`

`config.py` currently calls `logger.add(...)` at module level. This is what caused
the original duplicate-logging bug and is bad practice for a library — the consumer
should decide where logs go, not the library.

**Change:** Remove the `logger.add(...)` block from `core/config.py`. It becomes
just `from loguru import logger`.

**Add to `main.py`** (standalone magenta entrypoint), before other imports:
```python
from loguru import logger
logger.add(
    os.getenv("LOG_FILE", "logs/app.log"),
    rotation=os.getenv("LOG_ROTATION", "500 MB"),
    retention=os.getenv("LOG_RETENTION", "10 days"),
    level=os.getenv("LOG_LEVEL", "INFO")
)
```

Apps that use magenta as a library (like AIDM) configure their own sinks in their
own entrypoint. This is already done correctly in `app/main.py`.

---

## 5. Data Directory

`data/prompts/`, `data/users/`, `data/documents/` contain runtime seed data for the
standalone magenta app. They are **not** Python packages and should not move into `src/`.

Currently `data_import.py` and `main.py` reference these with hardcoded relative paths
(e.g. `"data/prompts"`), which are relative to the working directory at runtime — not
the package location. This works when running from the repo root but breaks if the
package is installed elsewhere.

**Options (pick one):**
- **Keep as-is (simplest):** document that magenta must be run from a directory
  containing a `data/` folder. Acceptable for a subtree use case.
- **`importlib.resources`:** bundle `data/` inside `src/magenta/data/` and access
  via `importlib.resources.files("magenta.data")`. Requires adding to
  `[tool.setuptools.package-data]` in `pyproject.toml`. More work but fully portable.

---

## 6. Dockerfile Changes (in AIDM)

Once the above are done, the Dockerfile simplifies to:

```dockerfile
# Install magenta as a package
COPY magenta/ ./magenta/
RUN pip install --no-cache-dir ./magenta

# No symlink, no flat copy, no rm __init__.py
```

And `ENV PYTHONPATH=/app` stays for AIDM's own `app/` imports.

---

## Summary Checklist

- [ ] Restructure into `src/magenta/` layout
- [ ] Update `pyproject.toml` (src discovery, drop the `where = [".."]` hack)
- [ ] Fix imports in `main.py` (3 import blocks)
- [ ] Fix imports in `routes/chats.py` (4 lines)
- [ ] Fix imports in `routes/documents.py` (3 lines)
- [ ] Fix imports in `routes/prompts.py` (2 lines)
- [ ] Fix imports in `routes/tenants.py` (2 lines)
- [ ] Fix imports in `routes/tools.py` (2 lines)
- [ ] Fix imports in `services/chat_service.py` (3 lines)
- [ ] Fix imports in `services/data_import.py` (4 lines)
- [ ] Fix imports in `services/document_service.py` (2 lines)
- [ ] Fix imports in `tests/tests.py` (3 lines)
- [ ] Remove `logger.add()` from `core/config.py`, add to `main.py`
- [ ] Decide on `data/` directory strategy
- [ ] Update AIDM `Dockerfile`
- [ ] Verify AIDM app still works end-to-end
