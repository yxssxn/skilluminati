# Skilluminati

A Hermes Agent plugin that checks whether your skills still earn their place. It replays a skill's latest uses twice, once with the skill and once without, and the Owl, judging blind, picks the better answer each time. The verdict is Enlightened, Dead weight, Hindrance or Unproven.

## Install

```bash
hermes plugins install yxssxn/skilluminati/plugin
hermes plugins enable skilluminati
```

That installs the latest version (v0.2.1) from the repo, an unreviewed source as far as Hermes is concerned. `hermes plugins install skilluminati` installs the catalogue copy, which stays on v0.1.0, the hackathon entry.

In Hermes Desktop, also turn on **Capabilities -> Plugins -> Skilluminati** to add the Skilluminati page.

## Try it

```
/skilluminati systematic-debugging
```

A new install has no history to test, so this uses the skill's 8 built-in questions and the run takes a few minutes. When it is done a popup says so: press **Show in chat** to put the verdict in the chat. On the Skilluminati page you can press **Ask the Owl** on any skill instead.

Ask again and the new run's answers are added to the earlier ones for the same skill version, model and judge. The result says what it rests on ("12 tasks, 3 runs"). A close result reads "No clear difference yet" with the next step "Ask again".

## What it touches

- **Model calls:** a run calls your configured model through Hermes (about 48 to 64 calls, one at a time). Runs start only when you type the command or press Ask the Owl; never from a scheduled job (cron) or a messaging platform.
- **Your history:** it reads your past chats from Hermes's `state.db` read-only. Keys, emails, the account name in home-folder paths and other private details are removed (names only if you list them) before any text is sent to a model. Removal works by pattern and is best effort. The text goes to your active provider, and to a judge's provider if you set one (see below).
- **Files it writes:** results go only to `HERMES_HOME/plugin-data/skilluminati/` (your Hermes folder, usually `~/.hermes`), owner-only. Run folders keep redacted copies of the tasks they tested.
- **Background work:** each run uses one background thread inside Hermes. `/skilluminati stop` ends it.
- **Chat:** when a run ends you get a popup; the verdict goes into the chat only when you click "Show in chat".
- No other network calls, no shell commands, no stored credentials.

## Choosing a judge

Out of the box the Owl is the same model you are testing, so it grades its own answers, and each result carries a "Same-model judge" flag: read it as a lean. To firm it up, use a judge from another maker (for example a GPT or Gemini model when you run on Claude). In `config.yaml`, write it as nested settings like this (one key inside another, not `judge.provider` on one line):

```yaml
plugins:
  entries:
    skilluminati:
      settings:
        judge:
          provider: openrouter
          model: your-judge-model
        harm_judge:        # optional: the harm check uses the judge above unless you set this
          provider: openrouter
          model: your-judge-model
      llm:                 # only if you keep allow-lists: every judge model and provider must be on them
        allowed_providers:
          - openrouter
        allowed_models:
          - your-judge-model
```

With a judge set, the redacted request and both answers go to that judge's provider as well as to your active provider. Without one, everything goes to your active provider only. The replayed chat includes its system prompt, so your memory and profile go with it.

Use a provider you already have set up in Hermes, and a model name it accepts. A judge on another model needs the plugin's two capabilities (`llm.provider_override`, `llm.model_override`). Hermes asks for them when you run `hermes plugins enable skilluminati` at a terminal; a scripted install does not grant them.

If you keep a list of allowed models for the plugin (`plugins.entries.skilluminati.llm.allowed_models`), the judge model must be on it. The same goes for its provider and `allowed_providers`. Hermes refuses a model or provider that is not on the list, with the error `PluginLlmTrustError`, and the judge calls fail. The `llm:` part of the example above shows both lists; leave them out if you do not keep a list.

Pick a judge that answers directly. Some reasoning models spend the whole short reply on thinking and return nothing. Skilluminati marks those pairs as not judged, and a run where the judge read fewer than half the pairs is flagged "Judge failed" and never replaces an earlier result.

The full guide, including how the judging works and what it cannot tell you, is in the [main README](https://github.com/yxssxn/skilluminati#readme).

MIT licence. See `LICENSE`.
