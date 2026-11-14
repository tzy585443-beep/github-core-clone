"""Model Context Protocol endpoint at /mcp.

Speaks revision 2026-07-28 (server/discover advertises supportedVersions) and
the earlier initialize handshake. tools/list is a fixed document so two calls
match byte for byte.
"""

from __future__ import annotations

import json

JSON = "application/json; charset=utf-8"
VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

TOOLS = [
    {
        "name": "create_repo",
        "description": "Create a repository owned by the authenticated user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repository name"},
                "description": {"type": "string", "description": "Repository description"},
                "private": {"type": "boolean", "description": "Whether the repository is private"},
                "auto_init": {"type": "boolean", "description": "Create an initial commit with a README"},
                "full_name": {"type": "string", "description": "owner/name, ignored on create"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_repo",
        "description": "Get a repository by owner and name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner login"},
                "repo": {"type": "string", "description": "Repository name"},
                "full_name": {"type": "string", "description": "owner/repo alternative to owner and repo"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "create_issue",
        "description": "Open an issue in a repository. Labels must already exist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner login"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body"},
                "full_name": {"type": "string", "description": "owner/repo alternative to owner and repo"},
            },
            "required": ["owner", "repo", "title"],
        },
    },
    {
        "name": "list_issues",
        "description": "List issues in a repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner login"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "description": "open, closed, or all"},
                "full_name": {"type": "string", "description": "owner/repo alternative to owner and repo"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "create_pull_request",
        "description": "Open a pull request from a head branch into a base branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner login"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Pull request title"},
                "head": {"type": "string", "description": "Branch containing the changes"},
                "base": {"type": "string", "description": "Branch to merge into"},
                "body": {"type": "string", "description": "Pull request body"},
                "full_name": {"type": "string", "description": "owner/repo alternative to owner and repo"},
            },
            "required": ["owner", "repo", "title", "head", "base"],
        },
    },
    {
        "name": "merge_pull_request",
        "description": "Squash-merge a pull request onto its base branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner login"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "commit_title": {"type": "string", "description": "Title of the squash commit"},
                "merge_method": {"type": "string", "description": "Ignored; the merge is always a squash"},
                "full_name": {"type": "string", "description": "owner/repo alternative to owner and repo"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
    },
]

_TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def dumps(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()


def _rpc(id_, result=None, error=None) -> dict:
    body = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return body


def _error(id_, code: int, message: str) -> dict:
    return _rpc(id_, error={"code": code, "message": message})


def _tool_named(name: str) -> dict | None:
    if not isinstance(name, str) or not name:
        return None
    direct = _TOOL_BY_NAME.get(name)
    if direct:
        return direct
    lowered = name.lower().replace("-", "_")
    for tool in TOOLS:
        canonical = tool["name"]
        if lowered == canonical or lowered.endswith("__" + canonical) or lowered.endswith("/" + canonical):
            return tool
    return None


def _owner_repo(args: dict) -> tuple[str, str]:
    owner = args.get("owner")
    repo = args.get("repo")
    full = args.get("full_name")
    if (not owner or not repo) and isinstance(full, str) and "/" in full:
        left, right = full.split("/", 1)
        owner = owner or left
        repo = repo or right
    return str(owner or ""), str(repo or "")


def _missing(tool: dict, args: dict) -> str | None:
    for name in tool["inputSchema"].get("required", []):
        # full_name can satisfy owner and repo together.
        if name in ("owner", "repo") and args.get("full_name"):
            owner, repo = _owner_repo(args)
            if name == "owner" and owner:
                continue
            if name == "repo" and repo:
                continue
        value = args.get(name)
        if value is None or value == "":
            return name
    return None


def _text(payload, is_error: bool = False) -> dict:
    if not isinstance(payload, str):
        payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return {"content": [{"type": "text", "text": payload}], "isError": is_error}


def _call_tool(tool: dict, args: dict) -> dict:
    from gitstore import ensure_git
    from store import get_store

    store = get_store()
    name = tool["name"]
    if name == "create_repo":
        status, repo = store.create_repo({
            "name": args.get("name"),
            "description": args.get("description"),
            "private": bool(args.get("private")),
            "auto_init": bool(args.get("auto_init")),
        })
        if status != 201:
            return _text(repo, True)
        created = store.get_repo(store.login, str(args.get("name")))
        if created is not None:
            ensure_git(created, store)
            repo = store.repo_public(created)
        return _text(repo)
    owner, repo_name = _owner_repo(args)
    repo = store.get_repo(owner, repo_name)
    if name == "get_repo":
        if repo is None:
            return _text({"message": "Not Found"}, True)
        return _text(store.repo_public(repo))
    if repo is None:
        return _text({"message": "Not Found"}, True)
    if name == "create_issue":
        status, issue = store.create_issue(repo, {"title": args.get("title"), "body": args.get("body")})
        if status != 201:
            return _text(issue, True)
        return _text(issue)
    if name == "list_issues":
        state = args.get("state") or "open"
        if state not in ("open", "closed", "all"):
            state = "open"
        issues = [store.issue_public(repo, issue) for issue in store.list_issues(repo, state)]
        return _text(issues)
    if name == "create_pull_request":
        ensure_git(repo, store)
        status, pull = store.create_pull(repo, {
            "title": args.get("title"),
            "head": args.get("head"),
            "base": args.get("base"),
            "body": args.get("body"),
        })
        if status != 201:
            return _text(pull, True)
        return _text(pull)
    if name == "merge_pull_request":
        ensure_git(repo, store)
        number = args.get("pull_number")
        try:
            number = int(number)
        except (TypeError, ValueError):
            return _text({"message": "pull_number must be an integer"}, True)
        pull = repo.pulls.get(number)
        if pull is None:
            return _text({"message": "Not Found"}, True)
        status, merged = store.merge_pull(repo, pull, {
            "commit_title": args.get("commit_title"),
            "merge_method": args.get("merge_method"),
        })
        if status != 200:
            return _text(merged, True)
        return _text(merged)
    return _text({"message": "Unknown tool"}, True)


def _handle_object(message: dict) -> tuple[dict | None, list[tuple[str, str]]]:
    if not isinstance(message, dict) or message.get("jsonrpc") not in (None, "2.0"):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request"), []
    method = message.get("method")
    id_ = message.get("id", None)
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    headers: list[tuple[str, str]] = []
    if not isinstance(method, str):
        return _error(id_, -32600, "Invalid Request"), headers
    if method.startswith("notifications/") or method == "initialized":
        return None, headers
    if method == "server/discover":
        return _rpc(id_, {
            "resultType": "complete",
            "supportedVersions": VERSIONS,
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": "GitHub-compatible forge. Use the tools to manage repositories, issues, and pull requests.",
            "ttlMs": 0,
            "cacheScope": "private",
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "github-forge", "version": "1.0.0"}},
        }), headers
    if method == "initialize":
        version = params.get("protocolVersion") or "2025-03-26"
        if version not in VERSIONS:
            version = "2025-03-26"
        headers.append(("Mcp-Session-Id", "forge"))
        return _rpc(id_, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "github-forge", "version": "1.0.0"},
            "instructions": "GitHub-compatible forge. Use the tools to manage repositories, issues, and pull requests.",
        }), headers
    if method == "ping":
        return _rpc(id_, {}), headers
    if method == "tools/list":
        return _rpc(id_, {"tools": TOOLS}), headers
    if method == "tools/call":
        tool = _tool_named(params.get("name"))
        if tool is None:
            return _error(id_, -32602, f"Unknown tool: {params.get('name')}"), headers
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(id_, -32602, "Invalid arguments"), headers
        missing = _missing(tool, arguments)
        if missing:
            return _error(id_, -32602, f"Missing required argument: {missing}"), headers
        try:
            return _rpc(id_, _call_tool(tool, arguments)), headers
        except Exception as exc:
            return _rpc(id_, _text({"message": str(exc)}, True)), headers
    return _error(id_, -32601, f"Method not found: {method}"), headers


def handle(body: bytes) -> tuple:
    if not body or not body.strip():
        return 400, dumps(_error(None, -32700, "Parse error")), JSON, []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, dumps(_error(None, -32700, "Parse error")), JSON, []
    if isinstance(payload, list):
        results = []
        headers: list[tuple[str, str]] = []
        for item in payload:
            result, extra = _handle_object(item if isinstance(item, dict) else {})
            if result is not None:
                results.append(result)
            headers.extend(extra)
        return 200, dumps(results), JSON, headers
    if not isinstance(payload, dict):
        return 400, dumps(_error(None, -32700, "Parse error")), JSON, []
    result, headers = _handle_object(payload)
    if result is None:
        return 202, b"", JSON, headers
    return 200, dumps(result), JSON, headers
