#!/usr/bin/env python3

import argparse
import os
import sys
from io import StringIO
from pathlib import Path

from openai import OpenAI

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
_MAX_HISTORY = 50


def _resolve_api_key(key):
    if key:
        return key
    env_key = os.getenv("NIM_API_KEY") or os.getenv("NVIDIA_API_KEY")
    if env_key:
        return env_key
    env_path = Path(__file__).parent / "clio_agent_2" / "config" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("NIM_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def stream_response(
    client, model, messages,
    temperature=1, top_p=0.95, max_tokens=16384,
    enable_thinking=True, reasoning_budget=8192,
):
    extra_body = {}
    if enable_thinking:
        extra_body["chat_template_kwargs"] = {"enable_thinking": True}
        extra_body["reasoning_budget"] = reasoning_budget
    try:
        completion = client.chat.completions.create(
            model=model, messages=messages,
            temperature=temperature, top_p=top_p,
            max_tokens=max_tokens,
            extra_body=extra_body or None, stream=True,
        )
    except Exception as e:
        print(f"Error: API request failed \u2014 {e}", file=sys.stderr)
        sys.exit(1)
    reasoning_buf = StringIO()
    content_buf = StringIO()
    try:
        for chunk in completion:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is not None:
                reasoning_buf.write(reasoning)
                print(reasoning, end="", flush=True)
            content = delta.content
            if content is not None:
                content_buf.write(content)
                print(content, end="", flush=True)
    except Exception as e:
        print(f"\nError: streaming interrupted \u2014 {e}", file=sys.stderr)
        sys.exit(1)
    print()
    return reasoning_buf.getvalue(), content_buf.getvalue()


def interactive_loop(client, args):
    print("NVIDIA Nimotron interactive mode. Type your messages below.")
    print("Commands: /exit, /quit, /clear, /help\n")
    history = []
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit"):
            break
        if user_input.lower() == "/clear":
            history.clear()
            print("History cleared.")
            continue
        if user_input.lower() == "/help":
            print("/exit, /quit  \u2013 exit")
            print("/clear        \u2013 clear conversation history")
            print("/help         \u2013 show this message")
            continue
        messages = [{"role": "user", "content": user_input}]
        print("Assistant: ", end="", flush=True)
        stream_response(client, args.model, messages, args.temperature,
                        args.top_p, args.max_tokens, not args.no_thinking,
                        args.reasoning_budget)


def main():
    parser = argparse.ArgumentParser(description="NVIDIA Nimotron CLI for NVIDIA Nemotron models via NIM")
    parser.add_argument("prompt", nargs="*", help="Prompt text")
    parser.add_argument("--key", help="NVIDIA NIM API key")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=1)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--reasoning-budget", type=int, default=8192)
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("-i", "--interactive", action="store_true")
    args = parser.parse_args()

    api_key = _resolve_api_key(args.key)
    if not api_key:
        print("Error: No NVIDIA NIM API key found.", file=sys.stderr)
        print("Set NIM_API_KEY in environment, config/.env, or pass --key", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=api_key)
    prompt = " ".join(args.prompt).strip()

    if not prompt or args.interactive:
        interactive_loop(client, args)
    else:
        messages = [{"role": "user", "content": prompt}]
        print("Assistant: ", end="", flush=True)
        stream_response(client, args.model, messages, args.temperature,
                        args.top_p, args.max_tokens, not args.no_thinking,
                        args.reasoning_budget)


if __name__ == "__main__":
    main()
