import json
import uuid

from fastapi import HTTPException, Request

from src.core.constants import Constants
from src.models.claude import ClaudeMessagesRequest


def convert_openai_to_claude_response(openai_response: dict, original_request: ClaudeMessagesRequest) -> dict:
    """Convert a non-streaming OpenAI chat completion into a Claude Messages response."""

    choices = openai_response.get("choices", [])
    if not choices:
        raise HTTPException(status_code=500, detail="No choices in upstream response")

    message = choices[0].get("message", {})
    content_blocks = []

    text_content = message.get("content")
    if text_content is not None:
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": text_content})

    for tool_call in message.get("tool_calls") or []:
        if tool_call.get("type") != Constants.TOOL_FUNCTION:
            continue
        function_data = tool_call.get(Constants.TOOL_FUNCTION, {})
        try:
            arguments = json.loads(function_data.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {"raw_arguments": function_data.get("arguments", "")}
        content_blocks.append(
            {
                "type": Constants.CONTENT_TOOL_USE,
                "id": tool_call.get("id", f"tool_{uuid.uuid4()}"),
                "name": function_data.get("name", ""),
                "input": arguments,
            }
        )

    if not content_blocks:
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": ""})

    finish_reason = choices[0].get("finish_reason", "stop")
    stop_reason = {
        "stop": Constants.STOP_END_TURN,
        "length": Constants.STOP_MAX_TOKENS,
        "tool_calls": Constants.STOP_TOOL_USE,
        "function_call": Constants.STOP_TOOL_USE,
    }.get(finish_reason, Constants.STOP_END_TURN)

    usage = openai_response.get("usage", {})
    return {
        "id": openai_response.get("id", f"msg_{uuid.uuid4()}"),
        "type": "message",
        "role": Constants.ROLE_ASSISTANT,
        "model": original_request.model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


async def convert_openai_streaming_to_claude(
    openai_stream,
    original_request: ClaudeMessagesRequest,
    logger,
    http_request: Request,
    openai_client,
    request_id: str,
):
    """Convert an OpenAI SSE stream into a Claude Messages SSE stream.

    Cancels the upstream request if the client disconnects mid-stream.
    """

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    yield (
        f"event: {Constants.EVENT_MESSAGE_START}\ndata: "
        + json.dumps(
            {
                "type": Constants.EVENT_MESSAGE_START,
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": Constants.ROLE_ASSISTANT,
                    "model": original_request.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )
    yield (
        f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: "
        + json.dumps(
            {
                "type": Constants.EVENT_CONTENT_BLOCK_START,
                "index": 0,
                "content_block": {"type": Constants.CONTENT_TEXT, "text": ""},
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )
    yield f"event: {Constants.EVENT_PING}\ndata: " + json.dumps({"type": Constants.EVENT_PING}) + "\n\n"

    text_block_index = 0
    tool_block_counter = 0
    current_tool_calls = {}
    final_stop_reason = Constants.STOP_END_TURN
    usage_data = {"input_tokens": 0, "output_tokens": 0}

    try:
        async for line in openai_stream:
            if await http_request.is_disconnected():
                logger.info("Client disconnected, cancelling request %s", request_id)
                openai_client.cancel_request(request_id)
                break

            if not line.strip() or not line.startswith("data: "):
                continue
            chunk_data = line[6:]
            if chunk_data.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(chunk_data)
            except json.JSONDecodeError as e:
                logger.warning("Failed to parse chunk: %s, error: %s", chunk_data, e)
                continue

            usage = chunk.get("usage")
            if usage:
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                usage_data = {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "cache_read_input_tokens": cached,
                }

            choices = chunk.get("choices", [])
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            if delta.get("content") is not None:
                yield (
                    f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: "
                    + json.dumps(
                        {
                            "type": Constants.EVENT_CONTENT_BLOCK_DELTA,
                            "index": text_block_index,
                            "delta": {"type": Constants.DELTA_TEXT, "text": delta["content"]},
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )

            for tc_delta in delta.get("tool_calls") or []:
                tc_index = tc_delta.get("index", 0)
                tool_call = current_tool_calls.setdefault(
                    tc_index,
                    {"id": None, "name": None, "args_buffer": "", "json_sent": False, "claude_index": None, "started": False},
                )

                if tc_delta.get("id"):
                    tool_call["id"] = tc_delta["id"]
                function_data = tc_delta.get(Constants.TOOL_FUNCTION, {})
                if function_data.get("name"):
                    tool_call["name"] = function_data["name"]

                if tool_call["id"] and tool_call["name"] and not tool_call["started"]:
                    tool_block_counter += 1
                    claude_index = text_block_index + tool_block_counter
                    tool_call["claude_index"] = claude_index
                    tool_call["started"] = True
                    yield (
                        f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: "
                        + json.dumps(
                            {
                                "type": Constants.EVENT_CONTENT_BLOCK_START,
                                "index": claude_index,
                                "content_block": {
                                    "type": Constants.CONTENT_TOOL_USE,
                                    "id": tool_call["id"],
                                    "name": tool_call["name"],
                                    "input": {},
                                },
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )

                arguments = function_data.get("arguments")
                if arguments and tool_call["started"]:
                    tool_call["args_buffer"] += arguments
                    try:
                        json.loads(tool_call["args_buffer"])
                        if not tool_call["json_sent"]:
                            yield (
                                f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: "
                                + json.dumps(
                                    {
                                        "type": Constants.EVENT_CONTENT_BLOCK_DELTA,
                                        "index": tool_call["claude_index"],
                                        "delta": {
                                            "type": Constants.DELTA_INPUT_JSON,
                                            "partial_json": tool_call["args_buffer"],
                                        },
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                            tool_call["json_sent"] = True
                    except json.JSONDecodeError:
                        pass

            if finish_reason:
                final_stop_reason = {
                    "length": Constants.STOP_MAX_TOKENS,
                    "tool_calls": Constants.STOP_TOOL_USE,
                    "function_call": Constants.STOP_TOOL_USE,
                }.get(finish_reason, Constants.STOP_END_TURN)

    except HTTPException as e:
        if e.status_code == 499:
            logger.info("Request %s was cancelled", request_id)
            error_event = {"type": "error", "error": {"type": "cancelled", "message": "Request was cancelled by client"}}
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            return
        raise
    except Exception as e:
        logger.error("Streaming error: %s", e)
        error_event = {"type": "error", "error": {"type": "api_error", "message": f"Streaming error: {e}"}}
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        return

    yield (
        f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: "
        + json.dumps({"type": Constants.EVENT_CONTENT_BLOCK_STOP, "index": text_block_index})
        + "\n\n"
    )
    for tool_data in current_tool_calls.values():
        if tool_data["started"] and tool_data["claude_index"] is not None:
            yield (
                f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: "
                + json.dumps({"type": Constants.EVENT_CONTENT_BLOCK_STOP, "index": tool_data["claude_index"]})
                + "\n\n"
            )

    yield (
        f"event: {Constants.EVENT_MESSAGE_DELTA}\ndata: "
        + json.dumps(
            {
                "type": Constants.EVENT_MESSAGE_DELTA,
                "delta": {"stop_reason": final_stop_reason, "stop_sequence": None},
                "usage": usage_data,
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )
    yield f"event: {Constants.EVENT_MESSAGE_STOP}\ndata: " + json.dumps({"type": Constants.EVENT_MESSAGE_STOP}) + "\n\n"
