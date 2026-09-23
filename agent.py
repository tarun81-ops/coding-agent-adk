"""ADK coding assistant: a "senior developer" agent with file and shell tools.

The module exposes ``root_agent`` — the symbol the ADK CLI looks for — and talks
to OpenRouter through ADK's ``LiteLlm`` wrapper, so the underlying model can be
swapped with a single environment variable.

Configuration is read from the ``.env`` file that sits next to this module:

``OPENROUTER_API_KEY``
    Required. Handed to litellm for OpenRouter.
``AGENT_MODEL``
    Optional litellm model id; defaults to ``_DEFAULT_MODEL``. Example:
    ``openrouter/anthropic/claude-opus-5.5``.
``MY_AGENT_ROOT``
    Optional workspace root. Relative tool paths and shell commands resolve
    against it; defaults to the process working directory.
``MY_AGENT_GUARD``
    Set to ``0`` / ``false`` to disable the destructive-command guard.
``MY_AGENT_SHELL_TIMEOUT``
    Optional shell timeout in seconds (default 120).
``MY_AGENT_DEBUG``
    Set to ``1`` to log which ``.env`` was loaded and the key length.

Run it with ``adk web my_agent`` or ``adk run my_agent`` (see README.md).
"""

import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 1. Environment loading
# --------------------------------------------------------------------------- #
_AGENT_DIR: Final[Path] = Path(__file__).resolve().parent
_ENV_PATH: Final[Path] = _AGENT_DIR / ".env"

# _ENV_PATH is absolute, so .env is found no matter which directory `adk web` /
# `adk run` was launched from.
#
# override=True is the important part: load_dotenv() defaults to override=False,
# which means a stale OPENROUTER_API_KEY already exported in the shell / Windows
# *user* environment silently wins over the value in .env. litellm then sends
# that stale key and OpenRouter replies:
#   401 {"error":{"message":"User not found.","code":401}}
load_dotenv(dotenv_path=_ENV_PATH, override=True)

# Imported after load_dotenv() so that anything ADK or litellm reads from the
# environment at import time already sees the .env values.
from google.adk.agents import Agent  # noqa: E402
from google.adk.models.lite_llm import LiteLlm  # noqa: E402
from google.adk.tools import BaseTool, ToolContext  # noqa: E402

# --------------------------------------------------------------------------- #
# 2. Configuration
# --------------------------------------------------------------------------- #
_DEFAULT_MODEL: Final[str] = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"

def _env_int(name: str, default: int) -> int:
    """Reads an integer environment variable, falling back to ``default``.

    Args:
        name: Environment variable name.
        default: Value used when the variable is unset or not an integer.

    Returns:
        The parsed integer, or ``default``.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %d instead.", name, raw, default)
        return default


# Model id handed to litellm. "openrouter/<author>/<model-id>" selects OpenRouter
# as the provider; the ":free" suffix is part of the model id itself.
MODEL_ID: Final[str] = os.getenv("AGENT_MODEL", "").strip() or _DEFAULT_MODEL

# Everything the tools touch is resolved against this root, so behaviour does not
# depend on where the ADK CLI happened to be launched from.
WORKSPACE_ROOT: Final[Path] = Path(
    os.getenv("MY_AGENT_ROOT", "").strip() or Path.cwd()
).expanduser().resolve()

# Tool output is truncated at this many characters per field to protect the
# model's context window from `dir /s`-style firehoses.
MAX_OUTPUT_CHARS: Final[int] = 20_000

SHELL_TIMEOUT_SECONDS: Final[int] = _env_int("MY_AGENT_SHELL_TIMEOUT", 120)

GUARD_ENABLED: Final[bool] = os.getenv("MY_AGENT_GUARD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

_DEBUG: Final[bool] = os.getenv("MY_AGENT_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if _DEBUG:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _resolve_api_key() -> str:
    """Returns ``OPENROUTER_API_KEY``, failing fast with an actionable message.

    Returns:
        The API key with surrounding whitespace removed.

    Raises:
        RuntimeError: If the variable is missing or empty. Failing here is
            friendlier than letting the first message die with an OpenRouter
            401 that looks like a model problem.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            f"OPENROUTER_API_KEY is missing or empty. Add it to {_ENV_PATH}:\n"
            "    OPENROUTER_API_KEY=sk-or-v1-...\n"
            "Create a key at https://openrouter.ai/keys and restart the agent."
        )
    # Belt and braces: litellm's OpenRouter handler falls back to its own env
    # lookup (get_secret_str("OPENROUTER_API_KEY")), so make sure the value
    # resolved here is the one it will see. The key itself is never logged.
    os.environ["OPENROUTER_API_KEY"] = api_key
    if _DEBUG:
        logger.info(
            "[debug] .env: %s | OPENROUTER_API_KEY loaded: True (len=%d)",
            _ENV_PATH,
            len(api_key),
        )
    return api_key


API_KEY: Final[str] = _resolve_api_key()

# --------------------------------------------------------------------------- #
# 3. Helpers shared by the tools
# --------------------------------------------------------------------------- #
# Every tool returns a dict rather than a bare string/list: ADK forwards a dict
# verbatim as the function response (`{'result': value}` is only synthesised for
# non-dict returns), so named fields are what the model actually gets to read.
def _ok(**payload: Any) -> dict[str, Any]:
    """Builds a successful tool result."""
    return {"status": "success", **payload}


def _error(message: str, **payload: Any) -> dict[str, Any]:
    """Builds a failed tool result.

    Tools return errors instead of raising so the model can read the message and
    correct itself, and so one bad path cannot abort the whole turn.
    """
    return {"status": "error", "error": message, **payload}


def _resolve_path(path: str) -> Path:
    """Expands ``~`` and resolves relative paths against ``WORKSPACE_ROOT``.

    Args:
        path: Absolute or relative path supplied by the model.

    Returns:
        An absolute, normalised ``Path``.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    return candidate.resolve()


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Caps ``text`` at ``limit`` characters.

    Args:
        text: Text to cap.
        limit: Maximum number of characters to keep.

    Returns:
        A ``(possibly truncated text, was_truncated)`` tuple. The truncation
        marker is explicit so the model knows the payload is incomplete.
    """
    if len(text) <= limit:
        return text, False
    omitted = len(text) - limit
    return f"{text[:limit]}\n[... truncated {omitted} of {len(text)} characters ...]", True


def _as_text(value: Any) -> str:
    """Normalises subprocess output, which may be bytes or ``None``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _os_error(action: str, target: Path, exc: OSError) -> str:
    """Formats an ``OSError`` without leaking a full traceback into the prompt."""
    return f"Could not {action} {target}: {exc.strerror or exc}"


# --------------------------------------------------------------------------- #
# 4. Tools
# --------------------------------------------------------------------------- #
def read_file(path: str) -> dict[str, Any]:
    """Reads a text file and returns its contents.

    Args:
        path: File to read. Absolute, or relative to the workspace root.

    Returns:
        A dict with ``status`` ("success" or "error"). On success it also has
        ``path`` (absolute), ``content``, ``lines`` (total, before truncation)
        and ``truncated``.
    """
    target = _resolve_path(path)
    if target.is_dir():
        return _error(
            f"{target} is a directory, not a file. Use list_directory instead.",
            path=str(target),
        )
    try:
        # Explicit UTF-8 + errors="replace": the default codec is the locale one
        # (cp1252 on Windows), which raises UnicodeDecodeError on UTF-8 sources.
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _error(_os_error("read", target, exc), path=str(target))

    line_count = content.count("\n") + 1
    content, truncated = _truncate(content)
    return _ok(path=str(target), content=content, lines=line_count, truncated=truncated)


def write_file(path: str, content: str) -> dict[str, Any]:
    """Writes text to a file, creating parent directories as needed.

    This replaces the whole file — it cannot patch or append. Read the current
    contents first, then re-emit the complete new version.

    Args:
        path: File to write. Absolute, or relative to the workspace root.
        content: Complete UTF-8 text to store in the file.

    Returns:
        A dict with ``status`` ("success" or "error"). On success it also has
        ``path`` (absolute), ``bytes_written``, ``created`` and ``lines``.
    """
    target = _resolve_path(path)
    if target.is_dir():
        return _error(f"{target} is an existing directory.", path=str(target))

    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" keeps the bytes on disk identical to `content` on every
        # platform; the default would translate "\n" to os.linesep on Windows.
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except OSError as exc:
        return _error(_os_error("write", target, exc), path=str(target))

    return _ok(
        path=str(target),
        bytes_written=len(content.encode("utf-8")),
        created=not existed,
        lines=content.count("\n") + 1,
    )


def run_shell_command(
    command: str,
    timeout_seconds: int = SHELL_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Runs a shell command and returns its exit code, stdout and stderr.

    The command goes through the platform shell (``cmd.exe`` on Windows, POSIX
    ``sh`` elsewhere) with the workspace root as its working directory. Prefer
    small, targeted commands over ones that dump the whole tree.

    Args:
        command: Command line to execute, e.g. ``python -m pytest -q``.
        timeout_seconds: Kill the command after this many seconds.
        cwd: Working directory for the command. Defaults to the workspace root.

    Returns:
        A dict with ``status`` — "success" when the exit code is 0, otherwise
        "error", or "timeout" if the command was killed — plus ``command``,
        ``cwd``, ``exit_code``, ``stdout``, ``stderr`` and ``truncated``.
    """
    workdir = _resolve_path(cwd) if cwd else WORKSPACE_ROOT
    if not workdir.is_dir():
        return _error(f"Working directory {workdir} does not exist.", path=str(workdir))

    try:
        completed = subprocess.run(
            command,
            shell=True,  # deliberate: this agent exists to drive the shell
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",  # text=True alone uses the locale codec (cp1252 on Windows)
            errors="replace",  # never crash on a mojibake console code page
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, out_truncated = _truncate(_as_text(exc.stdout))
        stderr, err_truncated = _truncate(_as_text(exc.stderr))
        return {
            "status": "timeout",
            "error": f"Command did not finish within {timeout_seconds}s and was killed.",
            "command": command,
            "cwd": str(workdir),
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_truncated or err_truncated,
            "hint": "Narrow the command down, or raise MY_AGENT_SHELL_TIMEOUT.",
        }
    except OSError as exc:
        return _error(
            f"Could not start the command: {exc.strerror or exc}",
            command=command,
            cwd=str(workdir),
        )

    stdout, out_truncated = _truncate(completed.stdout or "")
    stderr, err_truncated = _truncate(completed.stderr or "")
    result: dict[str, Any] = {
        "status": "success" if completed.returncode == 0 else "error",
        "command": command,
        "cwd": str(workdir),
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": out_truncated or err_truncated,
    }
    if result["status"] == "error":
        result["hint"] = (
            "Read stdout/stderr and fix the root cause instead of re-running the "
            "same command unchanged."
        )
    return result


def list_directory(path: str = ".") -> dict[str, Any]:
    """Lists the entries of a directory — non-recursive, sorted by name.

    Args:
        path: Directory to list. Absolute, or relative to the workspace root;
            defaults to the workspace root itself.

    Returns:
        A dict with ``status``, ``path``, ``count`` and ``entries``. Each entry
        has ``name``, ``type`` ("file", "dir" or "link"), ``size_bytes`` and
        ``modified`` (local ISO-8601 timestamp).
    """
    target = _resolve_path(path)
    try:
        children = sorted(target.iterdir(), key=lambda child: child.name.lower())
    except OSError as exc:
        return _error(_os_error("list", target, exc), path=str(target))

    entries: list[dict[str, Any]] = []
    for child in children:
        try:
            stats = child.stat()
        except OSError:
            continue  # broken symlink or an entry that vanished mid-scan
        if child.is_symlink():
            kind = "link"
        elif child.is_dir():
            kind = "dir"
        else:
            kind = "file"
        entries.append(
            {
                "name": child.name,
                "type": kind,
                "size_bytes": stats.st_size,
                "modified": datetime.fromtimestamp(stats.st_mtime).isoformat(
                    timespec="seconds"
                ),
            }
        )
    return _ok(path=str(target), count=len(entries), entries=entries)


# --------------------------------------------------------------------------- #
# 5. Guardrails
# --------------------------------------------------------------------------- #
# A deliberately small deny-list for commands that destroy work irrecoverably.
# This is a speed bump, not a sandbox: a regex can always be side-stepped, and it
# only inspects run_shell_command. MY_AGENT_GUARD=0 turns it off.
_DESTRUCTIVE_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    (r"\brm\s+(-{1,2}[a-z-]+\s+)*-[a-z]*[rf][a-z]*\b", "a recursive or forced delete (rm -rf)"),
    (
        r"\bremove-item\b[^\n|]*-[a-z]*(recurse|force)",
        "a recursive or forced delete (Remove-Item -Recurse/-Force)",
    ),
    (r"\b(del|erase)\s+/[a-z]*[fsq]", "a forced delete (del /f /s /q)"),
    (r"\b(rmdir|rd)\s+[^\n]*/s\b", "a recursive directory delete (rmdir /s)"),
    (r"\b(format|mkfs(\.\w+)?)\b", "formatting a filesystem"),
    (r"\bdd\b[^\n]*\bof=", "a raw device write (dd of=...)"),
    (r"\bgit\s+push\b[^\n]*(\s-f\b|--force)", "a force push (git push --force)"),
    (r"\bgit\s+reset\s+--hard\b", "a hard reset (git reset --hard)"),
    (r"\bgit\s+clean\s+-[a-z]*[fdx]", "deleting untracked files (git clean)"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "shutting the machine down"),
    (r"\bdrop\s+(table|database|schema)\b", "dropping a database object (DROP TABLE)"),
    (r":\s*\(\s*\)\s*\{[^\n]*\}\s*;\s*:", "a fork bomb"),
)


def _block_destructive_commands(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    """``before_tool_callback``: refuses obviously destructive shell commands.

    Returning a dict skips the tool call and hands that dict back to the model as
    the tool result, so it can explain the refusal instead of silently retrying.

    Args:
        tool: The tool ADK is about to call.
        args: Arguments ADK extracted from the model's function call.
        tool_context: ADK tool context (unused here).

    Returns:
        A refusal result, or ``None`` to let the call proceed.
    """
    if not GUARD_ENABLED or tool.name != run_shell_command.__name__:
        return None

    command = str(args.get("command", ""))
    for pattern, label in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            logger.warning("Blocked a destructive command (%s): %s", label, command)
            return _error(
                f"Refused to run this command: it looks like {label}. The guard "
                "blocked it, so nothing was executed.",
                command=command,
                matched_rule=label,
                hint=(
                    "Tell the user exactly what you wanted to run and let them run "
                    "it, or use a non-destructive alternative. Setting "
                    "MY_AGENT_GUARD=0 disables this guard."
                ),
            )
    return None


def _report_tool_error(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    error: Exception,
) -> dict[str, Any]:
    """``on_tool_error_callback``: turns an unexpected tool crash into a result.

    The tools already catch the failures they expect, so anything reaching here is
    a bug or an unhandled input. Reporting it as an ordinary tool result keeps the
    conversation going instead of tearing the turn down with a traceback.

    Args:
        tool: The tool that raised.
        args: Arguments the tool was called with.
        tool_context: ADK tool context (unused here).
        error: The exception raised by the tool.

    Returns:
        A tool result describing the failure.
    """
    logger.error("Tool %s raised %s", tool.name, error, exc_info=error)
    return _error(
        f"{tool.name} failed with {type(error).__name__}: {error}",
        tool=tool.name,
        hint="Fix the inputs and try again; do not repeat the identical call.",
    )


# --------------------------------------------------------------------------- #
# 6. The agent
# --------------------------------------------------------------------------- #
# One instruction string: two `instruction=` keywords in the same call is a
# SyntaxError, and the earlier draft had exactly that (plus a typo, "senor").
INSTRUCTION: Final[str] = f"""\
You are a senior software developer agent: an experienced engineer who reviews,
designs and writes code, and who explains trade-offs crisply.

# Workspace
- Root: {WORKSPACE_ROOT}
- Relative paths in tool calls resolve against that root.

# Tool results
Every tool returns a JSON object whose "status" is "success", "error" or
"timeout". Check "status" before trusting any other field.
- "error" means the call did not do what you wanted: read "error" and "hint", then
  correct the call. Never repeat a failing call unchanged.
- Outputs are cut off past {MAX_OUTPUT_CHARS} characters per field and flagged with
  "truncated": true. Use targeted commands (head, Select-String, --quiet) instead of
  dumping whole files or directory trees.
- Shell commands are killed after {SHELL_TIMEOUT_SECONDS}s and report exit_code,
  stdout and stderr.

# Rules
1. Read before you write: call read_file before editing a file, and never assume its
   contents.
2. write_file replaces a whole file, so re-emit the complete new version.
3. Verify your work: after changing code, run the relevant tests, linter or entry
   point with run_shell_command, and report what actually happened.
4. Prefer small, incremental changes over large rewrites, and match the existing code
   style, naming and structure.
5. After each action, state briefly what changed and why.
6. If a command fails, read stderr, fix the root cause, and do not retry blindly.
7. Never claim something works without having checked it.
8. Destructive commands (recursive deletes, force pushes, hard resets) are blocked by
   a guard. If a command is refused, say what you wanted to run and let the user run
   it themselves.

# Style
- Answer in the user's language; lead with the conclusion or the diff.
- Give complete, runnable snippets with file paths rather than fragments.
- Say "I don't know" instead of guessing about APIs, versions or behaviour.
"""

root_agent = Agent(
    model=LiteLlm(model=MODEL_ID, api_key=API_KEY),
    name="senior_developer_agent",
    description=(
        "Senior software developer agent: reviews code, proposes architectures, and "
        "implements and verifies changes through file and shell tools."
    ),
    instruction=INSTRUCTION,
    tools=[read_file, write_file, run_shell_command, list_directory],
    before_tool_callback=_block_destructive_commands,
    on_tool_error_callback=_report_tool_error,
)
