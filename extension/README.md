# Sourceror for Chrome

A Chrome side panel that checks the sources of **any article**: news, blog
posts, reports, and Wikipedia entries in every language. It is the browser
front end of [Sourceror](https://github.com/sirmmo/sourceror), which does the
checking and must be running on your computer.

> This repository is a mirror of the `extension/` folder of
> [sirmmo/sourceror](https://github.com/sirmmo/sourceror). Open issues and pull
> requests there.

## Install

1. Start the Sourceror server (see its README). By default the extension talks
   to `http://127.0.0.1:5010`; the address can be changed in the panel's
   settings (⚙).
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick this folder.
3. On any article, click the Sourceror icon (or press **Alt+Shift+S**), then
   **Check this page's sources**.

Chrome 116 or later is needed.

## What it does

- Extracts the article body as you see it, with Mozilla's
  [Readability](https://github.com/mozilla/readability), so paywalls, cookie
  walls and bot blocks don't get in the way.
- Judges every outbound link and every DOI in the text (WP:RSP, CITEWATCH,
  retractions, dead links), ignoring share buttons, navigation and tracking
  parameters.
- Lists the article's checkable claims as *linked*, *linked to a weak source*,
  *attributed* or *unsourced*, and suggests scholarly sources for the last two.
- Marks the page: a coloured dot after each judged link and a highlight on each
  problem claim. Clicking a mark selects it in the panel and vice versa.
- On Wikipedia it colours the reference list and the `[1]` markers and
  highlights paragraphs without notes.

Sourceror judges *who* published a source, not whether the source says what the
article claims.

## Privacy

The page is read only when you click the button. Its text and links go to the
Sourceror server you configured, and nowhere else. The extension asks to read
all sites because it has to work on whatever article you open.

## Licence

`vendor/Readability.js` is © Mozilla, Apache License 2.0
(`vendor/Readability-LICENSE.md`).
