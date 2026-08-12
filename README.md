# maoniang

A QQ group bot built on a layered `app/` spine: NoneBot2 + OneBot v11 adapter, replies via an
OpenAI **Responses API**–compatible upstream.

This is phase 0: the foundation only — typed config, a conversation store with the lock-and-reread
invariant in one place, a long-lived Responses client with bounded retry + a daily budget guard,
and a service layer that the thin NoneBot matchers delegate to. No new user-visible behavior vs the
reference bot; everything is behind defaults that reproduce current behavior.

## Layout

```
app/
  config.py    typed Config (one load_dotenv, one lenient csv_ints, no pydantic)
  text.py      pure message helpers (is_at_bot, 公共/command parse, display name)
  history.py   ConversationScope + InMemoryStore; re-read-after-acquire lives in locked()
  budget.py    daily API-call guard
  llm.py       ResponsesClient: long-lived httpx client, retry/classify, two timeout tiers
  service.py   ConversationService.handle() — lock→reread→call→remember/clear
plugins/
  auto_reply.py   thin group matcher (filter → @-check → parse → service.handle → finish)
  status_notify.py  online/offline group notifications
bot.py         NoneBot entry; wires client lifecycle
tests/         app-layer tests (run without nonebot)
```

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env   # then edit TARGET_USER_IDS / API_KEY / ...
python bot.py
```

Run a OneBot v11 implementation (e.g. NapCat) and point its reverse WebSocket at
`ws://127.0.0.1:8080/onebot/v11/ws`. The bot binds to loopback only — no public inbound port.

## Test

```bash
pytest -q
```
