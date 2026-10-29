# notifier

Scheduled announcements (a release reminder, a maintenance notice, a dated post) are already written. notifier posts them from cron or GitHub Actions when they are due. The process reads a schedule file, sends the message, records the id, and exits. It does not call a model.

An agent that wakes on a timer spends a model call to decide whether anything should be posted, including the runs where the answer is no. Point the timer at `notifier run` instead. The text is in the schedule. The clock decides.

notifier is one of the tools listed at <https://github.com/zlephir42069>. [zlegbot-wakegate](https://github.com/zlephir42069/zlegbot-wakegate) is the companion that starts an agent only when a cheap signal fires. notifier does not start an agent.

## 60-second quickstart

Requires Python 3.10+ and a POSIX system (the state lock uses `fcntl`).

```bash
git clone https://github.com/zlephir42069/zlegbot-notifier
cd zlegbot-notifier
python -m pip install -e .
cp notifier.example.yaml notifier.yaml
# Edit ids, due times, and message text. Leave secrets as ${NAME}.
export NOTIFIER_DISCORD_URL="https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN"
export NOTIFIER_DISCORD_TOKEN="replace-me"
export NOTIFIER_DISCORD_CHANNEL="123456789012345678"
export NOTIFIER_SLACK_URL="https://hooks.slack.com/services/T000/B000/XXXXXXXX"
export NOTIFIER_WEBHOOK_URL="https://example.com/hooks/notifier"
export NOTIFIER_WEBHOOK_TOKEN="replace-me"
notifier validate
notifier list
notifier run --dry-run --now 2026-12-01T16:00:00Z
notifier run
```

`python -m notifier` is the same entry point. `--dry-run` prints each decision and does not post or write state. `--now` replaces the clock so you can see what a later run would do.

With the example dates, a run on a clock before 2026-12-01 posts nothing. `notifier run --dry-run --now 2026-12-01T16:00:00Z` is inside the 24 hour grace window for `release-1-2` and reports that item only.

Put the real `run` on a timer. See `examples/crontab`, `examples/notifier.service` with `examples/notifier.timer`, and `examples/github-actions.yml`.

## Commands

| Command | What it does |
| --- | --- |
| `notifier validate` | Check the schedule. Exit non-zero with one message per problem. Does not post. |
| `notifier run` | Post every item that is due, unsent, not skipped, and still inside its grace window. |
| `notifier run --dry-run` | Print those decisions. Do not post and do not write state. |
| `notifier list` | Print upcoming, due, overdue, sent, and skipped items. |
| `notifier mark-sent <id>` | Record an id without posting. A second call keeps the original record. |

`--config` defaults to `notifier.yaml`. `--now` is an ISO-8601 timestamp with an offset, used by `run`, `list`, and `mark-sent`.

Exit codes: `0` ok, `1` a post failed, state could not be written, or the lock timed out, `2` config or usage error. A missed grace window is overdue, and that is still exit `0`. If one post fails and another succeeds, the exit code is `1` and the success is still recorded.

`list` and `mark-sent` do not need the webhook environment variables. `validate` and `run` do, because they resolve every target.

`run` prints one line per action and a summary. It does not print the message body. `posted` in a dry-run is the number of items that would have been posted. `dry_run=true` marks that line.

```
dry-run
would post release-1-2 discord
summary posted=1 failed=0 overdue=0 skipped=0 dry_run=true
```

```
upcoming:
  weekly-note  2026-12-02T09:00:00-05:00  slack
due:
  release-1-2  2026-12-01T15:00:00+00:00  discord
overdue:
sent:
skipped:
```

## Config reference

The file is YAML, or JSON when the path ends in `.json`. `notifier.yaml` is the default path. Relative paths, including `state`, are resolved from the file's directory.

| Setting | Default | Meaning |
| --- | --- | --- |
| `state` | `.notifier/state.json` | Sent ids. A real run creates the directory when it takes the lock. |
| `grace` | `24h` | How late a run can still post. The window is `due <= now <= due + grace`. |
| `lock_timeout` | `60` | Seconds to wait for the state lock. A duration such as `60s` is also accepted. `0` tries once. |
| `targets` | `{}` | Named delivery targets. |
| `items` | required | The announcements. An empty list is valid. |
| `skip` | `[]` | Ids that are never posted. Each id must be an item. |

A bare number is seconds. `24` is twenty-four seconds. `24h` is twenty-four hours. Grace is at most 3650 days. A target timeout is at most 86400 seconds. Retries are from 1 to 20.

`${NAME}` anywhere in a string is replaced from the environment when `validate` or `run` loads the file. A referenced variable that is unset is a config error. `$${NAME}` becomes a literal `${NAME}`. Values substituted into URLs, tokens, and headers are redacted from stdout and stderr. Incoming-webhook URLs are redacted even when they were pasted as literals.

Quote timestamps and Discord ids. Unquoted ids can be rounded by YAML. Unquoted timestamps may become datetime objects; a value with an offset is accepted either way, and a value with no offset is a config error.

`notifier validate` checks the file and exits non-zero with one message per problem. It does not contact the network.

### Items

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stable id, matched by `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`. A sent id is never posted again. Editing the message later does not send a correction; use a new id. |
| `due` | yes | ISO-8601 timestamp with a timezone offset. `Z` is UTC. There is no civil-time zone name: a recurring reminder is several items. |
| `message` | yes | Template. `{id}` and `{due}` are always available. Other names come from `values`. |
| `target` | yes | A name from `targets`, or an inline target mapping. |
| `grace` | no | Overrides the top-level grace for this item. |
| `values` | no | Placeholder values. Strings, numbers, and booleans. Environment values inserted with `${NAME}` are strings. |
| `mentions` | no | Mention policy. Default is no pings. |

Unknown keys are config errors.

### Targets

| Type | Required fields | What is posted |
| --- | --- | --- |
| `discord` | `url` | Incoming webhook `{"content", "allowed_mentions"}`. |
| `discord_bot` | `token`, `channel` | `POST https://discord.com/api/v10/channels/{channel}/messages` with `Authorization: Bot <token>`. `channel` is a numeric id. |
| `slack` | `url` | Incoming webhook `{"text"}`. |
| `webhook` | `url` | `POST` JSON `{"id", "due", "message", "mentions"}`. Optional `headers`. |

Each target also accepts `retries` (default 3), `backoff` (default `0.5` seconds, doubled after each failure), and `timeout` (default 30 seconds). Redirects are not followed. A 3xx is a failed post, so a token is not replayed to another host.

Use `discord` for a Discord incoming webhook. `webhook` is a raw JSON POST and does not set Discord's `allowed_mentions`. The generic `mentions` object tells that receiver what the item allowed. The receiver has to enforce it.

Discord content longer than 2000 characters is a config error, and so is Slack text longer than 40000 characters or a generic webhook message longer than 100000 characters. The check uses the text that would be posted, after Slack mass-mention tags are replaced.

Retries repeat the same body. A timeout after the server has already accepted the post can deliver it twice; the state file is written only after a completed response. A crash in the gap between that response and the state write can also deliver it twice on the next run. The state write is an `fsync` and `os.replace` before the next item is posted.

### Mentions

The default is no mass mentions.

Discord and `discord_bot` always send `allowed_mentions`. The default is `{"parse": []}`. `@everyone`, `@here`, role pings, and user pings in the text do not notify anyone. The text is not rewritten.

Set `mentions: true` to allow `users`, `roles`, and `everyone`. Or set a mapping:

```yaml
mentions:
  parse: ["users"]
  users: ["123456789012345678"]
```

`parse` accepts `users`, `roles`, and `everyone`. An explicit `users` or `roles` list is sent as that list and removed from `parse`, because Discord rejects a type that appears in both. Quote the ids.

Slack does not use those ids. With the default policy, `<!everyone>`, `<!channel>`, `<!here>`, and `<!subteam^...>` in the message are replaced with the words `everyone`, `channel`, `here`, and `group` before the post. Other text, including `<@U...>` user mentions and `<!date^...>`, is unchanged. Put `everyone` in `parse` (or set `mentions: true`) to keep the mass-mention tags.

### Templates

Placeholders are `{name}`, the same simple form as `str.format`. `{{` and `}}` are literal braces. `{id}` and `{due}` are the item id and the due timestamp. `{name.attr}`, `{name[0]}`, and `{name!r}` are rejected. A format spec such as `{count:d}` is allowed when the value is a number in the file. A value brought in with `${NAME}` is a string, so a spec that requires a number will not apply to it.

Environment substitution runs in `values`, URLs, tokens, and headers. It does not run in the message text. Put `${APP_VERSION}` in `values` if the text needs it.

## State, grace, skip

State is a JSON object:

```json
{
  "sent": {
    "release-1-2": {
      "at": "2026-12-01T16:00:00+00:00",
      "target": "discord",
      "via": "post"
    }
  }
}
```

`via` is `post` or `mark`. Timestamps in the file are to the second. The file does not contain URLs, tokens, or message bodies.

An item is:

- **upcoming** when `now < due`
- **due** when `due <= now <= due + grace`, and it is not sent or skipped
- **overdue** when `now > due + grace`, and it is not sent or skipped
- **skipped** when its id is in `skip` and it has not been sent
- **sent** when its id is in the state file (`sent` wins over `skip`)

`run` posts due items only. Overdue items are listed and left unsent, so a reminder does not go out days late. `mark-sent` is how you close one without posting. Deleting an id from the state file arms it again if the window is still open.

The skip list is for items that should stay in the file and never post. A skipped item that is inside the window is printed by `run` as `skipped`. A skipped item that is upcoming or overdue is quiet on `run` and visible in `list`.

A `fcntl` lock on `state.json.lock` is held for the whole non-dry run, so two cron runs do not both post the same id. If the lock is not acquired within `lock_timeout`, the run exits `1` and posts nothing.

## Scheduling

| File | Use |
| --- | --- |
| `examples/crontab` | One cron line, every 15 minutes. |
| `examples/notifier.service` and `examples/notifier.timer` | systemd oneshot plus a 15-minute timer. |
| `examples/github-actions.yml` | Scheduled workflow that commits `.notifier/state.json`. Copy it into `.github/workflows/`. |

Make `grace` longer than the gap between runs. A timer that is down longer than the grace window leaves the item overdue and does not post it.

The GitHub Actions file is the scheduler when you do not want a machine left on. It checks the clock, posts, and pushes the state file. Scheduled workflows can start later than the cron expression; the grace window is what absorbs that. The sample workflow does not run on `push`, so the state commit does not start another notifier run.

`.gitignore` ignores `.notifier/`. The workflow force-adds `state.json` only. State holds ids and timestamps.

## How it saves credits

A cron entry that launches an agent pays for a model call even when no announcement is due, and pays again when the announcement is only a sentence you already wrote. `notifier run` reads the schedule, and the only network calls are the posts for items that are due and unsent. There is no model call on the empty runs, and there is no model call on the run that posts.

`notifier list` and the `summary` line show how many items were posted, skipped, or overdue. Those are counts of items in that run. notifier does not turn them into a price.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -v
```

See `CONTRIBUTING.md`. Tests are offline. The license is MIT, copyright zlephir42069.
