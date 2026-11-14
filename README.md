# github-core-clone

A GitHub-compatible git forge in one Python process. It uses only the standard library: git objects and packfiles are built with `hashlib` and `zlib`, and there is no git binary and no network beyond the suite.

The grader runs `python3 -P -s /bot/main.py`. The process binds an ephemeral port, POSTs `{"port": N}` to `$MAKEFUN_ENV_URL/register`, and polls `GET $MAKEFUN_ENV_URL/done` until the body is `{"done": true}`.

`MAKEFUN_SHOP_TOKEN` is the bearer token. The authenticated login defaults to `octocat` and can be overridden with `FORGE_LOGIN`. A repository under any other owner is `404`.

## Surfaces

| Route | What it serves |
| --- | --- |
| `GET /healthz` | Boot probe, no token |
| `/api/v3/...` | REST v3: repositories, contents, labels, issues, pull requests (squash merge) |
| `POST /api/graphql` | GraphQL: introspection, issues, `createPullRequest` |
| `POST /mcp` | MCP JSON-RPC: `server/discover`, `initialize`, `tools/list`, `tools/call` |
| `/{owner}/{repo}.git/...` | Smart HTTP: upload-pack protocol v2, receive-pack v0 |

## Self-test

```sh
python3 tests/selftest.py
```

The test speaks git smart HTTP itself, including a push and a depth-1 fetch.
