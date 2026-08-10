# Webhook Inbox

**Webhook Inbox** is a self-hosted developer tool for receiving, inspecting, searching, and replaying webhook events.

It is designed to make debugging webhook integrations easier by providing a central place to store incoming requests and explore them through a web dashboard.

## Planned Features

* Receive webhooks from any HTTP-compatible service
* Multiple configurable webhook endpoints
* MongoDB document storage for flexible payloads
* Full-text and fuzzy search
* Event filtering and sorting
* Detailed request inspection
* Webhook replay
* Replay history and response inspection
* Event export
* Configurable retention
* Authentication and security controls
* Fully Dockerized deployment

## Planned Stack

* **Python / FastAPI**
* **MongoDB**
* **Docker / Docker Compose**

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

Send a webhook:

```bash
curl -X POST http://localhost:8000/webhooks/demo \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Event: user.created" \
  -d '{"event": "user.created", "user": {"id": 1827, "name": "John Doe"}}'
```

Then open <http://localhost:8000/events>.

## Status

**Early Development**

Webhook ingestion, storage and a minimal event list are working. Endpoint management,
dashboard authentication, search, and replay are still in progress.

## License

[Apache-2.0](LICENSE)