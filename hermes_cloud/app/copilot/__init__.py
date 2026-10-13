"""CRM Copilot — structured LLM tool-calling on a single record.

Phase B (charter §168 + Law 5): replace the frontend's rule-based regex
intent parser with a real LLM that can choose from a small, safe tool
set and call it server-side, keeping validation / permission / audit
inside the cloud boundary.

Submodules
----------
* ``tools``  — OpenAI-style function schemas + a dispatcher that turns a
  tool_call into a real execution against Save / ModuleFieldsDb /
  touchpoint adapters.
* ``llm``    — httpx client against opencode-zen (shared config with
  ``llm/summarize.py``) extended with ``tools=`` support.
* ``routes`` — ``POST /api/copilot/crm/send`` — the frontend CopilotPanel's
  single round-trip: user message in, reply + executed tools out.
"""
