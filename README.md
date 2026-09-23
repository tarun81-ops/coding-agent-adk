# my_agent — ADK Coding Assistant on OpenRouter

A minimal [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) agent that acts as a
**senior software developer / coding assistant** — answering questions, reviewing code and proposing
solutions to technical problems.

The agent is defined in [`agent.py`](agent.py) as a single `root_agent` and uses ADK's **`LiteLlm`**
wrapper to talk to **OpenRouter** instead of the default Gemini backend. That means the model behind the
agent is a one-line change, and any of OpenRouter's hundreds of models can be used without touching the
agent logic.

> **Note:** the default model is `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` — a free-tier
> OpenRouter model that is sometimes overloaded (it answers `503 Service temporarily overloaded` when it
> is). Set `AGENT_MODEL` in `.env` to switch models without touching the code — see
> [Model configuration](#model-configuration).

---

## Prerequisites

| Requirement | Version used in this project | Notes |
| --- | --- | --- |
| Python | **3.10 or newer** (verified on 3.14.7) | `google-adk` declares `Requires-Python: >=3.10` |
| `google-adk` | 2.9.2 | Provides the `adk` CLI (`web`, `run`, `api_server`) |
| `google-adk[extensions]` | — | The extra that installs **litellm** (declared as `litellm>=1.84`) |
| `litellm` | 1.102.1 | Installed automatically by the `extensions` extra |
| `python-dotenv` | 1.2.3 | Pulled in by `google-adk`, imports directly in `agent.py` |
| An OpenRouter API key | — | Generate one at <https://openrouter.ai/keys> |

---

## Setup

### 1. Get into the project folder

```powershell
# The folder that *contains* the my_agent package, not my_agent itself
cd "C:\Users\tarun verma\Documents\AI"

# If you have pushed this project to a remote, clone it instead:
# git clone <YOUR_REPO_URL> my_agent
```

### 2. Create and activate a virtual environment (Windows PowerShell)

```powershell
py -m venv my_agent\.venv
.\my_agent\.venv\Scripts\Activate.ps1
```

If activation is blocked by the execution policy, allow local scripts once and retry:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Your prompt should now be prefixed with `(.venv)`. Confirm you are on the right interpreter:

```powershell
python --version
```

### 3. Install dependencies

There is no `requirements.txt` in this project, so install the packages directly. The
`extensions` extra is **required** — without it, litellm is missing and importing
`LiteLlm` raises an `ImportError` immediately (see [Troubleshooting](#troubleshooting)).

```powershell
python -m pip install --upgrade pip
pip install "google-adk[extensions]"
pip install python-dotenv
```

Quotes around `"google-adk[extensions]"` keep the brackets from being interpreted by the shell.

Optional — pin what you installed so it can be reproduced later:

```powershell
pip freeze > requirements.txt
```

### 4. Create the `.env` file

Create a file named `.env` **inside the `my_agent` folder** (next to `agent.py`) and add your key:

```dotenv
# Required — consumed by agent.py and passed to LiteLLM for OpenRouter
OPENROUTER_API_KEY=your_key_here
```

Optional variables that also live in this project's `.env`. They are **not** needed for the OpenRouter
path and can be left out entirely unless you switch to a Gemini model:

```dotenv
# Optional — only relevant if you use a Gemini model directly instead of LiteLlm
GOOGLE_API_KEY=your_google_key_here
GOOGLE_GENAI_USE_ENTERPRISE=0
```

Optional variables `agent.py` itself reads — every one of them has a working default:

```dotenv
# Swap the model without editing agent.py (any litellm model id)
AGENT_MODEL=openrouter/nvidia/nemotron-3-super-120b-a12b:free

# Workspace root for relative paths and shell commands (default: current directory)
MY_AGENT_ROOT=C:\Users\tarun verma\Documents\AI\my_agent

# Uncomment to log which .env was loaded and the key length (never the key itself)
# MY_AGENT_DEBUG=1

# Seconds before run_shell_command kills a command (default 120)
MY_AGENT_SHELL_TIMEOUT=120

# Set to 0 to disable the destructive-command guard
MY_AGENT_GUARD=1
```

Notes:

- Get an OpenRouter key at <https://openrouter.ai/keys>.
- `.env` is listed in [`.gitignore`](.gitignore), so your key is never committed. Keep it that way.
- `agent.py` resolves the file as an **absolute path** relative to itself
  (`os.path.dirname(os.path.abspath(__file__))`), so it is found no matter which directory you launch
  `adk` from.

### 5. Verify your key before running the agent

```powershell
# PowerShell's `curl` is an alias for Invoke-WebRequest — use curl.exe explicitly
curl.exe https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer $env:OPENROUTER_API_KEY"
```

A healthy key returns `200` with your label, limit and usage. A `401` means the key being read is not
valid — see [Troubleshooting](#troubleshooting).

---

## Running the agent

All three commands expect the **path to the agent folder**, so run them from the folder that contains
`my_agent` (or point them straight at `my_agent`). `agent.py` refuses to start if `OPENROUTER_API_KEY` is
missing or empty, and your first message makes a real OpenRouter call — set `MY_AGENT_DEBUG=1` in `.env` to
log which `.env` file was loaded and how long the key is.

### `adk web` — browser dev UI (recommended while iterating)

```powershell
cd "C:\Users\tarun verma\Documents\AI"
adk web my_agent
```

- Serves a playground UI at the URL printed by the CLI (default `http://127.0.0.1:8000`).
- `--port 8765` changes the port; `adk web --help` lists the rest (`--session_service_uri`,
  `--enable_features`, ...).
- `adk web` with no argument scans the current directory, and `adk web .` works from inside the
  `my_agent` folder — all three forms resolve the app as `my_agent`.

### `adk run` — terminal

```powershell
# One-shot: send a single message and exit
adk run my_agent "Review this function for bugs: def add(a, b): return a - b"

# Interactive REPL (omit the query)
adk run my_agent
```

### `adk api_server` — local HTTP API

```powershell
adk api_server my_agent
```

Starts the FastAPI server exposed by ADK (e.g. `GET /list-apps` returns `["my_agent"]`). These endpoints
are **unauthenticated** — bind to localhost and do not expose them to untrusted networks.

---

## Project structure

```
my_agent/
├── agent.py          # The whole agent: loads .env, defines the four tools and
│                     #   the guardrails, then builds `root_agent`
├── __init__.py       # `from . import agent` — marks this folder as a loadable ADK app
├── .env              # Your secrets (OPENROUTER_API_KEY). Git-ignored, recreate locally
├── .gitignore        # Ignores .env and .adk/
├── README.md         # This file
├── .adk/             # Created at runtime by the CLI — session storage (session.db). Git-ignored
├── .venv/            # Local virtual environment (not committed)
└── __pycache__/      # Python bytecode cache (generated, not committed)
```

Only two files matter to ADK: `agent.py` (must define `root_agent`) and `__init__.py`.

---

## Model configuration

The model is declared in `agent.py` (with an optional `.env` override):

```python
_DEFAULT_MODEL: Final[str] = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
MODEL_ID: Final[str] = os.getenv("AGENT_MODEL", "").strip() or _DEFAULT_MODEL
API_KEY: Final[str] = _resolve_api_key()   # raises if OPENROUTER_API_KEY is missing

root_agent = Agent(
    model=LiteLlm(model=MODEL_ID, api_key=API_KEY),
    name="senior_developer_agent",
    description="Senior software developer agent: reviews code, proposes architectures, "
                "and implements and verifies changes through file and shell tools.",
    instruction=INSTRUCTION,
    tools=[read_file, write_file, run_shell_command, list_directory],
    before_tool_callback=_block_destructive_commands,
    on_tool_error_callback=_report_tool_error,
)
```

Notes on how this is wired:

- **`AGENT_MODEL`** (read from `.env`) overrides `_DEFAULT_MODEL`, so the model can be switched without
  editing the file at all.
- **`openrouter/<author>/<model-id>`** is litellm's routing convention: the `openrouter/` prefix selects
  OpenRouter as the provider, and everything after it is passed to OpenRouter as the model id
  (`nvidia/nemotron-3-ultra-550b-a55b:free`). The `:free` suffix is part of the model id.
- `api_key` is read from `.env` and passed to `LiteLlm` explicitly, **and** mirrored into
  `os.environ["OPENROUTER_API_KEY"]` so litellm's own credential lookup finds the same value.
- Extra keyword arguments given to `LiteLlm(...)` are forwarded to litellm's completion call, so anything
  litellm supports for the provider (e.g. `temperature`, `max_tokens`, `custom_llm_provider`, `drop_params`)
  can be added there.

### Swapping models

Change only the model string — the agent, tools and prompt stay untouched:

```python
# A different free OpenRouter model
model=LiteLlm(model="openrouter/nvidia/nemotron-3-super-120b-a12b:free", api_key=api_key)

# Another provider's model hosted on OpenRouter
model=LiteLlm(model="openrouter/anthropic/claude-opus-5.5", api_key=api_key)
```

Browse the full list of ids at <https://openrouter.ai/models> (or
<https://openrouter.ai/api/v1/models>) and copy the id exactly, then re-run the agent to pick up the
change (`adk web --help` lists the live-reload flags `--reload` and `--reload_agents`).

---

## Tools and guardrails

`root_agent` exposes four tools. Each one returns a **dict** rather than a bare string, so the model reads
named fields and a `status` of `"success"`, `"error"` or `"timeout"` instead of parsing prose.

| Tool | Fields returned | Notes |
| --- | --- | --- |
| `read_file(path)` | `path`, `content`, `lines`, `truncated` | UTF-8, `errors="replace"` |
| `write_file(path, content)` | `path`, `bytes_written`, `created`, `lines` | Replaces the whole file; creates parent directories; writes `\n` (never CRLF) |
| `run_shell_command(command, timeout_seconds, cwd)` | `command`, `cwd`, `exit_code`, `stdout`, `stderr`, `truncated` | `status` is `"error"` on a non-zero exit code, `"timeout"` when killed |
| `list_directory(path)` | `path`, `count`, `entries` (`name`, `type`, `size_bytes`, `modified`) | Non-recursive, sorted, one level deep |

Behaviour worth knowing:

- **Paths.** Relative paths resolve against the **workspace root** — `MY_AGENT_ROOT` if set, otherwise the
  directory the CLI was launched from. Every result echoes the absolute path it actually used.
- **Truncation.** File contents and stdout/stderr are cut off at 20 000 characters per field and flagged with
  `truncated: true`, so a single `dir /s` cannot blow up the context window.
- **Timeouts.** Shell commands are killed after `MY_AGENT_SHELL_TIMEOUT` seconds (default 120) and report
  `status: "timeout"` instead of hanging the agent forever.
- **Destructive-command guard.** `run_shell_command` runs through a `before_tool_callback` that refuses
  patterns such as `rm -rf`, `del /f /s /q`, `Remove-Item -Recurse/-Force`, `git push --force`,
  `git reset --hard`, `git clean -f`, `format`/`mkfs`, `dd of=`, `shutdown` and `DROP TABLE`/`DROP DATABASE`.
  The refusal is handed to the model as a normal `status: "error"` result, so it explains the block and asks
  you to run the command instead. This is a speed bump, **not a sandbox** — set `MY_AGENT_GUARD=0` to disable
  it, and treat the blocklist as guidance rather than a security boundary.
- **Tool crashes.** An unexpected tool exception is caught by `on_tool_error_callback` and returned as a
  reportable error, so it cannot kill the turn (the traceback still goes to the ADK log).
- **No permissions layer.** The file tools can read and write anything the process user can, and
  `run_shell_command` goes through a real shell (`shell=True`). Run the agent in a directory you are happy
  for it to modify, and keep `adk api_server` bound to localhost.

---

## Troubleshooting

### 1. `ImportError: LiteLLM support requires: pip install google-adk[extensions]`

Raised at import time by `google/adk/models/lite_llm.py` when litellm cannot be found, because litellm is
an **extra**, not a base dependency of `google-adk`:

```powershell
pip install "google-adk[extensions]"
```

Verify it landed:

```powershell
python -c "import litellm, os; print(os.path.dirname(litellm.__file__))"
```

> `pip show litellm` can report *"Package(s) not found"* in this environment even when litellm is
> installed and importable — trust the `import` check above.

### 2. `AuthenticationError: ... {"error":{"message":"User not found.","code":401}}`

OpenRouter returns this when the API key it received is not one it recognises. It is an **auth** problem,
never a model or agent-code problem — the same key that works in `curl` can still fail here if the process
is reading a different key.

**2a. A stale key in your environment is shadowing `.env` (the most common cause).**
`load_dotenv()` defaults to `override=False`, so an `OPENROUTER_API_KEY` already exported in your shell or
the Windows *user* environment silently wins over the value in `.env`, and litellm submits the old key.
Check what the OS has:

```powershell
[Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','Process')
[Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','User')
```

In this project `agent.py` already defends against it: `load_dotenv(dotenv_path=..., override=True)` makes
`.env` authoritative, and the resolved value is written to `os.environ["OPENROUTER_API_KEY"]` before the
agent is built. To delete a dead variable permanently, then restart your terminal / VS Code:

```powershell
[Environment]::SetEnvironmentVariable('OPENROUTER_API_KEY', $null, 'User')
```

**2b. The key is missing, empty, or still the placeholder.**
A missing or empty `OPENROUTER_API_KEY` now stops the agent at start-up and prints the file it looked in:

```
RuntimeError: OPENROUTER_API_KEY is missing or empty. Add it to C:\...\my_agent\.env:
    OPENROUTER_API_KEY=sk-or-v1-...
Create a key at https://openrouter.ai/keys and restart the agent.
```

To see which `.env` was loaded and how long the resolved key is, set `MY_AGENT_DEBUG=1` in `.env`:

```
INFO my_agent.agent: [debug] .env: C:\...\my_agent\.env | OPENROUTER_API_KEY loaded: True (len=73)
```

No line at all, or `len=0` → the variable is not being read: check that `.env` sits next to `agent.py` and
that the name is exactly `OPENROUTER_API_KEY`. A length you don't recognise → the wrong key is winning, see
2a. The key itself is never logged (not even its prefix).

**2c. Read the message to see how far the request got.**

| Message | Meaning |
| --- | --- |
| `"No cookie auth credentials found"` | No `Authorization` header was sent at all — the key was empty/unset |
| `"User not found."` | Header received, but the key is unknown (revoked, mistyped, or from another account) |
| `402` / credit errors | Key is valid; the account is out of quota |

**2d. Confirm the key on its own**, outside the agent (step 5 in [Setup](#5-verify-your-key-before-running-the-agent)):

```powershell
curl.exe https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer $env:OPENROUTER_API_KEY"
```

### 3. `ValidationError: ... Node name 'senior-developer_agent' must be a valid Python identifier`

ADK requires `Agent(name=...)` to be a valid **Python identifier** — no hyphens, no spaces. Use the
underscore form. The module-level variable can still be called `root_agent`, which is the name the CLI
looks for:

```python
root_agent = Agent(
    name='senior_developer_agent',   # ✅ valid identifier ('senior-developer_agent' ❌)
    ...
)
```

### 4. `adk : The term 'adk' is not recognized as the name of a cmdlet...`

The virtual environment is not active:

```powershell
.\my_agent\.venv\Scripts\Activate.ps1
# or call the executable directly
.\my_agent\.venv\Scripts\adk.exe web my_agent
```

### 5. The agent loads, but every message fails on the model call

Check that the model id is real at <https://openrouter.ai/models>. The `:free` suffix is part of the id —
dropping it selects the paid variant, and a typo produces a provider error rather than an auth error.

---

## License

No `LICENSE` file exists in this repository yet, so no license has been formally granted. If you intend to
publish this project, add a `LICENSE` file at the project root — **MIT** is the usual choice for a small
ADK agent like this one:

```text
MIT License

Copyright (c) <YEAR> <COPYRIGHT HOLDER>
```

Replace the placeholder above and append the full MIT text from
<https://opensource.org/license/mit>, or swap in whichever license you prefer.


