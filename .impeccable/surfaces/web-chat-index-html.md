---
version: 1
slug: "web-chat-index-html"
primary_target: "web/chat/public/index.html"
related_targets: ["web/chat/public/chat.css","web/chat/public/chat.js"]
---

# Chat surface

Mode: operate. Target: web/chat/public/index.html. User delegated visual decisions and requested a simple fast chat, then two separate sites: API console and user chat.

## Direction contract
THESIS: The conversation is the workspace; documents provide evidence without competing with the question.
OWN-WORLD: User-requested monochrome chat: neutral gray navigation, white/graphite reading surfaces, black/white controls, Segoe UI text, compact source tabs. Distinct jobs use familiar web controls.
STORY: Connect with a personal key, ask, read, inspect the exact fragment, continue or return to history. Keep technical API operations on the other site.
FIRST VIEWPORT: 288px neutral sidebar with brand, new conversation and date-grouped history; unnamed chats in a collapsible section, account and documents fixed below history; 88px header; central welcome with three real prompts; bottom composer. Source panel opens at right on demand. Session, selected conversation and per-conversation drafts restore on reload.
FORM: User explicitly requested a plain black-and-white design on 2026-10-23. Preserve the chat layout and controls; use only achromatic colors in both themes. Code-led app surface, no comp or raster assets.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
