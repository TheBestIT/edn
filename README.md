# Everything Delivery Network

The Everything Delivery Network (EDN) is a server system that delivers data.
The system has no user interface. All the functions of the system are available
through an API.

The system keeps all the data in a virtual filesystem (VFS). Each path in the
VFS is a node. Some nodes are files on a disk. Other nodes are virtual, and a
plugin supplies their data. For example, a plugin can supply a video for the
path `/youtube/<id>`. Each directory has its own permissions.

## Design rules

- **Keep all the functions in the API.** A machine can read the schema of the
  API (OpenAPI). No function is available only in a user interface.
- **Use the VFS as the interface.** Files, streams and plugin nodes are in the
  same namespace. They all have the same permission model.
- **Hide the difference between a physical node and a virtual node.** A client
  reads `/photos/cat.png` and `/youtube/dQw4w9WgXcQ` with the same call.
- **Keep the core small.** Add new functions as plugins or as different storage
  backends. Do not add them to the core.
- **Make the data safe.** The system calculates a hash of all the data. The
  system can also encrypt the data on the disk. The system decrypts the data
  when an approved client reads it.

---

## Roadmap

### Phase 1 — Core webserver and API

- [X] Write the webserver with Django. Use ASGI for streams and for
  asynchronous plugin routes.
- [X] Write the REST API with a version in the path (`/api/v1/…`).
- [X] Add authentication with API keys and with tokens (JWT).
- [X] Make the OpenAPI schema automatically. All the future clients use this
  schema.
- [X] Write a structured error model, envelopes for the requests and the
  responses, and rules for pagination.
- [X] Add rate limits and a quota for each key.

### Phase 2 — Storage and database

- [X] Write the database schema for the nodes and their metadata.
- [X] Write an interface for the storage backend. Use a local disk first. Then
  add a backend for S3.
- [X] Store each blob with its hash as the address. Then the system can find
  the identical data and keep only one copy.
- [X] Write a method to divide the data into clusters and shards. Then you can
  add more storage.
- [ ] Write a layer of background workers. The workers make the indexes,
  encrypt the data, and get the data from the plugins.

### Phase 3 — Virtual filesystem

- [X] Write the node model: directories, files, aliases and virtual nodes.
- [X] Write the engine that finds the node for a path. The engine uses one
  namespace for the physical nodes and the virtual nodes.
- [X] Add the metadata: size, MIME type, times, checksums and other attributes.
- [ ] Write the rules to move, copy, rename and ~~delete~~ a node.
- [X] Write API calls to show, to find and to go through the nodes.

### Phase 4 — Permissions

- [ ] Write a permission model for each directory. Use owners, groups and
  roles.
- [ ] Let a directory get the permissions of its parent directory. Let an
  operator replace these permissions.
- [ ] Apply the permissions to the physical nodes, the virtual nodes and the
  streams in the same way.
- [ ] Add tokens with a limited scope. Such a token gives access to one subtree
  only.
- [ ] Record all the access and all the changes of the permissions in an audit
  log.

### Phase 5 — Streams and files

- [ ] Add the upload and the download of a file. Divide an upload into chunks.
  Let a client continue an upload after an interruption.
- [ ] Add HTTP range requests. Then a client can go to a different position in
  a media file.
- [ ] Read and write the data as a stream. Do not put a full file in the
  memory.
- [ ] Add a download of many files and a download of a full directory as an
  archive.
- [ ] Add controls for the bandwidth.

### Phase 6 — Security: hashes and encryption

- [ ] Calculate a hash for each blob. Use this hash to make sure that the data
  is correct. Use this hash also to find the identical data.
- [ ] Examine the hash when the system reads a blob. Thus the system finds
  damaged data and changed data.
- [ ] Encrypt the data on the disk. Use a different key for each file (envelope
  encryption).
- [ ] Decrypt the data immediately when an approved client reads it.
- [ ] Write the procedures for the keys: how to store them, how to change them,
  and how to make a key for each node or for each user.
- [ ] Add an optional mode. In this mode the client encrypts the data, and the
  server cannot read it.

### Phase 7 — Plugin system

- [ ] Write the contract for a plugin. A plugin registers its routes, finds the
  virtual nodes, and sends the data.
- [ ] Let an operator attach a plugin to a path in the VFS.
- [ ] Add permissions for the plugins. Give each plugin the necessary subtrees
  only.
- [ ] Add a cache for the data that comes from a plugin.
- [ ] Write the example plugin `/random`. This plugin makes bytes, like
  `/dev/random`.
- [ ] Write the example plugin `/youtube/<id>`. This plugin finds a video with
  its ID.
- [ ] Write the functions to install, to enable, to disable, to configure and
  to give a version to a plugin.

### Phase 8 — Full API

- [ ] Write the full OpenAPI spec for all the endpoints. The clients get all
  the data about the API from this spec.
- [ ] Add webhooks and an event stream. They tell a client about the changes,
  the uploads and the plugin events.
- [ ] Make the stubs of the client SDKs from the schema.
- [ ] Add structured logs, metrics and endpoints that show the condition of the
  server.
- [ ] Write a guide to install the system. Add an example configuration for a
  production server.

### Other projects

The API makes these projects possible. They are not part of the core.

- [ ] A CLI tool. It attaches to a server, shows the nodes and uploads files.
- [ ] A GUI or a web client.
- [ ] SDKs for different languages.
- [ ] A FUSE adapter. It attaches the VFS to a computer as a usual filesystem.