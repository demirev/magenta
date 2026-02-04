---
  Initial Data Loading

  Purpose: Automatically populate the database with prompts, tools, users, and documents on application startup.

  ---
  Startup Sequence

  The `lifespan` context manager in `main.py:31-60` runs the following loaders in order:

  ```
  1. create_postgres_extensions()     # Enable pgVector extension
  2. load_prompts_from_files()        # Load prompts from data/prompts/
  3. create_initial_users()           # Load users from data/users/
  4. load_all_functions_in_db()       # Load function tools into MongoDB
  5. load_documents_from_files()      # Load and vectorize documents
  6. cleanup_mongo()                  # Remove test data from previous runs
  ```

  All loaders run for every tenant in `tenant_collections`.

  ---
  Directory Structure

  ```
  data/
  ├── prompts/                    # System prompts and agent configs
  │   └── *.json                  # Single prompt or array of prompts
  ├── users/                      # Initial user accounts
  │   └── *.json                  # One user per file
  └── documents/
      ├── instructions/           # Document import instructions
      │   └── *.json              # References to files + metadata
      └── pdf/                    # Actual document files
          └── *.pdf
  ```

  ---
  Prompts

  **Loader**: `load_prompts_from_files()` in `services/data_import.py:12-51`
  **Directory**: `data/prompts/`
  **Destination**: MongoDB `prompts` collection (per tenant)

  File Format (single or array):
  ```json
  [
    {
      "prompt_id": "diceroller",
      "name": "Dice Roller",
      "type": "agent",
      "description": "An assistant that throws dice.",
      "prompt": "You are an AI assistant that throws dice...",
      "toolset": ["roll_dice"]
    }
  ]
  ```

  Behavior:
  - Reads all `*.json` files in the directory
  - Validates each prompt against the `Prompt` model
  - By default (`drop_if_exists=True`), replaces existing prompts with same `prompt_id`
  - Logs each loaded prompt

  ---
  Users

  **Loader**: `create_initial_users()` in `core/security.py:114-137`
  **Directory**: `data/users/`
  **Destination**: MongoDB `users` collection (global, not per-tenant)

  File Format (one user per file):
  ```json
  {
    "username": "test_user",
    "password": "plaintext_password_here",
    "type": "test_user",
    "disabled": false
  }
  ```

  Behavior:
  - Reads all `*.json` files in the directory
  - Hashes the password using bcrypt (via `get_password_hash()`)
  - Removes plaintext password before storing
  - Validates against `UserInDB` model
  - Skips if username already exists (no overwrite)
  - Returns count of users created

  ---
  Function Tools

  **Loader**: `load_all_functions_in_db()` in `core/tools.py:151-183`
  **Source**: `default_function_tool_definitions` list in `core/tools.py`
  **Destination**: MongoDB `tools` collection (per tenant)

  Unlike other loaders, function tools are defined in Python code, not JSON files:

  ```python
  # Python implementation
  def roll_dice(d: int) -> int:
      return random.randint(1, d)

  # Tool definition
  default_function_tool_definitions = [
      {
          "tool_id": "rolldice",
          "type": "function",
          "function": {
              "name": "roll_dice",
              "description": "Roll a dice with d sides",
              "parameters": {...}
          }
      }
  ]

  # Function registry
  default_function_dictionary = {
      "roll_dice": roll_dice
  }
  ```

  Behavior:
  - Validates function dictionary against definitions (parameter names, types, required flags)
  - Raises `ValueError` on validation failure (prevents startup)
  - By default (`overwrite=True`), replaces existing tools with same `function.name`
  - Inserts into all tenant tool collections

  ---
  Documents

  **Loader**: `load_documents_from_files()` in `services/data_import.py:54-143`
  **Directory**: `data/documents/instructions/`
  **Destination**: MongoDB `documents` collection + PostgreSQL vector table (per tenant)

  File Format (import instructions, not the documents themselves):
  ```json
  [
    {
      "document_id": "test_blue_team",
      "name": "test_blue_team_password.pdf",
      "type": "test_document",
      "file_location": "data/documents/pdf/test_blue_team_password.pdf",
      "content_type": "application/pdf",
      "chunk_size": 1000,
      "metadata": {}
    }
  ]
  ```

  Required Fields:
  | Field          | Description                                      |
  |----------------|--------------------------------------------------|
  | document_id    | Unique identifier (auto-generated if omitted)    |
  | file_location  | Path to the actual document file                 |
  | content_type   | MIME type (e.g., "application/pdf")              |
  | name           | Display name                                     |
  | type           | Classification string                            |
  | metadata       | Arbitrary metadata object                        |
  | chunk_size     | Characters per chunk for vectorization (default: 1000) |

  Behavior:
  1. Creates PostgreSQL vector table for tenant if needed
  2. Reads all `*.json` instruction files
  3. For each instruction:
     - Inserts document metadata into MongoDB with status "pending"
     - Calls `process_document()` to extract text, chunk, and vectorize
     - Updates status to "completed" when done
  4. By default (`drop_if_exists=True`), removes existing documents with same ID or name

  ---
  Test Data Cleanup

  After loading, `cleanup_mongo()` removes known test artifacts:

  ```python
  # From main.py:45-50
  cleanup_mongo(tools, [{"function.name": {"$in": ["test_tool", "duplicate_tool", ...]}}])
  cleanup_mongo(documents, [{"name": {"$in": ["test_document", ...]}}])
  cleanup_mongo(chats, [{"chat_id": {"$in": ["test_rag_chat", ...]}}])
  cleanup_mongo(prompts, [{"name": "test_prompt"}])
  cleanup_mongo(tenants, [{"tenant_id": "test_tenant"}])
  ```

  This ensures a clean state after test runs without requiring a full database reset.

  ---
  Customization

  To add initial data to a new deployment:

  1. **Prompts**: Add JSON files to `data/prompts/`
  2. **Users**: Add JSON files to `data/users/` (one per file, plaintext password)
  3. **Function tools**: Define in `core/tools.py` and add to `default_function_dictionary`
  4. **Documents**: Add PDF to `data/documents/pdf/`, add import instruction to `data/documents/instructions/`

  To skip loading (e.g., for tests), the loaders check if directories exist and log a message if not.
