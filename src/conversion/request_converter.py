import json
import logging
from typing import Any, Dict, List

from src.core.constants import Constants
from src.models.claude import ClaudeMessage, ClaudeMessagesRequest

logger = logging.getLogger(__name__)


def convert_claude_to_openai(claude_request: ClaudeMessagesRequest) -> Dict[str, Any]:
    """Convert a Claude Messages API request into an OpenAI chat completion request.

    The model name is forwarded to the upstream unchanged -- callers must
    request a model ID the upstream actually serves (see GET /v1/models).
    """

    openai_model = claude_request.model
    openai_messages = []

    # OpenAI has no system role in the messages array; the system prompt is
    # prepended to the first user message instead.
    system_text_to_prepend = ""
    if claude_request.system:
        if isinstance(claude_request.system, str):
            system_text = claude_request.system
        else:
            text_parts = [
                block.text
                for block in claude_request.system
                if getattr(block, "type", None) == Constants.CONTENT_TEXT
            ]
            system_text = "\n\n".join(text_parts)
        if system_text.strip():
            system_text_to_prepend = system_text.strip()

    i = 0
    first_user_message = True
    while i < len(claude_request.messages):
        msg = claude_request.messages[i]

        if msg.role == Constants.ROLE_SYSTEM:
            i += 1
            continue

        if msg.role == Constants.ROLE_USER:
            openai_message = convert_claude_user_message(msg)
            if system_text_to_prepend and first_user_message:
                original_content = openai_message.get("content", "")
                if isinstance(original_content, str):
                    openai_message["content"] = f"{system_text_to_prepend}\n\n{original_content}".strip()
                elif isinstance(original_content, list):
                    if original_content and original_content[0].get("type") == "text":
                        original_content[0]["text"] = f"{system_text_to_prepend}\n\n{original_content[0]['text']}"
                    else:
                        original_content.insert(0, {"type": "text", "text": system_text_to_prepend})
                    openai_message["content"] = original_content
            openai_messages.append(openai_message)
            first_user_message = False

        elif msg.role == Constants.ROLE_ASSISTANT:
            openai_messages.append(convert_claude_assistant_message(msg))

            if i + 1 < len(claude_request.messages):
                next_msg = claude_request.messages[i + 1]
                if (
                    next_msg.role == Constants.ROLE_USER
                    and isinstance(next_msg.content, list)
                    and any(
                        getattr(block, "type", None) == Constants.CONTENT_TOOL_RESULT
                        for block in next_msg.content
                    )
                ):
                    i += 1
                    openai_messages.extend(convert_claude_tool_results(next_msg))

        i += 1

    openai_request = {
        "model": openai_model,
        "messages": openai_messages,
        "max_tokens": claude_request.max_tokens,
        "temperature": claude_request.temperature,
        "stream": claude_request.stream,
    }
    logger.debug("Converted Claude request to OpenAI format: %s", json.dumps(openai_request, ensure_ascii=False))

    if claude_request.stop_sequences:
        openai_request["stop"] = claude_request.stop_sequences
    if claude_request.top_p is not None:
        openai_request["top_p"] = claude_request.top_p

    if claude_request.tools:
        openai_tools = [
            {
                "type": Constants.TOOL_FUNCTION,
                Constants.TOOL_FUNCTION: {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
            for tool in claude_request.tools
            if tool.name and tool.name.strip()
        ]
        if openai_tools:
            openai_request["tools"] = openai_tools

    if claude_request.tool_choice:
        choice_type = claude_request.tool_choice.get("type")
        if choice_type == "tool" and "name" in claude_request.tool_choice:
            openai_request["tool_choice"] = {
                "type": Constants.TOOL_FUNCTION,
                Constants.TOOL_FUNCTION: {"name": claude_request.tool_choice["name"]},
            }
        else:
            openai_request["tool_choice"] = "auto"

    return openai_request


def convert_claude_user_message(msg: ClaudeMessage) -> Dict[str, Any]:
    if msg.content is None:
        return {"role": Constants.ROLE_USER, "content": ""}
    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_USER, "content": msg.content}

    openai_content = []
    for block in msg.content:
        if block.type == Constants.CONTENT_TEXT:
            openai_content.append({"type": "text", "text": block.text})
        elif block.type == Constants.CONTENT_IMAGE:
            source = block.source
            if (
                isinstance(source, dict)
                and source.get("type") == "base64"
                and "media_type" in source
                and "data" in source
            ):
                openai_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{source['media_type']};base64,{source['data']}"},
                    }
                )

    if len(openai_content) == 1 and openai_content[0]["type"] == "text":
        return {"role": Constants.ROLE_USER, "content": openai_content[0]["text"]}
    return {"role": Constants.ROLE_USER, "content": openai_content}


def convert_claude_assistant_message(msg: ClaudeMessage) -> Dict[str, Any]:
    if msg.content is None:
        return {"role": Constants.ROLE_ASSISTANT, "content": None}
    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_ASSISTANT, "content": msg.content}

    text_parts = []
    tool_calls = []
    for block in msg.content:
        if block.type == Constants.CONTENT_TEXT:
            text_parts.append(block.text)
        elif block.type == Constants.CONTENT_TOOL_USE:
            tool_calls.append(
                {
                    "id": block.id,
                    "type": Constants.TOOL_FUNCTION,
                    Constants.TOOL_FUNCTION: {
                        "name": block.name,
                        "arguments": json.dumps(block.input, ensure_ascii=False),
                    },
                }
            )

    openai_message = {"role": Constants.ROLE_ASSISTANT, "content": "".join(text_parts) if text_parts else None}
    if tool_calls:
        openai_message["tool_calls"] = tool_calls
    return openai_message


def convert_claude_tool_results(msg: ClaudeMessage) -> List[Dict[str, Any]]:
    tool_messages = []
    if isinstance(msg.content, list):
        for block in msg.content:
            if block.type == Constants.CONTENT_TOOL_RESULT:
                tool_messages.append(
                    {
                        "role": Constants.ROLE_TOOL,
                        "tool_call_id": block.tool_use_id,
                        "content": parse_tool_result_content(block.content),
                    }
                )
    return tool_messages


def parse_tool_result_content(content) -> str:
    """Normalize Claude tool_result content (str, list, or dict) into a string."""
    if content is None:
        return "No content provided"
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        result_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == Constants.CONTENT_TEXT:
                result_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                result_parts.append(item)
            elif isinstance(item, dict):
                result_parts.append(item.get("text") or json.dumps(item, ensure_ascii=False))
        return "\n".join(result_parts).strip()

    if isinstance(content, dict):
        if content.get("type") == Constants.CONTENT_TEXT:
            return content.get("text", "")
        return json.dumps(content, ensure_ascii=False)

    return str(content)
