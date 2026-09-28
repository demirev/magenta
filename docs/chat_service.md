---                                                                                                                                                                                   
  Function 1: call_gpt()                                     

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
  ├─────────────┼───────────────┼───────────────────────────────────────────┤
  │ response_   │ dict          │ Optional response_format used in          │
  │ format      │               │ json_mode, e.g. a strict json_schema spec │
  │             │               │ (default: {"type": "json_object"})        │
  └─────────────┴───────────────┴───────────────────────────────────────────┘
  Logic:
  1. In json_object mode, append "Respond in JSON." to the system prompt if neither
     the prompt nor the messages contain the word "json" (OpenAI rejects the request otherwise)
  2. Prepend the system prompt as a "developer" message (the caller's list is not modified)
  3. Strip storage fields (message_id, timestamp, internal, tool_id) before the API call
  4. Call the OpenAI API; RateLimitError, APIConnectionError (incl. timeouts) and
     InternalServerError are retried up to 3 times with 1/2/4 s backoff
  5. In json_mode, parse the content:
     - missing or invalid JSON is retried once, then raises ValueError
       (content next to tool_calls is returned unparsed instead)
     - C0 control characters other than \t, \n, \r in any string (garbled \u00XX escapes)
       trigger up to 2 retries, after which they are stripped
  6. Return {"message": ..., "tool_calls": ... or None}

  To use Structured Outputs in process_chat, pass
  call_llm_func=functools.partial(call_gpt, response_format={"type": "json_schema", ...}).

  call_gpt_stream applies the same "json" word rule and request retries. Chunks are already
  sent when the content is checked, so control characters are stripped from the final
  message without a retry.

  ---
  Function 2: call_gpt_single()

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
  Function 3: get_tools()

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
  Function 4: call_llm_and_process_tools()

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
  ├────────────────────────┼──────────────────────────────────────────┤
  │ tool_messages          │ Optional list; each completed tool round │
  │                        │ is appended to it                        │
  └────────────────────────┴──────────────────────────────────────────┘
  Logic:
  1. Call LLM with messages, sysprompt, and tools
  2. WHILE response contains tool_calls:
     a. Build the round: assistant message with tool_calls
     b. Check iteration count (prevent infinite loop)
     c. FOR each tool_call:
        - Execute via tool_handler (injects context_arguments)
        - Add a tool message with the result (as a string)
     d. Append the round to new_messages and to tool_messages
     e. Call LLM again with updated messages
  3. Return {"message": final message}

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
  Function 5: process_chat()

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
  ├── 15. Check skip_word (suppress if matched; the callback is then not called)
  ├── 16. Save status "completed", the tool rounds as internal messages and the
  │       assistant message to MongoDB in one update
  ├── 17. Call callback_func if provided
  └── 18. Return result

  PHASE 6: ERROR HANDLING
  ├── 19. Save completed tool rounds as internal messages and push
  │       {"message_id", "status": "failed", "error": <first 500 chars>}
  └── 20. Call error_callback_func, re-raise

  stream_chat follows the same persistence and failure rules.
  aprocess_chat(*args, **kwargs) runs process_chat in a worker thread for async callers.

  Stored message layout of one exchange:
    {"message_id": "q-<id>", "role": "user", ...}
    {"message_id": "<id>", "role": "assistant", "tool_calls": [...], "internal": true, ...}   ┐ per tool
    {"message_id": "<id>", "role": "tool", "tool_call_id": ..., "content": "...", "internal": true, ...} ┘ round
    {"message_id": "<id>", "role": "assistant", "content": "...", ...}
  Internal messages are sent to the LLM on later turns and excluded from get_chat,
  list_chats, get_chat_message and list_chat_messages (unless no_internal=False).
  Deleting a message by id removes its internal messages too.

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
  4. Status Tracking: Each message has a status trail (in_progress → completed or failed) for async monitoring. The status routes report the latest entry and, for failures, the error under result.error.
  5. Callbacks: Supports both success and error callbacks for integration with external systems (e.g., webhooks, notifications).
