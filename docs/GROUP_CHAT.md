# Study-group chat

Group chat uses Django Channels WebSockets. Message bodies and sender/timestamps are stored in the MindVault database; the channel layer only fans a saved message out to connected members. Chat history is checked against current group membership and the latest 100 messages are loaded when a member connects.

## Development

Install the packages in `requirements.txt` and apply the new sharing migration:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
```

Start an ASGI server for WebSocket support (the regular Django WSGI server does not serve these sockets):

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.dev"
.\.venv\Scripts\daphne.exe -b 127.0.0.1 -p 8000 config.asgi:application
```

For one local process, the in-memory channel layer is used when `REDIS_URL` is unset. It is suitable for development only; messages will not fan out between server processes.

## Production

Provision a private Redis service and set `REDIS_URL` in the server environment, for example `redis://127.0.0.1:6379/0` (use the provider's TLS URL and credentials when required). Production settings refuse to start without this value. Run MindVault under Daphne or another ASGI server using `config.asgi:application` with `DJANGO_SETTINGS_MODULE=config.settings.prod`; configure the reverse proxy to pass WebSocket `Upgrade` requests and serve the site over HTTPS. Continue running the existing Django HTTP application through this ASGI entry point.

No Redis password or provider credentials are placed in the browser. Group membership is checked when sockets connect, before messages are saved, and before saved messages are sent to each connected recipient.

## Two-member smoke test

1. Apply migrations and start Daphne. Use HTTPS in deployment; `localhost` is also a secure context for local browser APIs.
2. Sign in as two users who are members of the same study group in separate browser profiles.
3. Open **Group chat** for both. Send a message from one profile and confirm it appears in the other without a page refresh.
4. Refresh the second profile and confirm the message remains in history. Send another message while a group call is running in the other profile or tab to check that both features remain available.
5. Remove one member from the group and send a new message. Their open socket should be closed and the removed user should no longer receive group messages.
