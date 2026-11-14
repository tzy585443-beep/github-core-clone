"""In-memory forge state: one login, repositories, labels, and issues.

The login is a single owner for the process. MAKEFUN_SHOP_TOKEN authenticates
that user; a repository under any other owner is missing (404), never forbidden.
Timestamps come from MAKEFUN_TRIAL_NOW so two runs of a trial agree.
"""

from __future__ import annotations

import base64
import os
import threading
from datetime import datetime, timezone


def resolve_login() -> str:
    """The account the bearer token names. Override with FORGE_LOGIN."""
    for key in ("FORGE_LOGIN", "GITHUB_LOGIN", "GITHUB_USER", "MAKEFUN_LOGIN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return "octocat"


def _trial_now() -> str:
    raw = os.environ.get("MAKEFUN_TRIAL_NOW", "").strip()
    if not raw:
        return "1970-01-01T00:00:00Z"
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "1970-01-01T00:00:00Z"


def node_id(kind: str, number: int) -> str:
    return base64.b64encode(f"{kind}:{number}".encode()).decode("ascii")


def parse_node_id(value: str) -> tuple[str, int] | None:
    try:
        raw = base64.b64decode(value, validate=True).decode()
        kind, num = raw.split(":", 1)
        return kind, int(num)
    except Exception:
        return None


class Label:
    __slots__ = ("id", "name", "color", "description", "cache")

    def __init__(self, id_: int, name: str, color: str, description: str):
        self.id = id_
        self.name = name
        self.color = color
        self.description = description
        self.cache: bytes | None = None


class Issue:
    __slots__ = (
        "id",
        "number",
        "title",
        "body",
        "state",
        "labels",
        "created_at",
        "updated_at",
        "closed_at",
        "cache",
    )

    def __init__(self, id_: int, number: int, title: str, body: str | None, now: str):
        self.id = id_
        self.number = number
        self.title = title
        self.body = body
        self.state = "open"
        self.labels: list[str] = []
        self.created_at = now
        self.updated_at = now
        self.closed_at: str | None = None
        self.cache: bytes | None = None


class Repo:
    __slots__ = (
        "id",
        "name",
        "description",
        "private",
        "default_branch",
        "created_at",
        "updated_at",
        "auto_init",
        "labels",
        "issues",
        "issue_order",
        "next_number",
        "open_count",
        "pulls",
        "lock",
        "git",
    )

    def __init__(
        self,
        id_: int,
        name: str,
        description: str | None,
        private: bool,
        default_branch: str,
        now: str,
        auto_init: bool,
    ):
        self.id = id_
        self.name = name
        self.description = description
        self.private = private
        self.default_branch = default_branch or "main"
        self.created_at = now
        self.updated_at = now
        self.auto_init = auto_init
        self.labels: dict[str, Label] = {}
        self.issues: dict[int, Issue] = {}
        self.issue_order: list[int] = []
        self.next_number = 1
        self.open_count = 0
        self.pulls: dict[int, Pull] = {}
        self.lock = threading.RLock()
        self.git = None  # filled in when the object database is attached

    def label_named(self, name: str) -> Label | None:
        found = self.labels.get(name)
        if found is not None:
            return found
        lowered = name.lower()
        for label in self.labels.values():
            if label.name.lower() == lowered:
                return label
        return None


class Pull:
    __slots__ = (
        "id",
        "number",
        "title",
        "body",
        "state",
        "merged",
        "head",
        "base",
        "merge_sha",
        "created_at",
        "updated_at",
        "closed_at",
    )

    def __init__(self, id_: int, number: int, title: str, body: str | None, head: str, base: str, now: str):
        self.id = id_
        self.number = number
        self.title = title
        self.body = body
        self.state = "open"
        self.merged = False
        self.head = head
        self.base = base
        self.merge_sha: str | None = None
        self.created_at = now
        self.updated_at = now
        self.closed_at: str | None = None


class Store:
    def __init__(self) -> None:
        self.login = resolve_login()
        self.user_id = 1
        self.now = _trial_now()
        self.lock = threading.RLock()
        self.by_name: dict[str, Repo] = {}
        self.order: list[str] = []
        self._next_repo = 1
        self._next_obj = 1

    def alloc_obj(self) -> int:
        with self.lock:
            value = self._next_obj
            self._next_obj += 1
            return value

    def user_public(self) -> dict:
        login = self.login
        return {
            "login": login,
            "id": self.user_id,
            "node_id": node_id("User", self.user_id),
            "type": "User",
            "site_admin": False,
            "name": login,
            "avatar_url": "",
            "html_url": f"/{login}",
            "url": "/api/v3/user",
        }

    def create_repo(self, data: dict) -> tuple[int, dict]:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip() or "/" in name or name in (".", ".."):
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "Repository", "field": "name", "code": "invalid"}],
            }
        name = name.strip()
        key = name.lower()
        private = bool(data.get("private") or data.get("visibility") == "private")
        description = data.get("description")
        if description is not None and not isinstance(description, str):
            description = str(description)
        default_branch = data.get("default_branch") or "main"
        if not isinstance(default_branch, str) or not default_branch:
            default_branch = "main"
        auto_init = bool(data.get("auto_init"))
        with self.lock:
            if key in self.by_name:
                return 422, {
                    "message": "Validation Failed",
                    "errors": [{"resource": "Repository", "field": "name", "code": "already_exists"}],
                }
            repo = Repo(
                self._next_repo,
                name,
                description,
                private,
                default_branch,
                self.now,
                auto_init,
            )
            self._next_repo += 1
            self.by_name[key] = repo
            self.order.append(key)
        return 201, self.repo_public(repo)

    def list_repos(self) -> list[Repo]:
        with self.lock:
            keys = list(self.order)
        # Newest first, matching the user-repos listing GitHub returns.
        return [self.by_name[key] for key in reversed(keys) if key in self.by_name]

    def get_repo(self, owner: str, name: str) -> Repo | None:
        if owner.lower() != self.login.lower():
            return None
        with self.lock:
            return self.by_name.get(name.lower())

    def get_repo_by_id(self, repo_id: int) -> Repo | None:
        with self.lock:
            for repo in self.by_name.values():
                if repo.id == repo_id:
                    return repo
        return None

    def delete_repo(self, owner: str, name: str) -> bool:
        if owner.lower() != self.login.lower():
            return False
        with self.lock:
            key = name.lower()
            repo = self.by_name.pop(key, None)
            if repo is None:
                return False
            try:
                self.order.remove(key)
            except ValueError:
                pass
            return True

    def update_repo(self, repo: Repo, data: dict) -> dict:
        with repo.lock:
            if "description" in data:
                description = data["description"]
                repo.description = description if isinstance(description, str) or description is None else str(description)
            if "private" in data:
                repo.private = bool(data["private"])
            if "default_branch" in data and isinstance(data["default_branch"], str) and data["default_branch"]:
                repo.default_branch = data["default_branch"]
            if "name" in data and isinstance(data["name"], str) and data["name"].strip():
                new_name = data["name"].strip()
                new_key = new_name.lower()
                with self.lock:
                    old_key = repo.name.lower()
                    if new_key != old_key and new_key in self.by_name:
                        return {
                            "message": "Validation Failed",
                            "errors": [{"resource": "Repository", "field": "name", "code": "already_exists"}],
                        }
                    if new_key != old_key:
                        self.by_name.pop(old_key, None)
                        self.by_name[new_key] = repo
                        self.order = [new_key if key == old_key else key for key in self.order]
                    repo.name = new_name
            repo.updated_at = self.now
        return self.repo_public(repo)

    def repo_public(self, repo: Repo) -> dict:
        login = self.login
        full = f"{login}/{repo.name}"
        private = repo.private
        return {
            "id": repo.id,
            "node_id": node_id("Repository", repo.id),
            "name": repo.name,
            "full_name": full,
            "private": private,
            "owner": self.user_public(),
            "description": repo.description,
            "fork": False,
            "url": f"/api/v3/repos/{full}",
            "html_url": f"/{full}",
            "clone_url": f"/{full}.git",
            "ssh_url": f"git@github.com:{full}.git",
            "default_branch": repo.default_branch,
            "visibility": "private" if private else "public",
            "permissions": {"admin": True, "maintain": True, "push": True, "triage": True, "pull": True},
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "has_pages": False,
            "has_downloads": True,
            "archived": False,
            "disabled": False,
            "open_issues_count": repo.open_count,
            "open_issues": repo.open_count,
            "watchers_count": 0,
            "watchers": 0,
            "stargazers_count": 0,
            "forks_count": 0,
            "forks": 0,
            "size": 0,
            "language": None,
            "created_at": repo.created_at,
            "updated_at": repo.updated_at,
            "pushed_at": repo.updated_at,
        }

    def create_label(self, repo: Repo, data: dict) -> tuple[int, dict]:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "Label", "field": "name", "code": "missing"}],
            }
        name = name.strip()
        color = data.get("color") or "ededed"
        if not isinstance(color, str):
            color = "ededed"
        color = color.removeprefix("#").lower()
        if len(color) != 6 or any(ch not in "0123456789abcdef" for ch in color):
            color = "ededed"
        description = data.get("description") or ""
        if not isinstance(description, str):
            description = str(description)
        with repo.lock:
            if repo.label_named(name) is not None:
                return 422, {
                    "message": "Validation Failed",
                    "errors": [{"resource": "Label", "field": "name", "code": "already_exists"}],
                }
            label = Label(self.alloc_obj(), name, color, description)
            repo.labels[name] = label
        return 201, self.label_public(label)

    def label_public(self, label: Label) -> dict:
        return {
            "id": label.id,
            "node_id": node_id("Label", label.id),
            "name": label.name,
            "color": label.color,
            "description": label.description,
            "default": False,
        }

    def create_issue(self, repo: Repo, data: dict) -> tuple[int, dict]:
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "Issue", "field": "title", "code": "missing"}],
            }
        body = data.get("body")
        if body is not None and not isinstance(body, str):
            body = str(body)
        raw_labels = data.get("labels") or []
        if isinstance(raw_labels, str):
            raw_labels = [raw_labels]
        if not isinstance(raw_labels, list):
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "Issue", "field": "labels", "code": "invalid"}],
            }
        names: list[str] = []
        for item in raw_labels:
            if isinstance(item, dict):
                item = item.get("name")
            if not isinstance(item, str) or not item:
                return 422, {
                    "message": "Validation Failed",
                    "errors": [{"resource": "Issue", "field": "labels", "code": "invalid"}],
                }
            names.append(item)
        with repo.lock:
            resolved: list[Label] = []
            for name in names:
                label = repo.label_named(name)
                if label is None:
                    return 422, {
                        "message": "Validation Failed",
                        "errors": [{
                            "resource": "Issue",
                            "field": "labels",
                            "code": "invalid",
                            "message": f"Label {name} does not exist",
                        }],
                    }
                resolved.append(label)
            number = repo.next_number
            repo.next_number += 1
            issue = Issue(self.alloc_obj(), number, title.strip(), body, self.now)
            issue.labels = [label.name for label in resolved]
            repo.issues[number] = issue
            repo.issue_order.append(number)
            repo.open_count += 1
        return 201, self.issue_public(repo, issue)

    def update_issue(self, repo: Repo, issue: Issue, data: dict) -> tuple[int, dict]:
        with repo.lock:
            if "title" in data:
                title = data["title"]
                if not isinstance(title, str) or not title.strip():
                    return 422, {
                        "message": "Validation Failed",
                        "errors": [{"resource": "Issue", "field": "title", "code": "missing"}],
                    }
                issue.title = title.strip()
            if "body" in data:
                body = data["body"]
                issue.body = body if isinstance(body, str) or body is None else str(body)
            if "labels" in data and data["labels"] is not None:
                raw_labels = data["labels"]
                if isinstance(raw_labels, str):
                    raw_labels = [raw_labels]
                if not isinstance(raw_labels, list):
                    return 422, {
                        "message": "Validation Failed",
                        "errors": [{"resource": "Issue", "field": "labels", "code": "invalid"}],
                    }
                resolved = []
                for item in raw_labels:
                    if isinstance(item, dict):
                        item = item.get("name")
                    if not isinstance(item, str):
                        return 422, {
                            "message": "Validation Failed",
                            "errors": [{"resource": "Issue", "field": "labels", "code": "invalid"}],
                        }
                    label = repo.label_named(item)
                    if label is None:
                        return 422, {
                            "message": "Validation Failed",
                            "errors": [{
                                "resource": "Issue",
                                "field": "labels",
                                "code": "invalid",
                                "message": f"Label {item} does not exist",
                            }],
                        }
                    resolved.append(label.name)
                issue.labels = resolved
            if "state" in data and data["state"] is not None:
                state = data["state"]
                if state not in ("open", "closed"):
                    return 422, {
                        "message": "Validation Failed",
                        "errors": [{"resource": "Issue", "field": "state", "code": "invalid"}],
                    }
                if state != issue.state:
                    if state == "closed":
                        issue.closed_at = self.now
                        repo.open_count = max(0, repo.open_count - 1)
                    else:
                        issue.closed_at = None
                        repo.open_count += 1
                    issue.state = state
            issue.updated_at = self.now
            issue.cache = None
        return 200, self.issue_public(repo, issue)

    def issue_public(self, repo: Repo, issue: Issue) -> dict:
        login = self.login
        full = f"{login}/{repo.name}"
        labels = []
        for name in issue.labels:
            label = repo.label_named(name)
            if label is not None:
                labels.append(self.label_public(label))
        return {
            "id": issue.id,
            "node_id": node_id("Issue", issue.id),
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "locked": False,
            "user": self.user_public(),
            "labels": labels,
            "assignee": None,
            "assignees": [],
            "milestone": None,
            "comments": 0,
            "created_at": issue.created_at,
            "updated_at": issue.updated_at,
            "closed_at": issue.closed_at,
            "author_association": "OWNER",
            "html_url": f"/{full}/issues/{issue.number}",
            "url": f"/api/v3/repos/{full}/issues/{issue.number}",
            "repository_url": f"/api/v3/repos/{full}",
            "labels_url": f"/api/v3/repos/{full}/issues/{issue.number}/labels{{/name}}",
            "comments_url": f"/api/v3/repos/{full}/issues/{issue.number}/comments",
            "events_url": f"/api/v3/repos/{full}/issues/{issue.number}/events",
            "pull_request": None,
        }

    def issue_bytes(self, repo: Repo, number: int) -> bytes | None:
        """Cached JSON body for the hot GET /issues/{number} path."""
        import json

        with repo.lock:
            issue = repo.issues.get(number)
            if issue is None:
                return None
            if issue.cache is None:
                issue.cache = json.dumps(self.issue_public(repo, issue), separators=(",", ":")).encode()
            return issue.cache

    def list_issues(self, repo: Repo, state: str) -> list[Issue]:
        with repo.lock:
            numbers = list(repo.issue_order)
            issues = [repo.issues[n] for n in numbers if n in repo.issues]
        if state == "all":
            chosen = issues
        elif state == "closed":
            chosen = [issue for issue in issues if issue.state == "closed"]
        else:
            chosen = [issue for issue in issues if issue.state == "open"]
        # created_at ties break by number descending, and creation order is ascending.
        chosen.sort(key=lambda issue: (issue.created_at, issue.number), reverse=True)
        return chosen

    def create_pull(self, repo: Repo, data: dict) -> tuple[int, dict]:
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "PullRequest", "field": "title", "code": "missing"}],
            }
        head = data.get("head")
        base = data.get("base")
        if not isinstance(head, str) or not head or not isinstance(base, str) or not base:
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "PullRequest", "field": "head", "code": "missing"}],
            }
        head_branch = head.split(":", 1)[1] if ":" in head else head
        base_branch = base.split(":", 1)[1] if ":" in base else base
        if ":" in head and head.split(":", 1)[0].lower() != self.login.lower():
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "PullRequest", "field": "head", "code": "invalid"}],
            }
        git = repo.git
        if git is None or git.branch_sha(head_branch) is None or git.branch_sha(base_branch) is None:
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "PullRequest", "field": "head", "code": "invalid"}],
            }
        if head_branch == base_branch:
            return 422, {
                "message": "Validation Failed",
                "errors": [{"resource": "PullRequest", "field": "head", "code": "invalid"}],
            }
        body = data.get("body")
        if body is not None and not isinstance(body, str):
            body = str(body)
        with repo.lock:
            number = repo.next_number
            repo.next_number += 1
            pull = Pull(self.alloc_obj(), number, title.strip(), body, head_branch, base_branch, self.now)
            repo.pulls[number] = pull
            repo.open_count += 1
        return 201, self.pull_public(repo, pull)

    def pull_public(self, repo: Repo, pull: Pull) -> dict:
        login = self.login
        full = f"{login}/{repo.name}"
        repo_obj = self.repo_public(repo)
        git = repo.git
        head_sha = git.branch_sha(pull.head) if git else None
        base_sha = git.branch_sha(pull.base) if git else None
        if git and head_sha and base_sha and pull.state == "open":
            mergeable = git.mergeable(base_sha, head_sha)
        else:
            mergeable = False
        files = git.diff(base_sha, head_sha) if git and base_sha and head_sha else []
        additions = sum(item["additions"] for item in files)
        deletions = sum(item["deletions"] for item in files)
        commits = git.ahead(base_sha, head_sha) if git and base_sha and head_sha else 0
        return {
            "id": pull.id,
            "node_id": node_id("PullRequest", pull.id),
            "number": pull.number,
            "title": pull.title,
            "body": pull.body,
            "state": pull.state,
            "merged": pull.merged,
            "mergeable": mergeable,
            "mergeable_state": "clean" if mergeable else "dirty",
            "merge_commit_sha": pull.merge_sha,
            "draft": False,
            "user": self.user_public(),
            "head": {
                "label": f"{login}:{pull.head}",
                "ref": pull.head,
                "sha": head_sha,
                "repo": repo_obj,
                "user": self.user_public(),
            },
            "base": {
                "label": f"{login}:{pull.base}",
                "ref": pull.base,
                "sha": base_sha,
                "repo": repo_obj,
                "user": self.user_public(),
            },
            "commits": commits,
            "additions": additions,
            "deletions": deletions,
            "changed_files": len(files),
            "html_url": f"/{full}/pull/{pull.number}",
            "url": f"/api/v3/repos/{full}/pulls/{pull.number}",
            "issue_url": f"/api/v3/repos/{full}/issues/{pull.number}",
            "diff_url": f"/{full}/pull/{pull.number}.diff",
            "created_at": pull.created_at,
            "updated_at": pull.updated_at,
            "closed_at": pull.closed_at,
            "merged_at": pull.closed_at if pull.merged else None,
        }

    def merge_pull(self, repo: Repo, pull: Pull, data: dict) -> tuple[int, dict]:
        if pull.state != "open" or pull.merged:
            return 405, {"message": "Pull Request is not mergeable"}
        git = repo.git
        if git is None:
            return 405, {"message": "Merge conflict"}
        head_sha = git.branch_sha(pull.head)
        expected = data.get("sha")
        if isinstance(expected, str) and expected and head_sha != expected:
            return 409, {"message": "Head branch was modified"}
        title = data.get("commit_title") if isinstance(data.get("commit_title"), str) else pull.title
        body = data.get("commit_message") if isinstance(data.get("commit_message"), str) else (pull.body or "")
        message = f"{title} (#{pull.number})\n"
        if body:
            message += "\n" + body.strip() + "\n"
        sha = git.squash(
            pull.base,
            pull.head,
            message,
            self.login,
            f"{self.login}@users.noreply.github.com",
            self.now,
        )
        if sha is None:
            return 405, {"message": "Merge conflict"}
        with repo.lock:
            pull.state = "closed"
            pull.merged = True
            pull.merge_sha = sha
            pull.closed_at = self.now
            pull.updated_at = self.now
            repo.open_count = max(0, repo.open_count - 1)
            repo.updated_at = self.now
        return 200, {"sha": sha, "merged": True, "message": "Pull Request successfully merged"}

    def pull_as_issue(self, repo: Repo, pull: Pull) -> dict:
        full = f"{self.login}/{repo.name}"
        return {
            "id": pull.id,
            "node_id": node_id("PullRequest", pull.id),
            "number": pull.number,
            "title": pull.title,
            "body": pull.body,
            "state": pull.state,
            "locked": False,
            "user": self.user_public(),
            "labels": [],
            "assignee": None,
            "assignees": [],
            "milestone": None,
            "comments": 0,
            "created_at": pull.created_at,
            "updated_at": pull.updated_at,
            "closed_at": pull.closed_at,
            "author_association": "OWNER",
            "html_url": f"/{full}/pull/{pull.number}",
            "url": f"/api/v3/repos/{full}/issues/{pull.number}",
            "repository_url": f"/api/v3/repos/{full}",
            "pull_request": {
                "url": f"/api/v3/repos/{full}/pulls/{pull.number}",
                "html_url": f"/{full}/pull/{pull.number}",
                "diff_url": f"/{full}/pull/{pull.number}.diff",
                "patch_url": f"/{full}/pull/{pull.number}.patch",
                "merged_at": pull.closed_at if pull.merged else None,
            },
        }


_STORE: Store | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = Store()
    return _STORE


def reset_store() -> Store:
    global _STORE
    with _STORE_LOCK:
        _STORE = Store()
        return _STORE
