# MM-Importer Plugin

A Mattermost plugin for importing messages and metadata from external sources as any user, preserving threading and user metadata.

## Features

- Import messages as any user
- Preserve threading via `root_id`
- Custom timestamps for historical data
- Create/get channels with normalized names
- Add channel members in bulk
- Create/resolve DM and Group DM channels
- Import reactions

## API Endpoints (implemented here)

- POST `/plugins/mm-importer/api/v1/import` — создать пост от имени любого пользователя
- POST `/plugins/mm-importer/api/v1/reaction` — добавить реакцию к посту
- POST `/plugins/mm-importer/api/v1/channel` — создать/получить канал (нормализация имени)
- POST `/plugins/mm-importer/api/v1/channel/members` — добавить участников (bulk)
- POST `/plugins/mm-importer/api/v1/channel/archive` — архивировать канал
- POST `/plugins/mm-importer/api/v1/dm` — создать/получить личный канал (2 пользователя)
- POST `/plugins/mm-importer/api/v1/gdm` — создать/получить групповой DM

### Channel name normalization
- lower-case
- пробелы/точки/подчёркивания → `-`
- только ASCII буквы/цифры/`-`
- сжатие повторяющихся дефисов
- длина 2..64 символа

### Quick examples

Create or get channel:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/channel" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"team_id":"<TEAM_ID>","name":"general","display_name":"General","type":"O"}'
```

Add members:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/channel/members" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"channel_id":"<CH_ID>","user_ids":["<U1>","<U2>"]}'
```

Create/resolve group DM:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/gdm" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_ids":["<U1>","<U2>","<U3>"]}'
```

Import message:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/import" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_id":"<U>","channel_id":"<CH>","message":"hello"}'
```

## Deployment in this repo

- Backend exposes helper endpoints to manage this plugin:
  - `GET /plugin/status`, `POST /plugin/deploy`, `POST /plugin/enable`, `POST /plugin/ensure`
- On backend startup, a best-effort auto-ensure runs to deploy/enable the plugin.

For the original starter template documentation, see below.

---

# MM-Importer Plugin

A Mattermost plugin for importing messages and metadata from external sources as any user, preserving threading and user metadata.

## Features

- **Import messages as any user**: Create posts on behalf of any user in the system
- **Preserve threading**: Import threaded conversations with proper `root_id` linking
- **Custom timestamps**: Set custom `create_at` timestamps for historical data
- **REST API**: Simple HTTP API for integration with external systems
- **No authentication required**: Plugin endpoints work without user authentication (for testing)

## Installation

1. Build the plugin:
   ```bash
   cd plugins/mm-importer-starter
   make dist
   ```

2. Upload to Mattermost:
   ```bash
   curl -i -X POST \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -F "plugin=@dist/mm-importer-0.2.0.tar.gz" \
     http://YOUR_MATTERMOST_URL/api/v4/plugins
   ```

3. Enable the plugin:
   ```bash
   curl -i -X POST \
     -H "Authorization: Bearer YOUR_TOKEN" \
     http://YOUR_MATTERMOST_URL/api/v4/plugins/mm-importer/enable
   ```

## API Endpoints

### Import Message

**POST** `/plugins/mm-importer/api/v1/import`

Import a message as any user.

**Request Body:**
```json
{
  "user_id": "string",      // Required: ID of the user to post as
  "channel_id": "string",   // Required: ID of the channel to post in
  "message": "string",      // Required: Message content
  "create_at": 0,          // Optional: Custom timestamp (milliseconds)
  "root_id": "string"      // Optional: Parent post ID for threading
}
```

**Response:**
```json
{
  "post_id": "string"      // ID of the created post
}
```

**Example:**
```bash
curl -X POST 'http://localhost:8065/plugins/mm-importer/api/v1/import' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "o6b98rc1tpnfmy7ajxiadygmzy",
    "channel_id": "8q3dynerq7nzzmxo8dfckcfdnr",
    "message": "Hello from external system!",
    "create_at": 0
  }'
```

### Hello World

**GET** `/plugins/mm-importer/api/v1/hello`

Simple health check endpoint.

**Response:**
```
Hello, world!
```

### Import Reaction

**POST** `/plugins/mm-importer/api/v1/reaction`

Импортировать реакцию от любого пользователя к любому сообщению.

**Request Body:**
```json
{
  "user_id": "string",      // Required: ID пользователя, от чьего имени реакция
  "post_id": "string",      // Required: ID сообщения, к которому реакция
  "emoji_name": "string",   // Required: Имя emoji (например, "heart")
  "create_at": 0             // Optional: Время создания (мс)
}
```

**Response:**
```json
{
  "error": "string"         // Пусто при успехе, иначе текст ошибки
}
```

**Example:**
```bash
curl -X POST 'http://localhost:8065/plugins/mm-importer/api/v1/reaction' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "o6b98rc1tpnfmy7ajxiadygmzy",
    "post_id": "abc123def456",
    "emoji_name": "heart"
  }'
```

### Create or Get Channel

**POST** `/plugins/mm-importer/api/v1/channel`

Создать канал (или вернуть существующий) с нормализацией имени.

**Request Body:**
```json
{
  "team_id": "string",
  "name": "string",         
  "display_name": "string",
  "type": "O|P",
  "header": "string",
  "purpose": "string"
}
```

**Response:**
```json
{ "channel_id": "string" }
```

### Add Channel Members (bulk)

**POST** `/plugins/mm-importer/api/v1/channel/members`

Добавить список пользователей в канал.

**Request Body:**
```json
{ "channel_id": "string", "user_ids": ["id1", "id2"] }
```

**Response:**
```json
{ "added": ["id1", "id2"] }
```

### Archive Channel

**POST** `/plugins/mm-importer/api/v1/channel/archive`

Архивировать (soft-delete) канал.

**Request Body:**
```json
{ "channel_id": "string" }
```

### Create Direct Channel

**POST** `/plugins/mm-importer/api/v1/dm`

Создать (или получить) личный канал для двух пользователей.

**Request Body:**
```json
{ "user_ids": ["user1", "user2"] }
```

**Response:**
```json
{ "channel_id": "string" }
```

### Create Group DM Channel

**POST** `/plugins/mm-importer/api/v1/gdm`

Создать (или получить) групповой DM канал.

**Request Body:**
```json
{ "user_ids": ["u1", "u2", "u3"] }
```

**Response:**
```json
{ "channel_id": "string" }
```

## Usage Examples

### Import a Simple Message

```python
import requests

data = {
    "user_id": "user123",
    "channel_id": "channel456", 
    "message": "Hello from external system!"
}

response = requests.post(
    "http://localhost:8065/plugins/mm-importer/api/v1/import",
    json=data
)
post_id = response.json()["post_id"]
```

### Import a Threaded Conversation

```python
# First message
result1 = requests.post(
    "http://localhost:8065/plugins/mm-importer/api/v1/import",
    json={
        "user_id": "user1",
        "channel_id": "channel123",
        "message": "Original message"
    }
)
root_post_id = result1.json()["post_id"]

# Reply to the message
result2 = requests.post(
    "http://localhost:8065/plugins/mm-importer/api/v1/import", 
    json={
        "user_id": "user2",
        "channel_id": "channel123",
        "message": "This is a reply",
        "root_id": root_post_id
    }
)
```

### Import with Custom Timestamp

```python
import time

# Import message with timestamp from 1 hour ago
custom_time = int(time.time() * 1000) - 3600000

requests.post(
    "http://localhost:8065/plugins/mm-importer/api/v1/import",
    json={
        "user_id": "user123",
        "channel_id": "channel456",
        "message": "Historical message",
        "create_at": custom_time
    }
)
```

## Testing

Run the test script to verify the plugin functionality:

```bash
python3 test_plugin.py
```

This will:
1. Import a simple message
2. Create a threaded conversation
3. Import messages with custom timestamps
4. Verify all posts were created correctly

## Development

### Building

```bash
# Build for all platforms
make dist

# Build server only
make server

# Build webapp only  
make webapp
```

### Development Environment

The plugin is built using Docker Compose with Go 1.24.4 and Node.js:

```bash
docker compose up -d golang
docker compose exec golang bash
```

### Project Structure

```
plugins/mm-importer-starter/
├── server/                 # Go server code
│   ├── api.go             # HTTP API handlers
│   ├── plugin.go          # Plugin main file
│   └── command/           # Slash command handlers
├── webapp/                # React frontend (if needed)
├── build/                 # Build tools
└── dist/                  # Built plugin package
```

## Security Considerations

⚠️ **Warning**: This plugin allows posting as any user without authentication. This is intended for:
- Development and testing
- Internal tools with proper access controls
- Migration scripts with appropriate safeguards

For production use, consider:
- Adding authentication middleware
- Implementing rate limiting
- Restricting access to specific IP addresses
- Adding audit logging

## License

This plugin is based on the Mattermost Plugin Starter Template and follows the same licensing terms.

# Plugin Starter Template

[![Build Status](https://github.com/mattermost/mattermost-plugin-starter-template/actions/workflows/ci.yml/badge.svg)](https://github.com/mattermost/mattermost-plugin-starter-template/actions/workflows/ci.yml)
[![E2E Status](https://github.com/mattermost/mattermost-plugin-starter-template/actions/workflows/e2e.yml/badge.svg)](https://github.com/mattermost/mattermost-plugin-starter-template/actions/workflows/e2e.yml)

This plugin serves as a starting point for writing a Mattermost plugin. Feel free to base your own plugin off this repository.

To learn more about plugins, see [our plugin documentation](https://developers.mattermost.com/extend/plugins/).

This template requires node v16 and npm v8. You can download and install nvm to manage your node versions by following the instructions [here](https://github.com/nvm-sh/nvm). Once you've setup the project simply run `nvm i` within the root folder to use the suggested version of node.

## Getting Started
Use GitHub's template feature to make a copy of this repository by clicking the "Use this template" button.

Alternatively shallow clone the repository matching your plugin name:
```
git clone --depth 1 https://github.com/mattermost/mattermost-plugin-starter-template com.example.my-plugin
```

Note that this project uses [Go modules](https://github.com/golang/go/wiki/Modules). Be sure to locate the project outside of `$GOPATH`.

Edit the following files:
1. `plugin.json` with your `id`, `name`, and `description`:
```json
{
    "id": "com.example.my-plugin",
    "name": "My Plugin",
    "description": "A plugin to enhance Mattermost."
}
```

2. `go.mod` with your Go module path, following the `<hosting-site>/<repository>/<module>` convention:
```
module github.com/example/my-plugin
```

3. `.golangci.yml` with your Go module path:
```yml
linters-settings:
  # [...]
  goimports:
    local-prefixes: github.com/example/my-plugin
```

Build your plugin:
```
make
```

This will produce a single plugin file (with support for multiple architectures) for upload to your Mattermost server:

```
dist/com.example.my-plugin.tar.gz
```

## Development

To avoid having to manually install your plugin, build and deploy your plugin using one of the following options. In order for the below options to work, you must first enable plugin uploads via your config.json or API and restart Mattermost.

```json
    "PluginSettings" : {
        ...
        "EnableUploads" : true
    }
```

### Development guidance 

1. Fewer packages is better: default to the main package unless there's good reason for a new package.

2. Coupling implies same package: don't jump through hoops to break apart code that's naturally coupled.

3. New package for a new interface: a classic example is the sqlstore with layers for monitoring performance, caching and mocking.

4. New package for upstream integration: a discrete client package for interfacing with a 3rd party is often a great place to break out into a new package

### Modifying the server boilerplate

The server code comes with some boilerplate for creating an api, using slash commands, accessing the kvstore and using the cluster package for jobs. 

#### Api

api.go implements the ServeHTTP hook which allows the plugin to implement the http.Handler interface. Requests destined for the `/plugins/{id}` path will be routed to the plugin. This file also contains a sample `HelloWorld` endpoint that is tested in plugin_test.go.

#### Command package

This package contains the boilerplate for adding a slash command and an instance of it is created in the `OnActivate` hook in plugin.go. If you don't need it you can delete the package and remove any reference to `commandClient` in plugin.go. The package also contains an example of how to create a mock for testing.

#### KVStore package

This is a central place for you to access the KVStore methods that are available in the `pluginapi.Client`. The package contains an interface for you to define your methods that will wrap the KVStore methods. An instance of the KVStore is created in the `OnActivate` hook.

### Deploying with Local Mode

If your Mattermost server is running locally, you can enable [local mode](https://docs.mattermost.com/administration/mmctl-cli-tool.html#local-mode) to streamline deploying your plugin. Edit your server configuration as follows:

```json
{
    "ServiceSettings": {
        ...
        "EnableLocalMode": true,
        "LocalModeSocketLocation": "/var/tmp/mattermost_local.socket"
    },
}
```

and then deploy your plugin:
```
make deploy
```

You may also customize the Unix socket path:
```bash
export MM_LOCALSOCKETPATH=/var/tmp/alternate_local.socket
make deploy
```

If developing a plugin with a webapp, watch for changes and deploy those automatically:
```bash
export MM_SERVICESETTINGS_SITEURL=http://localhost:8065
export MM_ADMIN_TOKEN=j44acwd8obn78cdcx7koid4jkr
make watch
```

### Deploying with credentials

Alternatively, you can authenticate with the server's API with credentials:
```bash
export MM_SERVICESETTINGS_SITEURL=http://localhost:8065
export MM_ADMIN_USERNAME=admin
export MM_ADMIN_PASSWORD=password
make deploy
```

or with a [personal access token](https://docs.mattermost.com/developer/personal-access-tokens.html):
```bash
export MM_SERVICESETTINGS_SITEURL=http://localhost:8065
export MM_ADMIN_TOKEN=j44acwd8obn78cdcx7koid4jkr
make deploy
```

### Releasing new versions

The version of a plugin is determined at compile time, automatically populating a `version` field in the [plugin manifest](plugin.json):
* If the current commit matches a tag, the version will match after stripping any leading `v`, e.g. `1.3.1`.
* Otherwise, the version will combine the nearest tag with `git rev-parse --short HEAD`, e.g. `1.3.1+d06e53e1`.
* If there is no version tag, an empty version will be combined with the short hash, e.g. `0.0.0+76081421`.

To disable this behaviour, manually populate and maintain the `version` field.

## How to Release

To trigger a release, follow these steps:

1. **For Patch Release:** Run the following command:
    ```
    make patch
    ```
   This will release a patch change.

2. **For Minor Release:** Run the following command:
    ```
    make minor
    ```
   This will release a minor change.

3. **For Major Release:** Run the following command:
    ```
    make major
    ```
   This will release a major change.

4. **For Patch Release Candidate (RC):** Run the following command:
    ```
    make patch-rc
    ```
   This will release a patch release candidate.

5. **For Minor Release Candidate (RC):** Run the following command:
    ```
    make minor-rc
    ```
   This will release a minor release candidate.

6. **For Major Release Candidate (RC):** Run the following command:
    ```
    make major-rc
    ```
   This will release a major release candidate.

## Q&A

### How do I make a server-only or web app-only plugin?

Simply delete the `server` or `webapp` folders and remove the corresponding sections from `plugin.json`. The build scripts will skip the missing portions automatically.

### How do I include assets in the plugin bundle?

Place them into the `assets` directory. To use an asset at runtime, build the path to your asset and open as a regular file:

```go
bundlePath, err := p.API.GetBundlePath()
if err != nil {
    return errors.Wrap(err, "failed to get bundle path")
}

profileImage, err := ioutil.ReadFile(filepath.Join(bundlePath, "assets", "profile_image.png"))
if err != nil {
    return errors.Wrap(err, "failed to read profile image")
}

if appErr := p.API.SetProfileImage(userID, profileImage); appErr != nil {
    return errors.Wrap(err, "failed to set profile image")
}
```

### How do I build the plugin with unminified JavaScript?
Setting the `MM_DEBUG` environment variable will invoke the debug builds. The simplist way to do this is to simply include this variable in your calls to `make` (e.g. `make dist MM_DEBUG=1`).
