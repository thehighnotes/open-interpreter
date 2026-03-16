# Changelog

All notable changes to the Hub Tools suite are documented here.

---

## 2026-03-15

### Fixed — WebUI markdown rendering (CSS selector bug)

**Problem**: All text in the WebUI chat appeared visually "stacked" — no paragraph
spacing, no heading margins, no list indentation, no horizontal rule gaps. The
terminal OI rendered the same LLM output correctly.

**Root cause**: A single-space CSS selector typo caused every markdown styling rule
to be dead CSS. The chat bubble element carries both classes on the same `<div>`:

```html
<div class="msg-bubble markdown-content">...</div>
```

But the CSS used **descendant selectors** (space = "child inside parent"):

```css
/* WRONG — looks for .markdown-content INSIDE .msg-bubble */
.msg-bubble .markdown-content p { margin: 0 0 1em; }
```

This never matched because both classes are on the *same* element, not nested.
All markdown elements (`<p>`, `<h3>`, `<ul>`, `<hr>`, `<blockquote>`, etc.)
fell through to the global reset `* { margin: 0; padding: 0; }` in
`design-system.css`, collapsing all visual spacing to zero.

**Fix**: Changed all selectors to **compound selectors** (no space = "element
with both classes"):

```css
/* CORRECT — matches element that has BOTH classes */
.msg-bubble.markdown-content p { margin: 0 0 1em; }
```

Applied across all 20+ markdown rules in `webui.css`.

**Also changed**: `marked.parse()` option `breaks: false` → `breaks: true` in
`chat.js` so that single `\n` in LLM output renders as `<br>` (matching
terminal behavior where Rich renders each line individually).

**Files changed**:
- `tools/hub/webui/static/css/webui.css` — all `.msg-bubble .markdown-content` → `.msg-bubble.markdown-content`
- `tools/hub/webui/static/js/chat.js` — `marked.parse()` breaks option
