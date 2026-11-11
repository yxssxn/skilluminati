# Skilluminati

A Hermes Agent plugin that checks whether your skills still earn their place. It replays a skill's latest uses twice, once with the skill and once without, and the Owl, judging blind, picks the better answer each time. The verdict is Enlightened, Dead weight, Hindrance or Unproven.

## Install

```bash
hermes plugins install yxssxn/skilluminati/plugin
hermes plugins enable skilluminati
```

In Hermes Desktop, also turn on **Capabilities -> Plugins -> Skilluminati** to add the Skilluminati page.

## Try it

```
/skilluminati systematic-debugging
```

A new install has no history to test, so this uses the skill's 8 built-in questions and takes about 8 minutes. When it is done a popup says so: press **Show in chat** to put the verdict in the chat. On the Skilluminati page you can press **Ask the Owl** on any skill instead.

## What it touches

- **Model calls:** a test calls your configured model through Hermes (about 48 to 64 calls, one at a time). Runs start only when you type the command or press Ask the Owl; never from cron or a messaging platform.
- **Your history:** it reads your past chats from Hermes's `state.db` read-only. Keys, emails and other private details are removed (names only if you list them) before any text is sent to a model.
- **Files it writes:** results go only to `<HERMES_HOME>/plugin-data/skilluminati/` (owner-only).
- **Background work:** each test runs in one background thread inside Hermes. `/skilluminati stop` ends it.
- **Chat:** when a test ends you get a popup; the verdict goes into the chat only when you click "Show in chat".
- No other network calls, no shell commands, no stored credentials.

The full guide, including how the judging works and what it cannot tell you, is in the [main README](https://github.com/yxssxn/skilluminati#readme).

MIT licence. See `LICENSE`.
