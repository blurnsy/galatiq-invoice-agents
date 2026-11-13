"""Small Grok client. xAI's API is OpenAI-compatible, so this uses the openai SDK.

Every call asks for JSON matching a schema (structured output). If tools are
passed, Grok can call them first; we run the Python function and send the
result back until it answers.
"""

import json
import os


class Grok:
    def __init__(self, api_key, model=None, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1", timeout=60)
        self.client = client
        self.model = model or os.getenv("XAI_MODEL", "grok-3")

    def ask(self, messages, schema, name, tools=None, max_turns=6):
        messages = list(messages)
        extra = {}
        if tools:
            extra["tools"] = [{"type": "function", "function": {
                "name": fn.__name__, "description": fn.__doc__, "parameters": params}}
                for fn, params in tools]
        functions = {fn.__name__: fn for fn, _ in tools or []}

        for _ in range(max_turns):
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": name, "schema": schema, "strict": True}},
                **extra)
            msg = response.choices[0].message
            if not msg.tool_calls:
                return json.loads(msg.content)
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in msg.tool_calls]})
            for call in msg.tool_calls:
                result = functions[call.function.name](**json.loads(call.function.arguments or "{}"))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
        raise RuntimeError("Grok did not finish within the turn limit")


def from_env():
    """A Grok client if XAI_API_KEY is set, otherwise None (offline mode)."""
    key = os.getenv("XAI_API_KEY")
    return Grok(key) if key else None
