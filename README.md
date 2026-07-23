# Everything Delivery Network

A backend-only content delivery platform built around a **virtual filesystem (VFS)**
exposed entirely through an API. Every path is a node in a tree — some nodes are
real files on disk, others are *virtual* routes served by plugins (e.g. a
`/youtube/<id>` route that resolves a video on demand). Every directory carries
its own permission set. There is **no front-end**: the goal is a broad, stable
API that downstream projects — a GUI, a CLI, SDKs — can be built on top of.

## Design principles
- **API-first, headless.** Everything the system can do is reachable through the
  API and documented by a machine-readable schema (OpenAPI). No feature is
  UI-only.
- **The VFS is the interface.** Files, streams, and plugin routes all present as
  nodes in one namespace with a uniform permission model.
- **Physical vs. virtual is an implementation detail.** A consumer reading
  `/photos/cat.png` and `/youtube/dQw4w9WgXcQ` uses the same call.
- **Barebones core, broad surface.** The core stays small and unopinionated;
  capabilities are added as plugins and pluggable backends, not baked in.
- **Secure at rest by default.** Content is hashed for integrity/dedup and can be
  encrypted at rest with transparent decryption on read.

---

## Roadmap

### Phase 1 — Core webserver & API
- [X] Webserver (Django, ASGI for streaming/async plugin routes)
- [X] REST API layer with versioning (`/api/v1/…`)
- [X] Authentication: API keys + token/JWT sessions
- [X] Auto-generated OpenAPI schema (the contract every future client builds on)
- [X] Structured error model, request/response envelopes, pagination conventions
- [X] Rate limiting and per-key quotas

### Phase 2 — Storage & database
- [ ] Database schema for nodes and metadata
- [ ] Storage backend abstraction (interface first: local disk, then S3-compatible)
- [ ] Content-addressed blob store (store by hash; enables dedup)
- [ ] Data clusters / sharding strategy for scaling storage horizontally
- [ ] Background job/worker layer (for indexing, encryption, plugin fetches)

### Phase 3 — Virtual filesystem
- [ ] Node model: directories, files, symlinks/aliases, and virtual (plugin) nodes
- [ ] Path resolution engine (single namespace over physical + virtual nodes)
- [ ] Metadata (size, mime, timestamps, checksums, arbitrary key/value attrs)
- [ ] Move / copy / rename / delete semantics
- [ ] Listing, search, and traversal APIs

### Phase 4 — Permissions
- [ ] Per-directory permission model (owners, groups, ACLs or RBAC roles)
- [ ] Permission inheritance down the tree, with explicit overrides
- [ ] Enforcement layer applied uniformly to physical, virtual, and stream reads
- [ ] Capability/scoped tokens (grant access to a subtree, not the whole account)
- [ ] Audit log of access and permission changes

### Phase 5 — Streams & files
- [ ] File upload (chunked + resumable) and download
- [ ] HTTP range requests / partial content for media seeking
- [ ] Streaming reads and writes (don't buffer whole files in memory)
- [ ] Multipart and directory (archive) downloads
- [ ] Bandwidth/throttling controls

### Phase 6 — Security: hashing & encryption
- [ ] Content hashing for every stored blob (integrity + dedup key)
- [ ] Integrity verification on read (detect corruption/tampering)
- [ ] Encryption at rest (per-file keys via envelope encryption)
- [ ] Transparent, real-time decryption on read for authorized callers
- [ ] Key management: rotation, per-node/per-user keys, key storage strategy
- [ ] Optional client-side / zero-knowledge encryption mode

### Phase 7 — Plugin system (virtual routes)
- [ ] Plugin API/contract: register routes, resolve virtual nodes, stream results
- [ ] Route registration and namespacing (mount a plugin at a VFS path)
- [ ] Plugin permissions (which subtrees a plugin may serve; sandboxing)
- [ ] Caching layer for plugin-fetched content
- [ ] Reference plugin: `/random` (synthesizes bytes, like `/dev/random`)
- [ ] Reference plugin: `/youtube/<id>` (indexes/resolves videos by ID)
- [ ] Plugin lifecycle: install, enable/disable, config, versioning

### Phase 8 — API completeness & ecosystem enablement
- [ ] Full OpenAPI spec covering every endpoint (source of truth for clients)
- [ ] Webhooks / event stream (notify on changes, uploads, plugin events)
- [ ] Generated client SDK stubs from the schema
- [ ] Observability: structured logging, metrics, health/readiness endpoints
- [ ] Deployment guide and reference production config

### Downstream (enabled, out of core scope)
These are separate projects the API is designed to support — not part of the core:
- [ ] CLI tool (mount/browse/upload against a server)
- [ ] GUI / web client
- [ ] Language SDKs
- [ ] FUSE adapter (mount the VFS as a real filesystem)
