"""GitHub REST v3 routes under /api/v3 (and the same paths at the host root).

Every JSON error has a string message. Pagination uses a Link header with
rel="next" once a collection fills more than one page.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, unquote

from store import Repo, Store, get_store

JSON = "application/json; charset=utf-8"
BAD = object()

ROOT = json.dumps(
    {
        "current_user_url": "/api/v3/user",
        "user_url": "/api/v3/users/{user}",
        "repository_url": "/api/v3/repos/{owner}/{repo}",
        "issue_url": "/api/v3/repos/{owner}/{repo}/issues/{number}",
        "issues_url": "/api/v3/repos/{owner}/{repo}/issues",
        "rate_limit_url": "/api/v3/rate_limit",
    },
    separators=(",", ":"),
).encode()

META = json.dumps(
    {"verifiable_password_authentication": True, "installed_version": "1.0.0"},
    separators=(",", ":"),
).encode()

NOT_FOUND = json.dumps({"message": "Not Found"}, separators=(",", ":")).encode()
BAD_JSON = json.dumps({"message": "Problems parsing JSON"}, separators=(",", ":")).encode()
UNAUTH = json.dumps({"message": "Requires authentication"}, separators=(",", ":")).encode()


def dumps(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def parse_body(raw: bytes) -> dict | object:
    if not raw or not raw.strip():
        return {}
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return BAD
    if not isinstance(data, dict):
        return BAD
    return data


def qint(query: dict, key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


def paginate(items: list, query: dict) -> tuple[list, int, int, int]:
    page = qint(query, "page", 1)
    per_page = qint(query, "per_page", 30)
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 30
    if per_page > 100:
        per_page = 100
    start = (page - 1) * per_page
    return items[start : start + per_page], page, per_page, len(items)


def link_header(path: str, host: str, page: int, per_page: int, total: int) -> str | None:
    if per_page <= 0:
        return None
    last = (total + per_page - 1) // per_page if total else 1
    if last < 1:
        last = 1
    if page >= last:
        return None
    base = f"http://{host}{path}" if host else path
    links = [
        f'<{base}?page={page + 1}&per_page={per_page}>; rel="next"',
        f'<{base}?page={last}&per_page={per_page}>; rel="last"',
    ]
    if page > 1:
        links.append(f'<{base}?page={page - 1}&per_page={per_page}>; rel="prev"')
    return ", ".join(links)


def api_suffix(path: str) -> str | None:
    """Return the path relative to the API root, or None when it is not REST."""
    if path == "/api/v3" or path.startswith("/api/v3/"):
        suffix = path[len("/api/v3") :]
        return suffix or "/"
    if (
        path == "/user"
        or path.startswith("/user/")
        or path == "/repos"
        or path.startswith("/repos/")
        or path == "/meta"
    ):
        return path
    return None


def segments(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


def handle_rest(method: str, path: str, raw_query: str, body: bytes, host: str) -> tuple | None:
    """Return (status, body, content_type, extra_headers) or None if not REST."""
    suffix = api_suffix(path)
    if suffix is None:
        return None
    query = parse_qs(raw_query, max_num_fields=50)
    parts = segments(suffix)
    store = get_store()

    if not parts:
        if method == "GET":
            return 200, ROOT, JSON, (("X-OAuth-Scopes", "repo"),)
        return 404, NOT_FOUND, JSON, ()

    if parts == ["meta"]:
        if method == "GET":
            return 200, META, JSON, ()
        return 404, NOT_FOUND, JSON, ()

    if parts == ["user"]:
        if method == "GET":
            return 200, dumps(store.user_public()), JSON, ()
        return 404, NOT_FOUND, JSON, ()

    if parts == ["user", "repos"]:
        return _user_repos(method, body, query, host, path if path.startswith("/api/") else "/api/v3/user/repos", store)

    if parts[0] == "repos" and len(parts) >= 3:
        return _repo_routes(method, parts, query, body, host, path, store)

    return 404, NOT_FOUND, JSON, ()


def _user_repos(method: str, body: bytes, query: dict, host: str, path: str, store: Store) -> tuple:
    if method == "POST":
        data = parse_body(body)
        if data is BAD:
            return 400, BAD_JSON, JSON, ()
        status, payload = store.create_repo(data)
        if status == 201:
            from gitstore import ensure_git

            repo = store.get_repo(store.login, data.get("name", ""))
            if repo is not None:
                ensure_git(repo, store)
                payload = store.repo_public(repo)
        return status, dumps(payload), JSON, ()
    if method == "GET":
        repos = store.list_repos()
        chunk, page, per_page, total = paginate(repos, query)
        payload = dumps([store.repo_public(repo) for repo in chunk])
        link = link_header(path.split("?", 1)[0], host, page, per_page, total)
        headers = (("Link", link),) if link else ()
        return 200, payload, JSON, headers
    return 404, NOT_FOUND, JSON, ()


def _repo_routes(
    method: str,
    parts: list[str],
    query: dict,
    body: bytes,
    host: str,
    path: str,
    store: Store,
) -> tuple:
    owner, name = parts[1], parts[2]
    repo = store.get_repo(owner, name)
    tail = parts[3:]

    if not tail:
        if method == "GET":
            if repo is None:
                return 404, NOT_FOUND, JSON, ()
            return 200, dumps(store.repo_public(repo)), JSON, ()
        if method == "PATCH":
            if repo is None:
                return 404, NOT_FOUND, JSON, ()
            data = parse_body(body)
            if data is BAD:
                return 400, BAD_JSON, JSON, ()
            updated = store.update_repo(repo, data)
            if "errors" in updated and "id" not in updated:
                return 422, dumps(updated), JSON, ()
            return 200, dumps(updated), JSON, ()
        if method == "DELETE":
            if repo is None or not store.delete_repo(owner, name):
                return 404, NOT_FOUND, JSON, ()
            return 204, b"", JSON, ()
        return 404, NOT_FOUND, JSON, ()

    if repo is None:
        return 404, NOT_FOUND, JSON, ()

    head = tail[0]
    if head == "labels":
        return _labels(method, repo, tail[1:], body, store)
    if head == "issues":
        return _issues(method, repo, tail[1:], query, body, host, path, store)
    if head == "contents":
        return _contents(method, repo, tail[1:], query, body, store)
    if head == "pulls":
        return _pulls(method, repo, tail[1:], query, body, host, path, store)
    return 404, NOT_FOUND, JSON, ()


def _labels(method: str, repo: Repo, rest: list[str], body: bytes, store: Store) -> tuple:
    if not rest:
        if method == "POST":
            data = parse_body(body)
            if data is BAD:
                return 400, BAD_JSON, JSON, ()
            status, payload = store.create_label(repo, data)
            return status, dumps(payload), JSON, ()
        if method == "GET":
            with repo.lock:
                labels = [store.label_public(label) for label in repo.labels.values()]
            return 200, dumps(labels), JSON, ()
        return 404, NOT_FOUND, JSON, ()
    label_name = "/".join(rest)
    label = repo.label_named(label_name)
    if method == "GET":
        if label is None:
            return 404, NOT_FOUND, JSON, ()
        return 200, dumps(store.label_public(label)), JSON, ()
    return 404, NOT_FOUND, JSON, ()


def _issues(
    method: str,
    repo: Repo,
    rest: list[str],
    query: dict,
    body: bytes,
    host: str,
    path: str,
    store: Store,
) -> tuple:
    if not rest:
        if method == "POST":
            data = parse_body(body)
            if data is BAD:
                return 400, BAD_JSON, JSON, ()
            status, payload = store.create_issue(repo, data)
            return status, dumps(payload), JSON, ()
        if method == "GET":
            state = (query.get("state") or ["open"])[0]
            if state not in ("open", "closed", "all"):
                state = "open"
            issues = store.list_issues(repo, state)
            chunk, page, per_page, total = paginate(issues, query)
            payload = dumps([store.issue_public(repo, issue) for issue in chunk])
            link = link_header(path.split("?", 1)[0], host, page, per_page, total)
            headers = (("Link", link),) if link else ()
            return 200, payload, JSON, headers
        return 404, NOT_FOUND, JSON, ()

    if not rest[0].isdigit() or len(rest) > 1:
        return 404, NOT_FOUND, JSON, ()
    number = int(rest[0])
    if method == "GET":
        cached = store.issue_bytes(repo, number)
        if cached is not None:
            return 200, cached, JSON, ()
        pull = repo.pulls.get(number)
        if pull is not None:
            return 200, dumps(store.pull_as_issue(repo, pull)), JSON, ()
        return 404, NOT_FOUND, JSON, ()
    if method == "PATCH":
        with repo.lock:
            issue = repo.issues.get(number)
        if issue is None:
            return 404, NOT_FOUND, JSON, ()
        data = parse_body(body)
        if data is BAD:
            return 400, BAD_JSON, JSON, ()
        status, payload = store.update_issue(repo, issue, data)
        return status, dumps(payload), JSON, ()
    return 404, NOT_FOUND, JSON, ()


def _author(store: Store) -> tuple[str, str, str]:
    login = store.login
    return login, f"{login}@users.noreply.github.com", store.now


def _contents(method: str, repo: Repo, rest: list[str], query: dict, body: bytes, store: Store) -> tuple:
    from gitstore import ensure_git

    path = "/".join(rest)
    git = ensure_git(repo, store)
    branch = (query.get("ref") or query.get("branch") or [repo.default_branch])[0]
    if method == "GET":
        return _contents_get(repo, git, branch, path, store)
    data = parse_body(body)
    if data is BAD:
        return 400, BAD_JSON, JSON, ()
    if isinstance(data, dict) and isinstance(data.get("branch"), str) and data.get("branch"):
        branch = data["branch"]
    name, email, when = _author(store)
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str) or not message:
        message = f"Delete {path}" if method == "DELETE" else f"Update {path}"
    if method == "DELETE":
        sha = data.get("sha") if isinstance(data.get("sha"), str) else None
        result = git.delete_file(branch, path, message, name, email, when, sha)
        if result.status == "missing":
            return 404, NOT_FOUND, JSON, ()
        if result.status == "mismatch":
            return 409, dumps({"message": "sha does not match"}), JSON, ()
        return 200, dumps({"commit": {"sha": result.commit}, "content": None}), JSON, ()
    if method in ("PUT", "POST"):
        encoded = data.get("content")
        if not isinstance(encoded, str):
            return 422, dumps({
                "message": "Validation Failed",
                "errors": [{"resource": "Content", "field": "content", "code": "missing"}],
            }), JSON, ()
        try:
            content = base64.b64decode(encoded, validate=False)
        except Exception:
            return 422, dumps({
                "message": "Validation Failed",
                "errors": [{"resource": "Content", "field": "content", "code": "invalid"}],
            }), JSON, ()
        expected = data.get("sha") if isinstance(data.get("sha"), str) else None
        result = git.put_file(branch, path, content, message, name, email, when, expected)
        if result.status == "missing_branch":
            return 404, dumps({"message": "Branch not found"}), JSON, ()
        if result.status == "mismatch":
            return 409, dumps({"message": "sha does not match"}), JSON, ()
        if result.status == "invalid":
            return 422, dumps({"message": "Validation Failed"}), JSON, ()
        status = 201 if result.status == "created" else 200
        return status, dumps(_content_payload(repo, path, content, result, store)), JSON, ()
    return 404, NOT_FOUND, JSON, ()


def _contents_get(repo, git, branch: str, path: str, store: Store) -> tuple:
    tip, tree = git.tip_tree(branch)
    if tip is None:
        return 404, NOT_FOUND, JSON, ()
    if not path:
        return 200, dumps(_dir_listing(repo, git, tree, "", store)), JSON, ()
    found = git.walk(tree, [part for part in path.split("/") if part])
    if not found:
        return 404, NOT_FOUND, JSON, ()
    kind, mode, sha = found
    if kind == "tree":
        return 200, dumps(_dir_listing(repo, git, sha, path.strip("/") + "/", store)), JSON, ()
    blob = git.objects[sha][1]
    return 200, dumps(_file_object(repo, path, sha, blob, store)), JSON, ()


def _file_object(repo, path: str, sha: str, blob: bytes, store: Store) -> dict:
    full = f"{store.login}/{repo.name}"
    name = path.rsplit("/", 1)[-1]
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(blob),
        "name": name,
        "path": path,
        "sha": sha,
        "content": base64.b64encode(blob).decode(),
        "url": f"/api/v3/repos/{full}/contents/{path}",
        "html_url": f"/{full}/blob/{repo.default_branch}/{path}",
        "download_url": f"/{full}/raw/{repo.default_branch}/{path}",
        "git_url": f"/api/v3/repos/{full}/git/blobs/{sha}",
    }


def _dir_listing(repo, git, tree_sha: str, prefix: str, store: Store) -> list:
    from gitstore import parse_tree

    full = f"{store.login}/{repo.name}"
    entries = []
    for mode, name, sha in parse_tree(git.objects[tree_sha][1]):
        is_dir = mode == "40000"
        path = f"{prefix}{name}"
        size = 0 if is_dir else len(git.objects[sha][1])
        entries.append({
            "type": "dir" if is_dir else "file",
            "name": name,
            "path": path,
            "sha": sha,
            "size": size,
            "url": f"/api/v3/repos/{full}/contents/{path}",
            "html_url": f"/{full}/{'tree' if is_dir else 'blob'}/{repo.default_branch}/{path}",
            "download_url": None if is_dir else f"/{full}/raw/{repo.default_branch}/{path}",
        })
    return entries


def _content_payload(repo, path: str, content: bytes, result, store: Store) -> dict:
    return {
        "content": _file_object(repo, path, result.blob, content, store),
        "commit": {
            "sha": result.commit,
            "message": result.message,
            "tree": {"sha": result.tree},
            "parents": [{"sha": parent} for parent in result.parents],
            "html_url": f"/{store.login}/{repo.name}/commit/{result.commit}",
            "author": {"name": store.login, "email": f"{store.login}@users.noreply.github.com", "date": store.now},
            "committer": {"name": store.login, "email": f"{store.login}@users.noreply.github.com", "date": store.now},
        },
    }


def _pulls(method: str, repo: Repo, rest: list[str], query: dict, body: bytes, host: str, path: str, store: Store) -> tuple:
    from gitstore import ensure_git

    ensure_git(repo, store)
    if not rest:
        if method == "POST":
            data = parse_body(body)
            if data is BAD:
                return 400, BAD_JSON, JSON, ()
            status, payload = store.create_pull(repo, data)
            return status, dumps(payload), JSON, ()
        if method == "GET":
            state = (query.get("state") or ["open"])[0]
            with repo.lock:
                pulls = list(repo.pulls.values())
            if state == "closed":
                pulls = [pull for pull in pulls if pull.state == "closed"]
            elif state != "all":
                pulls = [pull for pull in pulls if pull.state == "open"]
            pulls.sort(key=lambda pull: pull.number, reverse=True)
            chunk, page, per_page, total = paginate(pulls, query)
            payload = dumps([store.pull_public(repo, pull) for pull in chunk])
            link = link_header(path.split("?", 1)[0], host, page, per_page, total)
            headers = (("Link", link),) if link else ()
            return 200, payload, JSON, headers
        return 404, NOT_FOUND, JSON, ()
    if not rest[0].isdigit():
        return 404, NOT_FOUND, JSON, ()
    number = int(rest[0])
    pull = repo.pulls.get(number)
    if pull is None:
        return 404, NOT_FOUND, JSON, ()
    if len(rest) == 1 and method == "GET":
        return 200, dumps(store.pull_public(repo, pull)), JSON, ()
    if len(rest) == 2 and rest[1] == "merge" and method == "PUT":
        data = parse_body(body)
        if data is BAD:
            return 400, BAD_JSON, JSON, ()
        status, payload = store.merge_pull(repo, pull, data)
        return status, dumps(payload), JSON, ()
    return 404, NOT_FOUND, JSON, ()
