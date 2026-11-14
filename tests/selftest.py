"""Stdlib self-test for the forge, including a minimal git smart-HTTP client.

Starts the server on an ephemeral port and exercises REST, a real push and
shallow fetch, GraphQL, and MCP. Run from the repository root:

    python3 tests/selftest.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MAKEFUN_SHOP_TOKEN", "sekrit")
os.environ.setdefault("MAKEFUN_TRIAL_NOW", "2026-10-28T00:00:00Z")
os.environ.setdefault("FORGE_LOGIN", "octocat")

import main  # noqa: E402
import store  # noqa: E402

TOKEN = os.environ["MAKEFUN_SHOP_TOKEN"]
ZERO = "0" * 40


def sha1(*parts: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    for part in parts:
        digest.update(part)
    return digest.hexdigest()


def object_bytes(typ: str, content: bytes) -> bytes:
    return f"{typ} {len(content)}\0".encode() + content


def oid_of(typ: str, content: bytes) -> str:
    return sha1(object_bytes(typ, content))


def pkt(data: bytes | str) -> bytes:
    if isinstance(data, str):
        data = data.encode()
    return f"{len(data) + 4:04x}".encode() + data


def encode_tree(entries: list[tuple[str, str, str]]) -> bytes:
    def key(entry: tuple[str, str, str]) -> bytes:
        mode, name, _sha = entry
        raw = name.encode()
        return raw + b"/" if mode == "40000" else raw

    out = bytearray()
    for mode, name, sha in sorted(entries, key=key):
        out += f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha)
    return bytes(out)


def build_pack(objects: list[tuple[str, bytes]]) -> bytes:
    type_id = {"commit": 1, "tree": 2, "blob": 3, "tag": 4}
    parts = [b"PACK", (2).to_bytes(4, "big"), len(objects).to_bytes(4, "big")]
    for typ, content in objects:
        size = len(content)
        header = bytearray()
        byte = (type_id[typ] << 4) | (size & 0x0F)
        size >>= 4
        while size:
            header.append(byte | 0x80)
            byte = size & 0x7F
            size >>= 7
        header.append(byte)
        parts.append(bytes(header))
        parts.append(zlib.compress(content, 1))
    data = b"".join(parts)
    return data + hashlib.sha1(data, usedforsecurity=False).digest()


def read_pkts(buf: bytes):
    index = 0
    items = []
    while index + 4 <= len(buf):
        size = int(buf[index : index + 4], 16)
        if size == 0:
            items.append(b"")
            index += 4
            break
        if size == 1:
            items.append(None)
            index += 4
            continue
        if size < 4 or index + size > len(buf):
            break
        items.append(buf[index + 4 : index + size])
        index += size
    return items, buf[index:]


def unpack_sideband(buf: bytes) -> bytes:
    """Collect band-1 bytes until a flush packet."""
    out = bytearray()
    index = 0
    while index + 4 <= len(buf):
        size = int(buf[index : index + 4], 16)
        if size == 0:
            break
        if size < 4 or index + size > len(buf):
            break
        chunk = buf[index + 4 : index + size]
        index += size
        if chunk[:1] == b"\x01":
            out += chunk[1:]
        elif chunk[:1] == b"\x03":
            raise AssertionError(f"remote error: {chunk[1:]!r}")
    return bytes(out)


def parse_pack(data: bytes) -> dict[str, tuple[str, bytes]]:
    assert data[:4] == b"PACK", data[:20]
    count = int.from_bytes(data[8:12], "big")
    body = data[:-20]
    index = 12
    found = {}
    names = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}
    for _ in range(count):
        byte = body[index]
        index += 1
        typ = (byte >> 4) & 7
        size = byte & 0x0F
        shift = 4
        while byte & 0x80:
            byte = body[index]
            index += 1
            size |= (byte & 0x7F) << shift
            shift += 7
        assert typ in names, typ
        inflater = zlib.decompressobj()
        content = inflater.decompress(body[index:])
        assert inflater.eof
        index = len(body) - len(inflater.unused_data)
        assert len(content) == size
        found[oid_of(names[typ], content)] = (names[typ], content)
    return found


class Client:
    def __init__(self, base: str):
        self.base = base

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None, token: str | None = TOKEN):
        req = urllib.request.Request(self.base + path, data=body, method=method)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        if body is not None and not (headers and any(k.lower() == "content-type" for k in headers)):
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def json(self, method: str, path: str, payload=None, token: str | None = TOKEN):
        raw = None if payload is None else json.dumps(payload).encode()
        status, body, headers = self.request(method, path, raw, token=token)
        parsed = json.loads(body) if body else None
        return status, parsed, headers

    def git(self, method: str, path: str, body: bytes | None = None, protocol: str | None = None, content_type: str | None = None):
        headers = {}
        if protocol:
            headers["Git-Protocol"] = protocol
        if content_type:
            headers["Content-Type"] = content_type
        return self.request(method, path, body, headers)


def commit_object(tree: str, parents: list[str], message: str) -> bytes:
    lines = [f"tree {tree}\n"]
    lines.extend(f"parent {parent}\n" for parent in parents)
    ident = "Octocat <octocat@users.noreply.github.com> 1782604800 +0000"
    lines.append(f"author {ident}\ncommitter {ident}\n\n")
    text = "".join(lines) + (message if message.endswith("\n") else message + "\n")
    return text.encode()


def _ref_oid(body: bytes, refname: str) -> str:
    items, _rest = read_pkts(body)
    marker = f" {refname}".encode()
    for item in items:
        if item and item.rstrip(b"\n").endswith(marker):
            return item.split()[0].decode()
    raise SystemExit(f"FAIL missing ref {refname} in {body!r}")


def check(cond, message: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {message}")


def main_test() -> None:
    store.reset_store()
    server = main.build_server()
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    port = server.server_address[1]
    client = Client(f"http://127.0.0.1:{port}")
    login = "octocat"

    status, _body, _headers = client.json("GET", "/healthz", token=None)
    check(status == 200, "healthz")
    status, body, _headers = client.json("GET", "/api/v3/user", token=None)
    check(status == 401 and "message" in body, "missing token is 401")
    status, body, headers = client.json("GET", "/api/v3/")
    scopes = headers.get("X-Oauth-Scopes") or headers.get("X-OAuth-Scopes") or ""
    check(status == 200 and "repo" in scopes, "api root scopes")
    check(headers.get("X-Github-Request-Id") or headers.get("X-GitHub-Request-Id"), "request id")
    status, body, _headers = client.json("GET", "/api/v3/meta")
    check(status == 200 and body["installed_version"], "meta")
    status, body, _headers = client.request("POST", "/api/v3/user/repos", b"{")
    check(status == 400 and body.startswith(b"{"), "bad json")
    status, body, _headers = client.json("GET", "/no/such")
    check(status == 404 and body["message"], "unknown route json")

    status, repo, _headers = client.json("POST", "/api/v3/user/repos", {"name": "demo", "description": "d", "auto_init": True})
    check(status == 201 and repo["owner"]["login"] == login, "create repo")
    status, got, _headers = client.json("GET", f"/api/v3/repos/{login}/demo")
    check(status == 200 and got["description"] == "d", "get repo")
    status, got, _headers = client.json("PATCH", f"/api/v3/repos/{login}/demo", {"description": "new"})
    check(status == 200 and got["description"] == "new", "patch repo")
    status, _body, _headers = client.json("GET", "/api/v3/repos/someone/demo")
    check(status == 404, "other owner is 404")
    status, label, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/labels", {"name": "bug", "color": "ff0000"})
    check(status == 201 and label["name"] == "bug", "label")
    status, _body, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/issues", {"title": "nope", "labels": ["missing"]})
    check(status == 422, "unknown label")
    status, issue, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/issues", {"title": "one", "body": "b", "labels": ["bug"]})
    check(status == 201 and issue["state"] == "open", "create issue")
    status, again, _headers = client.json("GET", f"/api/v3/repos/{login}/demo/issues/{issue['number']}")
    check(status == 200 and again["id"] == issue["id"], "get issue id stable")
    status, closed, _headers = client.json("PATCH", f"/api/v3/repos/{login}/demo/issues/{issue['number']}", {"state": "closed"})
    check(closed["state"] == "closed", "close issue")
    status, opened, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/issues", {"title": "two"})
    status, third, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/issues", {"title": "three"})
    check(status == 201, "third issue")

    # A second page of repositories carries rel="next".
    for index in range(32):
        status, _body, _headers = client.json("POST", "/api/v3/user/repos", {"name": f"r{index}"})
        check(status == 201, f"repo r{index}")
    status, page, headers = client.json("GET", "/api/v3/user/repos")
    check(status == 200 and len(page) == 30 and 'rel="next"' in (headers.get("Link") or ""), "repo pagination")

    # Contents: create, stale sha, update, delete.
    encoded = base64.b64encode(b"hello\n").decode()
    status, created, _headers = client.json("PUT", f"/api/v3/repos/{login}/demo/contents/notes.txt", {"message": "add", "content": encoded})
    check(status == 201 and created["content"]["sha"] == oid_of("blob", b"hello\n"), "put file")
    status, _body, _headers = client.json("PUT", f"/api/v3/repos/{login}/demo/contents/notes.txt", {"message": "stale", "content": encoded, "sha": ZERO})
    check(status == 409, "stale sha")
    encoded2 = base64.b64encode(b"hello!\n").decode()
    status, updated, _headers = client.json(
        "PUT",
        f"/api/v3/repos/{login}/demo/contents/notes.txt",
        {"message": "edit", "content": encoded2, "sha": created["content"]["sha"]},
    )
    check(status == 200, "update file")
    status, _body, _headers = client.json(
        "DELETE",
        f"/api/v3/repos/{login}/demo/contents/notes.txt",
        {"message": "rm", "sha": updated["content"]["sha"]},
    )
    check(status == 200 and _body["commit"]["sha"], "delete file")
    status, _body, _headers = client.json("GET", f"/api/v3/repos/{login}/demo/contents/notes.txt")
    check(status == 404, "deleted file is 404")

    remote = f"/{login}/demo.git"
    status, advert, _headers = client.git("GET", remote + "/info/refs?service=git-upload-pack", protocol="version=2")
    check(status == 200 and advert.startswith(b"000eversion 2\n"), "v2 advertisement opens with version 2")
    status, recv_ad, _headers = client.git("GET", remote + "/info/refs?service=git-receive-pack")
    text = recv_ad.decode()
    check(status == 200 and "# service=git-receive-pack" in text, "receive preamble")
    check("report-status" in text and "side-band-64k" in text and "delete-refs" in text, "receive caps")
    status, _body, _headers = client.git("GET", f"/{login}/missing.git/info/refs?service=git-upload-pack", protocol="version=2")
    check(status == 404, "missing repo upload")
    status, _body, _headers = client.git("GET", f"/{login}/missing.git/info/refs?service=git-receive-pack")
    check(status == 404, "missing repo receive")

    blob = b"pushed\n" + b"x" * 1024
    blob_id = oid_of("blob", blob)
    tree = encode_tree([("100644", "pushed.txt", blob_id)])
    tree_id = oid_of("tree", tree)
    # First push creates main. Read the current tip so the update fast-forwards.
    ls_body = b"".join([
        pkt("command=ls-refs\n"),
        b"0001",
        pkt("symrefs\n"),
        pkt("peel\n"),
        pkt("ref-prefix refs/heads/\n"),
        pkt("ref-prefix HEAD\n"),
        b"0000",
    ])
    status, listed, _headers = client.git("POST", remote + "/git-upload-pack", ls_body, "version=2", "application/x-git-upload-pack-request")
    check(status == 200 and b"refs/heads/main" in listed, "ls-refs")
    tip = _ref_oid(listed, "refs/heads/main")
    # auto_init created main, so tip is set. Build a child commit.
    check(tip and tip != ZERO, f"main tip {tip}")
    commit = commit_object(tree_id, [tip], "push one\n")
    commit_id = oid_of("commit", commit)
    pack = build_pack([("blob", blob), ("tree", tree), ("commit", commit)])
    push = pkt(f"{tip} {commit_id} refs/heads/main\0 report-status side-band-64k\n") + b"0000" + pack
    status, report, _headers = client.git("POST", remote + "/git-receive-pack", push, content_type="application/x-git-receive-pack-request")
    check(status == 200 and b"ok refs/heads/main" in report and b"unpack ok" in report, f"push report {report!r}")

    fetch = b"".join([
        pkt("command=fetch\n"),
        b"0001",
        pkt("deepen 1\n"),
        pkt(f"want {commit_id}\n"),
        pkt("done\n"),
        pkt("thin-pack\n"),
        pkt("ofs-delta\n"),
        b"0000",
    ])
    status, fetched, _headers = client.git("POST", remote + "/git-upload-pack", fetch, "version=2", "application/x-git-upload-pack-request")
    check(status == 200 and b"shallow-info" in fetched and b"packfile" in fetched, "shallow fetch headers")
    _lines, rest = read_pkts(fetched)
    # read_pkts stops at the first flush inside shallow lines? shallow section ends with delim 0001, not flush.
    # Re-scan for packfile then sideband.
    marker = b"packfile\n"
    at = fetched.find(marker)
    check(at > 0, "packfile section")
    # The pkt length prefix sits 4 bytes before the word.
    after = fetched[at + len(marker) :]
    pack_bytes = unpack_sideband(after)
    objects = parse_pack(pack_bytes)
    check(blob_id in objects and objects[blob_id][1] == blob, "shallow pack contains the blob")
    check(commit_id in objects, "shallow pack contains the commit")
    # Depth 1 must not include the parent commit object.
    check(tip not in objects, "depth 1 omits the parent commit")

    # Seed topic at the current tip so the next push can refuse a non-fast-forward
    # while still accepting a fast-forward of main.
    seed = pkt(f"{ZERO} {commit_id} refs/heads/topic\0 report-status side-band-64k\n") + b"0000"
    status, report, _headers = client.git("POST", remote + "/git-receive-pack", seed, content_type="application/x-git-receive-pack-request")
    check(b"ok refs/heads/topic" in report, f"seed topic {report!r}")
    side_blob = b"side\n"
    side_blob_id = oid_of("blob", side_blob)
    side_tree = encode_tree([("100644", "side.txt", side_blob_id)])
    side_tree_id = oid_of("tree", side_tree)
    side_commit = commit_object(side_tree_id, [], "unrelated\n")
    side_id = oid_of("commit", side_commit)
    good_tree = encode_tree([("100644", "pushed.txt", blob_id), ("100644", "extra.txt", side_blob_id)])
    good_tree_id = oid_of("tree", good_tree)
    good_commit = commit_object(good_tree_id, [commit_id], "forward\n")
    good_id = oid_of("commit", good_commit)
    pack = build_pack([
        ("blob", side_blob),
        ("tree", side_tree),
        ("commit", side_commit),
        ("tree", good_tree),
        ("commit", good_commit),
    ])
    push = b"".join([
        pkt(f"{commit_id} {good_id} refs/heads/main\0 report-status side-band-64k\n"),
        pkt(f"{commit_id} {side_id} refs/heads/topic\n"),
        b"0000",
        pack,
    ])
    status, report, _headers = client.git("POST", remote + "/git-receive-pack", push, content_type="application/x-git-receive-pack-request")
    check(b"ok refs/heads/main" in report, f"ff ref applied {report!r}")
    check(b"ng refs/heads/topic non-fast-forward" in report, f"non-ff refused {report!r}")

    status, pull, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/pulls", {"title": "P", "head": "main", "base": "main"})
    check(status == 422, "same-branch pull rejected")
    # topic does not exist; push a real fast-forward branch by creating it from zero.
    branch_commit = commit_object(side_tree_id, [good_id], "topic\n")
    branch_id = oid_of("commit", branch_commit)
    pack = build_pack([("commit", branch_commit)])
    push = pkt(f"{commit_id} {branch_id} refs/heads/topic\0 report-status side-band-64k\n") + b"0000" + pack
    status, report, _headers = client.git("POST", remote + "/git-receive-pack", push, content_type="application/x-git-receive-pack-request")
    check(b"ok refs/heads/topic" in report, f"create branch {report!r}")
    status, pull, _headers = client.json("POST", f"/api/v3/repos/{login}/demo/pulls", {"title": "Topic", "head": "topic", "base": "main", "body": "please"})
    check(status == 201 and pull["state"] == "open" and pull["head"]["ref"] == "topic" and pull["base"]["ref"] == "main", "open pull")
    status, merged, _headers = client.json("PUT", f"/api/v3/repos/{login}/demo/pulls/{pull['number']}/merge", {})
    check(status == 200 and merged["merged"] is True and merged["sha"], "squash merge")
    status, _body, _headers = client.json("GET", f"/api/v3/repos/{login}/demo/pulls/9999")
    check(status == 404, "unknown pull")

    # GraphQL.
    status, data, _headers = client.json("POST", "/api/graphql", {"query": "{ viewer { login } }"}, token=None)
    check(status == 401, "graphql auth")
    query = """query { __type(name: "Issue") { fields { name } } }"""
    status, data, _headers = client.json("POST", "/api/graphql", {"query": query})
    names = {field["name"] for field in data["data"]["__type"]["fields"]}
    check({"id", "number", "title", "state", "body"} <= names, "issue introspection")
    query = """query { __type(name: "Repository") { fields { name } } }"""
    status, data, _headers = client.json("POST", "/api/graphql", {"query": query})
    names = {field["name"] for field in data["data"]["__type"]["fields"]}
    check({"id", "databaseId", "issues", "pullRequest", "pullRequests", "defaultBranchRef"} <= names, "repo introspection")
    query = """query { __type(name: "SearchType") { enumValues { name } } }"""
    status, data, _headers = client.json("POST", "/api/graphql", {"query": query})
    check("ISSUE" in {item["name"] for item in data["data"]["__type"]["enumValues"]}, "SearchType")
    query = """query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){ issues(orderBy:{field:CREATED_AT,direction:DESC}, states:[OPEN]){ nodes { number } } }
    }"""
    status, data, _headers = client.json("POST", "/api/graphql", {"query": query, "variables": {"owner": login, "name": "demo"}})
    numbers = [node["number"] for node in data["data"]["repository"]["issues"]["nodes"]]
    check(numbers == sorted(numbers, reverse=True) and issue["number"] not in numbers, f"open issues desc {numbers}")
    status, data, _headers = client.json("POST", "/api/graphql", {"query": '{ repository(owner:"nope", name:"demo") { name } }'})
    check(data["data"]["repository"] is None and "errors" not in data, "unknown repository")
    status, data, _headers = client.json("POST", "/api/graphql", {"query": '{ repository(owner:"octocat", name:"demo") { notAField } }'})
    check(status == 200 and any("notAField" in err["message"] for err in data["errors"]), "unknown field")
    status, raw, _headers = client.request(
        "POST",
        "/api/graphql",
        json.dumps([
            {"query": "{ viewer { login } }"},
            {"query": '{ repository(owner:"octocat", name:"demo") { name } }'},
        ]).encode(),
    )
    batch = json.loads(raw)
    check(status == 200 and batch[0]["data"]["viewer"]["login"] == login and batch[1]["data"]["repository"]["name"] == "demo", "batch")
    status, data, _headers = client.json("POST", "/api/graphql", {"query": '{ repository(owner:"octocat", name:"demo") { id } }'})
    repo_id = data["data"]["repository"]["id"]
    # Another branch off the post-merge main, discovered via ls-refs.
    status, listed, _headers = client.git("POST", remote + "/git-upload-pack", ls_body, "version=2", "application/x-git-upload-pack-request")
    main_tip = _ref_oid(listed, "refs/heads/main")
    feature_blob = b"feature\n"
    feature_id = oid_of("blob", feature_blob)
    feature_tree = encode_tree([("100644", "feature.txt", feature_id)])
    feature_tree_id = oid_of("tree", feature_tree)
    feature_commit = commit_object(feature_tree_id, [main_tip], "feature\n")
    feature_commit_id = oid_of("commit", feature_commit)
    pack = build_pack([("blob", feature_blob), ("tree", feature_tree), ("commit", feature_commit)])
    push = pkt(f"{ZERO} {feature_commit_id} refs/heads/feature\0 report-status side-band-64k\n") + b"0000" + pack
    status, report, _headers = client.git("POST", remote + "/git-receive-pack", push, content_type="application/x-git-receive-pack-request")
    check(b"ok refs/heads/feature" in report, f"feature branch {report!r}")
    mutation = """mutation($input:CreatePullRequestInput!){ createPullRequest(input:$input){ pullRequest { number headRefName baseRefName } } }"""
    status, data, _headers = client.json("POST", "/api/graphql", {
        "query": mutation,
        "variables": {"input": {"repositoryId": repo_id, "headRefName": "feature", "baseRefName": "main", "title": "From GraphQL"}},
    })
    created_pr = data["data"]["createPullRequest"]["pullRequest"]
    check(created_pr["headRefName"] == "feature" and created_pr["baseRefName"] == "main", "createPullRequest")
    status, via_rest, _headers = client.json("GET", f"/api/v3/repos/{login}/demo/pulls/{created_pr['number']}")
    check(status == 200 and via_rest["head"]["ref"] == "feature" and via_rest["base"]["ref"] == "main", "graphql pr readable via rest")

    # MCP.
    status, _body, _headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=None)
    check(status == 401, "mcp auth")
    status, raw, _headers = client.request("POST", "/mcp", b"nope")
    check(status == 400 and b"-32700" in raw, "mcp parse error")
    status, data, _headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": "d", "method": "server/discover"})
    versions = data["result"]["supportedVersions"]
    check(versions and all(len(item) == 10 and item[4] == "-" and item[7] == "-" for item in versions), "discover versions")
    status, data, headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
    check(data["result"]["protocolVersion"] == "2025-03-26", "initialize")
    check(headers.get("Mcp-Session-Id") == "forge", "session id")
    status, _body, _headers = client.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode())
    check(status == 202, "initialized notification")
    status, first, _headers = client.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode())
    status, second, _headers = client.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}).encode())
    check(first.split(b'"tools":', 1)[1] == second.split(b'"tools":', 1)[1], "tools/list stable")
    tools = json.loads(first)["result"]["tools"]
    check({tool["name"] for tool in tools} >= {"create_repo", "get_repo", "create_issue", "list_issues", "create_pull_request", "merge_pull_request"}, "tool names")
    check(all(tool["description"] and tool["inputSchema"]["type"] == "object" for tool in tools), "tool schemas")
    status, data, _headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "missing", "arguments": {}}})
    check(data["error"]["code"] in (-32601, -32602), "unknown tool")
    status, data, _headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "create_issue", "arguments": {"owner": login}}})
    check(data["error"]["code"] == -32602, "missing argument")
    status, data, _headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "create_repo", "arguments": {"name": "mcp-demo"}}})
    check(data["result"]["isError"] is False, "mcp create_repo")
    status, data, _headers = client.json("POST", "/mcp", {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "create_issue", "arguments": {"owner": login, "repo": "mcp-demo", "title": "from mcp", "body": "hi"}}})
    created_issue = json.loads(data["result"]["content"][0]["text"])
    status, fetched_issue, _headers = client.json("GET", f"/api/v3/repos/{login}/mcp-demo/issues/{created_issue['number']}")
    check(fetched_issue["id"] == created_issue["id"] and fetched_issue["title"] == "from mcp", "mcp issue id stable")

    # Keep-alive: two responses on one socket.
    sock = socket.create_connection(("127.0.0.1", port))
    req = (
        f"GET /api/v3/repos/{login}/mcp-demo/issues/{created_issue['number']} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\nAuthorization: Bearer {TOKEN}\r\nConnection: keep-alive\r\n\r\n"
    ).encode()
    sock.sendall(req + req)
    sock.settimeout(3)
    blob_in = b""
    while blob_in.count(b"HTTP/1.1 200") < 2:
        chunk = sock.recv(4096)
        if not chunk:
            break
        blob_in += chunk
    sock.close()
    check(blob_in.count(b"HTTP/1.1 200") >= 2, "keep-alive")

    server.shutdown()
    print("selftest ok")


if __name__ == "__main__":
    main_test()
