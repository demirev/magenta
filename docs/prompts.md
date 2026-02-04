---
  Prompts

  Purpose: Define system prompts that configure LLM behavior, attach tools, and link documents for RAG.

  ---
  Data Model

  A prompt is defined by the `Prompt` class in `core/models.py:43-50`:

  ```python
  class Prompt(BaseModel):
    prompt_id: str
    name: str
    type: Literal["system", "agent"]
    description: Optional[str] = None
    prompt: str
    toolset: Optional[list[str]] = None
    documents: Optional[RagSpec] = None
  ```

  Field Reference:
  ┌─────────────┬──────────────────────┬───────────────────────────────────────────────────────────┐
  │   Field     │        Type          │                       Description                         │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ prompt_id   │ str                  │ Unique identifier (auto-generated from name if omitted)   │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ name        │ str                  │ Human-readable name (must be unique)                      │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ type        │ "system" | "agent"   │ Classification of the prompt                              │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ description │ str (optional)       │ Human-readable description                                │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ prompt      │ str                  │ The actual system prompt text sent to the LLM             │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ toolset     │ list[str] (optional) │ List of tool function names available to this prompt      │
  ├─────────────┼──────────────────────┼───────────────────────────────────────────────────────────┤
  │ documents   │ RagSpec (optional)   │ Document configuration for RAG and context injection      │
  └─────────────┴──────────────────────┴───────────────────────────────────────────────────────────┘

  ---
  Toolset

  The `toolset` field is a list of tool function names (not tool_ids) that the LLM can call when using this prompt.

  Example:
  ```json
  "toolset": ["roll_dice", "get_current_utc_datetime"]
  ```

  When a prompt with a toolset is used, the chat service:
  1. Looks up each tool by `function.name` in MongoDB
  2. Validates the tool against the `ToolWithContext` model
  3. Strips `context_parameters` (hidden from LLM)
  4. Passes the tool definitions to the OpenAI API

  ---
  Document Configuration (RagSpec)

  The `documents` field uses the `RagSpec` model (`core/models.py:36-40`):

  ```python
  class RagSpec(BaseModel):
    rag_documents: list[RagDocument]
    context_documents: list[RagDocument]
    rag_connecting_prompt: Optional[str] = None
    context_connecting_prompt: Optional[str] = None
  ```

  Two Document Modes:
  ┌────────────────────┬─────────────────────────────────────────────────────────────────┐
  │       Mode         │                          Behavior                               │
  ├────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ rag_documents      │ Retrieved via semantic similarity search against user message.  │
  │                    │ Relevant chunks appended to user message at runtime.            │
  ├────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ context_documents  │ Full document text injected directly into the system prompt.    │
  │                    │ Always included, regardless of user message content.            │
  └────────────────────┴─────────────────────────────────────────────────────────────────┘

  Each RagDocument has:
  - `document_id`: Reference to a document in MongoDB
  - `table_name`: PostgreSQL table containing the document's vector embeddings

  Connecting Prompts:
  - `rag_connecting_prompt`: Text inserted before RAG results (default: "Related information:")
  - `context_connecting_prompt`: Text inserted before context documents

  ---
  Examples

  Minimal Prompt:
  ```json
  {
    "prompt_id": "demoassistant0",
    "name": "Demo Assistant",
    "type": "system",
    "prompt": "You are a helpful assistant."
  }
  ```

  Prompt with Tools:
  ```json
  {
    "prompt_id": "diceroller",
    "name": "Dice Roller",
    "type": "agent",
    "description": "An assistant that throws dice.",
    "prompt": "You are an AI assistant that throws dice for the user...",
    "toolset": ["roll_dice"]
  }
  ```

  Prompt with RAG and Context Documents:
  ```json
  {
    "prompt_id": "passwordteller",
    "name": "Password Teller",
    "type": "agent",
    "prompt": "You are an AI assistant that shares secrets...",
    "documents": {
      "rag_documents": [
        {"document_id": "test_red_team", "table_name": "test_collection"}
      ],
      "context_documents": [
        {"document_id": "test_blue_team", "table_name": "test_collection"}
      ],
      "rag_connecting_prompt": "\nRelevant excerpts:\n",
      "context_connecting_prompt": "\nReference information:\n"
    }
  }
  ```

  ---
  Prompt Lifecycle in Chat Processing

  When `process_chat()` is called:

  1. Prompt is loaded from MongoDB by `sysprompt_id`
  2. If `sysprompt_suffix` is provided, it's appended to the prompt text
  3. Tools are loaded via `get_tools()` if `toolset` is defined
  4. Context documents are injected into the prompt via `add_documents_to_sysprompt()`
  5. RAG search is performed and results appended to user message
  6. The assembled prompt is sent to OpenAI as the "developer" role message

  ---
  API Endpoints

  | Method | Endpoint               | Description              |
  |--------|------------------------|--------------------------|
  | POST   | /prompts/create        | Create a new prompt      |
  | GET    | /prompts/              | List prompts (filterable)|
  | GET    | /prompts/{prompt_id}   | Get a specific prompt    |
  | PUT    | /prompts/{prompt_id}   | Update a prompt          |
  | DELETE | /prompts/{prompt_id}   | Delete a prompt          |

  ---
  Storage

  Prompts are stored in MongoDB in per-tenant `prompts` collections.

  On startup, prompts can be auto-loaded from JSON files in `data/prompts/` via `load_prompts_from_files()` in `services/data_import.py`.
