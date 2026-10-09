---
name: polish-message
description: "Draft or polish an email, text, or chat message that Louie will send as himself, in his voice, through a mandatory two-pass process: a throwaway first draft inside a discarded tool call, then a real rethink, then the final. Use whenever you write anything he'll send under his own name: an email, a reply, a follow-up, a complaint, a refund request, a text, a Slack or WhatsApp message. That includes when he pastes his own draft and asks you to tidy it, and when he never says 'polish'. Not for documents, posts, or anything published."
---

# polish-message

Anything Louie sends under his name goes through this. Two passes, and the
order is the whole point: **draft, discard, rethink, then write the
final.** Never one-shot it.

He edits and compacts every draft before sending. Your job is to get him
90% of the way: the right structure, length, register and facts, so his
pass is a trim, not a rewrite.

## 0. Load his voice

Once per conversation, before the first draft:

```bash
cat "$(ls -1dt /mnt/skills/*/polish-message ~/.claude/skills/synced/*/polish-message 2>/dev/null | head -1)/voice.md"
```

Read all of it. That file is the authority on how he writes. Everything
below is about process.

## 1. Gather

Before drafting, know:

- **Who it's to,** and how well he knows them. That picks the greeting
  and how warm to be.
- **What it's replying to,** if anything. Read the thread if he gave you
  access (Gmail connector, a paste, a screenshot).
- **The ask:** what he wants the recipient to do.
- **The facts:** ticket and booking numbers, amounts, dates, names.

Ask only about what changes the message. Missing an identifier you can't
find? Put a placeholder like `<booking #>` in the draft rather than ask
or invent one.

**If the message comes out of a longer conversation,** reuse his framing
and terms from that conversation: the categories he named, the way he
described the problem, the position he settled on. Don't swap them for
your own words. The message should sound like the conclusion of that
discussion.

**If he pasted his own draft,** the job is to edit his draft, not rewrite
it in yours. Keep his phrasing wherever it works; cut, reorder and fix.
His wording beats yours when both say the same thing.

## 2. First draft, inside a discarded tool call

```bash
cat > /dev/null <<'EOF'
<subject line, if it's a new email>

<full message, your honest best attempt>
EOF
```

**Why a tool call:** you can't seriously revise prose you've just written
in the same reply. The next tokens agree with it, so a "review" turns into
small word swaps. A tool call ends the message, v1 comes back as tool
input rather than your own text to continue, and the next message starts
with a fresh thinking block.

v1 must be a real attempt in his voice. A deliberately weak draft written
so step 3 has something to find defeats the purpose.

## 3. Think, then cut

**This thinking block is mandatory.** It's the only point in the process
where real reconsideration happens. In it, list every change before you
make any, starting with the biggest units:

1. **Whole paragraphs and sentences first.** For each one, say what the
   recipient loses without it. "Context" or "politeness" isn't an answer.
   If nothing comes to mind, cut it.
2. **Is the ask in the first two sentences?** If it sits after the
   backstory, move it up.
3. **Check the voice both ways.** Is anything on the "doesn't sound
   like" list in `voice.md` (greeting, sign-off, filler phrases, em
   dashes, stacked hedges)? And is it overdone: traits piled on beyond
   what this recipient calls for, or wording lifted from the examples in
   `voice.md`?
4. **Check the facts:** every identifier, amount and date he gave you is
   present and correct, and the recipient won't have to ask "which one?".
5. **Words last.**

His messages are short. Your v1 is almost certainly too long. Assume the
right length is a fraction of it, and keep cutting until the next cut
would remove information. A final that reads like v1 with a few words
changed means you started at the word level. Start again at step 1.

If extended thinking is off and there's no thinking block, write the cut
list into a second discarded heredoc instead. Never write it in your
reply.

## 4. Deliver the final

- Put a plain-text final in a fenced `text` block so it's one tap to copy
  on mobile. If it uses bold or lists, render it as normal markdown
  instead, so the formatting survives the paste into Gmail. Put the
  subject line above it, if there is one.
- **No signature, no name.** His Gmail adds one.
- **Never show v1.** Not in your reply, not as before-and-after, not as
  "here's what I cut". He wants the final.
- After the message, at most one line: placeholders he needs to fill in, or
  an assumption he should check. Nothing else.
- If he asked you to create the Gmail draft or send it, use the final
  text for that. Sending still needs his explicit go-ahead.

## Revisions

A small change he asks for ("make it firmer", "drop the second
question"): just make it and show the new final. A substantive rewrite
(new angle, new recipient, a big new fact): go through steps 2–4 again.
