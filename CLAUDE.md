# Working rules

## One step at a time: approve, then go on

Never start the next step until the result of the previous one is approved.
Expensive steps (a full render, a dub of the whole clip, a batch over many
clips) run only after a cheap preview of what they will produce has been
shown and accepted.

Order for a clip:

1. **Layout**: a still frame (or two) of the header and footer — logo, art,
   taglines, the video window. Wait for "yes".
2. **Translation**: the table of lines (the web interface's review step).
   Wait for "yes".
3. **Voice**: a short sample of the chosen voice on a line of the clip.
   Wait for "yes".
4. **Render** the whole clip, check it (Cyrillic leaks, header, audio level),
   then send it.

If a step changes after approval (a new logo, a different header), go back
to its preview before rendering again.
