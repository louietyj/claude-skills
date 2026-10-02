# polish-message

Drafts emails and messages Louie sends under his own name, in his voice, through a forced two-pass process. The equivalent of `polish-for-pr`, for prose instead of diffs.

```
SKILL.md   the process: gather, throwaway draft, rethink, final
voice.md   his writing voice, cat'd by SKILL.md on first use
```

## Why the throwaway draft

A model can't seriously revise prose it has just written in the same reply. Once v1 is in the response, the coherent continuation agrees with it, and "review" becomes word swaps. The draft therefore goes into a `cat > /dev/null` heredoc. The tool call ends the message, v1 returns as tool input rather than the model's own text, and the next message opens with a fresh thinking block, where the actual cutting happens. v1 is never shown.

## voice.md

Distilled from about ten years of his sent Gmail, with forwards and one-liners filtered out. Mail from 2017–2024 carries the most weight, because his longer emails since about 2025 often start as Claude drafts that he then rewrites.

The repo is public, so every example is paraphrased, with names, identifiers and medical, legal and financial details swapped out. A feature-by-feature description invites the drafting model to apply every feature at once, so the file says up front to pick traits by register and never reuse an example's wording.

## Package for claude.ai

`python package.py` writes `polish-message.zip`; upload it under Settings → Capabilities → Skills. No credentials, no dependencies on other skills.
