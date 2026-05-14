#!/usr/bin/env python3
"""Claude Code Session Manager - macOS Finder Style"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Any

from flask import Flask, jsonify, render_template_string, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

CLAUDE_DIR = Path.home() / ".claude"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
CUSTOM_PROJECTS_FILE = CLAUDE_DIR / "custom_projects.json"
DELETED_SESSIONS_FILE = CLAUDE_DIR / "deleted_sessions.json"
TERMINAL_CONFIG_FILE = CLAUDE_DIR / "terminal_config.json"

CMUX_CLI = "/Applications/cmux.app/Contents/Resources/bin/cmux"
CMUX_APP = "/Applications/cmux.app"

PATH_ENCODE_RE = re.compile(r"[^a-zA-Z0-9]")

# System-injected prefixes to skip when extracting titles
SYSTEM_CAVEATS = [
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<system-reminder>",
]

DEFAULT_PORT = 5199
DEFAULT_HOST = "127.0.0.1"
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
WINDOW_TITLE = "Claude Code 会话管理器"


# ============================================================
#  Utility functions (inspired by cc-switch)
# ============================================================

def encode_path(path: str) -> str:
    return PATH_ENCODE_RE.sub("-", path)


def port_is_open(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=1):
            return True
    except Exception:
        return False


def wait_for_server(url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"等待服务启动超时: {url}")


def parse_timestamp(value) -> Optional[int]:
    """Parse a timestamp value (int ms/sec or ISO string) into milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 1_000_000_000_000 else n * 1000
    if isinstance(value, str):
        try:
            n = int(value)
            return n if n > 1_000_000_000_000 else n * 1000
        except (ValueError, TypeError):
            pass
        # Try ISO format
        from datetime import datetime, timezone
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ]:
            try:
                dt = datetime.strptime(value.replace("Z", "+00:00"), fmt if "Z" not in value else "%Y-%m-%dT%H:%M:%S.%fZ")
                return int(dt.timestamp() * 1000)
            except (ValueError, AttributeError):
                continue
        try:
            dt = datetime.fromisoformat(value)
            return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
    return None


def extract_text(content: Any) -> str:
    """Recursively extract text from any content format (mirrors cc-switch extract_text)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            text = extract_text_from_block(item)
            if text and text.strip():
                parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        # Try common text fields
        for field in ("text", "content", "input_text", "output_text"):
            if field in content:
                return extract_text(content[field])
        return ""
    return str(content)


def extract_text_from_block(block: dict) -> str:
    """Extract text from a single content block."""
    block_type = block.get("type", "")

    # tool_use → show tool name
    if block_type == "tool_use":
        name = block.get("name", "unknown")
        return f"[Tool: {name}]"

    # tool_result → recursively extract nested content
    if block_type == "tool_result":
        if "content" in block:
            text = extract_text(block["content"])
            if text.strip():
                return text[:1000]  # truncate long tool outputs
        return ""

    # thinking block
    if block_type == "thinking":
        return "[思考中...]"

    # text block or other block with text
    if "text" in block:
        return str(block.get("text", ""))

    if "input_text" in block:
        return str(block.get("input_text", ""))

    if "output_text" in block:
        return str(block.get("output_text", ""))

    # nested content
    if "content" in block:
        return extract_text(block["content"])

    return ""


def classify_message_role(msg: dict) -> str:
    """Determine the display role for a message."""
    message = msg.get("message", {})
    role = message.get("role", "unknown")

    # Reclassify: pure tool_result user messages → "tool"
    if role == "user":
        content = message.get("content", "")
        if isinstance(content, list) and content:
            all_tool = all(
                b.get("type") == "tool_result"
                for b in content
            )
            if all_tool:
                return "tool"

    return role


def is_empty_content(content: Any) -> bool:
    """Check if content is effectively empty."""
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        return all(
            b.get("text", "").strip() == "" and b.get("type", "") not in ("tool_use", "tool_result")
            for b in content
        )
    return False


def is_system_message(text: str) -> bool:
    """Check if text is a system-injected message that should be skipped for titles."""
    stripped = text.strip()
    if not stripped:
        return True
    # Slash commands
    if stripped.startswith("/"):
        return True
    # System XML tags
    for caveat in SYSTEM_CAVEATS:
        if caveat in stripped:
            return True
    return False


def load_history():
    if not HISTORY_FILE.exists():
        return []
    entries = []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def build_path_map(history_entries):
    """Build reverse map: encoded path -> original path."""
    path_map = {}
    seen = set()
    for entry in history_entries:
        project = entry.get("project", "")
        if project and project not in seen:
            seen.add(project)
            encoded = encode_path(project)
            path_map[encoded] = project
    if PROJECTS_DIR.exists():
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name not in path_map:
                path_map[d.name] = d.name
    # Include custom (user-added) projects
    for proj in load_custom_projects():
        path = proj.get("path", "")
        if path and path not in path_map.values():
            encoded = encode_path(path)
            if encoded not in path_map:
                path_map[encoded] = path
    return path_map


def load_custom_projects():
    if not CUSTOM_PROJECTS_FILE.exists():
        return []
    try:
        data = json.loads(CUSTOM_PROJECTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_custom_projects(projects):
    CUSTOM_PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")


def load_terminal_config():
    if not TERMINAL_CONFIG_FILE.exists():
        return {"terminal": "cmux" if os.path.exists(CMUX_APP) else "terminal"}
    try:
        data = json.loads(TERMINAL_CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {"terminal": "cmux" if os.path.exists(CMUX_APP) else "terminal"}


def save_terminal_config(config):
    TERMINAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    TERMINAL_CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def load_deleted_sessions():
    if not DELETED_SESSIONS_FILE.exists():
        return set()
    try:
        data = json.loads(DELETED_SESSIONS_FILE.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def save_deleted_sessions(deleted_set):
    DELETED_SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DELETED_SESSIONS_FILE.write_text(
        json.dumps(sorted(deleted_set), ensure_ascii=False), encoding="utf-8"
    )


def get_effective_terminal():
    """Return the terminal to use: 'cmux' or 'terminal'."""
    config = load_terminal_config()
    preferred = config.get("terminal", "cmux")
    if preferred == "cmux" and os.path.exists(CMUX_APP):
        return "cmux"
    if preferred == "cmux":
        # cmux not installed, fall back to Terminal
        return "terminal"
    return preferred


def cmux_cli_works():
    """Check if cmux CLI is usable (Flask running inside cmux)."""
    if not os.path.exists(CMUX_CLI):
        return False
    try:
        result = subprocess.run([CMUX_CLI, "ping"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def run_osascript(script: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run AppleScript with optional argv, preserving argument boundaries."""
    cmd = ["osascript", "-e", script]
    if args:
        cmd.append("--")
        cmd.extend(args)
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)


def launch_cmux_via_applescript(project_path: str, command: str) -> dict:
    """Open a project in cmux and execute a command without manual paste."""
    launch_script = '''
    on run argv
        set shellCommand to item 1 of argv
        set deadline to (current date) + 10
        tell application "cmux" to activate
        tell application "cmux"
            set targetWindow to new window
            activate window targetWindow
        end tell
        repeat while (current date) is less than deadline
            try
                tell application "cmux"
                    set currentTerminal to focused terminal of selected tab of targetWindow
                    input text (shellCommand & linefeed) to currentTerminal
                    focus currentTerminal
                    return "ok"
                end tell
            end try
            delay 0.2
        end repeat
        error "cmux terminal did not become ready in time"
    end run
    '''
    run_osascript(launch_script, command, timeout=15)
    return {"success": True, "message": f"cmux: {command}"}


def get_session_status(session_id: str) -> Optional[dict]:
    if not SESSIONS_DIR.exists():
        return None
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("sessionId") == session_id:
                return data
        except (json.JSONDecodeError, OSError):
            continue
    return None


def read_head_tail_lines(path: Path, head_n: int = 10, tail_n: int = 30):
    """Read first head_n and last tail_n lines from a file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            all_lines = [line.strip() for line in f if line.strip()]
    except OSError:
        return [], []

    if not all_lines:
        return [], []

    head = all_lines[:head_n]
    skip = max(0, len(all_lines) - tail_n)
    tail = all_lines[skip:]
    return head, tail


def parse_session_meta(path: Path) -> dict:
    """Parse session metadata from a JSONL file (like cc-switch parse_session)."""
    # Skip agent session files
    if path.name.startswith("agent-"):
        return {}

    head, tail = read_head_tail_lines(path, 50, 30)

    meta = {
        "sessionId": "",
        "cwd": "",
        "title": "",
        "summary": "",
        "createdAt": None,
        "lastActiveAt": None,
        "messageCount": 0,
    }

    # Extract from head lines: sessionId, cwd, createdAt, title
    for line in head:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not meta["sessionId"]:
            meta["sessionId"] = msg.get("sessionId", "")
        if not meta["cwd"]:
            meta["cwd"] = msg.get("cwd", "")
        if meta["createdAt"] is None:
            meta["createdAt"] = parse_timestamp(msg.get("timestamp"))

        # Find first real user message as title
        if not meta["title"]:
            if msg.get("type") == "user" or msg.get("message", {}).get("role") == "user":
                content = msg.get("message", {}).get("content", "")
                text = extract_text(content).strip()
                if text and not is_system_message(text):
                    # Skip pure tool_result messages
                    if isinstance(content, list):
                        all_tool = all(b.get("type") == "tool_result" for b in content)
                        if all_tool:
                            continue
                    meta["title"] = text

        if all([meta["sessionId"], meta["cwd"], meta["createdAt"], meta["title"]]):
            break

    # Extract from tail lines: lastActiveAt, summary, custom-title
    for line in reversed(tail):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if meta["lastActiveAt"] is None:
            meta["lastActiveAt"] = parse_timestamp(msg.get("timestamp"))

        # custom-title overrides title
        if msg.get("type") == "custom-title":
            custom = msg.get("customTitle", "").strip()
            if custom:
                meta["title"] = custom

        if not meta["summary"]:
            if msg.get("isMeta") is True:
                continue
            if msg.get("message"):
                text = extract_text(msg["message"].get("content", "")).strip()
                if text:
                    meta["summary"] = text

        if meta["lastActiveAt"] is not None and meta["summary"]:
            break

    # Fallback sessionId from filename
    if not meta["sessionId"]:
        meta["sessionId"] = path.stem

    # Fallback title from cwd basename
    if not meta["title"] and meta["cwd"]:
        meta["title"] = os.path.basename(meta["cwd"])

    # Count total lines
    meta["messageCount"] = count_session_messages(path)

    # Truncate title
    if len(meta.get("title", "")) > 80:
        meta["title"] = meta["title"][:80] + "..."

    # Truncate summary
    if len(meta.get("summary", "")) > 160:
        meta["summary"] = meta["summary"][:160] + "..."

    return meta


def count_session_messages(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


@app.route("/")
def index():
    with open(STATIC_DIR / "index.html", "r", encoding="utf-8") as f:
        return render_template_string(f.read())


@app.route("/api/projects")
def api_projects():
    entries = load_history()
    path_map = build_path_map(entries)

    # Aggregate: project -> {sessions: set, lastActivity, sessionData: {sessionId: {firstMsg, timestamp, msgCount}}}
    projects: Dict[str, dict] = {}

    for entry in entries:
        project = entry.get("project", "unknown")
        session_id = entry.get("sessionId", "")
        timestamp = entry.get("timestamp", 0)

        if project not in projects:
            projects[project] = {
                "sessions": {},
                "lastActivity": timestamp,
            }

        proj = projects[project]
        if timestamp > proj["lastActivity"]:
            proj["lastActivity"] = timestamp

        if session_id not in proj["sessions"]:
            proj["sessions"][session_id] = {
                "firstDisplay": entry.get("display", ""),
                "timestamp": timestamp,
            }
        else:
            # Keep the earliest message as first display
            if timestamp < proj["sessions"][session_id]["timestamp"]:
                proj["sessions"][session_id]["firstDisplay"] = entry.get("display", "")
                proj["sessions"][session_id]["timestamp"] = timestamp

    result = []
    for project_path, data in projects.items():
        name = os.path.basename(project_path) or project_path
        parent = os.path.basename(os.path.dirname(project_path))
        encoded = encode_path(project_path)
        result.append({
            "projectPath": project_path,
            "projectName": name,
            "parentDir": parent,
            "encoded": encoded,
            "sessionCount": len(data["sessions"]),
            "lastActivity": data["lastActivity"],
        })

    # Include custom projects that have no history yet
    existing_paths = {p["projectPath"] for p in result}
    for proj in load_custom_projects():
        path = proj.get("path", "")
        if path and path not in existing_paths and os.path.isdir(path):
            name = os.path.basename(path) or path
            parent = os.path.basename(os.path.dirname(path))
            encoded = encode_path(path)
            result.append({
                "projectPath": path,
                "projectName": name,
                "parentDir": parent,
                "encoded": encoded,
                "sessionCount": 0,
                "lastActivity": 0,
            })

    result.sort(key=lambda p: p["lastActivity"], reverse=True)
    return jsonify(result)


@app.route("/api/projects/<path:encoded>/sessions")
def api_project_sessions(encoded: str):
    entries = load_history()
    path_map = build_path_map(entries)
    project_path = path_map.get(encoded)

    if not project_path:
        return jsonify([])

    deleted = load_deleted_sessions()
    encoded_correct = encode_path(project_path)
    session_dir = PROJECTS_DIR / encoded_correct

    # Collect session IDs and first display from history.jsonl
    sessions_index: Dict[str, dict] = {}
    for entry in entries:
        if entry.get("project") != project_path:
            continue
        sid = entry.get("sessionId", "")
        ts = entry.get("timestamp", 0)
        if sid not in sessions_index:
            sessions_index[sid] = {
                "sessionId": sid,
                "firstDisplay": entry.get("display", ""),
                "timestamp": ts,
            }
        elif ts < sessions_index[sid]["timestamp"]:
            sessions_index[sid]["firstDisplay"] = entry.get("display", "")
            sessions_index[sid]["timestamp"] = ts

    result = []
    for sid, data in sessions_index.items():
        if sid in deleted:
            continue
        jsonl_path = session_dir / f"{sid}.jsonl"

        # Parse rich metadata from JSONL file
        if jsonl_path.exists():
            meta = parse_session_meta(jsonl_path)
            first_msg = meta.get("title") or meta.get("summary") or data["firstDisplay"]
            msg_count = meta.get("messageCount", 0)
            ts = meta.get("createdAt") or data["timestamp"]
        else:
            first_msg = data["firstDisplay"]
            msg_count = 0
            ts = data["timestamp"]

        # Check active status
        status_info = get_session_status(sid)
        active = status_info is not None
        session_status = status_info.get("status", "ended") if status_info else "ended"

        if len(first_msg) > 120:
            first_msg = first_msg[:120] + "..."

        result.append({
            "sessionId": sid,
            "firstMessage": first_msg or "(空)",
            "timestamp": ts,
            "messageCount": msg_count,
            "status": session_status,
            "active": active,
            "pid": status_info.get("pid") if status_info else None,
        })

    result.sort(key=lambda s: (s.get("timestamp") or 0), reverse=True)
    return jsonify(result)


@app.route("/api/projects/<path:encoded>/sessions/<session_id>/messages")
def api_session_messages(encoded: str, session_id: str):
    entries = load_history()
    path_map = build_path_map(entries)
    project_path = path_map.get(encoded)

    if not project_path:
        return jsonify({"error": "Project not found"}), 404

    encoded_correct = encode_path(project_path)
    jsonl_path = PROJECTS_DIR / encoded_correct / f"{session_id}.jsonl"

    if not jsonl_path.exists():
        return jsonify({"messages": [], "total": 0})

    limit = request.args.get("limit", 1000, type=int)
    offset = request.args.get("offset", 0, type=int)

    # Parse all lines, filter to conversational messages
    all_messages = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Skip meta messages
                if msg.get("isMeta") is True:
                    continue

                message = msg.get("message")
                if not message:
                    continue

                # Classify role (tool_result → "tool")
                role = classify_message_role(msg)
                if role == "unknown":
                    continue

                # Extract content recursively
                content = extract_text(message.get("content", ""))

                # Skip empty messages
                if not content.strip():
                    continue

                ts = msg.get("timestamp", "")

                all_messages.append({
                    "role": role,
                    "content": content,
                    "timestamp": ts,
                })
    except OSError:
        return jsonify({"messages": [], "total": 0})

    total = len(all_messages)
    page_messages = all_messages[offset : offset + limit]

    return jsonify({"messages": page_messages, "total": total, "offset": offset, "limit": limit})


@app.route("/api/launch", methods=["POST"])
def api_launch():
    data = request.get_json() or {}
    action = data.get("action", "continue")
    project_path = data.get("projectPath", "")
    session_id = data.get("sessionId", "")
    fork = data.get("fork", False)
    name = data.get("name", "")
    skip_perms = data.get("dangerouslySkipPermissions", False)

    if not project_path or not os.path.isdir(project_path):
        return jsonify({"success": False, "error": "项目目录不存在"}), 400

    if action == "continue":
        cmd = "claude -c"
    elif action == "new":
        cmd = "claude"
        if name:
            cmd += f' --name {shlex.quote(name)}'
    elif action == "resume":
        if not session_id:
            return jsonify({"success": False, "error": "需要 sessionId"}), 400
        fork_flag = " --fork-session" if fork else ""
        cmd = f"claude --resume {session_id}{fork_flag}"
    else:
        return jsonify({"success": False, "error": f"未知操作: {action}"}), 400

    if skip_perms:
        cmd += " --dangerously-skip-permissions"

    terminal = get_effective_terminal()

    # --- cmux path ---
    if terminal == "cmux":
        full_cmd = f"cd {shlex.quote(project_path)} && clear && echo 'Claude Code - {project_path}' && {cmd}"

        # Try cmux CLI (works when Flask runs inside cmux)
        if cmux_cli_works():
            try:
                subprocess.run(
                    [CMUX_CLI, "new-workspace", "--cwd", project_path, "--command", full_cmd],
                    check=True, capture_output=True, text=True, timeout=10
                )
                return jsonify({"success": True, "message": f"cmux: {cmd}"})
            except subprocess.CalledProcessError as e:
                return jsonify({"success": False, "error": f"cmux CLI 失败: {e.stderr}"}), 500

        # Fallback: open workspace in cmux and inject the command automatically
        try:
            result = launch_cmux_via_applescript(project_path, full_cmd)
            return jsonify(result)
        except subprocess.CalledProcessError as e:
            return jsonify({"success": False, "error": f"cmux 启动失败: {e.stderr}"}), 500
        except subprocess.TimeoutExpired:
            return jsonify({"success": False, "error": "cmux 启动超时"}), 500

    # --- Terminal.app path (original) ---
    safe_path = project_path.replace('"', '\\"')

    applescript = f'''
    tell application "Terminal"
        activate
        do script "cd \\"{safe_path}\\" && clear && echo \\"Claude Code - {project_path}\\" && {cmd}"
    end tell
    '''

    try:
        subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True, text=True)
        return jsonify({"success": True, "message": f"Terminal: {cmd}"})
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "error": f"启动失败: {e.stderr}"}), 500


@app.route("/api/projects/<path:encoded>/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(encoded: str, session_id: str):
    entries = load_history()
    path_map = build_path_map(entries)
    project_path = path_map.get(encoded)

    if not project_path:
        return jsonify({"success": False, "error": "项目不存在"}), 404

    encoded_correct = encode_path(project_path)
    jsonl_path = PROJECTS_DIR / encoded_correct / f"{session_id}.jsonl"

    # Delete JSONL file
    if jsonl_path.exists():
        try:
            jsonl_path.unlink()
        except OSError as e:
            return jsonify({"success": False, "error": f"删除会话文件失败: {e}"}), 500

    # Delete status file
    if SESSIONS_DIR.exists():
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("sessionId") == session_id:
                    f.unlink()
                    break
            except (json.JSONDecodeError, OSError):
                continue

    # Record as deleted so it doesn't reappear from history.jsonl
    deleted = load_deleted_sessions()
    deleted.add(session_id)
    save_deleted_sessions(deleted)

    return jsonify({"success": True, "message": "会话已删除"})


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").lower()
    if not query or len(query) < 1:
        return jsonify([])

    entries = load_history()
    results = []
    seen = set()

    for entry in entries:
        display = entry.get("display", "")
        if query in display.lower():
            key = entry.get("sessionId", "")
            if key not in seen:
                seen.add(key)
                results.append({
                    "sessionId": entry.get("sessionId"),
                    "project": entry.get("project"),
                    "projectName": os.path.basename(entry.get("project", "")),
                    "display": display[:200],
                    "timestamp": entry.get("timestamp"),
                    "encoded": encode_path(entry.get("project", "")),
                })

    results.sort(key=lambda r: r["timestamp"], reverse=True)
    return jsonify(results[:50])


@app.route("/api/active-sessions")
def api_active_sessions():
    """Return all currently active sessions across all projects."""
    if not SESSIONS_DIR.exists():
        return jsonify([])

    entries = load_history()
    path_map = build_path_map(entries)

    # Build a lookup: sessionId -> (projectPath, firstDisplay)
    session_info: Dict[str, dict] = {}
    for entry in entries:
        sid = entry.get("sessionId", "")
        if sid not in session_info:
            session_info[sid] = {
                "projectPath": entry.get("project", ""),
                "projectName": os.path.basename(entry.get("project", "")),
                "firstDisplay": entry.get("display", ""),
                "timestamp": entry.get("timestamp", 0),
            }
        elif entry.get("timestamp", 0) < session_info[sid]["timestamp"]:
            session_info[sid]["firstDisplay"] = entry.get("display", "")
            session_info[sid]["timestamp"] = entry.get("timestamp", 0)

    result = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        sid = data.get("sessionId", "")
        pid = data.get("pid")
        cwd = data.get("cwd", "")
        status = data.get("status", "active")

        info = session_info.get(sid, {})
        project_path = info.get("projectPath", cwd)
        encoded = encode_path(project_path)

        # Try to get title from the JSONL file if available
        title = ""
        if encoded and sid:
            jsonl_path = PROJECTS_DIR / encoded / f"{sid}.jsonl"
            if jsonl_path.exists():
                meta = parse_session_meta(jsonl_path)
                title = meta.get("title") or info.get("firstDisplay", "")
        if not title:
            title = info.get("firstDisplay", "")

        if len(title) > 80:
            title = title[:80] + "..."

        result.append({
            "sessionId": sid,
            "pid": pid,
            "cwd": cwd,
            "projectPath": project_path,
            "projectName": os.path.basename(project_path) or project_path,
            "encoded": encoded,
            "status": status,
            "title": title or "(空)",
        })

    result.sort(key=lambda s: s.get("title", ""))
    return jsonify(result)


@app.route("/api/sessions/<session_id>/exit", methods=["POST"])
def api_exit_session(session_id: str):
    """Kill a running session by its PID."""
    if not SESSIONS_DIR.exists():
        return jsonify({"success": False, "error": "没有活跃的会话"}), 404

    status_file = None
    session_data = None
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("sessionId") == session_id:
                status_file = f
                session_data = data
                break
        except (json.JSONDecodeError, OSError):
            continue

    if not session_data:
        return jsonify({"success": False, "error": "未找到该会话"}), 404

    pid = session_data.get("pid")
    if not pid:
        return jsonify({"success": False, "error": "会话没有记录 PID"}), 400

    # Kill the process group
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass

    # Remove the status file
    try:
        if status_file:
            status_file.unlink()
    except OSError:
        pass

    return jsonify({"success": True, "message": "会话已终止"})


@app.route("/api/stats")
def api_stats():
    entries = load_history()
    path_map = build_path_map(entries)

    total_sessions = len(set(e.get("sessionId") for e in entries))
    total_projects = len(path_map)

    active_count = 0
    if SESSIONS_DIR.exists():
        active_count = len(list(SESSIONS_DIR.glob("*.json")))

    return jsonify({
        "totalProjects": total_projects,
        "totalSessions": total_sessions,
        "activeSessions": active_count,
        "totalMessages": len(entries),
    })


@app.route("/api/project-path")
def api_project_path():
    """Resolve an encoded project name to actual path."""
    encoded = request.args.get("encoded", "")
    entries = load_history()
    path_map = build_path_map(entries)
    actual = path_map.get(encoded, encoded)
    exists = os.path.isdir(actual) if actual else False
    return jsonify({"path": actual, "exists": exists})


@app.route("/api/select-folder", methods=["POST"])
def api_select_folder():
    """Open native macOS folder picker and return selected path."""
    applescript = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
    end tell
    tell application frontApp
        activate
    end tell
    set selectedFolder to choose folder with prompt "选择项目文件夹："
    return POSIX path of selectedFolder
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            check=True, capture_output=True, text=True, timeout=120
        )
        folder_path = result.stdout.strip()
        if folder_path and os.path.isdir(folder_path):
            return jsonify({"success": True, "path": folder_path})
        return jsonify({"success": False, "error": "未选择有效文件夹"})
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "选择超时"}), 500
    except subprocess.CalledProcessError as e:
        # User cancelled or other error
        if "User canceled" in (e.stderr or ""):
            return jsonify({"success": False, "error": "用户取消选择"})
        return jsonify({"success": False, "error": f"文件夹选择失败: {e.stderr}"}), 500


@app.route("/api/terminal-config", methods=["GET", "POST"])
def api_terminal_config():
    """Get or set terminal preference."""
    if request.method == "GET":
        config = load_terminal_config()
        cmux_installed = os.path.exists(CMUX_APP)
        inside_cmux = cmux_cli_works()
        return jsonify({
            "terminal": config.get("terminal", "cmux" if cmux_installed else "terminal"),
            "cmuxInstalled": cmux_installed,
            "insideCmux": inside_cmux,
        })

    if request.method == "POST":
        data = request.get_json() or {}
        terminal = data.get("terminal", "")
        if terminal not in ("cmux", "terminal"):
            return jsonify({"success": False, "error": "无效的终端类型，可选: cmux, terminal"}), 400
        config = load_terminal_config()
        config["terminal"] = terminal
        save_terminal_config(config)
        return jsonify({"success": True, "terminal": terminal})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Shut down the Flask server."""
    pid = os.getpid()
    # 延迟杀死进程，确保响应能返回给前端
    subprocess.Popen(["sh", "-c", f"sleep 0.5 && kill {pid}"])
    return jsonify({"success": True, "message": "服务已停止"})


@app.route("/api/custom-projects", methods=["GET", "POST", "DELETE"])
def api_custom_projects():
    """Manage custom (user-added) projects."""
    if request.method == "GET":
        return jsonify(load_custom_projects())

    if request.method == "POST":
        data = request.get_json() or {}
        path = data.get("path", "").strip()
        if not path or not os.path.isdir(path):
            return jsonify({"success": False, "error": "文件夹路径无效或不存在"}), 400

        custom = load_custom_projects()
        existing = next((p for p in custom if p["path"] == path), None)
        if existing:
            return jsonify({"success": True, "message": "项目已存在", "path": path})

        custom.append({
            "path": path,
            "addedAt": int(time.time() * 1000),
        })
        save_custom_projects(custom)
        return jsonify({"success": True, "message": "项目已添加", "path": path})

    if request.method == "DELETE":
        data = request.get_json() or {}
        path = data.get("path", "").strip()
        custom = load_custom_projects()
        new_custom = [p for p in custom if p["path"] != path]
        if len(new_custom) == len(custom):
            return jsonify({"success": False, "error": "未找到该项目"}), 404
        save_custom_projects(new_custom)
        return jsonify({"success": True, "message": "项目已移除"})


def run_flask_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False)


def run_browser_mode(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    print("Claude Code Session Manager")
    print(f"启动服务: http://{host}:{port}")
    webbrowser.open(f"http://{host}:{port}")
    run_flask_server(host=host, port=port)


def run_desktop_mode(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("缺少 pywebview，请先安装桌面模式依赖") from exc

    url = f"http://{host}:{port}"
    if not port_is_open(host, port):
        server_thread = threading.Thread(
            target=run_flask_server,
            kwargs={"host": host, "port": port},
            daemon=True,
        )
        server_thread.start()
        wait_for_server(url)

    webview.create_window(
        WINDOW_TITLE,
        url,
        width=1440,
        height=920,
        min_size=(1100, 720),
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Code 会话管理器")
    parser.add_argument("--browser", action="store_true", help="使用系统浏览器打开")
    parser.add_argument("--desktop", action="store_true", help="使用本地桌面窗口打开")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="监听端口")
    args = parser.parse_args()

    if args.browser and args.desktop:
        raise SystemExit("--browser 和 --desktop 不能同时使用")

    if args.browser:
        run_browser_mode(host=args.host, port=args.port)
    else:
        run_desktop_mode(host=args.host, port=args.port)
