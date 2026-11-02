# archive-today

Reads archive.today snapshots from the sandbox without a browser. It solves the "One
more step" reCAPTCHA over plain HTTP, finds or submits a capture, and extracts the
article with trafilatura. `TROUBLESHOOTING.md` describes how archive.today's flow
works; the agent reads it only when the script names a section.

No existing archive.today CLI or MCP server gets past the captcha (archive-is-pp-cli,
wabarc's archive.is, remove-paywall-mcp and the archivetoday npm package were all
tried 2026-11-02). Generic solver MCPs return a token, but don't know archive.today's
`chk_captcha` step.

## Packaging

`python package.py` writes `archive-today.zip`; upload it under Settings → Capabilities
→ Skills. It takes `capsolver.key` (required) and `2captcha.key` (optional, the
fallback) from the repo's `keys/` and embeds them in plaintext, so the zip is a
secret. `CAPSOLVER_API_KEY` / `TWOCAPTCHA_API_KEY` in the environment override the
bundled files, which is how `dev/sandbox.sh` hands them in.

## Testing

Live only: archive.today's responses, and its captcha, are the thing under test. From
a checkout, with the keys exported:

```bash
export CAPSOLVER_API_KEY=$(cat keys/capsolver.key) TWOCAPTCHA_API_KEY=$(cat keys/2captcha.key)
A=archive-today/bin/archive_today.py
python3 $A read 'https://www.wsj.com/world/china/china-xi-maritime-dominance-ef5f5c5a?mod=hp_lead_pos1'  # existing snapshot, full text
python3 $A read --fresh https://en.wikipedia.org/wiki/Web_archiving   # submit + wait, ~2-5 min; may exit 4
python3 $A read --fresh https://en.wikipedia.org/wiki/Memento_Project --budget 15  # exit 4, then run the printed wait
```
