---                                                                                                                                                                                   
  Function 1: call_gpt() (lines 12-83)                                     

  Purpose: Low-level wrapper around OpenAI's chat completions API.

  Parameters:
  ┌─────────────┬───────────────┬───────────────────────────────────────────┐
  │  Parameter  │     Type      │                Description                │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ messages    │ list[dict]    │ Conversation history                      │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ sysprompt   │ str           │ System prompt (inserted as first message) │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ client      │ OpenAI client │ Defaults to global openai_client          │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ json_mode   │ bool          │ Force JSON output format                  │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ model       │ str           │ Model name (default: gpt-4o)              │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ tools       │ list[dict]    │ Tool definitions                          │
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ tool_choice │ str           │ "auto", "none", or specific tool          │
  └─────────────┴───────────────┴───────────────────────────────────────────┘
  Logic:
  1. Insert system prompt as first message with role "developer"
  2. Strip internal fields (message_id, timestamp, tool_id) before API call
  3. Call OpenAI API (4 branches: json_mode × tools)
  4. Extract message content and tool_calls from response
  5. Return {"message": ..., "tool_calls": ... or None}

  Note: There's a bug on line 53 - uses result before it's defined (should be completion).

  ---
  Function 2: call_gpt_single() (lines 86-99)

  Purpose: Convenience wrapper for single-turn prompts without tool use.

  Parameters:
  ┌───────────┬────────────────────────────────────────────┐
  │ Parameter │                Description                 │
  ├───────────┼────────────────────────────────────────────┤
  │ prompt    │ Single user message string                 │
  ├───────────┼────────────────────────────────────────────┤
  │ sysprompt │ Optional system prompt                     │
  ├───────────┼────────────────────────────────────────────┤
  │ tools     │ Ignored (exists for signature consistency) │
  └───────────┴────────────────────────────────────────────┘
  Logic:
  1. Wrap prompt in [{"role": "user", "content": prompt}]
  2. Delegate to call_gpt() without tools
  3. Return result

  ---
  Function 3: get_tools() (lines 102-121)

  Purpose: Load tool definitions from MongoDB based on the prompt's toolset field.

  Parameters:
  ┌──────────────────┬─────────────────────────────────────────────────┐
  │    Parameter     │                   Description                   │
  ├──────────────────┼─────────────────────────────────────────────────┤
  │ sysprompt        │ Prompt object (dict) with optional toolset list │
  ├──────────────────┼─────────────────────────────────────────────────┤
  │ tools_collection │ MongoDB collection for tools                    │
  └──────────────────┴─────────────────────────────────────────────────┘
  Logic:
  1. Check if sysprompt has "toolset" field (list of tool names)
  2. For each tool name:
     a. Query MongoDB by function.name
     b. Validate against ToolWithContext model
     c. Strip context_parameters (hidden from LLM)
     d. Append to tools list
  3. Return tools list (or None if no toolset)

  Key behavior: Context parameters are removed here so the LLM never sees them.

  ---
  Function 4: call_llm_and_process_tools() (lines 124-192)

  Purpose: The tool execution loop - calls LLM, executes any requested tools, repeats until done.

  Parameters:
  ┌────────────────────────┬──────────────────────────────────────────┐
  │       Parameter        │               Description                │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ new_messages           │ Conversation messages (mutated in place) │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ sysprompt              │ Prompt object with prompt text           │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ tools                  │ Tool definitions for LLM                 │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ call_llm_func          │ LLM function to use (default: call_gpt)  │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ tool_handler           │ Function to execute tools                │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ tools_collection       │ MongoDB collection for tool lookup       │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ function_dictionary    │ Map of function names → Python callables │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ context_arguments      │ Hidden args to inject into tool calls    │
  ├────────────────────────┼──────────────────────────────────────────┤
  │ max_chained_tool_calls │ Loop limit (default: 10)                 │
  └────────────────────────┴──────────────────────────────────────────┘
  Logic:
  1. Call LLM with messages, sysprompt, and tools
  2. WHILE response contains tool_calls:
     a. Append assistant message with tool_calls to history
     b. Check iteration count (prevent infinite loop)
     c. FOR each tool_call:
        - Execute via tool_handler (injects context_arguments)
        - Append tool result to messages
     d. Call LLM again with updated messages
  3. Return final message

  Flow Diagram:
  ┌─────────────┐
  │  Call LLM   │
  └──────┬──────┘
         │
         ▼
  ┌──────────────────┐     No      ┌────────────┐
  │ Has tool_calls?  │────────────▶│   Return   │
  └────────┬─────────┘             └────────────┘
           │ Yes
           ▼
  ┌──────────────────┐
  │ Execute tools    │
  │ Append results   │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │ n_tries > 10?    │───Yes───▶ Raise Error
  └────────┬─────────┘
           │ No
           └──────────────────────┐
                                  │
           ┌──────────────────────┘
           ▼
      (loop back to Call LLM)

  ---
  Function 5: process_chat() (lines 195-359)

  Purpose: Main entry point - orchestrates the full chat processing pipeline.

  Parameters:
  ┌──────────────────────┬──────────────────────────────────┐
  │      Parameter       │           Description            │
  ├──────────────────────┼──────────────────────────────────┤
  │ chat_id              │ Chat session identifier          │
  ├──────────────────────┼──────────────────────────────────┤
  │ message_id           │ Unique ID for this exchange      │
  ├──────────────────────┼──────────────────────────────────┤
  │ new_message          │ User's message text              │
  ├──────────────────────┼──────────────────────────────────┤
  │ chats_collection     │ MongoDB collection for chats     │
  ├──────────────────────┼──────────────────────────────────┤
  │ prompts_collection   │ MongoDB collection for prompts   │
  ├──────────────────────┼──────────────────────────────────┤
  │ documents_collection │ MongoDB collection for documents │
  ├──────────────────────┼──────────────────────────────────┤
  │ tools_collection     │ MongoDB collection for tools     │
  ├──────────────────────┼──────────────────────────────────┤
  │ sysprompt_id         │ Override prompt ID (optional)    │
  ├──────────────────────┼──────────────────────────────────┤
  │ callback_func        │ Called with response on success  │
  ├──────────────────────┼──────────────────────────────────┤
  │ error_callback_func  │ Called with error on failure     │
  ├──────────────────────┼──────────────────────────────────┤
  │ dry_run              │ Skip LLM, return test message    │
  ├──────────────────────┼──────────────────────────────────┤
  │ json_mode            │ Force JSON output                │
  ├──────────────────────┼──────────────────────────────────┤
  │ tool_choice          │ Tool selection mode              │
  ├──────────────────────┼──────────────────────────────────┤
  │ call_llm_func        │ LLM function (injectable)        │
  ├──────────────────────┼──────────────────────────────────┤
  │ rag_func             │ RAG search function (injectable) │
  ├──────────────────────┼──────────────────────────────────┤
  │ rag_table_name       │ Override RAG table               │
  ├──────────────────────┼──────────────────────────────────┤
  │ persist_rag_results  │ Store RAG in DB vs. inline       │
  ├──────────────────────┼──────────────────────────────────┤
  │ context_arguments    │ Hidden tool parameters           │
  ├──────────────────────┼──────────────────────────────────┤
  │ function_dictionary  │ Python function registry         │
  ├──────────────────────┼──────────────────────────────────┤
  │ skip_word            │ Magic word to suppress response  │
  ├──────────────────────┼──────────────────────────────────┤
  │ sysprompt_suffix     │ Append to system prompt          │
  ├──────────────────────┼──────────────────────────────────┤
  │ new_images           │ Base64 images to include         │
  └──────────────────────┴──────────────────────────────────┘
  Logic (step by step):

  PHASE 1: SETUP
  ├── 1. Fetch chat from MongoDB
  ├── 2. Set status to "in_progress"
  ├── 3. Load system prompt (from chat or override)
  ├── 4. Append sysprompt_suffix if provided
  ├── 5. Load tools via get_tools()
  └── 6. Inject context documents into sysprompt

  PHASE 2: RAG
  ├── 7. Perform RAG search on user message
  └── 8. Get rag_result (search hits)

  PHASE 3: MESSAGE PREPARATION
  ├── 9. Build user message object
  │   ├── Text-only: {"role": "user", "content": "..."}
  │   └── With images: {"role": "user", "content": [{text}, {image_url}, ...]}
  ├── 10. Prefix message_id with "q-" (question)
  ├── 11. Save user message to MongoDB
  └── 12. Append RAG results to message content (if not persisted)

  PHASE 4: LLM CALL
  ├── 13. If dry_run: return test message
  └── 14. Else: call_llm_and_process_tools()

  PHASE 5: RESPONSE HANDLING
  ├── 15. Check skip_word (suppress if matched)
  ├── 16. Save status "completed" + assistant message to MongoDB
  ├── 17. Call callback_func if provided
  └── 18. Return result

  PHASE 6: ERROR HANDLING
  └── 19. On exception: call error_callback_func, re-raise

  ---
  Overall Architecture

  ┌─────────────────────────────────────────────────────────────────┐
  │                        process_chat()                           │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ 1. Load chat, prompt, tools from MongoDB                 │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                              │                                   │
  │                              ▼                                   │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ 2. RAG: add_documents_to_sysprompt()                     │   │
  │  │         add_rag_results_to_message()                     │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                              │                                   │
  │                              ▼                                   │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ 3. Save user message to MongoDB                          │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                              │                                   │
  │                              ▼                                   │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ 4. call_llm_and_process_tools()                          │   │
  │  │    ┌────────────────────────────────────────────────┐    │   │
  │  │    │ call_gpt() ◄──────────────────────┐            │    │   │
  │  │    │     │                             │            │    │   │
  │  │    │     ▼                             │            │    │   │
  │  │    │ tool_calls? ──Yes──► tool_handler()            │    │   │
  │  │    │     │                     │                    │    │   │
  │  │    │     No                    └────────────────────┘    │   │
  │  │    │     │                                               │   │
  │  │    │     ▼                                               │   │
  │  │    │  Return message                                     │   │
  │  │    └────────────────────────────────────────────────────┘    │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                              │                                   │
  │                              ▼                                   │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ 5. Save response to MongoDB, call callback               │   │
  │  └──────────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────────┘

  ---
  Key Design Decisions

  1. Dependency Injection: call_llm_func, rag_func, function_dictionary are all injectable, making the service testable and extensible.
  2. Message ID Convention: User messages get q-{id}, assistant responses get {id} - allows pairing question/answer.
  3. RAG Append Strategy: RAG results are appended to the user message after saving to DB, so stored messages stay clean but LLM sees context.
  4. Status Tracking: Each message has a status trail (in_progress → completed) for async monitoring.
  5. Callbacks: Supports both success and error callbacks for integration with external systems (e.g., webhooks, notifications).
