#!/usr/bin/env python3

import json
import os
import signal
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Config:
    MAX_HISTORY = int(os.environ.get('TINY_HISTORY', '10'))
    MAX_TOKENS = int(os.environ.get('TINY_MAX_TOKENS', '500'))
    MODEL = os.environ.get('TINY_MODEL', 'gpt-4o-mini')
    TIMEOUT = 30
    API_BASE = 'https://api.openai.com/v1'
    SYSTEM_PROMPT = "You are TinyAgent, a concise AI assistant. Be brief and helpful."


class HTTPClient:
    __slots__ = ()

    @staticmethod
    def post(url, headers, data, timeout=30):
        try:
            body = json.dumps(data).encode('utf-8')
            req = Request(url, data=body, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8') if e.fp else ''}")
        except URLError as e:
            raise RuntimeError(f"Connection failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")


class ContextWindow:
    __slots__ = ('_messages', '_max_size')

    def __init__(self, max_size=10):
        self._max_size = max_size
        self._messages = []

    def add(self, role, content):
        msgs = self._messages
        if len(msgs) >= self._max_size:
            msgs.pop(0)
        msgs.append({'role': role, 'content': content})

    def get_messages(self, system_prompt=None):
        msgs = self._messages
        if system_prompt:
            return [{'role': 'system', 'content': system_prompt}] + msgs
        return msgs[:]

    def clear(self):
        self._messages.clear()

    def __len__(self):
        return len(self._messages)


class LLMClient:
    __slots__ = ('_api_key', '_base_url', '_model', '_timeout')

    def __init__(self, api_key, base_url=None, model=None, timeout=30):
        self._api_key = api_key
        self._base_url = base_url or Config.API_BASE
        self._model = model or Config.MODEL
        self._timeout = timeout

    def chat(self, messages, max_tokens=None):
        if not self._api_key or self._api_key == 'your_api_key_here':
            raise RuntimeError("No API key configured")
        response = HTTPClient.post(
            f"{self._base_url}/chat/completions",
            {'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'},
            {'model': self._model, 'messages': messages, 'max_tokens': max_tokens or Config.MAX_TOKENS, 'temperature': 0.7},
            timeout=self._timeout,
        )
        if not response.get('choices'):
            raise RuntimeError("Empty response from LLM")
        return response['choices'][0]['message']['content']


class TinyAgent:
    __slots__ = ('_llm', '_context', '_running')

    def __init__(self, api_key, model=None):
        self._llm = LLMClient(api_key, model=model)
        self._context = ContextWindow(max_size=Config.MAX_HISTORY)
        self._running = True

    def process(self, user_input):
        stripped = user_input.strip() if user_input else ''
        if not stripped:
            return None
        self._context.add('user', stripped)
        response = self._llm.chat(self._context.get_messages(Config.SYSTEM_PROMPT))
        self._context.add('assistant', response)
        return response

    def run_interactive(self):
        print("=" * 50)
        print("TinyAgent v1.0 - Lightweight AI for TinyOS")
        print(f"Model: {Config.MODEL} | History: {Config.MAX_HISTORY}")
        print("Commands: /clear (clear history), /quit (exit)")
        print("=" * 50)
        while self._running:
            try:
                user_input = input("\nYou: ").strip()
                if not user_input:
                    continue
                if user_input.startswith('/'):
                    cmd = user_input.lower()
                    if cmd in ('/quit', '/exit', '/q'):
                        self._running = False
                        break
                    if cmd == '/clear':
                        self._context.clear()
                        print("[History cleared]")
                        continue
                    if cmd == '/help':
                        print("Commands: /clear, /quit, /help")
                        continue
                print("Thinking...", end='', flush=True)
                response = self.process(user_input)
                print("\r" + " " * 12 + "\r", end='')
                if response:
                    print(f"Agent: {response}")
            except KeyboardInterrupt:
                print("\n[Interrupted]")
            except RuntimeError as e:
                print(f"\n[Error] {e}")
            except EOFError:
                break
        print("\nGoodbye!")

    def run_once(self, user_input):
        return self.process(user_input)


def get_api_key():
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        return sys.argv[1]
    return os.environ.get('OPENAI_API_KEY', '')


def parse_args():
    model = Config.MODEL
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ('--model', '-m') and i < len(sys.argv):
            model = sys.argv[i + 1]
    return model


def main():
    def handle_signal(sig, frame):
        print("\n[Shutting down...]")
        sys.exit(0)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    api_key = get_api_key()
    model = parse_args()
    if not api_key:
        print("Error: No API key provided.", file=sys.stderr)
        print("Usage: python3 tiny_agent.py [API_KEY] [--model MODEL]", file=sys.stderr)
        print("Or set OPENAI_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)
    agent = TinyAgent(api_key, model=model)
    if not sys.stdin.isatty():
        user_input = sys.stdin.read().strip()
        if user_input:
            try:
                response = agent.run_once(user_input)
                if response:
                    print(response)
            except RuntimeError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        agent.run_interactive()


if __name__ == '__main__':
    main()
