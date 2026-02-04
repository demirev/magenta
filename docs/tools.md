---
  Tools

  Purpose: Define callable functions that the LLM can invoke during chat processing.

  ---
  Tool Types

  Magenta supports two types of tools:

  ┌──────────────┬────────────────────────────────────────────────────────────────┐
  │     Type     │                          Description                           │
  ├──────────────┼────────────────────────────────────────────────────────────────┤
  │ function     │ Python callable executed locally. Must be registered in the    │
  │              │ function_dictionary with a matching implementation.            │
  ├──────────────┼────────────────────────────────────────────────────────────────┤
  │ external     │ HTTP endpoint called via httpx. Supports GET, POST, PUT,       │
  │              │ DELETE methods. Arguments passed as query params or JSON body. │
  └──────────────┴────────────────────────────────────────────────────────────────┘

  ---
  Data Model

  Tools are defined using several models in `core/models.py`:

  Base Tool (`Tool`, lines 123-126):
  ```python
  class Tool(BaseModel):
    tool_id: str
    type: Literal["function", "external"]
    function: ToolBody
  ```

  Tool with Context Parameters (`ToolWithContext`, lines 142-144):
  ```python
  class ToolWithContext(Tool):
    function: Union[ToolBody, ExternalToolBody]
    context_parameters: Optional[list[ContextParameter]] = None
  ```

  Function Body (`ToolBody`, lines 112-115):
  ```python
  class ToolBody(BaseModel):
    name: str
    description: str
    parameters: ToolParameters
  ```

  External Tool Body (`ExternalToolBody`, lines 118-120):
  ```python
  class ExternalToolBody(ToolBody):
    url: HttpUrl
    method: HttpMethod  # GET, POST, PUT, DELETE
  ```

  ---
  Parameter Definition

  Parameters are defined using `ToolParameters` and `ToolParameter`:

  ```python
  class ToolParameters(BaseModel):
    type: Literal["object"]
    properties: dict[str, ToolParameter]
    required: list[str]

  class ToolParameter(BaseModel):
    type: Literal["string", "integer", "array"]
    description: str
    enum: Optional[list[str]] = None      # For restricted values
    items: Optional[dict] = None          # For array item schema
    min_items: Optional[int] = None       # Array minimum length
    max_items: Optional[int] = None       # Array maximum length
  ```

  Supported Parameter Types:
  | Type    | Python Annotation | Notes                          |
  |---------|-------------------|--------------------------------|
  | string  | str               |                                |
  | integer | int               |                                |
  | array   | List[str]         | Currently only string arrays   |

  ---
  Context Parameters

  Context parameters are values injected at runtime that the LLM never sees.

  ```python
  class ContextParameter(BaseModel):
    name: str
    type: Literal["string", "integer", "array"]
    description: str
    items: Optional[dict] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None
  ```

  Use cases:
  - Passing user IDs or session IDs to tools
  - Injecting API keys or credentials
  - Providing tenant-specific context

  Context parameters are:
  1. Stripped from tool definitions before sending to OpenAI (in `get_tools()`)
  2. Merged with LLM-provided arguments when executing (in `tool_handler()`)
  3. Passed via `context_arguments` parameter to `process_chat()`

  ---
  Function Tool Example

  Definition (in `core/tools.py`):
  ```python
  # Python implementation
  def roll_dice(d: int) -> int:
    return random.randint(1, d)

  # JSON schema for OpenAI
  {
    "tool_id": "rolldice",
    "type": "function",
    "function": {
      "name": "roll_dice",
      "description": "Roll a dice with d sides",
      "parameters": {
        "type": "object",
        "properties": {
          "d": {
            "type": "integer",
            "description": "Number of sides on the dice"
          }
        },
        "required": ["d"]
      }
    }
  }

  # Register in function dictionary
  default_function_dictionary = {
    "roll_dice": roll_dice
  }
  ```

  ---
  External Tool Example

  ```json
  {
    "tool_id": "weather-api",
    "type": "external",
    "function": {
      "name": "get_weather",
      "description": "Get current weather for a city",
      "parameters": {
        "type": "object",
        "properties": {
          "city": {
            "type": "string",
            "description": "City name"
          }
        },
        "required": ["city"]
      },
      "url": "https://api.weather.example/current",
      "method": "GET"
    }
  }
  ```

  HTTP Method Behavior:
  | Method | Arguments Sent As      |
  |--------|------------------------|
  | GET    | Query parameters       |
  | POST   | JSON body              |
  | PUT    | JSON body              |
  | DELETE | Query parameters       |

  ---
  Tool Execution Flow

  When the LLM requests a tool call:

  1. `call_llm_and_process_tools()` receives tool_calls from OpenAI response
  2. For each tool call, `tool_handler()` is invoked (`core/tools.py:186-236`)
  3. Tool is looked up by `function.name` in MongoDB
  4. Context arguments are merged with LLM-provided arguments
  5. Execution based on type:
     - **function**: Look up Python callable in `function_dictionary`, execute with arguments
     - **external**: Make HTTP request via httpx with appropriate method
  6. Result is appended to messages with role "tool"
  7. LLM is called again with updated message history
  8. Loop continues until LLM responds without tool calls (max 10 iterations)

  ```
  ┌─────────────┐
  │  LLM Call   │
  └──────┬──────┘
         │
         ▼
  ┌──────────────────┐     No      ┌────────────┐
  │ Has tool_calls?  │────────────▶│   Return   │
  └────────┬─────────┘             └────────────┘
           │ Yes
           ▼
  ┌──────────────────┐
  │  tool_handler()  │
  │  ├─ function:    │
  │  │   call Python │
  │  └─ external:    │
  │      HTTP request│
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │ Append result    │
  │ Loop back        │───────────▶ (max 10 iterations)
  └──────────────────┘
  ```

  ---
  Validation

  Before tools are loaded into MongoDB, `validate_function_dictionary()` checks:

  1. Every tool definition has a corresponding Python function (for function type)
  2. Function parameter names match definition
  3. Parameter types match (string → str, integer → int, array → List[str])
  4. Required parameters don't have default values
  5. Optional parameters aren't marked as required

  Validation errors cause startup to fail with detailed error messages.

  ---
  API Endpoints

  | Method | Endpoint            | Description               |
  |--------|---------------------|---------------------------|
  | POST   | /tools/create       | Create a new tool         |
  | GET    | /tools/             | List all tools            |
  | GET    | /tools/ids          | List tool IDs and names   |
  | GET    | /tools/{tool_id}    | Get a specific tool       |
  | PUT    | /tools/{tool_id}    | Update a tool             |
  | DELETE | /tools/{tool_id}    | Delete a tool             |

  ---
  Storage

  Tools are stored in MongoDB in per-tenant `tools` collections.

  On startup, function tools are auto-loaded via `load_all_functions_in_db()` in `core/tools.py`, which:
  1. Validates the function dictionary against definitions
  2. Inserts/updates tool documents in MongoDB for all tenant collections
