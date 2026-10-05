# phomymo-link

Turns a Phomymo label template plus some text into a link that opens the finished label at
[phomymo.louietyj.me](https://phomymo.louietyj.me), Louie's fork of
[Phomymo](https://github.com/transcriptionstream/phomymo), ready to print on his M110.

```
SKILL.md      when to use it, where templates live, how to write label text
make_link.py  template JSON + Field=value pairs -> link (stdlib only)
```

## The link format

`#design=v1.<base64url(deflate-raw(minified design JSON))>`. The fork decodes it with the browser's
`DecompressionStream('deflate-raw')` and hands it to the same code as Import from File, which replaces the
design on screen and saves nothing. The design rides in the fragment, which browsers never send to the
server. A text-only label comes to about 600 characters; `v1` leaves room to change the encoding.

## Templates

Not in this repo, since some carry personal details. They sit in Louie's Dropbox under
`/Phomymo Templates/`, as JSON exported from Phomymo, and the skill pulls them through the Dropbox
connector. `{{Field}}` placeholders are filled by
`make_link.py`; `[[...]]` expressions (dates) are left for Phomymo to fill at print time.

## Package for claude.ai

`python package.py` writes `phomymo-link.zip`; upload it under Settings → Capabilities → Skills. Needs
the Dropbox connector for the templates.
