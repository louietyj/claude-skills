---
name: gmail-unsub
description: "Weekly Gmail unsubscribe sweep for Louie: act on the choices he made on his triage page with one-click unsubscribes, sweep Promotions and Social for new senders, judge them into tiers, and republish the page. Use for the Monday scheduled run, or when he asks to run the unsubscribe sweep, unsubscribe from what he picked, or refresh the unsubscribe page."
---

# Gmail unsubscribe sweep

Louie marks senders **Unsubscribe** or **Keep** on a triage page (an artifact
whose database holds his choices). Each run acts on those choices, sweeps the
past week for new senders, sorts them into tiers, and republishes the page.

All the mechanics run in `unsub`, never by hand in context: Gmail search
paging, raw headers, unsubscribe requests, state. Once per conversation, after
`session-init`:

```bash
bash "$(ls -1dt /mnt/skills/*/gmail-unsub ~/.claude/skills/synced/*/gmail-unsub 2>/dev/null | head -1)/setup.sh"
```

That lookup is deliberate: chat keeps skills under `/mnt/skills/`, Cowork and
cloud Claude Code under `~/.claude/skills/synced/<bucket>/`. It puts `unsub` on
PATH. **Never re-derive the path.** Run every `unsub` command from the same
directory, since its working files go in `./unsub-work/`.

## Louie's rules come first

`cfs read /drafts/gmail-unsub/README.md` and read it in full before anything
else. Its standing rules override anything here. As of writing:

- **Never send a mailto unsubscribe.** Only one-click and header web links.
- **Never unsubscribe from recruiters**, and never list them.
- **Skip anything that needs a login, a form or a click.** Don't chase it.
- **Keep isn't permanent.** A kept sender that mails again shows under
  "Previously kept" so he can flip it.

When he states a new rule, add it there, dated.

## The run

1. **`unsub state pull`** downloads the newest snapshot from
   `/drafts/gmail-unsub/state/<date>/` and prints the last run date and the
   page's artifact URL.

2. **Read his choices.** Use `ArtifactData` `list`, with that artifact URL,
   collection `decisions`, `query: {"limit": 1000}`, and
   `out_dir: "<cwd>/unsub-work/dec"`. The choices land as files, not in
   context.

3. **`unsub sweep --after <last run minus 3 days, YYYY-MM-DD>`**. The 3-day
   overlap covers a late or missed run; senders it has already seen cost
   nothing. If the sweep reports failed header fetches, rerun it once.

4. **`unsub plan`** prints how many unsubscribes are due, then one line per
   new sender to judge (key, count, address, recent subjects).

5. **Judge the new senders.** Write `unsub-work/tiers.json` as
   `{"<key>": {"tier": "...", "why": "..."}}`, with an entry for every listed
   key:
   - `junk`: promos, sale blasts, surveys, onboarding drips, win-backs. No
     account role.
   - `maybe`: depends on whether he uses the product, holds the account or
     wants the content. Product news for tools he uses, loyalty programs,
     alumni and community lists. **When unsure, use `maybe`.**
   - `keep`: transactional, security or account mail that landed in
     Promotions. Refunds, login alerts, statements, prescription refills.
   - `exclude`: recruiters and anything else a rule says never to list.

   Judge from the name, address and subjects alone; don't open the emails.
   `why` is one plain line, shown under the sender on the page. A sender sent
   from a random domain that impersonates a brand gets `junk` with a why that
   says so. To re-tier a sender he says is misfiled, add its key here too.

6. **`unsub act`** sends the due unsubscribes (4 at a time) and prints every
   one that didn't go through, with the reason. No approval is involved.
   One-click results are mechanical: a 2xx response means accepted. A header
   web link was only visited, so it prints `REVIEW` with the page's text. Read
   each one and record your verdict:

   ```bash
   unsub resolve <key> accepted                      # the page says it's done
   unsub resolve <key> skipped "needs a button click"  # anything still to do
   ```

   It counts as accepted only when the page itself confirms the unsubscribe
   and nothing is left to click. A button, a form, a login, a preferences page,
   or an empty JavaScript shell all mean skipped. Never click through.
   `finalize` refuses to run while any page is unresolved.

7. **`unsub finalize --artifact <url>`** writes the new state to
   `unsub-work/new/` and the page to `unsub-work/triage.html`.

8. **Message Louie** (`SendUserMessage` in a scheduled run). Keep it short:
   - how many unsubscribed, plus any failures and skips by name with the
     reason;
   - any senders under "Still mailing";
   - how many new senders are waiting, by tier.

   If nothing happened and nothing is new, say so in one line.

9. **`unsub state push`** saves the snapshot to `state/<today>/`. It never
   overwrites, so each run is a dated record (the attempt log is `log.jsonl`
   inside it).

10. **Republish the page last.** It is an artifact from another conversation,
    so first `Artifact` `read` it with `url` and `path: "index.html"` (that
    counts as reading it, and saves the page to a file instead of context).
    Then publish `unsub-work/triage.html` with that `url`. Omit `icon` and
    `capabilities` so the page keeps its database.

## How state works

- **His choices** live in the page's database (`decisions/<key>` =
  `{d: "kill"|"keep"}`). Only he writes them.
- **Everything else** lives in the store, one dated folder per run:
  `registry.json` (every sender seen: tier, why, unsubscribe method, newest
  URL), `status.json` (what was done to each: unsubscribed / failed / skipped,
  when, and with which URL), the sweep, a decisions snapshot, the log, and the
  page data.
- **Retries are deliberate.** A failed sender is retried only when a newer
  email brings a different URL. A skipped one is never retried. An
  unsubscribed sender still mailing more than 14 days later gets exactly one
  retry with the newest URL. After that it stays on the page under "Still
  mailing" for him to deal with.
- **Unsubscribed senders drop off the page.** One that keeps mailing past the
  grace period comes back under "Still mailing".

The key for a sender is its lowercased address as Gmail reports it. For
SimpleLogin reverse aliases that is the alias address, and the page shows the
real sender.

## What not to do

- Don't open emails or paste raw headers into context. `unsub` keeps them on
  disk.
- Don't mark mail as spam, create filters or send anything. This skill only
  sends unsubscribe requests.
- Don't redo a whole sweep's tiers. Existing tiers stand unless he asks.
- If a Gmail read comes back needing approval (`unsub` stops and says so),
  stop and report it. The connector's read tools have to be always-allow.
