# intercom: you were woken by a button

Louie pressed a button (the watch's `/launch` shortcut, the landing page's
"Talk to Claude", or his phone's long-press) and the relay did the rest
before you existed: it opened a **talk** room, connected his audio (the
watch page, or a phone call to him), and fired your routine with the room's
id. He has probably been talking since. So, unlike a room you start:

- **Don't `intercom start`, and skip keyterms.** The room exists, with his
  standing keyterm list. `intercom join <id>` makes it yours; it creates
  nothing.
- **`join` hands you what he has already said** (it waits a few seconds for
  a sentence he is finishing), and its `next` says what to do. Do it in your
  very next response: `speak` and arm Monitor on `intercom follow --since <n>`
  as parallel calls. Answer the backlog as one moment: the last thing he
  asked is what he is waiting on. Every extra turn before your first `speak`
  is seconds of him waiting in silence.
- **You don't know which device he is on, and don't need to.** The device
  line says, if it ever matters.
- **Ending:** when `follow` reports the session ended (he tapped STOP or hung
  up, and the room sat empty for 5 minutes), write one line in chat with
  anything agreed or left open, then end your turn. Don't redial, restart or
  stop the room yourself.

What follows is the talk-mode part of the full guide, as written. Listen
mode, keyterms and the gate are left out: a button never opens those.
