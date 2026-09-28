import re
import json
import time
import asyncio
import openai
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import Depends
from core.config import logger, openai_client, spacy_model, get_db
from core.models import ToolWithContext
from core.tools import tool_handler, default_function_dictionary
from .document_service import perform_postgre_search, add_rag_results_to_message, add_documents_to_sysprompt


# OpenAI errors worth retrying with backoff (APITimeoutError is a subclass of APIConnectionError)
TRANSIENT_ERRORS = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)


def strip_control_chars(value):
  # recursively remove C0 control characters other than \t, \n, \r from all strings in a parsed JSON value
  if isinstance(value, str):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
  if isinstance(value, list):
    return [strip_control_chars(v) for v in value]
  if isinstance(value, dict):
    return {strip_control_chars(k): strip_control_chars(v) for k, v in value.items()}
  return value


def call_gpt(
    messages: list[dict], 
    sysprompt: str = None, 
    client=openai_client, 
    json_mode: bool = False, 
    model: str = "gpt-4o", 
    tools: list[dict] = None, 
    tool_choice: str = "auto",
    response_format: Optional[dict] = None # overrides json_object in json_mode, e.g. a json_schema spec
  ) -> dict:
  logger.info(f"Calling GPT")
  if json_mode and (response_format or {}).get("type", "json_object") == "json_object" and not any(
    "json" in str(m.get("content")).lower() for m in messages + [{"content": sysprompt}]
  ):
    # OpenAI rejects json_object mode unless the word "json" appears in the messages
    sysprompt = "Respond in JSON." if sysprompt is None else sysprompt + "\n\nRespond in JSON."

  if sysprompt is not None:
    messages = [{"role": "developer", "content": sysprompt}] + messages

  # make sure messages don't include message_id, timestamp and internal
  messages = [{k: v for k, v in d.items() if k not in ("message_id", "timestamp", "internal")} for d in messages]

  # OpenAI requires content to be a string (or multimodal array), not a dict
  for msg in messages:
    if isinstance(msg.get("content"), dict):
      msg["content"] = json.dumps(msg["content"])

  if not tools:
    logger.info(f"No tools found.")
    tools = []
  else:
    # remove tool_id from each tool
    tools = [{k: v for k, v in d.items() if k != "tool_id"} for d in tools]
    logger.info(f"Tools found: {tools}")

  kwargs = {"model": model, "messages": messages}
  if json_mode:
    kwargs["response_format"] = response_format or {"type": "json_object"}
  if len(tools):
    kwargs["tools"] = tools
    kwargs["tool_choice"] = tool_choice

  n_transient = 0
  n_bad = 0 # bad JSON-mode completions: retried twice for control characters, once for missing or invalid JSON
  while True:
    try:
      completion = client.chat.completions.create(**kwargs)
    except TRANSIENT_ERRORS as e:
      if n_transient >= 3:
        raise
      logger.warning(f"Transient OpenAI error, retrying in {2 ** n_transient}s: {e}")
      time.sleep(2 ** n_transient)
      n_transient += 1
      continue

    content = completion.choices[0].message.content
    if not json_mode:
      result = {"message": content}
      break

    try:
      parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as e:
      if completion.choices[0].message.tool_calls:
        # content alongside tool calls is not used as the answer
        result = {"message": content}
        break
      if n_bad >= 1:
        raise ValueError(f"LLM returned no valid JSON in json_mode (content: {content!r:.200})") from e
      logger.warning(f"LLM returned no valid JSON in json_mode, retrying: {content!r:.200}")
      n_bad += 1
      continue

    cleaned = strip_control_chars(parsed)
    if cleaned != parsed and n_bad < 2:
      logger.warning(f"Control characters in JSON-mode completion, retrying: {content!r:.200}")
      n_bad += 1
      continue
    if cleaned != parsed:
      logger.warning("Control characters in JSON-mode completion after retries, stripping them.")
    result = {"message": cleaned}
    break

  logger.info(f"Completion received: {completion.choices[0].message}")

  if completion.choices[0].message.tool_calls:
    logger.info(f"Tool calls detected.")
    logger.info(f"Tool calls: {completion.choices[0].message.tool_calls}")
    result["tool_calls"] = completion.choices[0].message.tool_calls
  else:
    logger.info(f"No tool calls detected.")
    result["tool_calls"] = None

  return result


def call_gpt_single(
    prompt, sysprompt=None, client=openai_client, 
    json_mode=False, model="gpt-4o",
    tools=[] # this is added for signature consistency with call_gpt
  ):
  # same as call_gpt but takes single prompt as input, no tools
  logger.info(f"Calling GPT")
  messages = [{"role": "user", "content": prompt}]
  
  result = call_gpt(
    messages=messages, sysprompt=sysprompt, client=client, json_mode=json_mode, model=model
  )

  return result


def call_gpt_stream(
    messages: list[dict],
    sysprompt: str = None,
    client=openai_client,
    json_mode: bool = False,
    model: str = "gpt-4o",
    tools: list[dict] = None,
    tool_choice: str = "auto"
):
  """Streaming version of call_gpt. Yields text chunks as they arrive.
  Access the full result dict (message + tool_calls) via StopIteration.value
  after exhausting the generator."""
  logger.info("Calling GPT (streaming)")
  if json_mode and not any("json" in str(m.get("content")).lower() for m in messages + [{"content": sysprompt}]):
    # OpenAI rejects json_object mode unless the word "json" appears in the messages
    sysprompt = "Respond in JSON." if sysprompt is None else sysprompt + "\n\nRespond in JSON."

  if sysprompt is not None:
    messages = [{"role": "developer", "content": sysprompt}] + messages

  messages = [{k: v for k, v in d.items() if k not in ("message_id", "timestamp", "internal")} for d in messages]

  for msg in messages:
    if isinstance(msg.get("content"), dict):
      msg["content"] = json.dumps(msg["content"])

  kwargs = {}
  if json_mode:
    kwargs["response_format"] = {"type": "json_object"}
  if tools:
    tools = [{k: v for k, v in d.items() if k != "tool_id"} for d in tools]
    kwargs["tools"] = tools
    kwargs["tool_choice"] = tool_choice

  # only the request is retried; errors after streaming has started propagate
  n_transient = 0
  while True:
    try:
      stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        **kwargs
      )
      break
    except TRANSIENT_ERRORS as e:
      if n_transient >= 3:
        raise
      logger.warning(f"Transient OpenAI error, retrying in {2 ** n_transient}s: {e}")
      time.sleep(2 ** n_transient)
      n_transient += 1

  full_content = ""
  raw_tool_calls = {}

  for chunk in stream:
    if not chunk.choices:
      continue
    delta = chunk.choices[0].delta
    if delta.content:
      full_content += delta.content
      yield delta.content
    if delta.tool_calls:
      for tc in delta.tool_calls:
        i = tc.index
        if i not in raw_tool_calls:
          raw_tool_calls[i] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        if tc.id:
          raw_tool_calls[i]["id"] += tc.id
        if tc.function.name:
          raw_tool_calls[i]["function"]["name"] += tc.function.name
        if tc.function.arguments:
          raw_tool_calls[i]["function"]["arguments"] += tc.function.arguments

  tool_calls = [raw_tool_calls[i] for i in sorted(raw_tool_calls)] if raw_tool_calls else None
  logger.info(f"Streaming completion received. tool_calls: {tool_calls is not None}")

  if json_mode and tool_calls is None:
    # chunks are already sent, so garbled escapes are stripped from the final message rather than retried
    try:
      parsed = json.loads(full_content)
    except json.JSONDecodeError:
      logger.warning(f"Streamed JSON-mode completion is not valid JSON: {full_content!r:.200}")
      parsed = None
    cleaned = strip_control_chars(parsed)
    if cleaned != parsed:
      logger.warning("Control characters in streamed JSON-mode completion, stripping them.")
      full_content = json.dumps(cleaned, ensure_ascii=False)
  return {"message": full_content, "tool_calls": tool_calls}


def get_tools(sysprompt, tools_collection):
  if "toolset" in sysprompt:
    logger.info(f"Toolset found in sysprompt: {sysprompt['toolset']}")
    tools = []
    tools_names = sysprompt["toolset"]
    for tool_name in tools_names:
      tool = tools_collection.find_one({"function.name": tool_name}, {"_id": 0})
      if not tool:
        raise ValueError(f"Tool {tool_name} not found.")      
      # Validate the tool using the ToolWithContext model
      validated_tool = ToolWithContext(**tool)
      tool_dict = validated_tool.model_dump(exclude_none=True)
      # Remove context parameters before appending to tools list
      if validated_tool.context_parameters:
        tool_dict.pop('context_parameters')
      tools.append(tool_dict)
  else:
    tools = None
  logger.info(f"Tools found in db: {tools}") 
  return tools


def call_llm_and_process_tools(
    new_messages, sysprompt, tools, call_llm_func, 
    tool_handler, tools_collection, 
    function_dictionary,
    json_mode=False,
    tool_choice="auto",
    context_arguments=None,
    model="gpt-4o",
    max_chained_tool_calls=100,
    tool_messages: Optional[list] = None # if given, each completed tool round (assistant tool_calls + tool results) is appended to it
):
  logger.info("Calling LLM")
      
  llm_result = call_llm_func(
    messages=new_messages, 
    sysprompt=sysprompt["prompt"],
    tools=tools,
    json_mode=json_mode,
    tool_choice=tool_choice,
    model=model
  )
  logger.info(f"LLM response received: {llm_result['message']}")
  
  n_tries = 0
  while llm_result["tool_calls"] is not None:
    round_messages = [
      {
        "role":"assistant", 
        "tool_calls":[tool_call.model_dump() for tool_call in llm_result["tool_calls"]]
      }
    ]
    
    n_tool_calls = len(llm_result["tool_calls"])
    logger.info(f"{n_tool_calls} tool calls detected. Iteration {n_tries}")

    # make sure we don't get stuck in an infinite loop
    if n_tries > max_chained_tool_calls:
      raise ValueError("Too many chained tool calls.")
    n_tries += 1
    
    # iterate over tool calls and append it openai format
    for tool_call in llm_result["tool_calls"]:
      logger.info(f"Calling tool {tool_call.function.name}")
      tool_result = tool_handler(
        name = tool_call.function.name,
        arguments = json.loads(tool_call.function.arguments),
        tools_collection=tools_collection,
        function_dictionary=function_dictionary,
        context_arguments = context_arguments
      )
      logger.info(f"Tool {tool_call.function.name} returned: {tool_result}")
      round_messages.append(
        {
          "tool_call_id": tool_call.id,
          "role":"tool",
          "name": tool_call.function.name,
          "content": str(tool_result),
        }
      )

    new_messages.extend(round_messages)
    if tool_messages is not None:
      tool_messages.extend(round_messages)

    # new call with tool results
    logger.info("Calling LLM with tool results.")
    llm_result = call_llm_func(
      messages=new_messages,
      sysprompt=sysprompt["prompt"],
      tools=tools,
      json_mode=json_mode,
      tool_choice="auto",  # always auto after first tool call so LLM can choose to stop
      model=model
    )

  result = {"message":llm_result["message"]}
  return result


def process_chat(
    chat_id: str,
    message_id: str,
    new_message: str,
    chats_collection,
    prompts_collection,
    documents_collection,
    tools_collection,
    sysprompt_id: Optional[str] = None, # used to overwrite the sysprompt id saved in the chat object
    callback_func=None,
    error_callback_func=None,
    dry_run=False,
    json_mode=False,
    tool_choice="auto",
    call_llm_func=call_gpt,
    model="gpt-5-mini",
    rag_func=perform_postgre_search,
    rag_table_name: str = None,
    persist_rag_results=False,
    context_arguments=None,
    db: Session = Depends(get_db),
    spacy_model=spacy_model,
    max_chained_tool_calls=10,
    function_dictionary=default_function_dictionary,
    skip_word=None, # e.g. "PASS" might mean "don't send message" depending on the prompt
    sysprompt_suffix: Optional[str] = None, # this will be added to the end of the sysprompt. Usefull for runtime modifications of the sysprompt
    new_images: Optional[list[str]] = None # a list of base64 encoded images to be added to the message
):
  tool_messages = [] # set to None once the turn is saved
  try:

    # Get the chat history
    chat = chats_collection.find_one({"chat_id": chat_id})
    if not chat:
      raise ValueError(f"Chat {chat_id} not found.")

    # Update chat status to in_progress
    new_status = {"message_id": message_id, "status": "in_progress"}
    chats_collection.update_one(
      {"chat_id": chat_id}, {"$push": {"statuses": new_status}}
    )

    # Find the sysprompt
    if sysprompt_id is None:
      sysprompt_id = chat["sysprompt_id"] # get saved sysprompt id from chat
      if not sysprompt_id:
        logger.error(f"System prompt for chat {chat_id} not found.")
        raise ValueError(f"System prompt for chat {chat_id} not found.")
    
    sysprompt = prompts_collection.find_one({"prompt_id": sysprompt_id})
    if not sysprompt:
      raise ValueError(f"Prompt {sysprompt_id} not found.")
    
    if sysprompt_suffix is not None:
      sysprompt["prompt"] = sysprompt["prompt"] + "\n\n" + sysprompt_suffix

    # check if prompt object includes "toolset"
    tools = get_tools(sysprompt, tools_collection)
    
    # check if the prompt object includes documents that need to be injected to the system prompt
    sysprompt = add_documents_to_sysprompt(sysprompt, documents_collection)
    
    # Perform RAG
    new_message, rag_result = add_rag_results_to_message(
      sysprompt=sysprompt, 
      new_message=new_message, 
      rag_func=rag_func, 
      db=db,
      spacy_model=spacy_model,
      persist_rag_results=persist_rag_results,
      table_name=rag_table_name
    )

    # add new message and update collection
    old_messages = chat["messages"]
    if new_images is None:
      new_message = {
        "message_id":"q-"+message_id, 
        "role": "user", 
        "content": new_message, 
        "timestamp": datetime.now()
      }
    else:
      new_message = {
        "message_id":"q-"+message_id, 
        "role": "user", 
        "content": [
          {"type": "text", "text": new_message}
        ] + [
          {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}} 
          for image in new_images
        ],
        "timestamp": datetime.now()
      }
    new_messages = old_messages + [new_message]
    
    # note: we add 'q-' to the message_id to differentiate between user and assistant messages part of the same exchange
    chats_collection.update_one(
      {"chat_id": chat_id}, {"$push": {"messages": new_message}} # push only the new message
    )

    if rag_result is not None and not persist_rag_results:
      rag_connecting_prompt = sysprompt.get("documents", {}).get("rag_connecting_prompt", "Related information:")
      new_messages[-1]["content"] = (
        new_messages[-1]["content"] + 
        "\n\n" + 
        rag_connecting_prompt + 
        "\n" + 
        rag_result
      )  # only add rag result after the message has been added to the db

    # call LLM
    if dry_run:
      logger.info("Dry run enabled. Skipping LLM calls.")
      result = {
        "message": "This is a test message."
      }
    else:
      result = call_llm_and_process_tools(
        new_messages=new_messages, 
        sysprompt=sysprompt, 
        tools=tools, 
        call_llm_func=call_llm_func, 
        json_mode=json_mode,
        tool_choice=tool_choice,
        tool_handler=tool_handler,
        tools_collection=tools_collection,
        context_arguments=context_arguments,
        function_dictionary=function_dictionary,
        max_chained_tool_calls=max_chained_tool_calls,
        model=model,
        tool_messages=tool_messages
      )

    if skip_word is not None: 
      # check if message is special value meaning "don't send message" was returned
      if result["message"] == skip_word:
        result.pop("message")
    
    # update mongo; tool rounds are stored as internal messages ahead of the response so later turns replay them
    new_status = {"message_id": message_id, "status": "completed"}
    content = result.get("message")
    if isinstance(content, dict):
      content = json.dumps(content)
    response_message = {"message_id": message_id, "role": "assistant", "content": content, "timestamp": datetime.now()}
    internal_messages = [
      {**m, "message_id": message_id, "internal": True, "timestamp": datetime.now()} for m in tool_messages
    ]
    
    chats_collection.update_one(
      {"chat_id": chat_id}, 
      {"$push": {"statuses": new_status, "messages": {"$each": internal_messages + [response_message]}}}
    )
    tool_messages = None
    logger.info(f"Chat {chat_id} completed successfully.")

    # send messages
    if callback_func is not None and "message" in result:
      logger.info(f"Sending messages for chat {chat_id}.")
      
      #session_id = chats_collection.find_one(
      #  {"chat_id": chat_id}
      #)["context_id"]
      
      callback_func(
        result["message"], 
        chat_id
      )

      logger.info(f"Message callback sent successfully: {result['message']}")

    return result

  except Exception as e:
    logger.error(f"Error processing chat {chat_id}: {e}")
    if tool_messages is not None:
      # keep completed tool rounds (their side effects happened) and mark the turn as failed
      try:
        chats_collection.update_one(
          {"chat_id": chat_id},
          {"$push": {
            "statuses": {"message_id": message_id, "status": "failed", "error": str(e)[:500]},
            "messages": {"$each": [
              {**m, "message_id": message_id, "internal": True, "timestamp": datetime.now()} for m in tool_messages
            ]}
          }}
        )
      except Exception as db_error:
        logger.error(f"Could not record failure of chat {chat_id}: {db_error}")
    if error_callback_func is not None:
      error_callback_func(chat_id, e)
    raise e


def stream_chat(
    chat_id: str,
    message_id: str,
    new_message: str,
    chats_collection,
    prompts_collection,
    documents_collection,
    tools_collection,
    sysprompt_id: Optional[str] = None,
    dry_run=False,
    json_mode=False,
    tool_choice="auto",
    call_llm_func=call_gpt_stream,
    model="gpt-4o",
    rag_func=perform_postgre_search,
    rag_table_name: str = None,
    persist_rag_results=False,
    context_arguments=None,
    db: Session = None,
    spacy_model=spacy_model,
    function_dictionary=default_function_dictionary,
    skip_word=None,
    sysprompt_suffix: Optional[str] = None,
    new_images: Optional[list[str]] = None,
    max_chained_tool_calls=10
):
  """Sync generator for streaming chat responses. Yields SSE-formatted strings.
  Handles the full pipeline (setup, RAG, LLM, tool loop, DB save) inline."""
  tool_messages = [] # set to None once the turn is saved
  try:
    # Get the chat history
    chat = chats_collection.find_one({"chat_id": chat_id})
    if not chat:
      raise ValueError(f"Chat {chat_id} not found.")

    # Update chat status to in_progress
    chats_collection.update_one(
      {"chat_id": chat_id},
      {"$push": {"statuses": {"message_id": message_id, "status": "in_progress"}}}
    )

    # Find the sysprompt
    if sysprompt_id is None:
      sysprompt_id = chat["sysprompt_id"]
      if not sysprompt_id:
        raise ValueError(f"System prompt for chat {chat_id} not found.")

    sysprompt = prompts_collection.find_one({"prompt_id": sysprompt_id})
    if not sysprompt:
      raise ValueError(f"Prompt {sysprompt_id} not found.")

    if sysprompt_suffix is not None:
      sysprompt["prompt"] = sysprompt["prompt"] + "\n\n" + sysprompt_suffix

    tools = get_tools(sysprompt, tools_collection)
    sysprompt = add_documents_to_sysprompt(sysprompt, documents_collection)

    new_message, rag_result = add_rag_results_to_message(
      sysprompt=sysprompt,
      new_message=new_message,
      rag_func=rag_func,
      db=db,
      spacy_model=spacy_model,
      persist_rag_results=persist_rag_results,
      table_name=rag_table_name
    )

    # Build and save user message
    old_messages = chat["messages"]
    if new_images is None:
      user_message = {
        "message_id": "q-" + message_id,
        "role": "user",
        "content": new_message,
        "timestamp": datetime.now()
      }
    else:
      user_message = {
        "message_id": "q-" + message_id,
        "role": "user",
        "content": [
          {"type": "text", "text": new_message}
        ] + [
          {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}}
          for image in new_images
        ],
        "timestamp": datetime.now()
      }
    new_messages = old_messages + [user_message]
    chats_collection.update_one(
      {"chat_id": chat_id}, {"$push": {"messages": user_message}}
    )

    if rag_result is not None and not persist_rag_results:
      rag_connecting_prompt = sysprompt.get("documents", {}).get("rag_connecting_prompt", "Related information:")
      new_messages[-1]["content"] = (
        new_messages[-1]["content"] +
        "\n\n" +
        rag_connecting_prompt +
        "\n" +
        rag_result
      )

    # LLM call
    if dry_run:
      full_response = "This is a test message."
      yield f"data: {json.dumps({'type': 'chunk', 'content': full_response})}\n\n"
    else:
      full_response = ""
      n_tries = 0
      while True:
        gen = call_llm_func(
          messages=new_messages,
          sysprompt=sysprompt["prompt"],
          tools=tools,
          json_mode=json_mode,
          tool_choice=tool_choice,
          model=model
        )
        try:
          while True:
            chunk = next(gen)
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        except StopIteration as e:
          llm_result = e.value

        if llm_result["tool_calls"] is None:
          full_response = llm_result["message"]
          break

        if n_tries >= max_chained_tool_calls:
          raise ValueError("Too many chained tool calls.")
        n_tries += 1

        round_messages = [{
          "role": "assistant",
          "tool_calls": llm_result["tool_calls"]
        }]
        for tc in llm_result["tool_calls"]:
          logger.info(f"Calling tool {tc['function']['name']}")
          tool_result = tool_handler(
            name=tc["function"]["name"],
            arguments=json.loads(tc["function"]["arguments"]),
            tools_collection=tools_collection,
            function_dictionary=function_dictionary,
            context_arguments=context_arguments
          )
          logger.info(f"Tool {tc['function']['name']} returned: {tool_result}")
          round_messages.append({
            "tool_call_id": tc["id"],
            "role": "tool",
            "name": tc["function"]["name"],
            "content": str(tool_result),
          })
        new_messages.extend(round_messages)
        tool_messages.extend(round_messages)

    if skip_word is not None and full_response == skip_word:
      full_response = None

    # Save to MongoDB
    content = full_response
    if isinstance(content, dict):
      content = json.dumps(content)
    response_message = {
      "message_id": message_id,
      "role": "assistant",
      "content": content,
      "timestamp": datetime.now()
    }
    internal_messages = [
      {**m, "message_id": message_id, "internal": True, "timestamp": datetime.now()} for m in tool_messages
    ]
    chats_collection.update_one(
      {"chat_id": chat_id},
      {"$push": {
        "statuses": {"message_id": message_id, "status": "completed"},
        "messages": {"$each": internal_messages + [response_message]}
      }}
    )
    tool_messages = None
    logger.info(f"Streaming chat {chat_id} completed successfully.")

    yield f"data: {json.dumps({'type': 'done', 'message_id': message_id, 'message': content})}\n\n"

  except Exception as e:
    logger.error(f"Error in streaming chat {chat_id}: {e}")
    if tool_messages is not None:
      # keep completed tool rounds (their side effects happened) and mark the turn as failed
      try:
        chats_collection.update_one(
          {"chat_id": chat_id},
          {"$push": {
            "statuses": {"message_id": message_id, "status": "failed", "error": str(e)[:500]},
            "messages": {"$each": [
              {**m, "message_id": message_id, "internal": True, "timestamp": datetime.now()} for m in tool_messages
            ]}
          }}
        )
      except Exception as db_error:
        logger.error(f"Could not record failure of chat {chat_id}: {db_error}")
    yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"
    raise


async def aprocess_chat(*args, **kwargs):
  """Runs process_chat in a worker thread so async callers do not block the event loop."""
  return await asyncio.to_thread(process_chat, *args, **kwargs)
