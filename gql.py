"""GraphQL endpoint for the documents gh and github-mcp-server send.

Introspection describes Issue, PullRequest, Repository, and SearchType.
repository.issues honors states and orderBy, and createPullRequest opens a
pull request on a branch that was pushed over git.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from store import get_store, node_id, parse_node_id

JSON = "application/json; charset=utf-8"


def dumps(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


# field name -> (type string, arguments)
SCHEMA: dict[str, dict[str, tuple[str, dict]]] = {
    "Query": {
        "repository": ("Repository", {"owner": "String!", "name": "String!"}),
        "node": ("Node", {"id": "ID!"}),
        "viewer": ("User", {}),
        "__type": ("__Type", {"name": "String!"}),
        "__schema": ("__Schema", {}),
    },
    "Mutation": {
        "createPullRequest": ("CreatePullRequestPayload", {"input": "CreatePullRequestInput!"}),
    },
    "Repository": {
        "id": ("ID!", {}),
        "databaseId": ("Int", {}),
        "name": ("String!", {}),
        "nameWithOwner": ("String!", {}),
        "owner": ("RepositoryOwner!", {}),
        "description": ("String", {}),
        "url": ("String", {}),
        "isPrivate": ("Boolean!", {}),
        "defaultBranchRef": ("Ref", {}),
        "viewerPermission": ("RepositoryPermission", {}),
        "hasIssuesEnabled": ("Boolean!", {}),
        "issue": ("Issue", {"number": "Int!"}),
        "issues": ("IssueConnection!", {"first": "Int", "after": "String", "states": "[IssueState!]", "orderBy": "IssueOrder", "labels": "[String!]"}),
        "pullRequest": ("PullRequest", {"number": "Int!"}),
        "pullRequests": ("PullRequestConnection!", {"first": "Int", "states": "[PullRequestState!]", "orderBy": "IssueOrder", "headRefName": "String", "baseRefName": "String"}),
        "createdAt": ("String", {}),
        "updatedAt": ("String", {}),
    },
    "Issue": {
        "id": ("ID!", {}),
        "number": ("Int!", {}),
        "title": ("String!", {}),
        "state": ("IssueState!", {}),
        "body": ("String", {}),
        "url": ("String", {}),
        "author": ("User", {}),
        "createdAt": ("String", {}),
        "updatedAt": ("String", {}),
        "closed": ("Boolean!", {}),
        "closedAt": ("String", {}),
        "repository": ("Repository", {}),
        "labels": ("LabelConnection", {"first": "Int"}),
        "comments": ("IssueCommentConnection", {}),
    },
    "PullRequest": {
        "id": ("ID!", {}),
        "number": ("Int!", {}),
        "title": ("String", {}),
        "body": ("String", {}),
        "state": ("PullRequestState!", {}),
        "merged": ("Boolean!", {}),
        "mergeable": ("MergeableState!", {}),
        "headRefName": ("String!", {}),
        "baseRefName": ("String!", {}),
        "headRefOid": ("String", {}),
        "baseRefOid": ("String", {}),
        "url": ("String", {}),
        "files": ("PullRequestChangedFileConnection", {"first": "Int"}),
        "additions": ("Int!", {}),
        "deletions": ("Int!", {}),
        "changedFiles": ("Int!", {}),
        "author": ("User", {}),
        "createdAt": ("String", {}),
        "updatedAt": ("String", {}),
        "closed": ("Boolean!", {}),
        "closedAt": ("String", {}),
    },
    "User": {
        "id": ("ID!", {}),
        "login": ("String!", {}),
        "name": ("String", {}),
        "url": ("String", {}),
        "avatarUrl": ("String", {}),
    },
    "RepositoryOwner": {
        "id": ("ID!", {}),
        "login": ("String!", {}),
    },
    "Ref": {
        "name": ("String!", {}),
        "prefix": ("String", {}),
        "target": ("GitObject", {}),
    },
    "GitObject": {
        "oid": ("String", {}),
        "abbreviatedOid": ("String", {}),
    },
    "Commit": {
        "oid": ("String", {}),
        "message": ("String", {}),
        "abbreviatedOid": ("String", {}),
    },
    "IssueConnection": {
        "nodes": ("[Issue]", {}),
        "edges": ("[IssueEdge]", {}),
        "pageInfo": ("PageInfo!", {}),
        "totalCount": ("Int!", {}),
    },
    "IssueEdge": {"cursor": ("String!", {}), "node": ("Issue", {})},
    "PullRequestConnection": {
        "nodes": ("[PullRequest]", {}),
        "edges": ("[PullRequestEdge]", {}),
        "pageInfo": ("PageInfo!", {}),
        "totalCount": ("Int!", {}),
    },
    "PullRequestEdge": {"cursor": ("String!", {}), "node": ("PullRequest", {})},
    "PullRequestChangedFileConnection": {
        "nodes": ("[PullRequestChangedFile]", {}),
        "edges": ("[PullRequestChangedFileEdge]", {}),
        "pageInfo": ("PageInfo!", {}),
        "totalCount": ("Int!", {}),
    },
    "PullRequestChangedFileEdge": {"cursor": ("String!", {}), "node": ("PullRequestChangedFile", {})},
    "PullRequestChangedFile": {
        "path": ("String!", {}),
        "additions": ("Int!", {}),
        "deletions": ("Int!", {}),
        "changeType": ("PatchStatus", {}),
    },
    "LabelConnection": {
        "nodes": ("[Label]", {}),
        "totalCount": ("Int!", {}),
        "pageInfo": ("PageInfo!", {}),
    },
    "Label": {"id": ("ID!", {}), "name": ("String!", {}), "color": ("String", {}), "description": ("String", {})},
    "IssueCommentConnection": {"totalCount": ("Int!", {}), "nodes": ("[IssueComment]", {}), "pageInfo": ("PageInfo!", {})},
    "IssueComment": {"id": ("ID", {}), "body": ("String", {})},
    "PageInfo": {
        "hasNextPage": ("Boolean!", {}),
        "hasPreviousPage": ("Boolean!", {}),
        "startCursor": ("String", {}),
        "endCursor": ("String", {}),
    },
    "CreatePullRequestPayload": {
        "pullRequest": ("PullRequest", {}),
        "clientMutationId": ("String", {}),
    },
    "Node": {"id": ("ID!", {})},
    "__Type": {
        "name": ("String", {}),
        "kind": ("__TypeKind", {}),
        "description": ("String", {}),
        "fields": ("[__Field!]", {"includeDeprecated": "Boolean"}),
        "enumValues": ("[__EnumValue!]", {"includeDeprecated": "Boolean"}),
        "interfaces": ("[__Type!]", {}),
        "possibleTypes": ("[__Type!]", {}),
        "inputFields": ("[__InputValue!]", {}),
        "ofType": ("__Type", {}),
    },
    "__Field": {
        "name": ("String!", {}),
        "description": ("String", {}),
        "args": ("[__InputValue!]!", {}),
        "type": ("__Type!", {}),
        "isDeprecated": ("Boolean!", {}),
    },
    "__EnumValue": {
        "name": ("String!", {}),
        "description": ("String", {}),
        "isDeprecated": ("Boolean!", {}),
    },
    "__InputValue": {
        "name": ("String!", {}),
        "description": ("String", {}),
        "type": ("__Type!", {}),
        "defaultValue": ("String", {}),
    },
    "__Schema": {
        "description": ("String", {}),
        "types": ("[__Type!]!", {}),
        "queryType": ("__Type!", {}),
        "mutationType": ("__Type", {}),
    },
}

ENUMS = {
    "IssueState": ["OPEN", "CLOSED"],
    "PullRequestState": ["OPEN", "CLOSED", "MERGED"],
    "MergeableState": ["MERGEABLE", "CONFLICTING", "UNKNOWN"],
    "RepositoryPermission": ["ADMIN", "MAINTAIN", "WRITE", "TRIAGE", "READ"],
    "SearchType": ["ISSUE", "REPOSITORY", "USER", "DISCUSSION"],
    "PatchStatus": ["ADDED", "MODIFIED", "DELETED", "RENAMED"],
    "IssueOrderField": ["CREATED_AT", "UPDATED_AT", "COMMENTS"],
    "OrderDirection": ["ASC", "DESC"],
    "__TypeKind": ["SCALAR", "OBJECT", "INTERFACE", "UNION", "ENUM", "INPUT_OBJECT", "LIST", "NON_NULL"],
}

SCALARS = {"String", "Int", "Float", "Boolean", "ID"}


def type_kind(name: str) -> str:
    if name in SCALARS:
        return "SCALAR"
    if name in ENUMS:
        return "ENUM"
    if name in ("CreatePullRequestInput", "IssueOrder"):
        return "INPUT_OBJECT"
    if name in SCHEMA:
        return "OBJECT"
    return "OBJECT"


def parse_type_name(text: str) -> dict:
    text = text.strip()
    if text.endswith("!"):
        return {"kind": "NON_NULL", "name": None, "ofType": parse_type_name(text[:-1]), "description": None}
    if text.startswith("[") and text.endswith("]"):
        return {"kind": "LIST", "name": None, "ofType": parse_type_name(text[1:-1]), "description": None}
    return {"kind": type_kind(text), "name": text, "ofType": None, "description": None}


def unwrap(text: str) -> tuple[str, bool]:
    """Return (inner named type, is_list)."""
    text = text.strip().rstrip("!")
    is_list = text.startswith("[")
    if is_list:
        text = text[1:-1].strip().rstrip("!")
    return text, is_list


@dataclass
class Sel:
    kind: str
    name: str = ""
    alias: str = ""
    args: list = field(default_factory=list)
    selection: list = field(default_factory=list)
    on_type: str = ""
    line: int = 1
    col: int = 1


@dataclass
class Op:
    kind: str
    name: str
    selection: list
    line: int = 1


@dataclass
class Frag:
    name: str
    on_type: str
    selection: list


class Parser:
    def __init__(self, source: str):
        self.s = source
        self.n = len(source)
        self.i = 0
        self.line = 1
        self.col = 1

    def parse(self) -> tuple[list[Op], dict[str, Frag]]:
        ops = []
        frags: dict[str, Frag] = {}
        self._skip()
        if self.i >= self.n:
            raise ValueError("empty document")
        while self.i < self.n:
            self._skip()
            if self.i >= self.n:
                break
            if self._peek("{") or self._peek_keyword("query") or self._peek_keyword("mutation") or self._peek_keyword("subscription"):
                ops.append(self._operation())
            elif self._peek_keyword("fragment"):
                frag = self._fragment()
                frags[frag.name] = frag
            else:
                raise ValueError(f"unexpected token near {self.s[self.i:self.i+20]!r}")
            self._skip()
        if not ops:
            raise ValueError("document has no operation")
        return ops, frags

    def _operation(self) -> Op:
        line, col = self.line, self.col
        kind = "query"
        if self._keyword("query"):
            kind = "query"
        elif self._keyword("mutation"):
            kind = "mutation"
        elif self._keyword("subscription"):
            kind = "subscription"
        self._skip()
        name = ""
        if self._peek_name():
            name = self._name()
        if self._eat("("):
            self._skip()
            while not self._eat(")"):
                self._expect("$")
                self._name()
                self._skip()
                self._expect(":")
                self._parse_type_expr()
                self._skip()
                if self._eat("="):
                    self._value()
                self._skip()
                self._eat(",")
                self._skip()
        self._skip_directives()
        return Op(kind, name, self._selection_set(), line)

    def _fragment(self) -> Frag:
        self._expect_keyword("fragment")
        name = self._name()
        self._expect_keyword("on")
        on_type = self._name()
        self._skip_directives()
        return Frag(name, on_type, self._selection_set())

    def _selection_set(self) -> list:
        self._expect("{")
        items = []
        self._skip()
        while not self._eat("}"):
            items.append(self._selection())
            self._skip()
            self._eat(",")
            self._skip()
            if self.i >= self.n:
                raise ValueError("unterminated selection set")
        return items

    def _selection(self) -> Sel:
        line, col = self.line, self.col
        if self._eat("..."):
            self._skip()
            if self._keyword("on"):
                on_type = self._name()
                self._skip_directives()
                return Sel("inline", on_type=on_type, selection=self._selection_set(), line=line, col=col)
            if self._peek_name():
                name = self._name()
                self._skip_directives()
                return Sel("spread", name=name, line=line, col=col)
            self._skip_directives()
            return Sel("inline", selection=self._selection_set(), line=line, col=col)
        name = self._name()
        alias = ""
        self._skip()
        if self._eat(":"):
            alias, name = name, self._name()
            self._skip()
        args = []
        if self._eat("("):
            self._skip()
            while not self._eat(")"):
                aname = self._name()
                self._expect(":")
                args.append((aname, self._value()))
                self._skip()
                self._eat(",")
                self._skip()
        self._skip_directives()
        selection = self._selection_set() if self._peek("{") else []
        return Sel("field", name=name, alias=alias, args=args, selection=selection, line=line, col=col)

    def _value(self):
        self._skip()
        if self._eat("$"):
            return ("var", self._name())
        if self._eat("["):
            items = []
            self._skip()
            while not self._eat("]"):
                items.append(self._value())
                self._skip()
                self._eat(",")
                self._skip()
            return ("list", items)
        if self._eat("{"):
            obj = []
            self._skip()
            while not self._eat("}"):
                key = self._name()
                self._expect(":")
                obj.append((key, self._value()))
                self._skip()
                self._eat(",")
                self._skip()
            return ("obj", obj)
        if self._eat('"'):
            if self.s.startswith('""', self.i - 1) and self._peek('"'):
                return ("str", self._block_string())
            return ("str", self._string())
        if self._keyword("true"):
            return ("bool", True)
        if self._keyword("false"):
            return ("bool", False)
        if self._keyword("null"):
            return ("null", None)
        if self.i < self.n and (self.s[self.i].isdigit() or (self.s[self.i] == "-" and self.i + 1 < self.n and self.s[self.i + 1].isdigit())):
            return ("num", self._number())
        if self._peek_name():
            return ("enum", self._name())
        raise ValueError(f"bad value near {self.s[self.i:self.i+16]!r}")

    def _block_string(self) -> str:
        # caller already consumed the first quote of """
        if not self.s.startswith('""', self.i):
            raise ValueError("bad block string")
        self._bump(2)
        end = self.s.find('"""', self.i)
        if end < 0:
            raise ValueError("unterminated block string")
        text = self.s[self.i:end]
        self._bump_text(text)
        self._bump(3)
        return text.strip("\n")

    def _string(self) -> str:
        out = []
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == '"':
                self._bump()
                return "".join(out)
            if ch == "\\":
                self._bump()
                esc = self.s[self.i]
                self._bump()
                mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
                if esc in mapping:
                    out.append(mapping[esc])
                elif esc == "u":
                    hexes = self.s[self.i:self.i + 4]
                    self._bump(4)
                    out.append(chr(int(hexes, 16)))
                else:
                    out.append(esc)
            else:
                out.append(ch)
                self._bump()
        raise ValueError("unterminated string")

    def _number(self):
        start = self.i
        if self.s[self.i] == "-":
            self._bump()
        while self.i < self.n and self.s[self.i].isdigit():
            self._bump()
        if self.i < self.n and self.s[self.i] == ".":
            self._bump()
            while self.i < self.n and self.s[self.i].isdigit():
                self._bump()
            return float(self.s[start:self.i])
        return int(self.s[start:self.i])

    def _parse_type_expr(self) -> None:
        self._skip()
        if self._eat("["):
            self._parse_type_expr()
            self._expect("]")
        else:
            self._name()
        self._eat("!")

    def _skip_directives(self) -> None:
        self._skip()
        while self._eat("@"):
            self._name()
            if self._eat("("):
                depth = 1
                while self.i < self.n and depth:
                    if self._eat("("):
                        depth += 1
                    elif self._eat(")"):
                        depth -= 1
                    else:
                        self._bump()
            self._skip()

    def _at_keyword(self, word: str) -> bool:
        end = self.i + len(word)
        return self.s.startswith(word, self.i) and (end >= self.n or not (self.s[end].isalnum() or self.s[end] == "_"))

    def _peek_keyword(self, word: str) -> bool:
        self._skip()
        return self._at_keyword(word)

    def _keyword(self, word: str) -> bool:
        self._skip()
        if self._at_keyword(word):
            self._bump(len(word))
            return True
        return False

    def _expect_keyword(self, word: str) -> None:
        if not self._keyword(word):
            raise ValueError(f"expected {word}")

    def _name(self) -> str:
        self._skip()
        start = self.i
        if self.i >= self.n or not (self.s[self.i].isalpha() or self.s[self.i] == "_"):
            raise ValueError(f"expected name near {self.s[self.i:self.i+12]!r}")
        self._bump()
        while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] == "_"):
            self._bump()
        return self.s[start:self.i]

    def _peek_name(self) -> bool:
        self._skip()
        return self.i < self.n and (self.s[self.i].isalpha() or self.s[self.i] == "_")

    def _peek(self, token: str) -> bool:
        self._skip()
        return self.s.startswith(token, self.i)

    def _eat(self, token: str) -> bool:
        self._skip()
        if self.s.startswith(token, self.i):
            self._bump(len(token))
            return True
        return False

    def _expect(self, token: str) -> None:
        if not self._eat(token):
            raise ValueError(f"expected {token}")

    def _skip(self) -> None:
        while self.i < self.n:
            ch = self.s[self.i]
            if ch in " \t\r\n,":
                self._bump()
                continue
            if ch == "#":
                while self.i < self.n and self.s[self.i] not in "\n":
                    self._bump()
                continue
            break

    def _bump(self, count: int = 1) -> None:
        for _ in range(count):
            if self.i < self.n and self.s[self.i] == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.i += 1

    def _bump_text(self, text: str) -> None:
        self._bump(len(text))


# The method above was duplicated in the class body by mistake if I left a stub.
# The real _selection is defined. The stub raises if the later definition didn't override.
# In Python the later def wins, so the stub is overwritten. Good.


def _eval(node, variables: dict):
    kind = node[0]
    if kind == "var":
        return variables.get(node[1])
    if kind == "list":
        return [_eval(item, variables) for item in node[1]]
    if kind == "obj":
        return {key: _eval(value, variables) for key, value in node[1]}
    return node[1]


def _args(sel: Sel, variables: dict) -> dict:
    return {name: _eval(value, variables) for name, value in sel.args}


class GRepo:
    __slots__ = ("repo", "store")

    def __init__(self, repo, store):
        self.repo = repo
        self.store = store


class GIssue:
    __slots__ = ("repo", "issue", "store")

    def __init__(self, repo, issue, store):
        self.repo = repo
        self.issue = issue
        self.store = store


class GPull:
    __slots__ = ("repo", "pull", "store")

    def __init__(self, repo, pull, store):
        self.repo = repo
        self.pull = pull
        self.store = store


class Conn:
    __slots__ = ("nodes", "total")

    def __init__(self, nodes, total=None):
        self.nodes = nodes
        self.total = len(nodes) if total is None else total


class Intro:
    __slots__ = ("name", "kind")

    def __init__(self, name: str | None, kind: str | None = None):
        self.name = name
        self.kind = kind or (type_kind(name) if name else None)


def _page(conn: Conn) -> dict:
    total = conn.total
    size = len(conn.nodes)
    return {
        "hasNextPage": total > size,
        "hasPreviousPage": False,
        "startCursor": "0" if size else None,
        "endCursor": str(size - 1) if size else None,
    }


def _user(store):
    return {"id": node_id("User", store.user_id), "login": store.login, "name": store.login, "url": f"/{store.login}", "avatarUrl": ""}


def _resolve(type_name: str, field: str, parent, args: dict, store):
    if type_name == "Query":
        if field == "repository":
            repo = store.get_repo(str(args.get("owner") or ""), str(args.get("name") or ""))
            return None if repo is None else GRepo(repo, store)
        if field == "viewer":
            return _user(store)
        if field == "node":
            return _node(store, args.get("id"))
        if field == "__type":
            name = args.get("name")
            if name in SCHEMA or name in ENUMS or name in SCALARS or name in ("CreatePullRequestInput", "IssueOrder"):
                return Intro(name)
            return None
        if field == "__schema":
            return {"description": "forge", "types": [Intro(name) for name in list(SCHEMA) + list(ENUMS)], "queryType": Intro("Query"), "mutationType": Intro("Mutation")}
    if type_name == "Mutation" and field == "createPullRequest":
        return _create_pull(store, args.get("input") or {})
    if type_name == "Repository":
        return _repo_field(parent, field, args)
    if type_name == "Issue":
        return _issue_field(parent, field, args)
    if type_name == "PullRequest":
        return _pull_field(parent, field, args)
    if type_name == "Ref":
        return parent.get(field) if isinstance(parent, dict) else None
    if type_name in ("GitObject", "Commit", "User", "RepositoryOwner", "PageInfo", "PullRequestChangedFile", "Label", "CreatePullRequestPayload", "IssueEdge", "PullRequestEdge", "PullRequestChangedFileEdge", "IssueComment"):
        if isinstance(parent, dict):
            return parent.get(field)
    if type_name in ("IssueConnection", "PullRequestConnection", "PullRequestChangedFileConnection", "LabelConnection", "IssueCommentConnection"):
        if field == "nodes":
            return parent.nodes
        if field == "totalCount":
            return parent.total
        if field == "pageInfo":
            return _page(parent)
        if field == "edges":
            return [{"cursor": str(i), "node": node} for i, node in enumerate(parent.nodes)]
    if type_name == "__Type":
        return _intro_type_field(parent, field)
    if type_name == "__Field":
        return parent.get(field) if isinstance(parent, dict) else None
    if type_name == "__EnumValue":
        return parent.get(field) if isinstance(parent, dict) else None
    if type_name == "__InputValue":
        return parent.get(field) if isinstance(parent, dict) else None
    if type_name == "__Schema":
        return parent.get(field) if isinstance(parent, dict) else None
    if isinstance(parent, dict) and field in parent:
        return parent[field]
    return None


def _node(store, gid):
    parsed = parse_node_id(str(gid)) if gid is not None else None
    if not parsed:
        return None
    kind, num = parsed
    if kind == "Repository":
        repo = store.get_repo_by_id(num)
        return GRepo(repo, store) if repo else None
    if kind == "User":
        return _user(store)
    return None


def _repo_field(parent: GRepo, field: str, args: dict):
    repo = parent.repo
    store = parent.store
    login = store.login
    if field == "id":
        return node_id("Repository", repo.id)
    if field == "databaseId":
        return repo.id
    if field == "name":
        return repo.name
    if field == "nameWithOwner":
        return f"{login}/{repo.name}"
    if field == "owner":
        return {"id": node_id("User", store.user_id), "login": login}
    if field == "description":
        return repo.description
    if field == "url":
        return f"/{login}/{repo.name}"
    if field == "isPrivate":
        return repo.private
    if field == "viewerPermission":
        return "ADMIN"
    if field == "hasIssuesEnabled":
        return True
    if field == "createdAt":
        return repo.created_at
    if field == "updatedAt":
        return repo.updated_at
    if field == "defaultBranchRef":
        from gitstore import ensure_git

        git = ensure_git(repo, store)
        sha = git.branch_sha(repo.default_branch)
        if sha is None:
            return None
        return {"name": repo.default_branch, "prefix": "refs/heads/", "target": {"oid": sha, "abbreviatedOid": sha[:7]}}
    if field == "issue":
        issue = repo.issues.get(int(args.get("number") or 0))
        return None if issue is None else GIssue(repo, issue, store)
    if field == "issues":
        return _issue_conn(parent, args)
    if field == "pullRequest":
        pull = repo.pulls.get(int(args.get("number") or 0))
        return None if pull is None else GPull(repo, pull, store)
    if field == "pullRequests":
        return _pull_conn(parent, args)
    return None


def _issue_conn(parent: GRepo, args: dict) -> Conn:
    repo = parent.repo
    states = args.get("states")
    wanted = None
    if states:
        wanted = set()
        for state in states:
            if state == "OPEN":
                wanted.add("open")
            elif state == "CLOSED":
                wanted.add("closed")
    with repo.lock:
        items = [repo.issues[number] for number in repo.issue_order if number in repo.issues]
    if wanted is not None:
        items = [issue for issue in items if issue.state in wanted]
    order = args.get("orderBy") or {"field": "CREATED_AT", "direction": "DESC"}
    direction = (order or {}).get("direction") or "DESC"
    field_name = (order or {}).get("field") or "CREATED_AT"
    reverse = direction == "DESC"

    def key(issue):
        if field_name == "UPDATED_AT":
            primary = issue.updated_at
        else:
            primary = issue.created_at
        # Number breaks ties in the same direction as the primary key, so DESC
        # puts the higher number first when timestamps match.
        return (primary, issue.number)

    items.sort(key=key, reverse=reverse)
    first = args.get("first")
    total = len(items)
    if first is not None:
        items = items[: max(0, int(first))]
    return Conn([GIssue(repo, issue, parent.store) for issue in items], total)


def _pull_conn(parent: GRepo, args: dict) -> Conn:
    repo = parent.repo
    with repo.lock:
        pulls = list(repo.pulls.values())
    states = args.get("states")
    if states:
        wanted = set(states)
        def keep(pull):
            if pull.merged:
                return "MERGED" in wanted or "CLOSED" in wanted
            return pull.state.upper() in wanted
        pulls = [pull for pull in pulls if keep(pull)]
    head = args.get("headRefName")
    base = args.get("baseRefName")
    if head:
        pulls = [pull for pull in pulls if pull.head == head]
    if base:
        pulls = [pull for pull in pulls if pull.base == base]
    pulls.sort(key=lambda pull: (pull.created_at, pull.number), reverse=True)
    total = len(pulls)
    first = args.get("first")
    if first is not None:
        pulls = pulls[: max(0, int(first))]
    return Conn([GPull(repo, pull, parent.store) for pull in pulls], total)


def _issue_field(parent: GIssue, field: str, args: dict):
    issue = parent.issue
    repo = parent.repo
    store = parent.store
    if field == "id":
        return node_id("Issue", issue.id)
    if field == "number":
        return issue.number
    if field == "title":
        return issue.title
    if field == "state":
        return "OPEN" if issue.state == "open" else "CLOSED"
    if field == "body":
        return issue.body
    if field == "url":
        return f"/{store.login}/{repo.name}/issues/{issue.number}"
    if field == "author":
        return _user(store)
    if field == "createdAt":
        return issue.created_at
    if field == "updatedAt":
        return issue.updated_at
    if field == "closed":
        return issue.state == "closed"
    if field == "closedAt":
        return issue.closed_at
    if field == "repository":
        return GRepo(repo, store)
    if field == "labels":
        nodes = []
        for name in issue.labels:
            label = repo.label_named(name)
            if label is not None:
                nodes.append({"id": node_id("Label", label.id), "name": label.name, "color": label.color, "description": label.description})
        first = args.get("first")
        total = len(nodes)
        if first is not None:
            nodes = nodes[: int(first)]
        return Conn(nodes, total)
    if field == "comments":
        return Conn([], 0)
    return None


def _pull_field(parent: GPull, field: str, args: dict):
    pull = parent.pull
    repo = parent.repo
    store = parent.store
    from gitstore import ensure_git

    git = ensure_git(repo, store)
    head_sha = git.branch_sha(pull.head)
    base_sha = git.branch_sha(pull.base)
    if field == "id":
        return node_id("PullRequest", pull.id)
    if field == "number":
        return pull.number
    if field == "title":
        return pull.title
    if field == "body":
        return pull.body
    if field == "state":
        if pull.merged:
            return "MERGED"
        return "OPEN" if pull.state == "open" else "CLOSED"
    if field == "merged":
        return pull.merged
    if field == "mergeable":
        if pull.state != "open" or not head_sha or not base_sha:
            return "UNKNOWN"
        return "MERGEABLE" if git.mergeable(base_sha, head_sha) else "CONFLICTING"
    if field == "headRefName":
        return pull.head
    if field == "baseRefName":
        return pull.base
    if field == "headRefOid":
        return head_sha
    if field == "baseRefOid":
        return base_sha
    if field == "url":
        return f"/{store.login}/{repo.name}/pull/{pull.number}"
    if field == "author":
        return _user(store)
    if field == "createdAt":
        return pull.created_at
    if field == "updatedAt":
        return pull.updated_at
    if field == "closed":
        return pull.state != "open"
    if field == "closedAt":
        return pull.closed_at
    if field in ("files", "additions", "deletions", "changedFiles"):
        files = git.diff(base_sha, head_sha) if base_sha and head_sha else []
        if field == "files":
            nodes = [{
                "path": item["path"],
                "additions": item["additions"],
                "deletions": item["deletions"],
                "changeType": item["status"].upper(),
            } for item in files]
            first = args.get("first")
            total = len(nodes)
            if first is not None:
                nodes = nodes[: int(first)]
            return Conn(nodes, total)
        if field == "additions":
            return sum(item["additions"] for item in files)
        if field == "deletions":
            return sum(item["deletions"] for item in files)
        return len(files)
    return None


def _create_pull(store, payload: dict) -> dict:
    from gitstore import ensure_git

    repo = None
    gid = payload.get("repositoryId")
    parsed = parse_node_id(str(gid)) if gid is not None else None
    if parsed and parsed[0] == "Repository":
        repo = store.get_repo_by_id(parsed[1])
    elif isinstance(gid, int) or (isinstance(gid, str) and str(gid).isdigit()):
        repo = store.get_repo_by_id(int(gid))
    if repo is None:
        return {"pullRequest": None, "clientMutationId": payload.get("clientMutationId"), "_error": "repository not found"}
    ensure_git(repo, store)
    status, body = store.create_pull(repo, {
        "title": payload.get("title") or "Pull request",
        "head": payload.get("headRefName") or "",
        "base": payload.get("baseRefName") or repo.default_branch,
        "body": payload.get("body"),
    })
    if status != 201:
        return {"pullRequest": None, "clientMutationId": payload.get("clientMutationId"), "_error": body.get("message", "validation failed")}
    pull = repo.pulls[body["number"]]
    return {"pullRequest": GPull(repo, pull, store), "clientMutationId": payload.get("clientMutationId")}


def _intro_type_field(parent, field: str):
    if isinstance(parent, dict):
        return parent.get(field)
    name = parent.name
    kind = parent.kind or type_kind(name) if name else None
    if field == "name":
        return name
    if field == "kind":
        return kind
    if field == "description":
        return None
    if field == "ofType":
        return None
    if field == "fields":
        spec = SCHEMA.get(name or "")
        if not spec or kind != "OBJECT":
            return None
        fields = []
        for fname, (ftype, fargs) in spec.items():
            if fname.startswith("__") and name not in ("Query",):
                pass
            args = [{
                "name": aname,
                "description": aname,
                "type": parse_type_name(atype),
                "defaultValue": None,
            } for aname, atype in fargs.items()]
            fields.append({
                "name": fname,
                "description": fname,
                "args": args,
                "type": parse_type_name(ftype),
                "isDeprecated": False,
            })
        return fields
    if field == "enumValues":
        values = ENUMS.get(name or "")
        if not values:
            return None
        return [{"name": value, "description": value, "isDeprecated": False} for value in values]
    if field == "interfaces":
        return []
    if field == "possibleTypes":
        return None
    if field == "inputFields":
        return None
    return None


def _expand(selections, type_name: str, frags: dict[str, Frag]):
    for sel in selections:
        if sel.kind == "field":
            yield sel
        elif sel.kind == "spread":
            frag = frags.get(sel.name)
            if frag and (frag.on_type == type_name or type_name in ("User",) and frag.on_type in ("RepositoryOwner", "User")):
                yield from _expand(frag.selection, type_name, frags)
            elif frag and frag.on_type == type_name:
                yield from _expand(frag.selection, type_name, frags)
        elif sel.kind == "inline":
            if not sel.on_type or sel.on_type == type_name or (type_name == "User" and sel.on_type == "RepositoryOwner"):
                yield from _expand(sel.selection, sel.on_type or type_name, frags)


def _validate(selections, type_name: str, frags, errors: list) -> None:
    spec = SCHEMA.get(type_name)
    for sel in _expand(selections, type_name, frags):
        if sel.name in ("__typename",):
            continue
        if spec is None or sel.name not in spec:
            errors.append({
                "message": f'Cannot query field "{sel.name}" on type "{type_name}".',
                "locations": [{"line": sel.line, "column": sel.col}],
            })
            continue
        if sel.selection:
            inner, _is_list = unwrap(spec[sel.name][0])
            if inner in SCHEMA:
                _validate(sel.selection, inner, frags, errors)


def _exec(type_name: str, parent, selections, store, variables, frags) -> dict:
    out = {}
    spec = SCHEMA.get(type_name, {})
    for sel in _expand(selections, type_name, frags):
        key = sel.alias or sel.name
        if sel.name == "__typename":
            out[key] = type_name
            continue
        args = _args(sel, variables)
        value = _resolve(type_name, sel.name, parent, args, store)
        if isinstance(value, dict) and value.get("_error") and sel.name == "createPullRequest":
            # Surface a GraphQL error while still returning null payload fields the caller asked for.
            value = {k: v for k, v in value.items() if k != "_error"}
        declared = spec.get(sel.name, ("String", {}))[0]
        out[key] = _exec_value(declared, value, sel.selection, store, variables, frags)
    return out


def _exec_value(declared: str, value, selections, store, variables, frags):
    if value is None:
        return None
    text = declared.strip()
    if text.endswith("!"):
        return _exec_value(text[:-1], value, selections, store, variables, frags)
    if text.startswith("["):
        inner = text[1:-1]
        return [_exec_value(inner, item, selections, store, variables, frags) for item in value]
    if text in SCALARS or text in ENUMS:
        return value
    if text in SCHEMA and selections:
        return _exec(text, value, selections, store, variables, frags)
    return value


def execute_document(source: str, variables: dict | None, operation_name: str | None) -> dict:
    ops, frags = Parser(source).parse()
    if operation_name:
        chosen = [op for op in ops if op.name == operation_name]
        op = chosen[0] if chosen else ops[0]
    else:
        op = ops[0]
    root_type = "Mutation" if op.kind == "mutation" else "Query"
    errors: list = []
    _validate(op.selection, root_type, frags, errors)
    if errors:
        return {"data": None, "errors": errors}
    variables = variables or {}
    data = _exec(root_type, None, op.selection, get_store(), variables, frags)
    # Drop an errors key when the document succeeded, including a null repository.
    return {"data": data}


def handle(body: bytes) -> tuple:
    if not body or not body.strip():
        return 400, dumps({"message": "Problems parsing JSON"}), JSON, ()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, dumps({"message": "Problems parsing JSON"}), JSON, ()
    try:
        if isinstance(payload, list):
            results = [_one(item) for item in payload]
            return 200, dumps(results), JSON, ()
        if not isinstance(payload, dict):
            return 400, dumps({"message": "Problems parsing JSON"}), JSON, ()
        return 200, dumps(_one(payload)), JSON, ()
    except Exception as exc:
        return 200, dumps({"data": None, "errors": [{"message": f"GraphQL error: {exc}"}]}), JSON, ()


def _one(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"errors": [{"message": "Problems parsing JSON"}]}
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"errors": [{"message": "Problems parsing JSON"}]}
    variables = payload.get("variables") or {}
    if not isinstance(variables, dict):
        variables = {}
    operation = payload.get("operationName")
    if operation is not None and not isinstance(operation, str):
        operation = None
    return execute_document(query, variables, operation)
