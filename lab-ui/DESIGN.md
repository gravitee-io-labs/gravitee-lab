# Lab UI design

How the Lab UI looks and behaves, and why. [AGENTS.md](AGENTS.md) has the rules for changing its code,
[docs/lab-ui.md](../docs/lab-ui.md) says what it does for a person. Change this file with the behavior it describes.

## The design system

Every page is built from the tokens and components below, and from nothing else. A page builder reads this part first; the
parts after it say how each page uses them. The [gallery](tests/web/gallery/README.md) draws every component in every tone and
state, light and dark side by side, from the page's own code: look at it at 2x zoom after any change to `web/css` or to a
component of `ui.js`.

### Principles

- **Calm surfaces.** Neutral cards on a warm page, hairlines rather than boxes, color only where it says a state. The accent
  is for the one primary button of a page and the current page of the menu.
- **One component for one kind of thing.** A status is a pill; a state is a dot and a word; an explanation is a popover; a
  fact about the whole lab is a notice. The same thing never has two looks on two pages.
- **One place per fact.** A fact is said once where it is used: the platform's state is the sidebar's chip, on every page, and
  no head repeats it; the Dashboard's head has the Apply badge and button, so the banner leaves them out there; a group of
  Containers says Apply needed, its containers say their version; a notice's summary is not repeated as its popover's title.
- **Explanations are opened, not printed.** No helper sentence on the page: a popover may explain, and the badge or the word
  that opens it carries an information mark.
- **Measured, not judged by eye.** Heights, paddings, alignment and contrast are measured in the DOM (below).

### Tokens

All in `:root` of `web/css/base.css`, redefined for dark under `prefers-color-scheme` and `[data-theme="dark"]`. A value in a
stylesheet is a token; a literal is a fault, but for a one-off geometry (a chart's size, a column of one grid).

| Group | Tokens | Values |
|---|---|---|
| Space | `--sp-1` ... `--sp-6` | 4, 8, 12, 16, 24, 32 px. `--pad` (16) is a card's inner edge, `--gap` (16) between blocks |
| Type sizes | `--fs-1` ... `--fs-5`, with `--lh-1` ... `--lh-5` | 12/16 caption (column heads, pills, tooltips, chips), 13/18 secondary (notes, descriptions, popovers), 13.5/20 body (names, controls), 15/22 a card's title and a figure, 20/28 a page's title. A guide's long text keeps its reading scale (`.guide`) |
| Weights | `--fw-1` ... `--fw-3` | 400 regular, 550 medium (controls, pills, labels), 620 strong (names, titles) |
| Mono | `--mono` | Versions, tags, settings' names, identifiers, ports: in a `.chip` inside a popover |
| Surfaces | `--bg`, `--surface`, `--surface-2`, `--surface-3`, `--border`, `--border-strong`, `--field-line` | The page, a card, two quiet fills, two hairlines, the edge of a field (its only boundary: 3.3:1 on `--surface`, 3:1 on `--bg`, 3.4:1 dark) |
| Text | `--text`, `--text-2`, `--muted`, `--accent-ink` | Primary, secondary, muted (4.7:1 on `--surface-3` light, 4.9:1 dark), the accent as text (a link hovered, a choice: 5.4:1 light, 5.0:1 dark) |
| Accent | `--accent`, `--accent-2`, `--accent-soft`, `--accent-fill`, `--accent-fill-2` | The brand, for icons and soft fills; the two ends of the primary button's gradient, on which white is 5.5:1 and 4.5:1 |
| Highlight | `--hl`, `--hl-text` | What a search found in a log line |
| Status | `--ok`, `--warn`, `--err`, `--info`, each with `-soft` and `-line` | Foreground, background, line. Neutral is `--text-2` on `--surface-2` with `--border-strong` |
| Radii | `--radius` 14, `--r-pop` 10, `--r-control` 8, `--r-inner` 6, `--r-pill` 999 | A card; a popover, a menu, a notice; a control; a choice inside a control, a tooltip; a pill |
| Elevation | `--shadow-sm`, `--shadow`, `--shadow-md`, `--shadow-lg` | A control, a card, a popover or a menu, a dialog |
| Heights | `--h-control` 32, `--h-inner` 26, `--h-pill` 24, `--h-option` 36, `--h-head` 32, `--h-row` 56, `--h-row-2` 48, `--h-row-3` 40 | A control; a choice in one, a row's icon; a pill; an option of a list that opens (a picker's option, a menu's entry); a table's head; its rows by kind |
| Fields | `--field-pad` 12 | The left side of a picker: a value shown as text among pickers starts where theirs do (`.field-text`) |
| Veils | `--row-hover`, `--backdrop` | The hover of a row of every table (`--surface-2` at 70 %); the veil under a dialog |
| Motion | `--dur-1` .12s, `--dur-2` .2s, `--dur-3` .6s, `--delay` .15s, `--ease` | Hovers, popovers and menus; larger moves (a chevron, the menu's width); the glide of a measure (a progress bar, the fill of a button at work); the wait before a bubble opens on hover. Nothing moves under `prefers-reduced-motion` but a slow spinner. A pulse and a spinner keep their own beat |

Contrast of text, measured: `--ok` on `--ok-soft` 4.6:1 light, 8.2:1 dark; `--warn` 4.8 and 7.2; `--err` 6.0 and 5.7; `--info`
5.4 and 8.1; `--muted` on `--surface` 5.8 and 5.6.

### Components

Each has one class and, when it is markup, one function of `web/js/components/ui.js`. Use it as it is; when a page needs more, extend the
component here, never with a variant of its own.

**Pill** (`.pill`, its tones `.ok .warn .err .info`, none for neutral). A status or a label: *Running*, *Personal*,
*Connected* (an account, in the head of its card), *Apply needed*, the license in words (*Community Edition*, *Enterprise Edition* and
its tier, *License not saved*; never an acronym). 24 px high, 10 px at its sides, 6 px between its parts, 12 px
medium, a 1 px line of its tone. A trailing information mark when it opens an explanation (then the pill is the button: a hover a
shade deeper), an arrow when it leads somewhere (its fix, the license), an icon alone in `.icon-pill` (24 px round). `.state` is
the pill with a leading dot, the platform's chip of the sidebar (in a target of 32 px), pulsing while on its way (`.busy`). Do: one
word or a short phrase. Don't: a sentence in a pill, two pills that say one thing, a pill for what is off or stopped (that is a
muted word), a pill of another height or weight.

**State** (`statusHtml(dot, word, {tip, quiet, extra})`, `.status`). What something does, on a line: a dot of `.dot-status`
(running, starting, stopping, stopped, off, issue, warn; no class while it is not known) and its word, muted while it is off
or stopped (`quiet`), amber or red while it warns or fails, the dot in the same tone as the word (a muted check, `done`, for what
needs nothing: *Up to date*). A tooltip or a popover (lines, or a card) adds what is known, with no mark: the word is underlined
while hovered or focused. Used for a product and its update (*Up to date*, *Update available*), an environment's gateway and its
deploys, an account that cannot be read (a row of the Dashboard: why is its popover, never a sentence beside it), an item of the
catalog (the State of the lists and of the Dashboard). Don't: a pill on one row and a dot on the next for the same kind of state, a
green dot beside a word that warns, a second line under it, an icon and a word of its own (an arrow and a version) for a state.

**Tooltip** (`tipHtml(inner, line)` with one short line; `data-tip` on a control). A label or one line of 40 characters at most
(`LINE` in `ui.js`): a dark bubble (`--text` on `--bg`, inverted in dark), 12 px, 6 by 10 inside, 28 px high, on one line that
never wraps, an arrow toward its button. Anything longer is a popover, whichever way it is given: `tipHtml` and the shared bubble
decide by the length. It opens on hover after `--delay`, at once on keyboard focus and on a tap; it is placed in the window so
that no card clips it, and its own box clips nothing (its line is cut inside it, its arrow and the bridge to its button are outside
it), so the pointer can go from the button into it. Escape closes it while the pointer or the focus stays (WCAG 1.4.13), and that
alone: in a dialog the next Escape closes the dialog, and what was typed stays. A resize of the window closes it. `tipHtml` makes its own button,
which carries the line as its name for a screen reader (the bubble is `aria-hidden`); `short` names it by its label alone and makes
the explanation its description (a column's head). A control that acts on a click (a note badge that leads to its fix, the cloud
mark, the sidebar's chip) takes `data-tip` instead, and `data-tip-card` for a card: one shared bubble shows it on hover and focus,
never in the way of the click, and is its `aria-describedby`. `data-tip-cut` shows it only while the text is cut (a description),
`data-tip-collapsed` only while the menu shows icons (the name of an item of the menu). Don't: the browser's `title`, which waits a
second, ignores the theme and the keyboard: the name of an icon button and the full text of a name a row cut are each its `data-tip`,
the reason a control cannot be used its `data-why` (below). Nothing of the Lab UI has a `title`, and `test_static.py` refuses one.

**Popover** (`tipHtml(inner, lines)` with several lines or one long one, or a card `{title, text, rows, foot, columns}`; `.tip-card`).
An explanation. A card of `--surface` with a 1 px `--border-strong` line and `--shadow-md`, as wide as its content between 260 and 460 px, 12 by 16 inside, 13 px,
8 px between its parts: a title (medium, `--text`; of several lines the first is the title unless `title: false`, a list of
equals), a sentence (`text`, `--text-2`; one long line alone is that sentence, and a title with one sentence is a title over it,
with no hairline: a term and what it means; under a field's label, which says the term, the sentence alone), rows under a hairline,
a foot under a hairline (what the action will do). **The rows of a card are one table of two columns, the whole card's**: first the components a line is about, one to a line (a row's subject, a product, is a heading over both columns, between hairlines), then why and in what. A leading word (*Settings changed*) is muted over its chips, which wrap; a version that runs and the one saved (`{versions}`) are side by side, each after its caption (*Running* and *Saved*, or `columns`). Both columns are the card's, so what a line says starts at one place, and a long name wraps in its cell: nothing is cut. a version saved that is older than the one that runs (`older`) is a chip
in the warn tone, *(older)* for a screen reader. Other lines take both columns: their leading words (`{lead}`: "Settings changed", "Uses",
"Update available") muted in a column of their own, then what follows them (settings' names as mono `.chip`s) wrapping under its
first part, never under the leading words; after whom a line is about, where the room is narrow, the leading words stand over what
follows them, so that no chip is cut. A sentence of the card never ends on a word alone (`text-wrap: pretty`). On a phone whom a line
is about takes a line of its own. Taller than the window allows, its body scrolls inside (`.pop-body`), and a scroll inside it keeps it open. The same opening,
placing, hover bridge and Escape as the tooltip; a tap toggles it, a tap elsewhere, a scroll of the page, a resize or leaving the
page closes it.

**Apply needed** (`applyBadge(reason, cls, causes)`, `behindWhy(platform)`). An amber pill with an information mark; its
name for a screen reader is the server's sentence (`note_title`, `reason`), its popover is drawn from the server's causes
(`note_causes`, `platform.behind[].causes`): *Differs from what is saved*, a line for each cause (the versions under *Running* and
*Saved*), *Apply restarts it with what is saved.* (*them* when the causes are about several services; *, older versions included*
when a version saved is older than the one that runs: Apply goes back). The words are neutral: what runs may be older or newer than
what is saved. The head's badge has *N containers differ from what is saved*, a row for each product, add-on or playground, and the
same foot. Without causes the reason is its explanation, a tooltip when it is short. A badge that stands in the place of another
word (a product's Status, whose newer version it would hide) says that word as a last line of its card (`more`: *Update available*
and the version), and to a screen reader.

**Notice** (`noticeHtml({tone, title, text, action})`, `.notice`). A fact about the lab as a whole, above the page (the banner):
its icon in its tone (`warn`, `err`, `info`, `calm` for a fact that is no problem), its headline in strong `--text`, a short
summary in `--text-2` that may open a popover (its mark follows its last word, wherever the words wrap; the summary is the start of
its name for a screen reader, so that a voice that says it finds it), its action at the right: the platform's Start and Apply are the
primary, as they are in the heads that have them. 44 px with its 32 px button, 14 px at its left, a 10 px radius, the soft fill and
the line of its tone; its icon, its words and its button stand on the line of its headline, however many lines the words take.
Under 520 px the words take the whole width and the button goes on its own line, at its right, at its size. Do: a count in the
summary ("12 containers differ from what is saved"), the detail in its popover. Don't: names as underlined links in a row, a notice
for what a page's head already says (the Apply notice is not drawn on Containers, the Dashboard, Images and Plugins: a page has one
Apply).

**Table** (`.list-head`, `.stack-head`, `.dash-heads`, `.plat-heads`, `.table th`; `.stack-sep`; rows of each page). The card
tables of every page are one family: the head of the columns is 32 px of caption (12 px, medium, `--muted`, sentence case, never
capitals), `.table th` too; a group of rows opens with a band of 40 px (`.stack-sep`: `--surface-2` at 60 %, a hairline under it,
the core's line of Containers and the third-party section of Images included); rows are 56 px (an item with two lines, a product,
an environment, the Dashboard's row of Gravitee Cloud: the Dashboard's table and the Gravitee Cloud page's have one rhythm), 48 (a group, a
quiet line that holds a 32 px button: 8 px around it, a hybrid gateway of the Gravitee Cloud page) or 40 (a container, a quiet line with none), cells centered on the row, a hairline between rows, one hover
for every table (`--row-hover`). A cell is one line: what else is known of it is in its popover. The first column starts at `--pad` on every
table; a name with a dot starts after it, and a name with none starts under its head, but on the Dashboard, where the row of Gravitee
Cloud stands under the products, its name keeps the room of a product's dot, and so does the head of the first column, so that every
name and its head start at one place, a product's dot in the margin before it. Versions and ports are mono and left-aligned, a version without the system its image is built on (`-debian`, `bareTag`; the
whole tag in its tooltip), in the color of every version (what differs from what is saved is the badge's to say); figures are
tabular. A value that cannot be chosen in a column of pickers (a version of Plugins, the registry of a third-party component) is
the value of a field without its box (`.field-text`): it starts where the pickers' values do (`--field-pad` and their line) and has
their type. A cell cut with an ellipsis says itself whole in a tooltip. Under 640 px of card a row
stacks its cells and the heads go. On a phone (under 520 px of page) a row of Containers is two lines, its name with its badge, which wraps under it, and its
state, then its icons under the name; Images and Plugins keep their columns and scroll sideways inside their card. The name of an
item that leads to it (`.name-link`) reads as a name and is underlined only while it is hovered or focused, on every page.

**Metric strip** (`.dash-resources`, `.dash-cell`). Folded by default to one line, 36 px with the edge of its card (`.dash-res-head`):
its toggle and its name, a heading (`h2` holding the button, with the chevron of the rows that open, `aria-expanded`, `aria-controls`),
then the figures in words, each its label muted and its value (`.dash-res-item`), the versions of Docker muted at its end while the line
has 720 px; the whole line is clickable, its toggle the stop of the keyboard, its ring the line's. What does not fit goes on a second line, under the toggle: two lines at most, at 375 px too. Unfolded (`.open`, quickly, at once with reduced motion; hidden from the keyboard and a screen
reader while folded), the line keeps its toggle and its name, and under it the strip, a row of measures, 64 px: a cell for each, apart by hairlines, its label
(12 px) over its figure, one markup in every cell (`.dash-figure`: the value, 15 px strong, then what it is of, muted, on its
baseline), its chart filling the rest of the cell (16 px from its words and from the cell's edge), all centered on the
strip's middle; a measure that changes slowly is a figure with no chart (Storage, as wide as its words); the versions of Docker are a last cell in the same markup (the engine's version as its value, Compose's muted after
it), which takes what is left. Cells of one kind have one width (CPU and Memory 330 px);
a chart is its line and its end dot, with no fill under it, between a guide at the top of its scale and one at zero, with the numbers of
that scale beside it (`.dash-axis`: the top, written as it changes, and 0, in 12 px muted) and its tooltip ("Last 60 seconds, 0 to 12%").
Cells of a chart are 330 px at least so that a line has room to be read. Narrower than 1260 px of page: the two charts share a line, as wide as
it lets them, with Storage and Docker under; under 640 px a cell over the other. A browser keeps the choice (`dash-resources`); folded, the strip draws no chart.

**List row and note badges** (`list.js`, `noteBadge(x, fix)`, `itemState(x)`, `itemButton(x)`). A catalog row: its toggle (the
chevron, a button named for the item, which the keyboard reaches and which says whether the detail is open; the row itself is no
button, so that its controls are controls of their own, and a click anywhere on it but its controls opens it too), its name
(13.5 strong), its star right after it (a hollow one shows under the pointer, in its place: nothing moves), then what it is
(*Personal*, the cloud mark), the description under the name as one muted line, then its State (`itemState`: the state component,
*Running*, *Stopped*, *Off*, then the server's note as a badge, on one line, the badge cut before the state is: the row says its state
in one cell, as the Dashboard says it on one line), its category and its actions in fixed slots: *Open*, its Start or Stop (`.act`, one
width, its word on it, named for its item; Start and Stop have their icon, Turn on and Turn off none, as they start and stop
nothing; never another action in its place: an add-on that lacks settings keeps its Start, waiting for them with their names as its
tooltip, or its Stop while it runs; Turn on and Turn off say in their tooltip what they change, `TURNS`), its files and its
menu. A note is never in the description: it is a pill in its tone with a
short label; one that leads to its fix is a quiet pill-button with an arrow (an item, the license form, its own settings when it
lacks some, the form a place lacks), its explanation a tooltip or, from the server's causes (`note_causes`: the versions it needs,
the settings without a value, what of the license it uses), a popover whose foot says where it is fixed; one with more to say has an
information mark and opens it; *Apply needed* is its badge. So a row has one control per thing: the badge leads to the fix, the key
icon opens the settings, the button starts or stops. A long note is cut with an ellipsis, its explanation saying it whole. The name
gives way first: its marks keep their size, the name is cut. Under 900 px of card the row is lines (the name with its star and
its marks, the description on two lines at most, the state with its badge, which wraps under it when it does not fit, the actions),
and a hollow star that only the pointer shows takes no room; on a phone the actions are one bar across the row, its buttons at its
left, its icons at its right, never a line of their own. The Dashboard's catalog has this anatomy on two lines (the name, then the state and the badges, which
are never cut there: what does not fit beside the state goes under it), with the same State and the same Start or Stop, and
Containers follows it (its name, its note badges,
its state in a State column of its own, the plug button of a product in the first slot of its icons). A State cell of a table
(`.list-state`, Images and Plugins) holds its state (the state component, or a pill for a change not saved) and what it adds on one
line, never stacked.

**Buttons and controls.** `.btn` 32 px, 12 px at its sides, a 8 px radius, 13.5 medium; `.primary` (one a page, the gradient
of `--accent-fill`), `.danger`, plain; `.icon-btn` 32 px (26 inside a row's icons, the Dashboard's
included, and a star wherever it is), its name its tooltip (`data-tip`). The primary of a head is its last button, before its menu.
**A control that cannot be used now says why**, and keeps the pointer and the keyboard so that it can be asked: it is drawn
`aria-disabled="true" data-why="<reason>"`, not disabled, its reason its tooltip (`setOff` of `ui.js`, which `paintBusy` and the
buttons of actions use, takes it over the first time it is set, hovered or focused), the look of a disabled one, and a click on it
does nothing. A choice of one among several is a
`.segmented` control, 32 px with 26 px choices (the choices that do not fit in the room there is go on the next line: none is
cut, none hides); an option on or off by itself (*Running*, *Starred*) is a loose `.toggle-chip`, 32 px, never a segmented
control with an *All* of its own, with the box of a checkbox before its word, empty or ticked (Logs' levels have their eye instead).
What is on (the checked segment, a tab, a chip ticked) has the edge of a field, 3:1 against what is around it, and the current
page of the menu its name in the strong weight: none is told by its fill alone. `.search` 32 px, its edge `--field-line` (a search with options inside, Logs',
keeps its text on the line and puts them under it on a phone); a search that holds text has the Lab UI's own clear button, 26 px,
which every search gets once it is drawn (the browser's blue one is hidden); `.combo-btn` 32 px, the field of every choice among
values (a form's `select` is drawn as one: [Dropdown](#components)), a value that follows another component's with whose it is after
it, in muted words that give way first (*Same as API Management*). A dialog is `--radius`, `--sp-5` inside, a `--border-strong` line and `--shadow-lg`; it opens
on its first field; a field's label may have the information mark of its term (what it is, where it is made), never a helper
sentence under the fields; a form of settings is such rows, what a setting is for as its label and its name in `.env` under it in
mono, hairlines between rows, no box for each. **Empty** is one block, `.empty`, wherever a list, a table, a card or a picker has no
row to show: its words centered in `--text-2`, then the one thing to do under them as a plain button (*Clear filters*, *Choose
services*, *Connect account*; `emptyHtml` for a list's words); loading: one `.spinner` in it, or rows of bars (`.plat-skel`) where the
rows will be. On a touch screen every target reaches 32 px without moving the layout: what draws smaller reaches beyond it.

**Where API Management runs** (`runsControl()`, `environmentControl()` of `core-choice.js`; `.runs-icons`, `.runs-control`,
`.runs-label`, `.core-env`). One setting of this machine (`state.core`) that belongs to one product: the group of the platform the
server names (`state.core.group`, the product of the Cloud's gateway scope), never a name in the page. Only that product can run in the
Cloud, so the choice is never called *Core*, a word that says the whole platform (the welcome's Core layer keeps it). A segmented
control of two choices, *This machine* and *Gravitee Cloud* (a `radiogroup` named *Where API Management runs*, its segments `radio`, the
arrows move the choice), each with a popover that says what it changes, in two looks:
- **On the product's row of the Dashboard**, after its name: two icons (laptop, cloud), each segment 26 px square, the control 32 px high
  and 60 px wide, their names for a screen reader and in their popovers. A 56 px row has the room of nothing more: the words did not fit
  with the version, the status and the consoles at 1440 px. The first column is 208 px at least so that the name and the control fit.
  With *Gravitee Cloud*, the Version cell says the environment alone (its place's name in its tooltip), or is the picker of the linked
  environments (`environmentControl({ short: true })`), which takes a line of its own on a phone.
- **In a page's head** (the Gravitee Cloud page): its question, muted, then the two names, then the picker of the environment when
  several are linked (`.combo-btn`, 240 px, the place's name). The question goes over the control when they do not fit side by side.
A choice that cannot be kept yet (no account, no linked environment) opens what makes it possible (Connect Gravitee Cloud, the Set up
of the first environment) and the control keeps saying what is kept: never a silent change of what runs, and choosing starts and stops
nothing. Don't: a third look for this choice, a choice that starts something, the choice in a card's head.

**Choice cards** (`.core-cards`, `.core-card`, `.core-card-text`, `.core-card-where`, `.core-more`). A choice of one among two or three that
needs more words than a segment, the welcome dialog's *Where does your Gravitee run?*: a `radiogroup` named by its question, each card a
`label` around a visible radio (at its top right), the `.cta-icon` of the license cards, a strong title, one sentence of what it is, then
muted facts read from the state (the products of this machine's platform) and what the choice still needs, or the place it is, as a pill.
Side by side, one over the other under 640 px. What is chosen has the edge of a field, as a checked segment; the keyboard's ring is the
card's. A card never states a version or what a platform does not run: that changes with time, and comes from data where it is said.

**Card head** (`.card-head`). One anatomy for every card (the Dashboard's, Containers' *Consoles*, a pane of a dialog): its title
(`h2`), what is about it after it (an account's name, muted; the license pill of the platform), its buttons at its end, 56 px; the
hairline under it is the top line of what follows.

**Dropdown** (the picker `Combo` of `combo.js`, the menu `openMenu` of `ui.js`; both placed by `Popup` of `popup.js`). Every list that
opens from a control, and nothing else: the pickers (the services and the lines of Logs, the category filters of the lists and of
Plugins, the kind of the Dashboard's catalog, the versions and registries of Images, a plugin's version, Deploy to and the page of a
guide), the menus (*More*, ⋯, *Open* with several consoles, the account's and an environment's *More*) and the fields of a form that
choose among values (`selectField`: the Category of New add-on, the Environment of the Gravitee Cloud forms, each a `select` that stays
the field the form reads and checks, out of sight, behind a `.combo-btn`). A popover of `--r-pop`, a `--border-strong` line,
`--shadow-md`, `position: fixed`, put in the dialog of its control when it has one (a modal dialog makes the rest inert).
- *Where.* Below its control when it fits there whole, else above when it fits there, else on the side with more room; a side with
  less than 240 px (`MIN`) is not used, and when neither has it (a window a few rows high) it lies over its control. The side it
  opened on is kept while its content fits there: a search that narrows the list does not move it.
- *How high.* As high as its content, so that a menu of four entries is not stretched, and at most the room of its side: down to (or
  up to) 16 px (`MARGIN`) from the window's edge, 6 px (`GAP`) from its control. Above its control it grows upward (`bottom`). Its
  search, its buttons (*All running*), its segmented control and its foot stay; the list scrolls in the height that is left, its
  groups' titles (`.combo-group`) stuck at the top while their options pass under them. A placing never moves what was scrolled to
  (the natural height is read from what scrolls, never by letting it out).
- *How wide.* As wide as its control at least (280 px for a picker, 200 for a menu), as its longest line when that is wider, up to
  640 px (`MAX_WIDTH`), and never wider than the window less 16 px at each side; a picker's left edge on its control's, a menu's
  right edge on its button's, moved in to stay inside the window.
  Nothing is cut that room allows: the width is measured from the content, then from what is still cut in the width it
  got (`clipped` in `popup.js`), and grows by that, a few times at most. When the lines are wider than the room it can
  have, the list is `tight`: each option is drawn in two lines, its name and under it its tail, in line with it, and no line
  is cut.
- *Rows.* An option and an entry are 36 px (`--h-option`), larger than a control so that a long list is easy to aim at and to
  tap, with a `--r-inner` radius; the hint of an entry, at its right, stays on its line, and the tail of an option (`.age`, a service's
  id, a version's age) keeps 12 px from its name.
- *Moving.* It follows its control on a resize, a scroll of the page or of what holds the control, and a change of what it holds (a
  list that loads, a search): once a frame at most. It closes when its control leaves the window or is drawn again.
- *Keyboard.* The arrows move the active option, which is never left under its group's title; Enter chooses, Escape closes it alone
  (not the dialog or the drawer behind it) and gives the keyboard back to its control, Tab leaves it. A menu opened from the
  keyboard focuses its first entry.
- *Phone.* At 480 px of window and narrower (`SHEET`) it is a sheet along the bottom, across the window, its top corners round, as
  high as its content up to 16 px from the top, above a phone's keyboard; on a touch screen the keyboard is on its list, not its
  search, which a tap opens.
Don't: a dropdown with a fixed height, one of its own placement, a native `select` that opens the system's list.

**Generic words.** A page of the Lab UI is a hub: it never names an item of the catalog (a playground, a setup, an add-on) in its
own words, nor states a version or what a platform runs or lacks as a sentence. What it says of an item comes from the item (its
name, its file, its requirements); what it says of a platform comes from data (the state, a live read and what the gateways of an
environment say they run, the reasons `project.judge` gives a playground). An example names paths with `<id>`. `test_static.py` refuses a name of its deny list and a
version said in words ("API Management 4.12") in the strings of a module.

### Measuring

Every change is measured in the DOM before it is judged by eye: `getBoundingClientRect` for heights and the left edges of first
columns, computed styles for sizes, weights and colors, the middle of every cell against the middle of its row, `scrollWidth`
against the window for a sideways scroll, and the contrast of each text against its background. At 1966, 1440, 900 and 375 px,
light and dark. Then the screenshots at full size and at 2x for the details. The figures this system promises, as measured in
the gallery and on the pages: a pill 24 px with 10 px sides; the Apply badge 120 by 24; a tooltip 28 px high, one line, 6 by 10,
12 px; a popover 260 to 460 px, as wide as its content, 12 by 16, 13 px, rows 8 px apart; a notice 44 px; a button, a search, a toggle chip and a
segmented control 32 px, a segment 26; a menu's entry and a picker's option 36, a dropdown longer than its room
ending 16 px from the window's edge (on the pages at 1440 by 900, 1440 by 600 and 375 by 700; `place` in `tests/web/js/popup.test.mjs`); a card's head 56, a table's head 32 (`.table
th` too), a catalog row 56 (the lists' and the Dashboard's), a product row 56, the Dashboard's row of Gravitee Cloud 56, an environment
56, a group 48, a quiet environment with its Set up 48, a hybrid gateway 48, a band 40 (the core's line too), a container 40; a row's icon
and a star 26; the metric strip folded 36 (with its card's edge), unfolded 64 under its line.

## One way for each thing

Every page and dialog does the same thing the same way. Before adding a piece, find the one that does it already.

### Pages and lists

- **A page**: a head that stays in view (its title, then its actions), then one card (`.card.list-card`): tools
  row, column header, rows, pager. What sticks inside a page sits under the head (`--head`).
- **Tools row**: `.list-tools`, controls 32 px high: the search first (`.search.grow`, "Search…"), then the
  filters, which go under it as one row when the line is too short (`.list-filters`): pickers, segmented controls,
  toggle chips. A filter that is on has the neutral look of the checked segmented button, never the accent.
- **Choosing**: one picker (`Combo` in `combo.js`, opened from a `.combo-btn`), for one value or several.
  - Several values: boxes to tick, the picker stays open, "All" first when it means no filter.
  - Its search shows past 8 options, or when a value can be typed.
  - With nothing to list it still opens, and says so (`empty`: "Nothing running" in Logs). It is disabled only while
    what it lists cannot be known, its tooltip saying why ("Docker is not reachable").
  - Its place and its size are the rule of every dropdown ([Dropdown](#components)): it takes the height there is.
  - The categories of a list (the three lists, Plugins) are `CategoryFilter`: the fullest first, each with its count.
  - A field of a form that chooses among values is a `select` drawn as the picker (`selectField`): the picker opens in the
    dialog of its field. The `select` stays the field (its value, `required`, its `input` and `change`); a placeholder of no value
    ("Choose a category") is no choice while the field is required.
  - Its button is named by what it shows, after what it chooses ("Category: All categories", a word for a screen reader only),
    so that a voice that says the visible words finds it. Tab leaves it as it leaves a menu: it closes, and the keyboard goes on
    from its button.
  - Logs before a service is chosen says the one thing to do, in the one empty block: its words and *Choose services*, which opens its picker; the counts
    of its levels show once there is something to count. Its sections are the platform's groups, then the playground and the
    add-ons that are on or run (or hold a service chosen); the services of an add-on that runs a gateway of a group (`gateway.group`,
    [One gateway runs here](#one-gateway-runs-here)) are in that group's one section, after its own, under a title of their own (a heading inside the group: the add-on's name and, when a gateway runs here for an
    environment, the account and the environment it serves, "Hybrid gateway (Acme Cloud, Development)"), the gateway first.
- **A control**: one size, wherever it sits. A button, a search, a select and a segmented control are 32 px high; a
  choice inside one (a segment, a toggle chip, an option of a search) is 26 px; all have the same text size. There is
  no small variant. The one other size is the icon buttons of a row and of a panel's actions (`.row-icons`,
  `.panel-actions`), 26 px. A choice of one among several is a segmented control; an option on or off by itself, which
  combines with others, is a loose toggle chip (32 px, 26 px inside a search).
- **A row**: 56 px, its name over one line that never wraps (ellipsis, the full text in its tooltip). Columns: the noun
  of the thing ("Add-on", "Plugin", "Component"), secondary information, State, then the control or the actions at the
  right end. The three lists have their State column too: the state component, its dot and its word.
- **Rows that open**: one chevron, a button named for the row with `aria-expanded` and `aria-controls` (`.s-toggle`), which
  the keyboard reaches and Enter and Space press; one hover, one focus ring (the row's, while its toggle has the focus), one
  tint for the open row. A click anywhere on the row but its controls opens it too. The row itself is no button: its
  controls (its name's link, its badges, its buttons) are reached apart. A row that cannot be closed has no toggle. The
  lists, the groups of Containers and the lines of a detail that open work so; a pack of the license is a row that opens
  with nothing inside it (`onRowKey`). An address opens one (`#/addons/<id>`, `#/setups/<id>`), in view, its toggle focused.
  Back returns to the list as it was, with its filters and its page, unless they were changed by hand meanwhile.
- **Row icons**: fixed slots in a fixed order (open, file, settings, logs, restart, stop or start, more). A missing
  one keeps its place (`GAP`). A file and its settings open from their icons (`fileButtons`), never from a menu.
- **Links to an item**: a name that leads to an add-on or a setup opens its row in its list, a playground's its page
  (`itemLink`, `hrefOf`): the "via" note, the lines of a detail, the groups of Containers, a playground's "What runs" and
  "Before you start".
- **What to open** (`paintLinks` in `ui.js`, one component): the consoles of the components under their product, each
  in a browser tab.
  - The order is the same on every page: the platform's groups in their order (API Management, Access Management...),
    a group for each platform a playground deployed to ([Places and Deploy to](#places-and-deploy-to)), the playground's
    own apps, then the categories of the add-ons, in `.links` (`.link-group`, `.link-section`, `.link`).
    - Containers lists every component that is on or still runs, in its card *Consoles*. The details of a running playground list
    those of its own apps and add-ons and of the platform, each gray while its service does not run, as those of Containers are.
  - A link says where it answers after its name: its port and the path of its address when it has one (*:8085/next*, *:8085*),
    so that two consoles of one port read apart.
  - The columns are as wide as the widest link, measured once it is in view.
  - A console is the UI of a component, never a page of another (`io.gravitee.console.<id>` labels): Gamma's Tracing
    is a page of Gamma.

### State and words

- **State**: every line says it with the state component, a dot and a word (`statusHtml`, `stateDot`, `itemState`): a row of
  the three lists and of the Dashboard's catalog in its State, a component of Images, a group and a container of Containers in its
  State column, a line of a detail, a service of a playground, the Dashboard's and the Gravitee Cloud page's rows. A pill is for a
  status or a label, never for a state. See [The design system](#components).
  - The dot is green for what runs, partly running too; amber and pulsing on its way; red for an issue; a ring for what
    is on and does not run (`stopped`); gray for what is off or has no container; a muted check for a one-shot that
    ended well (`done`), never green.
  - What is off or stopped is never a pill: nothing, or a muted word. Only what is on its way pulses, never a warning.
  - While what runs is not known (`blind`) no dot is colored and no state is said (`dockerDown`). While only Docker
    does not answer, what is on keeps its ring: the files say it (`onKnown`).
- **Words**: one per idea.
  - The words of a line are told apart by a comma ("Valid, 2 warnings"; "Expires on 12 Nov, in 30 days"), parentheses
    ("Gateway 4.13 (Docker Hub)") or the gap of the layout between two elements; never by a middle dot or a bullet, in
    the page, in the texts of the server and in the docs (`test_page.py` scans them).
  - State, Settings, Restarts, Choose, Discard, "Loading", "No matches" with "Clear filters", "Show output",
    "Running" for an item ("Healthy" is a container's word).
  - On and off are turned ("Turn on", "Turn off", "Turned on at start"). What starts or stops containers says so
    ("Start", "Stop", "Starts with the platform", "Stops at start"). What runs behind what is saved says "Apply needed".
  - The health chip names the platform ("Platform running"); it is the one place of the platform's state, on every page. One
    order everywhere: the platform, the playground, then the add-ons.
  - Where a playground deploys: "Deploy to" (the picker), "Deploys to" (the last step of a dialog), "Deployed to" (the
    panel of what it created), "This machine", and a place written "Development (My Account)", the environment then the
    platform, never with a middle dot or any bullet between them. "Platform" alone is still the core in the chip, the
    banner and Containers; where the two meet, the core is "This machine". Cloud's own words are kept: Account Token,
    Account ID, Cloud Token, environment. The token of a hybrid gateway is never named.
  - No label ends with "…", whether it asks first or not; a working face does, while nothing is measured. The title
    of a dialog is the label that was clicked, without a question mark; its button is the verb.
- **An explanation** is a tooltip (one line) or a popover (several lines, or a card of causes), one implementation in `ui.js`
  ([Tooltip, Popover](#components)): `tipHtml` makes its button, which keeps the look of what it sits in and carries the whole
  explanation as its name for a screen reader; `data-tip` explains a control that acts on a click. A bubble for the pointer
  alone (`tab: false`) is on a button that is no stop of the keyboard: the part of a bar, whose key says the same.
- **What cannot be used says why**, on hover, on keyboard focus and on a tap: the control is `aria-disabled` with its reason as
  its tooltip ("<name> is not running"; `setOff`), so that the keyboard reaches it and a screen reader hears why. What
  waits for Docker, for the running job or for the Lab UI is marked `data-busy`: `paintBusy` makes it wait with the
  reason the state gives (`wait`), or "Lab UI offline".
- **Empty and loading**: `.empty`, in the place of the rows, with the texts of `emptyHtml` and its action as a plain button.

### Actions

- **An action at work shows on the button that started it** (`actionButton`, set by `paintActions`).
  - *Working face.* The button does not move: inside it, its face (`.pbtn-face`) fades out and a second face
    (`.pbtn-side`) fades in over it, in 0.2 s.
    That face has a soft fill that grows with the job's progress and, centered on it, the verb in -ing and the
    percentage ("Stopping 45%"). While nothing is measured, it shows a spinner and the verb with "…" ("Creating…").
    Nothing else moves on it: no turn, no line along its edge, no light. With reduced motion the two faces swap at once.
  - *How long it works.* A job that ended well keeps its button at work until what it started or stopped got there.
    For an add-on, a setup, a playground, a group or a container, that is while it is still starting or stopping, its
    health checks running. For the platform, and for what no item names (`plugins:<product>`), it is while the server
    still measures anything the job started (`store.measuring`). Meanwhile the button shows the progress the server
    gives that item, else the job's.
  - *How it ends.* Once there, the button says 100% for a moment when the job got all the way, then its working face
    says how it went with the word of the action ("✓ Start", or the error tone and "Start" when it got there with an
    issue), and the face fades back in, saying what it offers now ("Stop"). A failed job says so at once in
    the error tone. A canceled one gives its face back with nothing to say.
  - *Icon buttons.* A small icon button (the rows of Containers) shows a spinner in place of its icon, and its check at
    once.
  - *The word* is the job's title without its item's name ("Turn off Kafka" becomes "Turn off"), else the verb's
    (`WORD` in `ui.js`). The verb in -ing comes from one table (`ING` in `ui.js`). An action started from a menu shows
    on the main button of its row.
  - *While it works*, a busy button takes no click and says what runs in its tooltip. A refusal is said in a toast, or
    in the problem line of the form that sent it, and in the error tone of the button. At rest a button has the size
    of its face; it takes the width of its working face only while it works.
  - *Its key* is what it acts on (`addon:<id>`, `platform`, `group:<id>`, `service:<name>`, `job` for Cancel...),
    matched with the running job's `for` and `verb`. Until the server answers, the click is in `store.pending`:
    `runJob(ask, {key, verb})` for a job, `busy(key, verb, promise)` for a request without one.
  - *A request without a job* on what a job acts on takes that key, to show on the same buttons (Share and Delete on
    their item's row, the save of a "Save & apply" on `platform`). One on what no job acts on has a key of its own,
    never one a job uses: `file:<path>` (the editor's Save), `new:<kind>` (Create), `images` (the Save of Images),
    `catalog:<product>` (Refresh in Plugins), `dashboard` (Refresh in Dashboard).
- **Start and Stop of an item** (`actionButton` with no verb, clicked through `runAction`): one width at rest
  (`.act`). Its word is the server's (`action.label`): Start, Stop, Turn on, Turn off. An add-on that lacks a required
  setting keeps its Start, waiting for the settings it names, or its Stop while it runs: its badge opens the settings.
  - When the item has a second action, it is in the row's menu (`secondary`).
  - When it does more than start or stop the item, a dialog says it once clicked, as numbered steps (see
    [Actions and their plans](#actions-and-their-plans)).
  - It shows any action on its item, and what its item is on its way to whoever moves it (an add-on a playground
    starts).
  - A playground's row has "Open" in every state, then its Start or Stop, as every row. Its Stop asks as its page's
    does.
- **A pending change** (Images, Plugins, the editor): the row is tinted, its State cell holds a warn pill that says what
  will happen ("Will change", "Will install", "Unsaved"), and its buttons are in the head: Discard, Save, "Save &
  apply" or "Apply". They stay gray without a change ("No changes").
    - The second button of Images and of the editor reads "Save & apply" while there is a draft, else "Apply", gray ("No changes")
    without one: the Apply of what runs behind what is saved is the banner's, on every page but Containers.
  - "Save & apply" acts only when something running is recreated. Images keeps it gray otherwise ("No running
    container changes"). The editor shows it only for a unit something of which runs (`runs`), never for a setup, and
    its Save then says "Applied on next start".
  - Save & apply says first what the draft changes, restarts and starts, as the server counts it (`confirmApply`,
    `/api/effects`). When that is nothing, it only saves and says why ("Saved: no running container changes").
  - Apply, with nothing unsaved, is the platform's, the one of Containers and the banner (`runPlatform`): its dialog is
    the state's.
  - Plugins says what it installs, removes and restarts, and the playgrounds that are on and need a plugin it removes
    ("Needed by").
  - A draft is registered with `drafts`: leaving the page or the window asks before it is lost.

### Dialogs, tabs and focus

- **Dialogs**: one head for the large ones and the drawer (`.dialog-head`: the title first, the cross last). Escape and
  the cross close them, a click outside does not (Escape closes a popover that is open first, and that alone). The activity
  drawer is a dialog that is not modal, named by its title (a heading): opened by a person it takes the keyboard on its cross and
  gives it back as it closes; opened by a job that failed it leaves the keyboard where it is. What a job does is said to a screen
  reader as it starts and ends (`#job-said`, a polite live region). A toast that confirms goes by itself; one that says an error
  is an alert, stays while the pointer or the keyboard is on it, and has its cross. Enter confirms a neutral dialog, never a dangerous one. A module
  builds its dialog once, as it loads; the confirm and choice dialogs are in `index.html`.
- **Wizard** (`dialogOf` in `dialogs/platform.js`, `.platform-wizard`): steps that follow one another are one large dialog,
  never closed between them, `min(1200px, 96vw)` wide and of one height from step to step (`min(720px, 100dvh - 48px)`; under
  860 px as tall as what it holds). Its head is the head of the large dialogs: the title, the steps in the middle of the line
  (`.wizard-steps`: an `ol` named *Steps*, a number in a circle and a name each, a hairline between them; the current one filled
  and `aria-current="step"`, one done has its check and *Done:* for a screen reader; none is told by color alone), the cross.
  Each step is a `form` of its own in the body, one shown at a time: going on shows the next in place, puts the keyboard on its
  first field and says *Step 2 of 2, Environment* in a polite live region. One footer under the body (`.dialog-foot`, a hairline
  above): the refusal of the step at its left, in the server's words, then its Cancel (*Skip for now* on an optional step) and its
  primary, which waits with its reason (`setOff`) until the step can be sent. Nothing is ever under the footer: the body takes the
  height left. The body is two columns, `.connect-body`: at the left, 400 px, what to do and the fields, in one column that
  scrolls by itself (`.connect-fields`: sections apart by `--sp-5`, a hairline and `--sp-5`; `--sp-4` inside a section; a label
  above its field, 6 px; fields as wide as the column); at the right, on `--bg`, the guide (`.connect-guide`): a picture of the
  console, whole (as large as the room it has, never cut, a hairline and `--radius`), several chosen one at a time by a
  `.segmented` control above them, which the numbered lines of the left follow (`.steps`: the number of a line is the number drawn
  on the picture; the line of the picture shown has its number filled; a click on a line shows its picture). Under 860 px the guide
  goes under the fields and the body scrolls as one; on a phone (600 px) it is folded behind *Show where to find it*. A short form
  is the same `dialogOf` with one panel and no head of its own (`formDialog`).
- **Tabs**: a `.segmented` control whose buttons are `role="tab"` with `aria-selected`, the panels `role="tabpanel"`.
  The arrows, Home and End move the focus (`ui.js`). Enter or Space opens the tab, so that no UI is loaded on the way.
  Which tab shows and what a tab says are in
  [docs/lab-ui.md](../docs/lab-ui.md#the-guide-and-the-uis).
- **Focus**: one look for every field (the neutral border and ring). Every row that opens is reached by keyboard. A
  toggle chip is reached with Tab and toggled with Space, and shows the ring. Rows drawn again give the focus back to
  the control the keyboard was on (`refocus`).
- **Moving between pages**: the first stop of Tab is *Skip to content*, which leads to `main` (`#content`) of the page on
  screen, which stays (its click is the page's own: its address is no page of the router). The menu scrolls inside when the
  window is shorter than what it holds (a page zoomed in): every page and the platform's state stay within reach. A page
  chosen in the menu takes the focus (`main`, which shows no ring), so that the next Tab is in the page and a screen
  reader reads it from its title; a page reached by Back, Forward or a link of a page leaves the focus where it was.
  The platform's state (`#health-said`) and the banner are polite live regions (`role="status"`): a change is said,
  never the same words again. A dialog opens on its first field.

### The page of a running playground

What the person sees is in [docs/lab-ui.md](../docs/lab-ui.md#the-guide-and-the-uis), and what an app does in
[docs/playgrounds.md](../docs/playgrounds.md#what-an-app-does-when-it-runs-in-the-lab-ui). This is how it is built
(`workspace.js`).

- **Layout.** The page itself does not scroll (`.fill`, set whenever the card is there), each panel does, and the page
  has what is left of the window: no width cap, narrow side margins, which is all that sets it apart from the other
  pages, whose menu it keeps. The two panels are columns, or rows (`.rows`) when the card has not the width for both
  (`fits`, `split.js`): the same panels, nothing is unloaded or disabled, only the divider goes.
- **Frames.** A frame is made when its tab is first shown and **never drawn again by a refresh**: its card is written
  once (`setHTML` keeps it as long as its text is the same) and what changes is written apart, so nothing that the state
  holds may be in the card's own text.
  - A UI that is not there yet starts at its own first address and goes on to the one asked for once it has loaded (a
    browser makes an address loaded before then take the place of the page, so it waits a tick).
  - A UI that cannot be framed, does not run or does not answer shows a notice in its panel and looks again by itself.
    The tab bar offers what applies to a UI once it shows.
  - The details tab has an id no service has (`@details`). The frames follow the system's light or dark, not the Lab
    UI's switch.
- **The page knows nothing of the apps in its frames.** It posts one message, the action of a guide's button, and
  listens to two, an address to load in a tab and that the app listens (`split.js` decides whom to believe).
- **History.** A page of the guide is turned with `turn` (`playground.js`: `router.replace`, never `pushState` or
  `location.hash =`), running or not, because a UI adds entries of its own to the one session history. A tab change
  and a divider move are no navigation. An address loaded in a frame is an entry of that UI's own (`location.href`;
  `location.replace` only for a UI that came back, whose page is gone). The address always names the page of the guide
  on screen: the one read last, which comes up when it names none, is written into it.
- **An action in a guide** (the syntax and the contract are in
  [docs/playgrounds.md](../docs/playgrounds.md#actions-in-a-guide)).
  - `markdown.js` draws the link. The sanitizer keeps no other `action:` and no `data-action` or form of a guide's own.
    `make check` says an id that is none before it is shared.
  - A click is `Workspace.send`: it shows the first console and posts the action at that UI's own origin, never `*`,
    at once when the app has said that it listens, else queued, in order, once each.
  - The page forgets that an app listens when it loads an address itself in a frame (`load`; one with a `#` may be the
    page it is on) and when its UI goes away, which also drops the queue.
  - The button says why it is disabled (`actionReason`: not running, no UI, the UI itself), and the workspace repaints
    it whenever any of that changes. Nothing the app does is read back: the guide is as good as the app is.
- **A guide reads as a page** (`.guide` in `pages.css`, one style for its page, the details before a start and the
  editor's preview).
  - Text of 15 px on a 1.7 line that takes the whole width of its panel (no measure: padding keeps it comfortable; only
    a picture or a diagram is capped, `--media`), blocks `--flow` apart.
  - The page's title with a lead under it, `###` sub-headings that open its parts, `####` as a quiet label, real
    bullets and numbers one item to a line, a quote as a calm callout (never a warning), tables for reference, links
    underlined, code in a box, images and diagrams spaced, nothing glued to what is above it.
  - Check a long page at 600, 750 and 1000 px of guide, light and dark.

## The model

What the server decides and the page shows. A name in backticks is the code to read.

### On, off and running

- Truth is the running containers and `.lab/on.yml` (`ON_FILE` in `paths.py`, "the on file"): an `include` list of at
  most one playground, then the add-ons turned on. Nothing on is an empty file. Compose reads it with the core through
  `COMPOSE_FILE`, which the Lab UI sets for its own commands and `make init` writes to `.env`.
- Every add-on, setup and playground has two facts, both always shown.
  - Whether it is on (`on`): part of what runs with the platform.
  - What its containers do (`state`): running; partly running (`partly`: some run, the others stopped cleanly or were
    never created); starting; stopping; stopped (it is on, nothing of it runs); issue (a bad exit, out of memory,
    unhealthy, restarting); off.
  - A container also says "Not created", "Not started" (made, never started), and a one-shot that ended well "Done".
  - An item without a service of its own (a playground without apps) is what it brings while it is on, a playground's
    seed with it. What only ended well, or nothing, is never running.
- An add-on is on when the on file brings it, itself or through what needs it. A playground brings the add-ons it
  includes, and those of the setups it includes. One playground is on at a time.
- A setup is a shortcut: it is its add-ons, whoever turned them on.
  - It is on when they all are: running when they all run, stopped when none does, partly running in between. With only
    some of them on it is off, and says "Partly on".
  - Its Start turns on what it includes. Its Stop turns its add-ons off but those that something else that is on uses
    (a playground, an add-on that needs one, another setup), which it keeps, and says so ("Keeps").
- An item is shared with the project, or personal: of this machine, in the `local/` folder of its kind, git-ignored,
  with the id `local/<name>` in every route, in the on file and on the page (`paths.personal`). Everything else treats
  the two alike. A shared file never includes a personal one; a personal one includes what it wants.
- Conflicts work both ways: either add-on may declare the other. A start is never refused for one: what is in conflict
  with what comes stops, or is turned off, with what needs it.
- A required setting does block: an add-on that uses `${VAR}` without a default cannot be turned on until its `.env`
  holds a value. Its Start waits for it ("Needs settings: VAR"), its note opens its settings; one that runs all the same
  (started by a command) is stopped from its row.

### Creating, sharing and deleting

- The three kinds are created here, personal or shared: from a form that writes the compose file, or as the prompt that
  has an AI agent follow a skill of `.agents/skills/` (`personal.SKILLS`).
- A personal item is deleted here, or shared. "Share" moves it into the project with the paths that change, once `make
  check` has nothing to say of it as a shared item (`personal.share`). Share, and the rename of a setup, list first the
  other files they write again ("Changes").
- An item cannot be shared or deleted while it is on (`personal.held`).
  - An add-on or a playground says "Stop it first" while something of it runs, "Turn it off first" otherwise.
  - A setup is held only when the on file names it itself (a person may write it), and it is not renamed either.
  - Nothing that another item includes is deleted ("Used by").
  - The item's menu says so (`held`). The Lab UI does not run git.

### Actions and their plans

- An item's button says what the click does to it (`action.label`, from `layers.plan`).
  - Start turns it on and runs it. Stop stops it and turns it off. Turn off turns off what is on and does not run. Turn
    on turns on what runs without being on (started outside the Lab UI, its note *Started by hand*). An add-on that lacks a
    required setting has a Start that waits for it, or its Stop while it runs, never another action in its place.
  - What is on and does not run has Start, and Turn off in its menu (`secondary`). What runs without being on has Turn
    on, and Stop (a setup, when one of its add-ons does).
  - What is on and keeps restarting has Stop: a Start would change nothing. So does what still runs after its Stop
    failed: the retry.
  - A failed Stop of a playground or a setup is a failed Stop of the add-ons it was stopping too, and a setup says the
    failed Stop of one of its add-ons (`jobs.Jobs.failed_job`): what it turned off still runs, it was not started
    outside the Lab UI. A playground's Stop again takes the add-ons it brings that still run.
- An item's action touches only the item.
  - Its Start runs, by name (`up -d --no-deps`), what of it and of the add-ons it brings does not run, with what that
    depends on among what is on and does not run (Compose's `depends_on`, which the plan names). It makes again the core
    services it changes and what of it runs behind what is saved, the core services it configures included (a Start that
    failed after the on file named the item left them as they were).
  - The one-shots all that depends on run again first, as Compose would run them (a migration before what it migrates,
    which has nothing more to do the second time).
  - A playground's Start also runs the platform while none of it runs, else what of it its apps and its seed need.
  - Its Stop removes its own containers by name (`rm --stop --force`) and recreates the core services it changed.
  - Nothing is removed as an orphan: what was stopped on purpose stays stopped, what was started outside the Lab UI
    stays.
- **Side effects are asked first.** The plan (`layers.plan`) lists what starts, stops (it runs), is turned off (it is on
  and does not run), is kept (what something else that is on uses, what a playground's seed created) and restarts.
  - When the action does more than the item (it starts, stops or turns off anything else, keeps an add-on, restarts
    anything), its `dialog` shows them first, as numbered steps in the order the job does them: Stops (turned off too),
    Turns off, Starts, Restarts (recreated to take the changes), one line per item with its kind (Platform, Playground,
    Add-on, Container), then what it Keeps (`effectsDialog`, `planRows`). With a choice (a playground's Stop, keeping or
    deleting what its seed created), the steps come once, then the choice.
  - A deploy to a place besides This machine always asks, because it does more than the item, and its plan has no
    service of the core ([Places and Deploy to](#places-and-deploy-to)).
  - The page sends back what it showed (`accept`, each row as `<kind>:<id>`). The server makes the plan again and
    refuses to stop or turn off anything else ("Would also turn off Mail server").
  - The platform's Start and Apply say first what they start beyond the core and what they make again
    (`platform.start`, `platform.apply`), and what that does besides, in the danger tone, the button too: what goes back to an
    older version than the one that runs (`goes_back`, "Elasticsearch 9.5.3 → 9.5.2": data a newer version wrote may not open in
    it) and what loses the license it runs when none is saved (`loses_license`: Enterprise features stop; `model._consequences`), and so does the Start of a group or a container on Containers (`brings`,
    `recreates`). Deleting a playground whose seed created something says what stays in Gravitee. A playground's Start
    says also what installing its plugins restarts, which only its checks read: the `dialog` of
    `GET /api/playgrounds/<id>/check`.

### Notes

The note of a row (`note`, `note_tone`, its tooltip `note_title`) is one fact, the first that applies:

1. None, while it is starting or stopping.
2. "Failed" (the job's last line in its tooltip, or "Base image node:20-alpine could not be fetched" when a build could
   not fetch what it starts from), "Canceled", or "Not logged in to Azure" (Log in in its menu).
3. The culprit of an issue ("Kafka UI exited (1)").
4. What keeps it from starting, amber: "Needs settings" (its popover the settings not set, as chips, `note_causes` of kind
   `unset`, and the badge opens its settings, while its Start waits for them; for an add-on it brings, the badge leads to that add-on and its explanation names it:
   "Kafka needs settings"), "Needs newer versions" (its popover a line for each component, the version it needs or later, kind
   `needs`, and that they are chosen on Images, to which it leads); "Needs a hybrid gateway" for the add-on that
   runs a platform's gateway, once a platform of its kind is registered and its token is the one setting it lacks: it
   leads to the Gravitee Cloud page).
5. On and stopped: "Starts with the platform" while the platform does not run, also while Docker does not answer (what
   is on is read from the files), "Stopped" once it does.
6. "Partly on".
7. "Apply needed", the badge, its popover the causes (`note_causes`), its name the reason (`note_title`).
8. What of it does not run, muted ("Developer Portal stopped").
9. "via X", a link to X (`note_target`).
10. (Nothing: what its Start stops or turns off is said once, in the dialog of that Start, and never on the row, where every
    other item of the list would say it too.)
11. "Started by hand": it runs, and the platform does not start it. The badge names the fact, not the switch it lacks, short
    enough to stand beside its state on the Dashboard; its explanation says the rest: "Not from the Lab UI, so the platform does not
    start it." (a setup's names its add-ons that run; `layers.outside`). What Turn on does is the button's tooltip ("Start it with the
    platform from now on"), so the row reads *Running*, *Started by hand*, *Turn on* without contradicting itself. The description of
    an item never says it: a description says what the item is, the badge what its state is. Images says it too, beside *Running*,
    with the same badge (`outside` of each component of `/api/images`).
12. What the license lacks ("Needs a license", "License expired", "Not in the license"; "License needed for lesson 4"
    when each feature missing is for a part of it), which opens the License form; its popover what it uses (`note_causes` of kind
    `license`, the packs by name) and where a license is set.

On a row of the three lists the note is a badge in its State, after its state (`noteBadge`, [List row and note badges](#components)); a
playground whose images are older than it needs says *Needs newer versions*, the versions in its explanation. A playground's
row in the Playgrounds list adds notes for the place chosen, while a platform is registered: "Needs a
hybrid gateway", "Needs a Cloud Token" (also when it expired or is refused, the form then replaces it), "Invalid token"
and "Unreachable", after the server's own note. They are drawn by the page from the places (`pages/list.js`), because the
choice lives in this browser, and each leads to the form or the row that fixes it. Nothing is said for This machine, a
place gone with its platform, or a place not read yet.

### Playgrounds

- A playground's first Start sets it up, in phases: Add-ons, Plugins, Apps and services ("Platform, apps and services"
  when the platform starts too: its apps are built), Seed. A deploy to a place besides This machine has Add-ons, Apps and
  services, Gateway and Seed, never Plugins or Platform.
- Once it is on and its seed recorded what it created (`layers.fresh`), its Start runs it again as an add-on's does, in
  one job: no build, no seed. After its seed failed, its Start sets it up again, its seed with it, and follows only that
  run of the seed.
- Re-seed runs the seed again. It, and the stop that deletes what the seed created, wait for what the seed needs, and
  what that needs in turn ("Management API is not running"). Re-seed also waits for the playground to be on ("Acme Bakery
  is off").
- Containers does not start a seed ("Re-seed runs it again"); a service's `role` says which one is.
- A playground has its own `.env` and settings, as an add-on has (`paths.unit_env`): its form shows what its compose file
  describes, the image variables staying on the Images page.
- Its UIs next to its guide are its `tabs` in the state (`frames.declared`).
  - A tab is the first console of each service its `consoles` lists, in that order, each with the state of the service
    behind it (`state`, `up`: a console is a service of the project, so both are always known). A service's consoles are
    ordered the current before the `legacy` ones, then by name (`project._consoles`).
  - Nothing else is a UI: there is no other key, no address of its own, no flag on a link.
  - Whether a UI answers and the page may frame it is a look of the Lab UI's server
    (`GET /api/playgrounds/<id>/frame?url=`, `probe.py`, kept 4 s), asked only for these addresses, judged against the
    origin the page was opened with: `frame-ancestors` first, then X-Frame-Options, as a browser does.
  - `make check` reads what files can say: each console is a service the playground runs, listed once, and a console of
    the platform lets the Lab UI's pages frame it (its command, `x-ui-frames`). The headers of an app are its own, which
    only that look sees.
- A playground's `guide` in the state is a version of its text: its page reads the guide again when it changes.
  - The page shows it a page per `## ` section (the introduction before the first): `#/playgrounds/<id>/<page>`, a
    lesson by its number, another section by its title in lower case. Without one, the page read last in this browser
    shows.
  - Another page of the same guide does not read the checks again.

### Dashboard

The first item of the menu, alone above its sections, and the page the Lab UI opens on (the address with no hash, an address
that is no page, and the one this page used to have lead to `#/dashboard`). It answers four questions and nothing else: is it
all right, what is in the Gravitee platform and which version (and how many hybrid gateways the Gravitee Cloud accounts have: a row
under the products, with no version, as Cloud updates itself), what else is on it, how much the lab uses of the machine. It is not a launcher of cards
(the Home pages rejected on 2026-10-02), its head has one button for the platform and it has no row of consoles of its own (the
first rework of this page, 2026-10-11, had both), and every other action of the platform is the Containers page's. The rows of
its catalog have the Start or Stop of their item, as the lists' rows have.

- **Where API Management runs decides its row.** The choice is on API Management's row, after its name. With *Gravitee Cloud*
  (`state.core.place` is a place), the row is its environment there: the environment's name in the Version column (the place's name
  in its tooltip; the picker when several are linked), the state of the environment's gateway here in the Status column
  (`environmentStatus`, `gatewayCell`: the Gravitee Cloud page's words), and in the last column the environment's one action when it is
  no start or stop of the gateway (*Set up*, *Create gateway*, a token), else *Open console*. Under it, a muted row (`.dash-idle`,
  `.dash-local`, its dot under the name above) says *This machine*: API Management's local services, *Not used*, their dot saying
  whether they run, their consoles while they do. The other products' rows are unchanged. The lean head's *Start all* and *Stop all*
  follow it (`state.core.start`, `state.core.stop`): *Start all* asks `GET /api/core/plan`, then always confirms (*Starts*, *Serves*
  with the environment, *Not started* with the server's reason after its pills), then sends `POST /api/stack/start {core: true,
  accept}`; it is disabled with the server's reason while it cannot start (no hybrid gateway, an account that cannot be read, a license
  the gateway lacks). The banner leaves out *Gravitee platform is stopped* while API Management runs in the Cloud.
- **The order is the layout.** The questions, most frequent first: the head, then two columns, *Gravitee Platform* with a row for
  *Gravitee Cloud* under its products, and *Playgrounds, setups and add-ons*, then *Resources* across the page, one line that unfolds. Spacing is one scale,
  `--sp-1` to `--sp-5` in `base.css` (4, 8, 12, 16 and 24 px): 16 px at the sides of a card (`--pad`), 24 px between the cards and
  the blocks of the page, 12 px between controls, 8 px between a control and what is next to it. Every card has one head of one
  anatomy (`.card-head`: its `h2`, what it is about muted beside it, its buttons with their words at its end, 56 px; a card too
  narrow for both puts the buttons under the title, never only some of them as icons), then its rows under hairlines. Rows have one
  height for one kind: a product 56 px and the row of Gravitee Cloud 56, a catalog item 56 (two lines), the heads of the columns 32,
  Resources folded 36 with its card's edge, unfolded its line and a strip of 64 px at the most from 1440 px of window. Every cell of a row is centered on the row's middle (and every section of Resources on the strip's middle), its controls
  are 32 px, and the icons of a row (the star and the Settings key of an item) 26 px, as on the lists, with a target of 32 on a touch
  screen. The page has no
  subtitle and no helper sentence. **A state is one component wherever the page says one**: a dot and its word (`statusHtml`), the
  word muted while what it says is off or stopped, amber or red while it warns or fails, its dot in the same tone; an account that
  cannot be read, an item of the catalog and a product (its dot, its name saying it
  to a screen reader) all use it, and no state is a pill on one row and plain text on another. A word that opens a
  tooltip has no mark of its own (it is underlined while hovered or focused); no text is underlined at rest, a name least of all, so
  that no row has a link and a button side by side. A state that needs explaining has a badge with an information mark, which is a
  tooltip: the page never shows "Apply needed", "Platform issue" or a disabled button with no reason to read.
  - **Three to two from 1150 px of page.** The page here is the space next to the menu (`main`, 1164 px at a 1440 px window): the
    two columns stand side by side, 1.6 to 1 (the platform's grid needs 686 px, the catalog's 438), each as high as its content and
    starting at the same line (`align-items: start`: a column that is shorter simply ends, there is no blank to fill). Under 1150 px the
    cards are one over the other, each as wide as the page, which is balanced too: a window of 1366 px or less has them so. Every
    card is a container of its own (`container: dash / inline-size`), so a row adapts to its card and not to the window.
  - **The platform's card, one grid.** The columns are the product or Gravitee Cloud (22 % of the card, 160 to 220 px), its version or
    the accounts (20 %, 156 to 200 px), its update or how many hybrid gateways (what is left) and what to do (as wide as its buttons,
    which keep their padding): every row starts at one place, so the heads line up, and no word or button is cut at any width. From
    1012 px of card the consoles of a product are buttons and *More*; narrower (1440 px: 702 px) each product has one *Open* button.
    Under 640 px a row is two or three lines (a product's name and its *Open*, then its version and its status; Gravitee Cloud and its
    action, then its accounts and its count or what keeps it from being said, which wraps). At 375 px nothing scrolls sideways and no
    word is cut. Under 420 px a product's name
    has its line whole and its *Open* goes on the line of its version; the buttons of a card's head share a line when their words fit
    with their sides, else each takes a line (*Containers 12* and *Change versions*), and the catalog's filters are rows (the kind
    picker over *Running* and *Starred*); an item is three lines: its name with the icon of its kind, its state and badges, its buttons
    at its right end.
- **Every state is the store's**, drawn live, with no read of its own: the head, the products, the catalog and the consoles
  change as the state does, the rows one by one (`keyed`), so that what did not change keeps its hover and its focus. The
  page reads for itself only the latest versions, Docker's data, the load and the Cloud (below).
- **The head is the Containers' head, leaner** (`platformHead(title, {lean: true})`, `paintHead` and `onHeadClick` of
  `stack.js`: one implementation): its title and beside it, while something runs behind what is saved, the badge "Apply needed"
  in amber, in whatever state the platform is (`applyBadge`, its popover a row for each product, add-on or playground that runs
  behind with its causes: [Behind what is saved](#behind-what-is-saved), `behindWhy`). The platform's state is not in a head: it is
  the sidebar's chip, on every page ("Platform running", "Platform stopped", "Platform issue", "Docker is not reachable"), whose
  popover says, for an issue, a line for each group that says what is wrong ("API Management: Gateway exited (1)",
  `store.health().why`); Docker not reachable has none, as the banner above says the cause. Then at most one button, on the key `platform` with its progress, its dialogs and its refusals: Start while the platform is stopped, Apply when
  something runs behind what is saved (the
  primary then), else Stop, on the line of the title at every width (its badge wraps under the title on a phone). Restart, Update
  and the menu with Remove containers and Reset are not drawn; they are the Containers page's. A button that is still saying how
  its action went stays alone: nothing is offered beside it. Because the head has them, the global banner leaves the Start of a
  stopped platform and the Apply it names to it on this page, as on the Containers page (`PLATFORM_HEAD` in `sidebar.js`): no
  button twice.
- **Gravitee Platform.** The head of the card has the license pill after its title, the one of Containers' core line
  (`License.pill`: *Community Edition*, *Enterprise Edition* and its tier, *License not saved* with what the next Apply does), so
  that the edition and what an Apply does to it are said where Apply is pressed; then two buttons with their words at every width,
  the count of the platform's containers, which leads to Containers ("Containers 12", "Containers" alone while what runs is not
  known) and *Change versions*, which leads to Images with no row chosen and no picker open. Under it the headings *Product*,
  *Version* and *Status*, and one row for each group of the platform that has a
  version, so no product is named in `web/`: API Management, Access Management and Alert Engine, not the databases, which have none
  and are on the Containers page. A product is named once, in its row. There is no band, no second line and no word *This machine*:
  the Cloud is below, and a row says something only when something is wrong.
  - *Product*: the product's state as a dot (the Containers page's: green while it runs, a ring while it is on and does not run, amber
    and pulsing on its way, red for an issue, gray while it is off or held for want of a license, a ring while only Docker does not
    answer) and its name, a heading (`role="heading"`, level 3).
  - *Version*: what it runs, in mono (a pre-release says it by its version: no pill says it again); its configured version, muted,
    while it does not run. Until the numbers are read, a spinner, and a dash when they could not be: the version saved
    is never drawn in its place, as it may not be the one that runs.
    - *Status*: the first that holds. An issue (its culprit, in red) or "Needs a license" (which opens the form that takes one) says
    that; "2 of 5 running" in amber while the group is partly running; the badge "Apply needed" while the product runs behind what
    is saved, its popover the group's causes ("4.12.20 is saved, 4.13.0-alpha.4 still runs": the version beside it is what runs) and,
    when a newer version is out, *Update available* and that version under them, which the badge would hide otherwise;
    else the state component: "Up to date", muted, with a muted check, when what runs is the newest of its channel (its popover which
    channel), "Update available" in amber when a newer one exists (its popover the two versions under *Running* and *Latest release*
    or *Latest pre-release*, and that it is chosen on the Images page), nothing while it cannot be told, a spinner while the registry
    is being asked. A pre-release is compared with the latest pre-release, else with the stable one (`dashboard.judge`). The words say
    it without a heading over them (under 640 px). What only informs ("Management API stopped") is for the Containers page.
  - *Consoles*, at the end of the row: the ones that run, from the state's `links` whose `service` is one of the group's, a 32 px
    button each (`.btn` with the icon of a link out and the console's name, cut with an ellipsis and named in full for a screen
    reader when it is long) in the server's order, then the older ones (`legacy` of the link, from the
    `io.gravitee.console.<id>.legacy` label) together in one *More* menu of the same height, and never a link in the row. A card too
    narrow for the buttons (under 1012 px) has one *Open* button for every product, whatever it has, 88 px at least: the console itself
    when it has one, with the icon of a link out, else a menu of them all (*Open* and a chevron, no icon of a link: it opens a menu),
    the older ones after a line (both are in the row and CSS shows one: `.dash-full`, `.dash-compact`). It is the lists' own pattern:
    *Open* for one target, *Open* and a chevron for several. A product with one console has that button only.
    A console that does not run is not drawn, and neither is a link of an add-on or a playground, whose services are in no group of the
    platform: those are on their lists' rows and on Containers. Nothing waits on a click on a name.
  - The server decides the versions (`dashboard.judge`): each product has `channel` (`stable` or `pre-release`), `latest` (the
    newest of that channel, null while unknown) and `status` (`latest`, `newer`, `unknown`), and keeps `stable` and
    `prerelease`. They are the Docker Hub tags that `images.py` reads, kept an hour; a missing one fills in as it comes
    (`pending`).
  - **Gravitee Cloud is a row of the platform's card**, under its products, on their grid (`.dash-cloud`, 56 px), one for all the
    accounts: its name (a heading, as a product's is, starting where they do, in the room of a product's dot), the accounts in muted
    words, cut, in the column of the versions (none when an account is named as its kind), then in the column of the status how many
    hybrid gateways they have (*No gateway*, muted, *1 gateway*, *2 gateways*: `gatewayCount` and `gatewayList` of `environments.js`, the
    list of the Gravitee Cloud page, so that the two say one number), a name that leads to the Gravitee Cloud page (`#/cloud`, `.name-link`)
    and whose bubble, on hover and on focus, lists them: *Hybrid gateways*, then each environment (with its account when there are
    several: *Development (My Account)*) over its gateways, each with its state in the page's words (Running here, Connecting, Stopped
    here, Connected elsewhere, Not connected, Pending, Removed in Cloud, Needs a license...). It has no version and no update: Gravitee
    Cloud updates itself; no button of its own either: the environments, their gateways and their actions are the Gravitee Cloud page's.
    The catalog says the Hybrid gateway add-on's own note, as the lists do.
    - **States**, in the place of the count, the row the same in every state, its words wrapping rather than cut and what to do at its
      end (under 640 px the row is two lines). With no platform registered: "No account connected", or one sentence, "A hybrid gateway
      runs here, with no account connected", when the gateway add-on of a platform kind runs (`gateway` of an add-on, `up`, Docker
      answering, so that the row never says Cloud is not connected next to a gateway that syncs from it), and *Connect account* (a plain
      button: attaching an account is rare and the one primary of the page is the head's Start or Apply), which opens the Gravitee Cloud
      page's wizard (`dialogs/platform.js`) and, once the account is registered, reads the platforms again behind its second step. An
      account that cannot be read says its state as the Gravitee Cloud page does (`accountState`: Account Token refused in red,
      Unreachable in amber), the server's reason in its popover (with the account's name when there are several; never a sentence beside
      it), and what can change it: a refused token is replaced (*Replace Account Token*, the Gravitee Cloud page's form), an account that
      does not answer is asked again (*Check again*, which says "Checking" while it asks). An account not read yet: *Loading*. Accounts
      that cannot be listed: "Accounts cannot be read" in red, the reason in its popover, and *Check again*, once however many times it is
      pressed. The row is one line in every state, so nothing under it moves when the Cloud answers.
    - **Only the routes of the Gravitee Cloud page are used**: `GET /api/platforms`, then `POST /api/platforms/<id>/check` of each
      platform at once whose last answer is more than 30 seconds old (`FRESH`), when the page is shown and when the tab is seen
      again: a trip through the menu costs Gravitee Cloud nothing; Refresh of the Gravitee Cloud page asks again whatever its age. They never hold up the rest of the page. A check that fails is *Unreachable*. The
      answers and their time are one record for this page and the Gravitee Cloud page (`store.cloud`), so that what one read, and what
      an action on the other changed, is what both say. A gateway that runs here and has not connected yet is read again after 5
      seconds, then 10, 20, 40 and 80 (`POLL`, `POLLS`), armed again each time the page is shown (an account read a moment ago too),
      then left as it is, so that a gateway that never connects is not asked about for ever, and no longer once the page is left.
- **Playgrounds, setups and add-ons.** What is on now, then what is starred, so that it holds what runs and what is yours; and,
  with a search, the whole catalog, so that the card is also the quick launcher. Under the heading, the toolbar of the lists
  (`.list-tools`: the same search field, the same `.combo-btn` of `combo.js`, the same toggle chips), with the same words and the
  same rules as the lists:
  - a search over what the lists' searches look into (`words` of `list.js`: the name, the description, the category, the tags and
    the members of a setup; not the starred only, not the running only: it looks through all of the catalog of the kind chosen)
    and, under it when the card is too narrow for both, a picker, *Kind* (All kinds, Playgrounds, Setups, Add-ons, each with how
    many there are), and the two toggle chips of the lists, *Running* (what the Running filter of the lists keeps) and *Starred*,
    two options that are independent, so two checkboxes and not a segmented choice. With none of them on the card holds what is on
    and what is starred; Running and Starred narrow that to what runs and to what is starred, and both together to what runs and is
    starred. A search finds
    everything that matches, whatever runs or is starred, and keeps its filters. The filters are kept from one visit to the next,
    as the lists keep theirs (Starred in the browser's storage, as `list-starred:<kind>` is), and *Clear filters* gives the default
    back and the keyboard to the search. The totals that the card used to end with ("3 playgrounds") are gone: the kind picker says
    how many there are, and the lists are in the menu.
  - A row is the list row on two lines, 56 px (`.dash-item`, a grid of fixed columns): the icon of its kind, then its name (a link to
    its page: a playground's page, an add-on's or a setup's row in its list, plain text until it is hovered or focused) with a star
    when it is starred (turned from here; its name for a screen reader says which: "Unstar Acme Bakery"), and under the name its state
    and its badges; then two slots of a fixed width that keep their place when what they hold is absent: its Start or Stop and its
    Settings. Its consoles are on its own page and in its list, so a row has no *Open*: three buttons on a row of a card this
    narrow would be the row. The star and the key are a row's icons, 26 px, as on the lists.
    - *State* is the lists' own (`itemState` of `list.js`), a dot and its word: Running, Starting and Stopping (the dot pulses, a thin
      bar under the word fills as the job goes), an issue in red, Stopped for what is on and does not run and Off for the rest, both
      muted; nothing while Docker does not answer. There is no pill for some states and plain text for others.
    - After the state, its badges, as the lists' rows have them (`noteBadge`, `.dash-marks`): *Personal*, and what its list says of
      it (why it cannot start, "Started by hand", "Needs settings", "Needs a license", the badge "Apply needed"), a pill in its tone,
      never a line of words that reads as a description; what a badge means is its popover, never words beside it. A badge is never
      cut: what does not fit beside the state goes under it. One that leads to its fix is a pill-button with an arrow, never an
      underlined link. The Hybrid gateway add-on says its own note here as on its list: the row of Gravitee Cloud says a count, not
      the state of the gateway that runs here.
    - *Start or Stop* is the list row's button (`itemButton`, `actOn`) with its word, 104 px whatever it says, on the key of the item
      (`playground:<id>`, `setup:<id>`, `addon:<id>`), with the server's word and its dialogs, never another action in its place.
      *Settings* is the key icon, which opens the item's settings, always in its slot.
    - What is on comes first, then a playground, the setups and the add-ons, each by name, an item once. A row stays until its button has
      said how its action went.
  - Eight rows a page and the lists' pager (`Pager`) at the foot, hidden when everything fits.
  - With no search, no filter, and nothing on and nothing starred the card says "Nothing running or starred", and offers a *Browse
    playgrounds* button when there is a playground; while what is on is not known (the core file does not load) it says why.
    With a search or a filter that finds nothing it says what the lists say (`emptyHtml`): "Nothing running" or "Nothing
    starred" when that filter alone empties it, else "No matches" and *Clear filters*, which gives the default back.
- **Resources.** What the lab's containers use of the machine, a [metric strip](#components). By default one line of 36 px with the
  edge of its card, as most visits want a glance (the owner's request of 2026-10-12: "by default a really thin line, you can then expand
  to see this"): *Resources* and its chevron, then "CPU 1.0%", "Memory 4.7 GB" in words, the versions of Docker muted at
  its end while the line has room (`summaryBody`; a read that failed says why in their place). The figures of the line are the strip's:
  the same reads and the same samples, written by the same `paintValues`. A click anywhere on the line, or Enter or Space on its
  toggle, unfolds it (`fold`), and this browser keeps the choice (`dash-resources` in its storage, read and written in try/catch).
  Unfolded, the line keeps its toggle and its name, and under it the strip of 64 px: CPU, Memory and Storage,
  each its label over its figure in a cell of its own apart by hairlines, then Docker, its label over its versions in the same
  markup ("29.7.2", then "Compose 5.4.0" muted), a section like the others. Every figure is one markup (`figure` of `dashboard.js`, `.dash-figure`): its value
  and what it is a share of, muted, on one baseline. CPU and Memory are as wide as each other, Storage takes what is left up to
  760 px (`--store`), and Docker the rest. Narrower than 1150 px of page, CPU, Memory and Docker share a line over Storage; on a phone, a cell over the other. Free disk space is not shown: Docker's data may be on another disk than the one that holds the home
  folder, and the card says what the lab uses, not what the machine is.
  - *CPU* and *Memory*: a label over the live figure in 15 px and what it is a share of in muted words ("of 6 cores", "of 7.7 GB":
    the cores and the memory Docker has, else the machine's), and a chart 28 px high that fills the rest of its cell, the same for
    both, a line and a dot at its end, with no area under it. Only the containers of the lab count (see
    *Live numbers*). Every cell is centered on the strip's middle (`.dash-cell`).
    A load that stops coming (nothing for 8 seconds, `SILENT`) is not kept as if it were live: its figures are dashes until the
    stream speaks again.
  - *Storage*: the label over what Docker stores with "in Docker", which is the total of `docker system df` (its images, containers,
    volumes and build cache), then how much of that is unused ("57.9 GB unused", the sum of what Docker says for each type, or "Nothing
    unused") with an info icon and a popover that says what it is: what no container uses (unused images, stopped containers, unused
    volumes and unused build cache), how much of each type and, when volumes are unused, that they can hold data, so removing one loses
    it; each type's line says its size and its unused share ("58.9 GB, 42.7 GB unused"). The word Docker uses for it, *reclaimable*, is not
    on the page. An icon button at the end, named for what it does (*Count storage again*; its tooltip "Count again, counted 1 min ago"),
    reads `docker system df` and Docker's versions again. Docker's data is the slowest answer: the figure says "Loading" until the server has
    it. It is a line of words, no chart and no bar: what Docker stores changes slowly and is seldom what a person came for (the owner's
    request of 2026-10-16: it took too much room for what it says), so the strip fits one row from 1150 px and the figure is in the strip,
    not in the folded line.
  - *What weighs most*: the label of CPU and of Memory has the information mark and opens a popover (`.dash-weigh`, written by `paintValues`
    from each sample's `top`, the load of each container): "Heaviest containers", the five that take most of the memory, or of the CPU as
    a share of the cores, each with what it takes, and for the memory what is left of Docker's ("3.6 GB left in Docker"). It answers what to
    stop to make room. A container is named as the Containers page names it (`Management API (API Management)`). With no container running
    it says "Nothing runs", before a sample "No load".
  - *Memory running out*: the memory figure, the strip's and the line's, turns amber from 85 % of Docker's memory (`WARN`) and red from
    95 % (`FULL`), as a container killed for want of memory comes after: the chart already shows the climb, the color says it is time.
    It cools down as it falls. A Docker that does not say its memory has no level.
  - Without Docker's figures (loading, Docker not reachable) there is no unused figure. Docker not reachable is said once, by
    the banner (and the sidebar's chip): Storage, CPU and Memory are dashes with no chart, and the page degrades as the Containers page does:
    no dot is colored, no count is said, the actions wait with the reason as their tooltip, nothing is to open.
  - **The tooltips and popovers of the whole Lab UI** (`tipHtml` of `ui.js`): the unused figure, the label of the load and the words of
    the Cloud use them. The Dashboard has no tooltip of its own, and no `title`
    of the browser's: the name of an icon button and the reason a button waits are tooltips of the Lab UI too.
- **The charts** are two SVGs that stay in the DOM and are moved from the clock, never rebuilt by a sample (`paintCharts`,
  `paintChart`). No library.
  - One `requestAnimationFrame` loop runs while the page is shown, Resources unfolded, the tab visible and a stream open, and
    stops otherwise: folded, no chart is drawn and no frame asked for, its figures still written as samples come. Each
    frame draws the line as the clock says it is, `LAG` (1.5 s) behind it, so that the right edge is between two samples it
    knows (interpolated there; the last value is held while a sample is late, no longer than `GAP`, 5 s) and the whole line
    slides left continuously. A sample is dated by the moment it came, not by the `at` of the server, which counts whole
    seconds. A line breaks where samples are more than `GAP` apart (nothing was listening). The width is always `SPAN` (60) seconds, from the first load: the scale never changes, the line grows in from the right
    and what is older leaves at the left.
  - The line is a quadratic curve through the midpoints of its points, which never goes beyond them, with no area under it (a
    clip of its own for each chart: `dash-clip-<id>`) and a dot at the right edge, a zero-length
    path with a round cap and a stroke that does not scale, so that it stays round at any width. Colors are the neutral
    tokens (`--text-2`, `--text`).
  - The top edge is the highest point against 10 percent at least for the CPU and what Docker has for the memory. It follows
    a rise at once and eases down (`EASE`, 0.8 s) when a high point leaves the window, so that the line does not jump.
  - The figure beside a chart is written when a sample comes (`paintValues`: a text node, not a redraw). With
    `prefers-reduced-motion` no loop runs and the chart is drawn when a sample comes, to the sample.
- **Live numbers** come from one long-lived `docker stats --format '{{json .}}'` process, behind `GET /api/dashboard/stats`,
  an event stream of at most one event a second (`cpu_percent`, `memory_used`, `containers`, `at`). Only the lab's containers
  are counted: `dashboard.lab_names` lists the names of the containers of the Compose project (`docker ps` by its label, kept 10
  seconds) and `Stats.feed` leaves out the others, what else Docker runs not being the lab's load. When the names cannot be told,
  every container counts.
  - It starts when a client connects and stops when the last one leaves. The page closes the stream when it is hidden or
    left, and opens it again when it shows (`visibilitychange`). A stream that the browser gave up on (an error answer closes an
    `EventSource` for good: `readyState` closed) is opened again after 3 seconds, then 6, 12 and so on up to a minute while it keeps failing, and no frame is
    asked for while it is dead.
  - `cpu_percent` is the sum of the containers' CPU divided by the cores Docker has, from 0 to 100. Docker's screen escapes
    are stripped from its output.
- **The machine's own numbers** are cheap local readings with the standard library: `sysctl` and `vm_stat` on macOS,
  `/proc/meminfo` on Linux and WSL 2, `os.cpu_count`, `shutil.disk_usage`. No `psutil`.
- **What Docker holds** is `data` of `GET /api/dashboard`, parsed from `docker system df --format '{{json .}}'`
  (`dashboard.parse_df`): for `images`, `containers`, `volumes` and `cache` a `count`, a `size` and the `reclaimable` size (what
  no container uses, never above `size`), the `running` containers, and a `total` with the sum of the four sizes and of the four
  unused sizes. Docker writes sizes in powers of 1000 (`58.9GB`), and the page does the same. The page says "unused" for what
  the answer calls `reclaimable`.
- **When it reads.** `GET /api/dashboard` (`docker`, `machine`, `data`, `products`, `pending`) when the page
  opens, when what the versions depend on moves (Docker answering, the state of each group, the tag each container runs:
  `signature` in `dashboard.js`, checked at each draw, one read at a time) and by *Count storage again*. Docker's answers are cached for 60
  seconds: `docker info`, `docker compose version` and `docker system df` (about 2 seconds) are read when the page opens, so
  quick trips between pages read nothing again. *Count storage again* reads them again (`GET /api/dashboard?refresh=1`) and renews the
  cache. Nothing polls them. The one thing the page asks for again is the latest versions, every 3 seconds while the server
  says it is still reading them (`pending`), and a read that failed, after 10 seconds (`RETRY`), so that the page does not stay
  half empty. The page is drawn before its first read: the state alone, a spinner where the version is (the version saved is not
  said: it may not be the one that runs), nothing for the update.
- A click on a name acts on nothing: the actions are the buttons, each one click, with the confirmations the pages that own
  them ask.
- Each block is written again only when its own text changed (`setHTML`), a row of a card on its own (`keyed`), and a cell of
  Resources is a block of its own: the chart's figure and path are written apart, so a sample never draws a cell.
- `#/stack` still works and leads to Containers.

### Containers

The Containers page acts on containers and keeps what is on (`stack.py`).

- **Start** runs the platform and everything that is on, what was stopped from Containers too, but a seed and what
  needs a license that is not usable ([License](#license)). The head shows it while something that is on does not run,
  and it asks first when it starts more than the core or makes anything again.
- **Stop** stops every container of the project.
- **Restart** restarts what runs, as it is: what was stopped stays stopped, nothing is made again.
- **Update** pulls, then makes again what runs with what it pulled, as an apply would: every service that runs is named,
  Compose makes again what changed, building again what is built here, and what failed starts again. What was stopped
  stays stopped. What it makes again is not known before its pull, so it asks nothing.
- The head shows Stop, Restart and Update while something of what is on runs, an add-on alone too (`platform.restart`,
  `platform.update`: `needed`, else "Nothing runs"). Its primary is Start while the platform is stopped, else Apply, the last
  button before the menu, as in every head.
- **Remove containers** keeps the volumes. **Reset** deletes them too, turns off the playground that is on
  (`platform.reset_turns_off`, not in an on file with the user's own changes, which it does not write) and empties what
  every seed recorded: its next Start sets it up again.
- With an on file with the user's own changes, Start and Update name every service Compose reads in it, but a seed.
- A group, a container, and the containers of an add-on or a playground start, stop and restart on their own line
  (`/api/(addon|playground)s/<id>/containers/<do>`, the catalog's Restart of an add-on too). The item stays on or off. A
  one-shot that ended well has no Start there, a playground's seed neither Start nor Restart (`role`: its playground's
  Re-seed runs it).
- None of these removes what was started outside the Lab UI.

### Behind what is saved

- What keeps running has `restart: unless-stopped`: a container that crashes starts again, one stopped from Containers
  stays stopped when Docker restarts. `make check` refuses `restart: always` in the core and in what is shared.
- A container runs behind what is saved (`model.behind`) when it was created from another configuration than the one
  Compose would make now, or runs another image than its tag names here.
  - The first is its `com.docker.compose.config-hash` label against `docker compose config --hash "*"` of what is on
    (`project.saved`).
  - Only what Compose reads counts: an add-on that is off and runs, an apply would leave as it is.
  - Its item, its group and its line show the badge "Apply needed" (`applyBadge`, `note_apply` in the note the server
    gives), its name for a screen reader the reason (`reasons.py`, one plain line, `note_title`) and its popover the same causes
    as data (`reasons.causes`, `note_causes`: the kind, `tag` with `saved` and `running`, `image` with its `tag` and `built`,
    `settings` with their names, `config`; and `services`, who a cause is about when it is not all of the row's). The rows of
    `platform.behind` carry them too, with `count`, how many of their containers run behind. The cause is the first that holds: another tag saved
    than the one that runs ("4.12.20 is saved, 4.13.0-alpha.4 still runs"; the system the two tags share, `-debian`, is left
    out of the words, and said in full when it is what differs), the same tag on another image ("Image 3.0.2 changed since it
    started"; also a tag that a pull moved, which Docker then names by its id or digest, never read as a version), a
    configuration that changed, with what differs told when it can be (`drift.py`: the variables of the container against the
    ones Compose would set, compared as digests, named by the `.env` setting that holds them when one does, never a value, and
    the labels Compose sets, which are no word of a person's and are said as the configuration): "Its settings changed since it started:
    INTERNAL_API_PASSWORD", else "Its configuration changed since it started". Services that say the same are one
    clause, each set of settings its own, never a union said of all. A group, an add-on and a playground say it for their services
    grouped by cause, naming them while they are three or fewer, and with no name at all when one clause covers all of them. The
    banner's rows and the head begin with the owner's name ("API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs."; "Databases:
    MongoDB (8.0.29 is saved, 8.3.11 still runs); Redis (...)"), `platform.behind` giving a line for each of them: the head's
    tooltip and the banner's badge are those lines, in the words of the rows, with no count of "services" and no collapsed
    sentence for a few products. A group's version is then the tag it runs, without its system and in the color of every version,
    its tooltip the one saved ("4.12.20 is saved"), and its containers have no badge of their own: the group's badge says it for them, as the head's says it
    for the groups (one level, one badge). The license chip of the core, the license dialog, the editor's license line and a playground's License check show it of a license saved that
    what runs does not read yet; with no license saved, while one still runs, it says that none is saved.
  - The banner's notice says how many containers run behind, its popover which and why, with Apply, on every page but
    Containers and the Dashboard, whose heads have the badge and Apply, Images, whose head's Apply (or Save & apply) is the
    one action, and Plugins, whose head's Apply is the platform's while no plugin is chosen, with the badge beside its title
    (`PLATFORM_HEAD`, `OWN_APPLY` in `sidebar.js`): there it is not drawn, so that the page says it once and has one Apply. The second button of the editor and of Images reads Apply while nothing is unsaved.
  - These Applies show only while something of what is on runs (`platform.apply.needed`): stopped, an apply would do
    nothing, and its Start makes them again.
- An apply (`stack._apply`) runs `up -d --no-deps` of what runs behind what is saved and of what failed whose
  dependencies are fine, after the one-shots they depend on. What was stopped stays stopped, what has no container is
  not created (but what needs a license, held back until one is usable, is started once it is), nothing is removed. While nothing of what is on runs it does nothing ("Nothing runs: applied on next
  start").
- `POST /api/effects` says before it what it makes again and starts again. It is read only.
  - Of what is saved (`{}`), or with a draft not saved yet: a `.env` form's values (`{file, values}`), a compose file's
    text (`{path, content}`), versions and registries (`{images}`). The page asks it of a draft only: an Apply without
    one says what the state's `platform.apply` says.
  - It answers `{restarts, starts, runs, exact}`. `runs` false: the apply would do nothing. `exact` false: what the
    draft makes cannot be known (an add-on's value another file reads too, for one), or what is saved could not be read,
    and every running service of what it may change counts ("May restart").
- What an add-on or a playground builds comes as its pull policy says (`pull_policy: ${<ID>_PULL_POLICY:-build}`: built
  here, or `always` pulled from the registry the repository's CI pushes to). The Lab UI never passes `--build`, which
  would ignore the policy.
  - A build that changed nothing gives the tag a new image id (Docker's image store records each build in it) for the
    same image, which Compose keeps running. "Apply needed" compares the manifest the container was made from
    (`compose.same_image`), never the ids alone.
  - A service that runs the image another one builds reads "local build" too.
  - A pull a registry refuses says "Not logged in to <registry>" in the warn tone. Compose then runs the image it has,
    or builds it.
- What is read from an image (the plugins it bundles, the license model) is kept by the image the container runs, and
  read again once it runs another. An image not pulled is said so ("<image> is not pulled"), with Pull images.

### Images

- A component is read from the `image:` lines of the Compose files (`images.py`). Its version is chosen among the tags
  its registry lists, and is a draft until it is saved. A tag has a variant, the system its image is built on
  (`-debian`, `-chainguard`: `VARIANTS` in `js/core.js`), chosen with a Variant control when the tags come in
  several.
- The components are in the order of the groups of the platform, each followed by the image of an add-on that runs a gateway of
  it (`group` of `images.components`: the Hybrid gateway's after API Management's, *API Management* under its name), then the
  others by the name of what owns them.
- The page has the sections of Containers, in the same order, each a band that folds (`stack-sep`, its name a button, the browser
  keeping the choice: `platformOpen`, `addonsOpen`, `thirdPartyOpen`): the platform's, named as the core's line is (the Gravitee components
  of a group, the Hybrid gateway among them), under it, on a band of its own (`.sub`), the components of the platform's own add-ons (those of the category named after the core,
  `ofPlatform`: the federation agents, Kafka Desk, the Kubernetes operator's tools), then the third-party ones, which are not Gravitee's
  (the databases, Kafka, Jade, a playground's). Folded, a band sums up what is on under it and says *Will change* when it hides a change; a
  search shows every section open, their names no buttons; an address that names a component opens its section.
- A version that other images take is chosen among plain tags only, with no Variant control, because a variant tag of
  the first image may not exist for the others. That is the case when the component's images come from several
  repositories, and when another component's default is made of it by adding text (the gateway's
  `${APIM_VERSION}-debian`). A component that another follows with nothing added (Kiosk follows Elasticsearch) keeps
  its variants.

### Gravitee Cloud

- A platform is a remote Gravitee this lab is registered with (`platforms.py`), Gravitee Cloud today: a record (id, name,
  kind, address, account id, hrid and name) in `.lab/platforms.json`, which holds no secret, and its tokens, which are
  settings of the core's `.env` (`PLATFORM_<ID>_TOKEN`, the Account Token; `PLATFORM_<ID>_ENV_<ENVIRONMENT>_TOKEN`, a Cloud
  Token; `PLATFORM_<ID>_GATEWAY_<GATEWAY>_TOKEN`, the token of a gateway the Lab created; the ids upper-cased, `-` as `_`).
  Compose never reads them, so no container is recreated for them, and the Settings form lists only keys that a compose
  file names. No answer, error, job line, command or record holds one. An id Cloud gives (an environment, a gateway) is
  one segment of letters, digits and dashes (`platforms.ID`), or it is left out: it reaches a path, a setting name and a
  folder.
- Three credentials, each asked where its absence is felt, in the order the work needs them.
  - The **Account Token**, with the **Account ID**, in *Connect account*. It reaches its account by the account's UUID
    alone: Cloud answers 401 to the list of accounts, to `/me` and to an account's hrid. So the person gives the Account
    ID, which Cloud shows next to its Account Tokens, and the Lab checks the pair with one call, `GET /accounts/{id}`
    (`cloud.account`). It lists the environments and the gateways, and creates and deletes gateways.
  - The **hybrid gateway's token** is never asked, named or shown: the Lab creates the gateway, keeps the token and
    writes it, so the two things Cloud itself calls Cloud Token never meet in the UI.
  - The **Cloud Token** (Settings > Cloud Tokens, type Automation) is asked for one environment only when something is
    deployed there, on its line or from a *Before you start* line. It is the only credential that deploys.
- A Cloud Token is read without being verified (`cloud.claims`: its kind, organization, environment, expiry, gateway and
  region; never logged or answered) and refused in plain words before it is kept: *This is a gateway token, not a Cloud
  Token*, *This is not a Cloud Token*, *This Cloud Token has expired*, *This Cloud Token is for Test, not Development*,
  *This Cloud Token is not for an environment of <platform>*, *Gravitee Cloud refuses this token*, *This Cloud Token
  cannot read API Management*. Cloud Gate is asked one cheap question (the first API of the environment). Its line reads
  Valid, Not set, Expired (the claim's expiry has passed, known locally) or Refused (the last answer of Cloud Gate was
  401 or 403, remembered in memory until the token is replaced or forgotten).
- Cloud Gate's address is not stored. For a platform whose host ends `.cloud.gravitee.io` it is
  `https://<region>.cloudgate.gravitee.io`, the region being the token's `cpg` claim; any other address (the fake Cloud, a
  proxy, a later kind) is its own Gate, so a fake token's region never sends it to the real one.
- **An Account Token that Cloud refuses makes every environment of its platform unusable**: they are listed with that
  token alone. A deploy is blocked until *Replace Account Token* (in the card's connection, and in its block, checked against the Account
  ID that was kept; the record, the Cloud Tokens and the kept gateway tokens stay). Cloud does not say revoked or
  expired, so the state reads *Account Token refused* for both.
- **No license of the organization is read or judged**, and no sharding tag. Cloud's license model is not the one the
  local images give, so a judgment would be wrong. A deploy that Cloud refuses shows
  the seed's last line. The gateways that Cloud hosts are ignored; only hybrid ones are listed.
- Its page (`js/pages/platforms.js`) is a card for each account, built to answer three questions about an environment and
  nothing else: does a gateway of it run on this machine, may the Lab deploy into it and what did it deploy, and what is the
  one thing to do next. It was a table of accounts that opened on eight near-identical empty lines for four environments (a
  heading, "No hybrid gateway" and a heavy button, a "Cloud Token" line and another), with four unlabeled icons on the
  account's row; the redesign of 2026-10-11 removed them: no empty block is repeated for an environment, no icon is alone, and
  a term the page uses is explained where it appears.
- **The card** (`.card.plat-card`, a container of its own: `plat`). Its head is the account, not the page (the page is already
  *Gravitee Cloud*): a 32 px tile with the cloud mark, the account's name at the card-title size (the account's own name and its hrid
  are the tooltip), the host of its address under it, muted, its state as a pill, the key of its connection (an `.icon-btn` of 32 px with
  the key icon, as the settings of every add-on, its tooltip *Manage connection*, named *Manage connection, My Account*), *Open console*
  (the account's address, in a new tab, named for its account for a screen reader) and a labeled *More*: *Rename*, *Check again*, then,
  in red, *Remove account*. The key opens *Connection of My Account*, a short modal dialog of facts (`.pairs`): *Account*, its name;
  *Account ID* in mono with the copy button of every dialog (`copyButton`); *Address*, the URL; *Account Token*, a state, *Set* or
  *Refused* in red when the last check says so (why in its popover), never its value; then *Replace Account Token*, which closes it and
  opens that form, and *Close*. The Account Token is replaced from there alone, and from the notice of an account whose token is refused. The state is the answer to the last check (`POST /api/platforms/<id>/check`): *Connected*, *Account Token
  refused* (Cloud refuses the Account Token), *Unreachable*, *Loading* before there is one and *Checking* while a person asks again
  (the reads the page makes by itself change nothing until they find something). Under the head is the table of environments or, for
  an account that cannot list them, a notice (`noticeHtml` in `.plat-notice`) that says the cause as its text, *Environments cannot
  be listed* as its title and the one action that changes it: *Replace Account Token* in a red notice when the token is the cause,
  *Check again* in an amber one when Cloud does not answer. An account with no environment says *No environment in this account*,
  calm, with *Check again*. An account that answered before and does not now (a read that fails by itself, not a refusal) keeps its
  last table under a notice titled *Showing what was read before*: a moment of trouble does not empty the card. While the first
  check runs, rows of bars stand where the table will be. `#/cloud/<id>` brings a card into view, focused. The page before any card
  is the one empty block of every page (`.empty`): *No account connected* and its action, *Connect account*, a plain button (the
  primary of a page is the banner's Start or Apply). The page's
  address is `#/cloud`; `#/platforms` and `#/platforms/<id>` lead to it (`MOVED` in `js/core.js`).
- **The table** is one grid for its heads and every row (`.plat-grid`), four tracks: the environment (up to 260 px), the gateway, the
  deploys and the actions (248 px, the same in every row), so that states line up from one row to the next. It is the table of
  the Dashboard's Gravitee Cloud cards, drawn by the same cells (`gatewayCell`, `deployCell`): the heads *Environment*, *Gateway* and *Deploys* are drawn as the products' (12 px medium,
  `--muted`, one look), only when an environment is in use; what the two terms mean is the popover of the information icon next
  to the head (`TERMS` in `js/platform/environments.js`, `tipHtml`, which the keyboard reaches): a hybrid gateway runs on this machine and
  gets its APIs from the environment, *Deploys* means the Lab can create the APIs of a playground there with a Cloud Token of the
  environment, created in Gravitee Cloud. A cell is a state on one line (a dot and a word, `statusHtml`), and what to know of it
  (a gateway's name, version and address; a token's expiry; what each playground created) is its popover, not a second line of
  muted words under it. Rows are 56 px, as the Dashboard's environments are (48 for a quiet line, whose *Set up* has 8 px around it), and every cell is
  centered. The environments *In use* come
  first, in the account's order, under that label (a `.stack-sep` band in a `rowgroup` named for it, the band itself hidden from a
  screen reader), then those *Not set up* under theirs, each a quiet line: its name and *Set up*. With more than three of them, the
  first two show and a quiet line, *Show N more environments* (*Show fewer environments* once open), lists the rest: a line that
  would hide one only takes the room of the one it hides, so it is not drawn for one. The shared popover carries the name of a
  gateway and what was deployed: no `title` attribute holds information. An environment with more gateways than the one its state is
  about says how many after it (*2 gateways*, `.plat-count`, a quiet button named "2 gateways in Development"), which brings the list of
  the account's hybrid gateways into view, the keyboard on its band: no *+1* hides names in a tooltip. The card's width
  decides the layout, not the window's: four tracks from 940 px; below, the name and its actions over two cells (*Gateway*,
  *Deploys*, each with its label, and the explanation of the term on the first row's), top-aligned; below 600 px one cell over
  the other, the actions across, and the head in three lines (the name, the host with the state, the buttons); the table never
  scrolls sideways.
- **The hybrid gateways** (`gatewayList` of `js/platform/environments.js`, the Dashboard's count too): under the environments, a band *Hybrid
  gateways* (`h3.stack-sep`, `data-gateways-of`, which the count brings into view) and a table of its own (`.plat-gw-table`, `role="table"`
  named *Hybrid gateways of My Account*), every hybrid gateway of the account, hosted ones left out, as Cloud lists them (the check's
  `gateways`: `here`, `kept`, `connection`, `status`, `environmentId`, `version`, `domains`; Cloud gives no last connection), and the one
  Cloud deleted while it runs here (`orphan`, named by its id). The heads *Gateway*, *Environment*, *State*, *Version*, *Domains*; rows of
  48 px: its name, its environment, its state in the words of an environment's Gateway (*Running here*, *Connecting*, *Stopped here*,
  *Connected elsewhere*, Cloud's *Pending*, *Deleting*, *Failed*, *Removed in Cloud*, the add-on's own *Needs a license*; *Not connected*,
  a gray dot, for one that no one runs and the Lab does not keep), its version and its domains in mono (cut, whole in their tooltip),
  then the two slots of the actions: *Stop gateway* for the one that runs here, *Start gateway* for one whose token the Lab keeps (the
  add-on's own action on `addon:<id>`, which swaps the one that runs here, its tooltip *Stops the gateway of Development*, waiting with
  the add-on's note while a license is missing), *Replace gateway* for the one Cloud deleted; *More* has *Show logs* and *Copy address*
  while it runs here, then *Remove gateway* in red, the confirmation of the environment's. The one that runs here comes first, then the
  connected ones, then the others, each in the account's order of environments. None: the empty block, *No hybrid gateway* and *Set up
  Development* (the Set up of the first environment) when an add-on runs gateways here. No *Open console* in a row: Cloud has no
  address for a gateway, and the card's head opens the account's. Under 940 px of card a row is three lines (its name and its actions,
  its environment and its state, its version and its domains, each after its label of 96 px); under 600 px one cell over the other,
  a state too long for the line wrapping rather than cut.
- **What an environment says** is `environmentStatus(platform, check, env, {late})` (`js/platform/environments.js`), rules only. It returns `{id, name, used, gateway, deploy, deployed, summary, action}`.
  - *used*: it holds a hybrid gateway (any: one the Lab cannot use is there to be removed), a gateway the Lab lost in Cloud, a
    Cloud Token (in any state), or a playground deployed there.
  - *gateway*, the one of the environment in the order the Lab prefers: the one that runs here, one it keeps, one that is
    connected (`others` are the rest, `count` all of them), as `{key, dot, word, tip, details, version, address, name, id, here,
    kept, others, count}`: a dot and a word, and a popover titled with its name whose rows are its version and,
    while it runs here, `address`, the first domain that is not the name of its container, in monospace: *Running here*, *Connecting* (it runs here and Cloud has not seen it, amber and pulsing), *Not connected*
    (`late`: it has been so for 2 minutes, an amber dot, its tooltip says to read its logs), *Starting*, *Stopping*, *Stopped here*
    (a ring while the add-on is on, gray while it is off), *Connected elsewhere* (a ring: another machine runs it, its name under
    the word), *Not created* (muted), *Removed in Cloud* (red: `check.orphan`, the gateway whose token the add-on holds and Cloud no
    longer lists; *Still runs here* or *Its token is still set* under it), and Cloud's own *Pending*, *Deleting* and *Failed* while
    it is not deployed. For the gateway that runs here the add-on's own warn or error note replaces them: *Needs a license* (its
    own state, `key: license`, amber: the add-on cannot start, so no gateway of the platform can), *Needs settings* (`key:
    settings`, amber: a value of the add-on is not set, which its popover names), *License expired*, *Failed*, *Hybrid gateway
    exited (1)*, *Apply needed*; a dot never green while it does not run; its container's state while it does not run comes next. While Docker does not
    answer, what runs here is not known: only what Cloud says is (*Connected*, *Not connected*, no colored dot).
  - *deploy*, as `{key, dot, word, note, tip, expires, soon}`: *Can deploy* (a valid Cloud Token, and a gateway the Lab keeps the
    token of, which a deploy goes through), *Needs a gateway* (a valid token and none: the ring, and *Cloud Token set* under it),
    *Needs a Cloud Token*, *Cloud Token expired*, *Cloud Token refused*; `note` is *Cloud Token set* or, when the token expires
    within 14 days, *Expires in 5 days* (*today*, *tomorrow*). The cell (`deployCell`) says one of them, the first that holds:
    what the playgrounds deployed there, from the records the Lab keeps for each place ("Acme Hostels API deployed", "2 playgrounds
    deployed"), that the token is about to expire (amber, an amber dot), *Cloud Token set* while the gateway's column says there is
    none, else the word. Its popover has the rest: the word, the expiry date and each deployed item with what it created; a token
    not set has the explanation of a Cloud Token instead.
  - *action*, the first that applies: *Add license* (*Replace license* when it expired or lacks a feature: the add-on cannot
    start), *Settings* (a value of the add-on is not set: its settings form, `openSettings`), *Create gateway* (no gateway of the Lab's here; *Replace gateway* for one that another machine runs or that Cloud
    lost), *Start gateway* (the add-on does not run, or another gateway runs: its tooltip says *Stops the gateway of Test*), *Add Cloud
    Token*, *Stop gateway*; but *Replace Cloud Token* comes before the gateway when the token expired or is refused, the one thing the
    row says in red; *Set up* for an environment not in use. Whether the add-on runs is read from
    its state, not from the verb of its action, so that a gateway started outside the Lab UI is stopped by its `secondary` Stop.
    Nothing to run gateways (no add-on that declares one) means no gateway action. The gateway comes before the token because a
    deploy needs both and the gateway is the one that can take a minute.
- **One action, then More**, in two slots of one width in every row: the action (160 px, *Set up* of an environment not in use
  too) and *More* (80 px; its place is kept on a row that has none), so that every action starts and ends at one place.
  *Start gateway* and *Stop gateway* are the add-on's
  own action (`addon:<id>`), so that the Hybrid gateway row of Add-ons, the list of the gateways and this button show the same job at
  work; another gateway of the environment is started and removed from its row of the list;
  they wait for Docker and the running job, and say why when they cannot (*Wait for Start Acme Hostels*). The others open a form.
  *More* (labeled, 32 px, a menu `openMenu`) holds what is left, in groups, and is not drawn when there is nothing to hold: *Stop
  gateway* when the token is the action, *Start gateway* (or *Create gateway*, *Replace gateway*) while a token that expired or is
  refused is, *Show logs*, *Copy address* (its hint is the address), a *Start gateway* waiting for the license (disabled, with the reason as its hint), *Add Cloud Token* or
  *Replace Cloud Token*; then, in red, *Remove Cloud Token* ("Deploys stop"), *Remove gateway* ("Deleted in Gravitee Cloud"; its
  name is in the title of its confirmation), and *Stop using Development* ("Removes its token and gateway", only when the Lab keeps both a token and
  a gateway of the environment). A hint says what follows, never what the label says again; it stays on the line of the label,
  cut with an ellipsis when the menu is narrow. There is no *Open console* in a row: the Lab does not know a console address per environment, so it
  would lead to the platform's, which the card's button does.
- The forms are modal dialogs (`dialogs/platform.js`), the same wherever they open from (the page, the Dashboard, or the plus and
  key buttons of *Before you start*, which preselect the environment): *Connect account* and *Set up* are the large dialog of the
  [wizard](#dialogs-tabs-and-focus), the others short forms. The title of a dialog is the label that opened it (the
  wizard's is what it does, *Connect Gravitee Cloud*), and a form opened for one environment says it in its title (*Create gateway for Test*, *Add Cloud Token for Development*) and does
  not ask which: its Environment field exists only for a caller that gives none. Cancel is the button marked `data-cancel`:
  information icons are buttons too, and come first. Every field says what it is in its label, and what its term means is the
  popover of the information mark beside the label (`field(id, label, control, term)`, `TERMS`): no line of help under a field,
  no placeholder that says what the label says. A dialog opens with the keyboard on its first field.
  - *Connect account* opens *Connect Gravitee Cloud*, a wizard of two steps, *Account* then *Environment*. The first: *Open
    Gravitee Cloud* (the console, following the URL under *More options*) and under it *No account? Register for free*, a quiet
    link to the registration; then two numbered lines, the numbers of the picture beside them (*1 In Settings, Account Tokens,
    generate an Account Token and copy it*, *2 Copy the Account ID, at the top of the same page*); then, after a hairline, Account
    Token (password) and Account ID, each label with its information mark: what an Account Token is
    and where it is (Settings, Account Tokens), and why the Account ID is asked as well (a token cannot say whose it is); then
    *More options*, folded, with the URL (prefilled with Europe's: the fake Cloud and a Cloud of one's own change it). No Name:
    the account's is read from Cloud, and *Rename* in the card's menu changes it. An Account ID that is no UUID is said under its
    field once it is whole or left (*The Account ID is a UUID, as the Account Tokens page shows it*), and the button waits with
    that reason (the field `aria-invalid` and described by the line). An Account Token cannot say whose it is (every call outside its account answers 401,
    [cloud.md](../docs/cloud.md#why-the-account-id-is-asked)), so the id is asked. Its button says *Connect*, and stays at work
    while the account is registered and its environments read. Then the pages are told (the Gravitee Cloud page and the Dashboard
    show the account behind the dialog), the Account Token is cleared from the dialog, and the same dialog goes on to its second
    step, the first step checked: no toast says it again. An account with no environment, or whose environments cannot be read,
    ends the wizard with a toast instead of an empty step (*My Account connected, with no environment yet*; or *My Account
    connected* and the reason). The second step is optional: *Skip for now* closes it and the account stays connected, as it does
    with Escape and the cross; there is no Back, the account exists.
  - *Rename My Account*: the name alone, filled, and *Save*, which waits until it changed.
  - *Replace Account Token*: the new token alone, where to create it in the popover of its label.
  - *Create gateway for Test* (from this page; *Create hybrid gateway for Test* from *Before you start*): Name (*Gravitee Lab -
    Test*: named for its environment, so that two are told apart in Cloud and the confirmation *Remove gateway of Test* is not
    mistaken for removing the Lab). No domain and no closing notice: the domains come from the add-on and the toast says *Created*.
    On this page its button says *Create and start*: the gateway made is started through the plan and the confirmation of a Start
    (unless the add-on lacks a license, which the row then offers), and when another environment's gateway runs here one line says
    *The gateway of Development runs here now: starting this one stops it*. From *Before you start* it says *Create*: the
    playground's Start starts it.
  - *Add Cloud Token for Test*, *Replace Cloud Token for Test*: the Cloud Token, where to create it (Gravitee Cloud: Settings,
    Cloud Tokens, type Automation) in the popover of its label; the token must name that environment.
  - The second step of the wizard, *Environment*, and *Set up <environment>* (*Set up* of a row) are one panel: in the wizard the
    environment is chosen first (*Environment*, Development first) and its button says *Skip for now* and *Set up*; from a row it
    is that environment's dialog alone, titled *Set up Test*, with no steps and no choice, *Cancel* and *Set up*. Two parts in the
    order a person thinks of them, sections of their own, each a box to tick, ticked, with the information icon of its term, its
    fields shown while it is ticked (never a disabled field): *Run a gateway here* (Name, and the same line when another gateway
    runs here) and *Allow deploys* (*Open Gravitee Cloud*, to the account's own console, the two numbered lines of the two
    pictures, *1 In Settings, Cloud Tokens, generate a Cloud Token* and *2 Choose the type Automation and the environment Test,
    confirm, then copy the token*, then the Cloud Token). The guide shows one picture at a time, *1 Generate* or *2 Choose the
    environment*; the Cloud Token field and ticking *Allow deploys* show the first. With both left out the
    button says *Choose a step*. The token is sent first, because it is proved before it is kept and asks nothing of Cloud, then
    the gateway is created and the page starts it; a step that is done stays done (*Done*, unticked) if the other one is refused, so
    that sending again does what is left, and the page reads the platform when the dialog closes, however it closes, so that the
    card shows what was kept; in the wizard the environment can no longer be chosen once a part is done for it. Without an add-on
    that runs gateways its first part is not offered.

  A refusal stays in the form's problem line, in the server's words; a token or an Account ID that Cloud refuses is one
  sentence, *The token or the Account ID is not right*, because Cloud does not say which. The button says what it does
  (*Connecting*, *Replacing*, *Creating*, *Adding*, *Setting up*). Every secret is cleared as its dialog closes, however it closes.
- **Remove** asks first, in the dialog of effects, and the word is *Remove* in the menu, the title and the button. An account:
  *Removes* it with its tokens, *Turns off* the Hybrid gateway when its gateway runs here, and what *Stays there*: its gateways
  and what the playgrounds deployed ("Acme Hostels API: 3 APIs in Development"). A gateway, titled *Remove gateway of Test*:
  *Removes* it (in Gravitee Cloud first; on a Cloud error nothing local changes; for one that another machine is connected to the
  hint says so) and *Turns off* the add-on when it ran here. A Cloud Token: *Removes* the token of the environment, and *Stays
  there* the token in Gravitee Cloud. *Stop using Test*: *Removes* the gateways in Gravitee Cloud and the token from the Lab, *Turns
  off* the add-on when one ran here, *Stays there* the token in Cloud and what was deployed; the calls are those of the removals
  above, in order, the gateway that runs here last because its removal turns the add-on off.
- **Reading.** The page reads every account when it opens and again after each action. A gateway that runs here and has not
  connected yet is read again every 5 seconds for a minute, every 10 for the next, then every 30, and is said *Not connected*
  after 2 minutes; an account that does not answer is read every 30 seconds; every card is read while a gateway job runs and once
  after it; none of it once the page is left; a gateway that does not run is never polled. A redraw replaces what it changes, so
  the control a person is on is found again by its card, its attribute and its environment: keyboard focus stays where it was.

### One gateway runs here

- The add-on that runs the gateway of a kind of platform says so with `x-gravitee.gateway` (`platform`, `service`,
  `token`); the Lab finds it by that key. Its domains are derived from the service's first published port, never listed:
  `localhost:8090` for a browser and `apim-hybrid-gateway:8082` for a container of the lab. Cloud matches the `Host` of a
  request against the domains registered for the environment, so both are registered when the gateway is created.
- **It is a gateway of the group that runs its image** (`gateway.group`, `project._gateway_groups`): the group of the platform one
  of whose services runs the same image as the gateway's service, whatever its registry and its tag (`apim-gateway`: API
  Management), never a name. The pages that list services or images by product list the add-on's there: the services picker of
  Logs has the Hybrid gateway and its Redis (its other services follow its gateway, whatever image they run) in API Management's one section, under a title
  naming the account and the environment it serves; Images has its image right after API Management's, *API Management* under its name. It stays an add-on: on, off, its
  Start, its Stop, its settings and its row of Add-ons are its own. Containers keeps it under *Add-ons*: a group there is what its
  Start, Stop, Restart and Apply act on and what its state counts, and the band of the platform holds the core's file, settings and
  license, none of which is the add-on's. The Dashboard's products are the core's versions and consoles; the hybrid gateway is counted
  in its row of Gravitee Cloud, and listed on the Gravitee Cloud page.
- **One hybrid gateway runs on this machine at a time** in this wave. Several at once is a later wave, and nothing in the
  data model blocks it: ids, setting names and keys already carry the gateway's id.
- *Here* is the gateway whose id is in the claims of the add-on's token (`HYBRID_CLOUD_TOKEN`). *Kept* is a gateway whose
  token the Lab holds: its own setting, or the one in `HYBRID_CLOUD_TOKEN`. The gateway of a place is the one that runs
  here when it is in the place's environment, else the first kept gateway of that environment, else none.
- **Creating a gateway never replaces the one that runs.** Its token is kept, and written as `HYBRID_CLOUD_TOKEN` only
  when the add-on holds none, so the first gateway ever created runs at once. The Gravitee Cloud page starts what it creates, which
  is a Start of that gateway and swaps when another one runs.
- **The swap** is a Start: on the line of another gateway, or of a playground whose place is served by another gateway.
  A dialog says first what it does: *Restarts* the Hybrid gateway (*Recreated to take the changes*), *Serves* the place
  from now on, *Stops serving* the place it served. The job writes the kept token as `HYBRID_CLOUD_TOKEN`
  (`platforms.swap_gateway`) before Compose recreates the container, whenever the place's gateway is not the one here,
  also when the token the add-on holds belongs to no listed gateway any more. The APIs of the environment that stops
  being served stay in Cloud and answer from nothing here; the note says *Stops serving* for that reason.
- **The gateway outlives the playground's Stop.** It is an add-on that a deploy turned on and is independent from then
  on, like any add-on a person turned on. The Lab keeps no record of who turned it on; it stops from its own line.
- A gateway that Cloud says is connected from another machine reads *Connected elsewhere*. One made in the Cloud console
  and pasted into the add-on's settings is the one that runs here, and loses its token when another is started.

### Places and Deploy to

- **A place** is where a seed creates its APIs: *This machine*, or one environment of a registered platform. The
  containers always run here. A place is labelled *<environment> (<platform>)*, as in *Development (My Account)*, in the
  picker, the dialogs, the tooltips, the records and the Reset text: never a middle dot or any bullet between the two
  (`places.label`, `placeLabel`). The words are Deploy to (the picker), Deploys to (the last step of a dialog) and
  Deployed to (the panel). The UI never says place, target, mode or active platform; code and routes say `place`.
- **A place's id** is `machine`, or `<platform id>/<environment id>`. A platform that cannot be used is one place with
  its own id. Records, jobs and the localStorage key take these ids.
- **The places are read in the background** (`places.py`): each platform is asked once a minute at most, served stale
  while a read runs, `Loading` before the first answer, so that `/api/state` never waits for Cloud. What this machine
  keeps (the tokens, the gateway that runs here) is read at each call. After a platform action the platform is read
  again at once (`invalidate`). A deploy asks live before any job (`verify`: the platform again, then one question to
  Cloud Gate) and fails with the reason.
- **A place is ready, missing or unusable.** Missing names the first thing, the gateway before the token: *No hybrid
  gateway*, *No Cloud Token*, *Cloud Token expired*, *Cloud Token refused*, and its sentence for a disabled button
  (*Set up a gateway in Development (My Account) first* when the environment has no gateway at all, *Create a hybrid
  gateway for Development (My Account)* when it has only gateways the Lab cannot run, *Cloud Token for Development (My Account) is not set*, *has
  expired*, *Gravitee Cloud refuses the Cloud Token of Development (My Account)*). Unusable is a whole platform: *Invalid
  token*, *Unreachable*, *Loading*.
- **The catalog never says where it deploys.** No playground or setup names a platform, and an add-on names a kind only
  to say that it runs that kind's gateway here (`x-gravitee.gateway`). The Lab computes it from a playground's compose
  file, its `seed/` folder and its `requires` (`project.asks`), against what an environment runs (`project.judge`).
  Nothing of what a platform runs is written in the code: `platforms.capabilities` reads it from the gateways of the
  environment in the platform's read (each gateway's scope, named as the core's group whose plugins folder it is, at
  the version of the oldest), `platforms.plugins` from the mounts of the gateway add-on's service, and nothing of this
  machine's core changes. An environment with no gateway is not set up: nothing is compared, and the place's reason is
  *Set up a gateway in <place> first*. The state carries `deploys_to`, the kinds a playground's files let it go to
  (what does not depend on an environment); each place carries `capabilities` and `cannot`, the first reason of each
  playground that cannot go there, which the picker and the server's refusal use. A compose file that still carries `deploys_to` is told
  *x-gravitee.deploys_to is no longer a key: compatibility with a platform is computed*. The reasons, the first that
  applies, are exact strings:

  | Reason | When |
  |---|---|
  | *Nothing to deploy* | No seed service, or no seed folder |
  | *Needs Access Management* | `seed/` holds `am/`, or `requires.images` names an Access Management image, and no gateway of scope `AM` is there |
  | *Needs Gamma* | `seed/` holds `aim/` or `authz/` |
  | *Needs API Management 4.13* | `requires.images` asks for a later version of API Management than the environment's oldest gateway runs |
  | *Needs plugins on the gateway* | `requires.plugins` lists a plugin and the gateway add-on mounts no plugins folder |
  | *Changes this machine's gateway* | An overlay of a core service by the playground, or by an add-on or setup it brings |
  | *Its apps use this machine's gateway* | An app, or a service of an add-on it brings, depends on a core service; or an app calls a core service or a gateway |
  | *Needs more than API Management* | Any other seed content: `openfga/`, `controls.yaml`, `refills.yaml`; or a seeded product the environment runs that a deploy does not fill |

- **The picker** is the Combo (single choice, 32 px, search only past 8 options) in the head of every playground's page,
  drawn while at least one platform is registered. Its text is *Deploy to <place>*, cut with an ellipsis and whole in its
  title. Its options are *This machine*, then each platform by name with its environments in the account's order, each
  with a muted tail: the reason when the playground cannot go there (disabled), else the first thing missing, none when
  the place is ready. A place with something missing stays selectable, because *Before you start* offers the fix there. A
  platform that cannot be used is one disabled option with its reason. The picker stays enabled while the playground
  runs, and waits (`data-busy`) while a job runs.
- **The default place is option A, isolated in one function.** The place chosen on any page becomes the default of every
  playground, kept in this browser under the single localStorage key `deploy` (read and written in try/catch: where
  storage is blocked it is held in memory for the page), falling back to the core's place (`state.core.place`, *This machine*
  unless the core is a Cloud environment). Choosing a core forgets the key (`forgetPlace`), so that every playground follows it. A playground that cannot go to
  the remembered place's platform shows *This machine*. A remembered place whose platform is still *Loading* holds the
  buttons (*Loading*) rather than deploy to the wrong place. The rule is `chosenPlace` in `js/platform/places.js`, the one place to
  change if the default should be per playground, or never remembered. A remote place always opens the confirmation
  dialog that names it.
- **The head's main button** reads *Start* while the playground does not run. Once it runs it reads *Re-seed* where the
  chosen place holds a record (and always on *This machine*, as today) and *Deploy* where it holds none (the jobs
  `reseed` and `deploy`). On a remote place it waits for the place alone, not for what the local seed needs.
- ***Before you start*, for a remote place**, replaces the lines about the core (the platform, its plugins and image
  versions, its license) with: the platform (*Connected*), then in the existing three groups.
  - *Needs your attention*: *Cloud Token* (*Not set*, *Expired*, *Refused*; a key button; blocks *Start*), *Hybrid gateway*
    (*None in Development* with a plus button, *Its token is not kept here*, *Not connected*), the add-on's missing
    settings and the host prerequisites.
  - *Done for you at start*: *Hybrid gateway* (*Turned on at start*, *Started at start*, *Switches to Development*), and
    the playground's add-ons and apps.
  - *Already in place*: *Connected*, *Valid*, *Running*.

  The lines of the core never appear: the core is not part of the start. They are read again when the window comes back,
  as today.
- **A dialog always opens for a remote place**, even when nothing starts, because the action does more than the item.
  Numbered steps in the job's order: *Stops* (another playground, because one runs at a time), *Starts*, *Restarts* (the
  Hybrid gateway, only when it swaps, with the notes *Serves* and *Stops serving*), then *Deploys to* with the place
  (kind label *Environment*; for *This machine* of a playground that has other places, *Platform*). No row names a service
  of the core: the plan of a remote place is the plan of a lab without it (`layers._remote_view`), tested over every
  service name of the core. The phases are Add-ons, Apps and services, Gateway (waits for the platform to say the
  gateway is connected, 2 minutes at most, then fails with *Hybrid gateway did not connect to Gravitee Cloud*) and Seed;
  never Platform or Plugins.
- **The seed of a remote place runs apart**: `docker compose run --rm --no-deps -e NAME ... <seed> [--remove]`. The
  values are in the process environment of that one command and go by name, never in the command, which is a line of the
  job. They are the seeder's own settings (`APIM_MANAGEMENT_URL`, `APIM_MANAGEMENT_PATH`, `APIM_TOKEN`, `APIM_ORG`,
  `APIM_ENV`, `APIM_GATEWAY_URL`, `GATEWAY_INTERNAL_URL`, `STATE_DIR`): the seeder is unchanged and there is no
  vocabulary of its own. The local seed container, its exit state and its record are never read or written by it. A
  failed seed fails the job with its last line (`[seed] ✗ ...`), as today.
- **The records are per place**: `playgrounds/<id>/.state/created.json` for *This machine*, the only one Reset empties,
  and `.state/<platform id>/<environment id>/created.json` for each environment, with a `meta.json` that names the
  environment, since nothing offline holds its name. The state carries `deployed`, a row for each place besides *This
  machine* that holds a record, in the order of the accounts. Deleting a platform deletes its folders.
- ***Deployed to*** is a panel on the page of a playground, after *What runs* on the not-running page and in *Details*,
  drawn only when a place besides *This machine* holds a record. A row per place that holds one, *This machine* first,
  with a muted summary ("3 APIs, 1 Application") and a *Delete* icon on the remote rows. It has no *Re-seed* or *Open*:
  the head and the *Open* card carry them. *Delete* needs the playground on, as *Re-seed* does (the seed service is in the
  project only then), and says why when it is disabled; a delete that fails keeps its record.
- **Open** has a group named after each platform where the playground deployed, after the platform's own groups, with a
  link per environment that holds a record, named after it, to the platform's address. The Lab does not know a console
  address per environment.
- **Stop** lists the places: *Keep what it created* has a row *Stays* for each place that holds a record, *Delete what it
  created* a row *Deletes* for each, disabled with the reason of the first place that cannot be reached. The Hybrid
  gateway is not part of it. **Reset** (Containers) keeps its typed word and adds the sentence *Stays: ...* for the records
  of other places: it empties This machine's record only, because Cloud was not touched.
- **The Playgrounds list**: a playground that can go to a platform has a neutral cloud mark (title *Deploys to Gravitee
  Cloud*) in the marks slot, while one is registered. The note of its row, by the usual priority, adds *Needs a hybrid
  gateway*, *Needs a Cloud Token*, *Invalid token* or *Unreachable* for the place chosen, each leading to the form or the
  row that fixes it. The row's *Start* deploys to that place and is disabled, with the reason as its title, while the
  place is not ready. Nothing is said for *This machine*.
- **The shell**: the health chip keeps its words and means the core. The banner drops its calm *stopped* line while a
  platform is registered, the core is stopped and an add-on or a playground runs. The *Ports* cell of a service gets the
  tooltip *In the lab: smtp4dev:80* (the service name and the container port, not the published port), on Containers and
  in every detail's service table, only while a platform is registered. The Hybrid gateway add-on's note reads *Needs a
  hybrid gateway* and leads to the Gravitee Cloud page when a platform is registered and the token is the one setting it lacks.
- **Where API Management runs is the one choice drawn without a platform.** *Where does API Management run?* in the welcome dialog
  and the choice on API Management's row and on the Gravitee Cloud page are the way to the Cloud, so they show before any account; *Gravitee Cloud* chosen
  without one opens Connect Gravitee Cloud. The lists, the banner and Containers are as they were (`lists.main.txt`).
- **Without a platform, nothing changes.** A person with only the local core sees one menu entry (*Gravitee Cloud*, which
  offers *Connect account*) and one add-on row (the Hybrid gateway, *Needs settings*) more. No picker, no mark, no tooltip,
  no *Deployed to*, no banner change. All of it is drawn from data that exists only once a platform is registered, and
  `tests/web/js/lists.test.mjs` compares the three lists, the banner and Containers with `lists.main.txt`, the DOM they drew
  before platforms ([AGENTS.md](AGENTS.md#layout) says how it is written).

### The editor and the on file

- The editor shows a file with the ones that go with it, by section, each saying whether it is on and whether something
  of it runs while it is on (`runs`): Save & apply only then, never for a setup or a guide.
- The on file is one of its files: a "What is on" section after Core, with the core's files. It opens from its problem
  line in the banner too, is checked on the core and saved like a compose file.
- An on file with more than the Lab UI's `include` list, or with an entry whose file does not load, is never rewritten
  (`layers.current`, `locked`). The catalog's Start and Stop are disabled, and its problem line says why ("has your own
  changes: the catalog's Start and Stop are off", "<path> does not load: the catalog's Start and Stop are off"). So they
  are while `docker-compose.yml` does not load.

### Docker, jobs and progress

- What runs is not known while Docker does not answer or `docker-compose.yml` does not load (`blind`).
  - No dot is colored, no state is said, and every action waits, saying which ("Docker is not reachable",
    "docker-compose.yml does not load").
  - While only Docker does not answer, what is on still says so, with its ring and "Starts with the platform": the files
    say it.
  - With the core file at fault, what is on is not known either: the banner holds only the problem lines and Containers
    lists no add-on or playground.
- The state says of Docker why it does not answer (`docker.error`), or that its memory is short, whatever is on
  (`docker.warning`: in the banner, and in a playground's checks).
- Docker Compose 5.0 or later is needed: an add-on or a playground changes core services from an included file, which
  older versions refuse ("conflicts with imported resource"). The Lab UI says so at start (`api.requirement`).
- A failed job leaves the on file as written: the item is on, does not run, and can be started again or turned off. Its
  row says "Failed", the last line its command printed in its tooltip, with Show output in its menu. A canceled one says
  "Canceled", muted. The activity chip and the drawer say "<title> failed" or "<title> canceled".
- One job runs at a time. Meanwhile every action that waits gives the one reason of the state (`wait`): "Wait for" and its
  title, or why what runs is not known; while the Lab UI does not answer, "Lab UI offline". When the Lab UI
  restarts while the page follows a job, that job is gone: a toast says "<title>: interrupted".
- A job says what it acts on (`for`) and what it does (`verb`); the button with that key shows it at work.
  - `for`: `addon:<id>`, `setup:<id>`, `playground:<id>`, `group:<id>`, `service:<name>`, `platform`,
    `plugins:<product>`, `registry:<host>`. A login asked from an item's menu is the item's. The gateway of a platform
    is the job of its add-on (`addon:hybrid`), whichever button started it.
  - `verb`: start, stop, restart, update, pull, apply, reseed, deploy, delete, down, reset, login. `deploy` is a seed of
    a place that holds no record yet, `delete` what the seed created in one place.
- A job measures how far it is from what each of its stages waits for, known before the stage begins (`jobs.awaited`): the
  containers its changes start or stop (a start from Containers, also the stopped services they depend on, which Compose
  starts first), the images of a pull, the plugins a plugin apply downloads. One not named yet counts 0.
  - A container that starts is made (0.2), runs (0.5), then is ready (1) as the Lab UI judges it: healthy when Docker
    checks its health (from its file or its image), running otherwise, a one-shot that ended well. One that stops is
    there once stopped or removed.
  - What Docker says counts too (`Job.observe`, at each state refresh), after Compose returned as well, until every
    container the job waits for is ready, or none is on its way any more (it settled: one is unhealthy, keeps restarting
    or exited). It never goes back; a job that failed or was canceled stays where it was.
  - What the job starts or stops says so (Starting, Stopping) until the job measured its containers there. What the job
    is for has the job's `progress`; anything else it starts or stops, the mean of its containers the job waits for,
    while it is starting or stopping.
  - A stage with nothing to count (a login, following the seed) is not measured. Progress shows only while there is
    something to measure: the button or the pill of what changes fills, a head or a phase gets a bar.
- Logs follows each service chosen through its restarts: once its container runs again after a new start, the stream
  sends a `restarted` event (`{service, timestamp}`), drawn as a muted "Restarted" line, then its lines from there.

### License

- The license is part of the state (`license`, null without a variable that holds it), read from the core's `.env`
  without Docker: its status (none, unreadable, invalid, expired, expiring, valid), its expiry, the banner's line when
  something is wrong with it (`problem`; no license is the *Community Edition* chip, not a line, or *License not saved* while what
  runs still reads one), and whether what runs reads the one
  saved (`applied`).
- What it lacks is said in the words of a row (`model.license_note`): by the items of the state, and by `GET
  /api/plugins/installed` of each plugin it loads that needs a feature (`licensed`).
- Its tooltip names the packs that give what is missing ("Uses: Agent management, Authorization management",
  `license.named`), from the license model last read, with a license or without; the features' ids while the model is not
  known.
- **A plugin that needs a feature of the license is an Enterprise Edition plugin** (`editionOf` of `pages/plugins.js`): every tier
  of the license model is an Enterprise one. So is a plugin that download.gravitee.io publishes in its Enterprise folder
  (`graviteeio-ee/`, the catalog's `edition`), whether it needs a feature or not; any other is Community, one the catalog does not
  list too. The feature is the one the plugin declares (`feature=` in its `plugin.properties`, `features` of
  `/api/plugins/installed`), read once it is installed: never a list of names. The Plugins page says it with the *EE* pill after the
  name (*Enterprise Edition* for a screen reader) and its filter, under which each plugin is once.
- `requires.license` is a list of features, or a mapping of each one to the part that needs it
  (`project.license_features`). When every feature missing is for a part, the row says that part ("License needed for
  lesson 4"), the tooltip what the license lacks.
- A service the compose file labels `io.gravitee.license: required` (`needs_license`: the Alert Engine) exits without a
  usable license, so the Lab UI does not start it. A license is usable when it is valid or expiring (`license.usable`),
  read from the core's `.env` with the project's other inputs (`license_usable`, in `project.gather`): the `.env` is in
  the fingerprint, so a license saved shows in the next state, and the project is read again each day, a license
  expiring with time.
  - `layers.up_services` leaves it out, so no start, plan, apply or wait of a job names it, and none fails for it
    (`layers.held_back`; `layers.changing` does not make a job start it). A Start of it on its own, or of a group that
    has nothing else, is refused ("Alert Engine needs a license").
  - Its row, and its group's when nothing else is in it, is off, never an issue, whatever its container last ended on,
    with the warn note "Needs a license" that opens the License form (`model.held`, the same words as the notes above); its
    tooltip says why the license saved does not do. The platform reads Running while the rest does, and has nothing to
    start for it. The page lists it all the same (`needs_license`) and offers it no Start.
  - One that runs already (a license removed since) is what it is: its state, its notes and its Apply, as any other.

### Order

The server orders what the page lists: add-ons, setups and playgrounds by name (a number by its value: "Lab 2" before
"Lab 10"), an item's consoles its own first and the `legacy` ones last.

## Manual checks

Tests do not see the page. After a change, with the platform running, click through the lines that cover it, in light and
dark, at 1440 and 900 px wide.

1. **Dashboard.** The Lab UI opens on it with no hash, `#/stack` leads to Containers and an address that is no page leads
   here. The menu has it first, alone above Catalog, collapsed too. At 1440 px the platform's card (its products, the Cloud's row under
   them) and the catalog's card are side by side,
   about three to two, on the same top line, each ending where its content does with nothing blank under it; at 1100 and 900 px they are one
   over the other and as wide as the page; at 375 px a product is a name with its *Open*, then its version and its update, the Cloud its
   name, then its accounts and its count, and nothing scrolls sideways. Rows have the heights of the page (product 56, the Cloud 56, item
   56, card heads 56) and Resources is one line of 36 px, folded, at every width but a phone's (two lines), its strip 64 px at 1440 and
   1966 px once unfolded. No text is underlined at rest,
   and a state is the same dot and word on every row. Run the measure of the page's rules, if you have one, at 1966, 1440, 1100, 900
   and 375 px: heights, cells centered, columns lined up, nothing cut that carries a version or a reason.
   - *The head*: its title, no state (the sidebar's chip says it, on every page), the badge "Apply needed", in amber, while
     something runs behind what is saved (hover it, Tab to it, tap it, and the popover says why), and one button at most: Start with the platform stopped
     (the one primary, and the banner has no Start line), Apply with changes to apply (the primary, and the banner has no
     Apply line), else Stop, which shows its progress here and on the Containers page. No Restart, no Update and no menu: they
     are the Containers page's. On any other page the banner has its lines. Start the platform from here: the same dialog as
     Containers.
      - *Gravitee Platform*: the license pill after its title, then *Containers 12* and *Change versions*, two buttons in its head; the
     first leads to Containers and follows a container stopped there. One row per product (API Management, Access Management, Alert Engine; no databases), 56 px: the
     product's dot (a ring and a muted version while it does not run) and its name, the version that runs, "Up to date" (muted, a
     check) or "Update available" (amber; its popover the two versions) (nothing while it is not known, a spinner while it is asked),
     and at the end its consoles that run as 32 px buttons (the APIM Console and the Developer Portal in API Management, the AM
     Console in Access Management, the Gamma Console where its service is) and the older ones in one *More* menu, never a link; in a
     card under 1012 px (1440 px) each product has one *Open* button, a menu when it has several consoles. The names, the versions and
     the heads of this card and of the Cloud's line up. Stop the platform: they are gone. No tool of an add-on and no link
     of a playground, no word *This machine*. Stop a container of a product from Containers: its row says "n of m running" in amber
     in the place of the update; make one exit: its culprit in red. With an Alert Engine and no license: "Needs a license" opens the
     form. With something to apply: the badge "Apply needed" in the place of the update, its tooltip says which version is saved and
     which one runs. Without internet the updates show nothing and the rest still draws.
   - *Gravitee Cloud*, a row of the platform's card under its products, with no platform: "No account connected" and *Connect account*,
     a plain button, which opens the dialog (the only primary on the page is the head's Start or Apply). With the fake Cloud
     (`python3 lab-ui/tests/fake_cloud.py --port 9990`, token `fake-account-token` and the Account ID it prints) and an account with
     no gateway: *No gateway*, muted. Create hybrid gateways from the Gravitee Cloud page (one started here): the row says *2 gateways*,
     a name that leads to the Gravitee Cloud page, underlined only while hovered or focused; hovered or reached with Tab, its popover
     lists them under their environment with the words of the Gravitee Cloud page's list (Running here, Connected elsewhere...), in light
     and dark. No version, no update and no button on the row. Stop the fake: *Unreachable* in amber, the reason in its popover, and
     *Check again*; with a refused token *Account Token refused* and *Replace Account Token*. Its columns line up with the products',
     and nothing moves when the Cloud answers late. Another page and back within 30 seconds asks the fake nothing (its log).
   - *Playgrounds, setups and add-ons*: what is on first, then what is starred and off, a star turned from here; each row two
     lines, its name over its state and badges, its Start or Stop with its word and its key, no *Open*; a Start shows its job on
     its button and under its state; a row stays until its button said how it went. The search finds an item that is off and not
     starred (by its name or its description) and starts it; the kind picker keeps one kind with its counts; the toggle chips
     *Running* and *Starred*, alone or together; "Nothing running", "Nothing starred" and "No matches" with *Clear filters*; more
     than eight rows make pages. Nothing on: "Nothing running or starred" and *Browse playgrounds*. A visit starts without a
     search; Starred is kept.
   - *Resources*: folded by default, one line: *Resources*, its chevron, "CPU 1.0%", "Memory 4.7 GB", "Storage 85.8 GB" that follow the
     samples, Docker's versions muted at its end on a wide window; at 375 px two lines at most, nothing cut. A click anywhere on it,
     or Tab to its toggle and Enter or Space, unfolds it quickly (at once with reduced motion), the ring on the whole line; a reload
     keeps the choice. Folded, the Performance panel shows no animation frame. Unfolded, the CPU and memory lines slide continuously
     (no step every second) and end in a round dot at the right edge, at any width, light and dark. A hidden tab, or leaving the page, ends the stream and the animation: with two tabs on the page
     there is one `docker stats` process (`ps`), and none once both are gone. With reduced motion set in the system, the lines
     are drawn when a sample comes. The strip is one row of 64 px at 1440 and 1966 px, two or three short rows at 1100 and 900 px,
     and one item over the other at 375 px. Hover, then Tab to, each part of the storage bar and the "unused" figure, and tap them
     on a touch screen: the same popover as the Apply needed badge opens for each, in light and dark,
     inside the page. *Storage* says what Docker keeps and what is
     unused: compare each figure with `docker system df` (the sizes and the unused column, the
     unused total being their sum) and with `docker system df -v`. *Count storage again* reads Docker's data again (about 2 seconds,
     the button at work); another page and back within a minute does not. No word *reclaimable* anywhere.
   - Docker stopped: Storage and the charts are dashes, no dot is colored, no count
     is said, the actions say it as their title, nothing is to open; the Cloud and the catalog still show. A first
     start, nothing created: the dots are rings, and Start leads.
   - Light and dark, keyboard only: Tab goes through the head, each row and tile, with the same ring as every page.
2. **The three lists.** Search (`/` focuses it), categories (several, then All), the toggle chips Running and Starred (kept
   at the next visit), All / Shared / Personal, a star on a row (S on its toggle) and on a playground's page, the pager,
   "Clear filters". A row opened and closed (a click on it, Enter or Space on its toggle, which says expanded or collapsed),
   its detail with what uses it, the setup of a playground opened on its add-ons, the
   file and key buttons of a row and of what its detail lists. A name in a detail and a "via" note lead to that row
   (`#/addons/<id>`, in view and focused), Back returns.
3. **Start and stop an add-on.** Its button says so at the click and fills, the activity chip too, the drawer has
   its bar. Once the job ended the button stays at work until the add-on runs, then says "✓ Start" and gives its face
   back, "Stop", after 100% for a moment when the job got all the way (not while a gateway it restarts still passes its health
   check). The first add-on that configures the gateway says first that it restarts it.
4. **A dependency.** Kafka Desk brings Kafka, which says "via Kafka Desk". Stopping Kafka shows that both stop.
5. **A conflict.** Jade while AI Agent Lens runs says "Stops AI Agent Lens"; while it is on and stopped, "Turns
   off".
6. **A setup** (Event Streams). Start, stop. It is Running when its add-ons run, whoever started them; with some of them
   on, off and "Partly on". Its Stop keeps what a playground that is on uses ("Keeps").
7. **New add-on, New setup and New playground.** The form, the AI agent prompt and its Copy, Personal and Shared. A setup
   edited, renamed ("Changes" when another file includes it) and duplicated. A personal item started, stopped, shared
   ("Share": what keeps it, then where it goes and what changes with it) and deleted ("Turn it off first" while it is on
   and stopped).
8. **The playground** (Acme Bakery).
   - "Before you start": the platform line first, the names that lead to their items, a license line that opens the
     License form, a version line whose "Images page" opens that row (`#/images/<VARIABLE>`).
   - Its guide is readable before it starts. Start until it runs, Re-seed, then its two ways to stop.
   - Stopped from Containers, its Start runs it again in one job, without its seed. Re-seed says which service it waits for.
   - Its settings (the key) say whether its apps are built here or pulled.
9. **A running playground with UIs** (Acme Bakery), at 1664 and at 1100 px, the menu open and collapsed.
   - The page is pushed by the menu as on Containers, nothing over it, its toggle there above 1100 px, its guide and its
     tabs side by side, the guide's text running the width of its panel.
   - The head's Open (the first console), Details > Open under Playground and the products of the platform. An action link
     of the guide is shown, disabled with its reason while it cannot work (not running).
   - The divider: the mouse, the arrows (Shift, Home, End) and a double click, kept after a reload, and held within its
     limits when the window narrows.
   - A tab and its UI are kept while the state refreshes, another tab opens (with Enter, not the arrows), the guide turns a
     page. A link of the guide under a tab's UI loads in it, the history unchanged.
   - A UI that refuses frames, does not run or does not answer says so, and shows once it does.
   - Too narrow for columns (the menu open at 1200), the guide is over the UIs, with the same tabs and buttons and the
     divider gone. Clicking between a frame and the guide reads no check again.
   - Several pages of Gamma opened with the guide turned in between, then Gamma's own Back (a detail page's, the Policy
     Studio's) and the browser's: the guide and the tab stay where they are, and turning the guide never moves Gamma.
10. **A failed job and a canceled one.** The row says Failed, "Show output" opens it, Start works again.
11. **Containers.** Stop, Restart, Update, Start or Apply (the primary, last), the ⋯ menu.
    - The core's line, *Gravitee Platform* (its toggle, which folds the groups and the add-ons' band under it; the *Add-ons* band, which holds the platform's own add-ons (`ofPlatform`) and not Kafka or Jade, which come after under *Other add-ons*,
      folds its own groups, the browser keeping both in `shut`; each stays open, with no toggle, while a group under it is in an issue; its license pill in words, *Enterprise Edition* and the tier, *Community Edition*, or
      *License not saved* in amber while what runs still reads one, whose popover says where and what the next Apply does; a button
      to the License dialog; its file and key buttons), a group (its ports and uptime, its toggle opening it), a container, the Consoles card (a link's port and path:
      *:8085/next* and *:8085* read apart). The head has no state chip; the badge "Apply needed" is on the head and on a group that differs from what is saved, not
      on its containers, and the group's version is the tag that runs, without its system, in the color of every version.
    - A group stopped, then an add-on started: its dialog says the group starts too. Restart and Update leave that group
      stopped.
    - After Stop, what is on has a ring and says "Starts with the platform", with Turn off in its menu. Start lists what it
      starts beyond the core.
    - A container stopped leaves its group "Partly running". Reset says that it turns off the playground that is on.
    - Without a license, the Alert Engine is off with "Needs a license" (its form opens), has no Start, and the platform
      reads Running; once a license is saved, it is a group as any other and Start runs it.
    - The platform stopped, an add-on running alone: the head has Stop, Restart and Update.
12. **Apply needed.** A version saved on Images while its container runs: the rows and the banner's notice, on every page but Containers, show the badge "Apply needed" (the notice its count; no page has it beside its title); hover it, tab to it (its name for a screen reader holds
    the reason) and tap it on a touch screen: the popover is one table, the tags that run and those saved under *Running* and
    *Saved* (an older one saved in amber), or the names of the settings that changed, and what Apply does; Apply's dialog says what
    goes back and what loses its license, its button in red. Images and the editor offer Apply, which lists what it
    restarts. Once applied, it is gone. Escape, a tap elsewhere or another badge closes the popover. A badge "Needs settings"
    names the settings as chips with a button to the form; "Needs newer versions" each version needed.
13. **Logs, Images, Plugins.**
    - Logs: first *Choose services*, then several services, a level, search, the lines loaded. A service chosen is followed
      again once its container is back, after a "Restarted" line. The picker has the Hybrid gateway and its Redis in API
      Management, after its own services, while the add-on runs.
    - Images: a version, a registry, Save; "Save & apply" gray while nothing running changes; "Apply" gray while
      nothing is unsaved; an add-on that runs and is not
      turned on says *Running* and *Started by hand* (its popover why), as its row of the lists does; a followed value is plain in its
      field with *Same as API Management* after it, the registry too, at 900 px both lines of the cell fields. The image of an add-on
      that runs a gateway of a group is after that group's.
    - Plugins: a category, install one, remove it; a version that cannot be chosen starts where the pickers' versions do, in their
      type. With something to apply and no plugin chosen, the head has the badge
      "Apply needed" and its one Apply is the platform's; the banner has no Apply line there. Search *auth*: every plugin that says
      *Needs a license* has the *EE* tag (the authz plugins of Gamma too); *Enterprise* and *Community* together hold every plugin
      once.
    - Every dropdown (a filter, a version, the services of Logs, a *More* menu, the Category of New add-on) at 1440 by 900, 1440
      by 600 and 375 by 700: it opens where there is room, a long one ends 16 px from the window's edge and scrolls with its search
      and its titles in view, a short one is as high as its content, nothing leaves the window, a phone has a sheet; the arrows keep
      the active option in view, Escape closes it alone and gives the keyboard back.
    - Leaving either page with a change that is not applied asks first.
14. **The editor.** On a compose file (an error is marked on its line), a settings form (a required field) and a
    playground's guide (Code and Preview, saved, then shown again on its page). Opened from an item, its pane lists the
    core, the item, the setups a playground includes and the add-ons it brings. Opened from the core, it lists the on file
    and everything that is on. A setup's file has Save only.
14. **Docker stopped, then started again.** Meanwhile no dot is colored and every action says "Docker is not reachable".
    Once it is back, what was stopped from Containers stays stopped.
15. **Back and Forward**, through the pages and a playground's page.
16. **Gravitee Cloud.** With the fake Cloud (`python3 lab-ui/tests/fake_cloud.py --port 9990`, which prints the Account Tokens,
    the Account IDs and a Cloud Token for each environment), light and dark, at 1966, 1440, 900 and 375 px (the table is four
    columns, then two cells under the name, then stacked rows; never a horizontal scroll):
    - *No account*: the name of the space, one line, *Connect account*; no button in the head. While the page loads, rows
      of bars; with the Lab UI answering an error, *Accounts cannot be read* and *Try again*.
    - *Connect account* with http://localhost:9990: the keyboard on the token's field, the information marks of the two labels
      say what each is and where it is, no line under the fields; a wrong token refused in the dialog, then the account's hrid
      `my-account` as Account ID, refused in the same words, then `fake-account-token` with the Account ID the fake prints.
      With the Name empty the card says *My Account*; connecting that account again with the same name says *A platform is
      already named My Account*. The card lists Development, Test and Production under *Not set up*, each with *Set up*, no
      table and no label of columns, behind the dialog, which goes on to *Environment* in place: *Account* checked, the picker on
      Development, the two pictures of the Cloud Token chosen above them, *Skip for now* closes it and the account stays. At 1440 x
      900, 1440 x 700, 1100 x 800 and 375 x 800: the footer never covers the picture or the fields, the picture is whole inside the
      dialog, the page behind does not scroll, the left column scrolls by itself when the window is short.
    - *Set up* on Development: *Set up Development*, the same dialog without its steps, both parts ticked, *Set up* gray until the Cloud Token is typed;
      the information icons say what a hybrid gateway and a Cloud Token are, and the token step says where to create it; Cancel
      closes it (with the information icons before it in the form). Untick the token and the gateway alone is made. A token of
      another environment is refused in the dialog (*This Cloud Token is for Test, not Development*) and no gateway is made; refuse
      the gateway after a good token (stop the fake) and close the dialog: the card shows the environment *In use* with its token.
      A good pair makes the environment *In use*, and the gateway is started at once: with the add-on lacking a license it reads
      *Needs a license* (an amber dot) and its action is *Add license*; with a value of the add-on not set, *Needs settings* and
      *Settings*; with one it goes to *Starting*, after a dialog when another gateway runs.
    - *Start gateway* shows its job on the button and on the Hybrid gateway row of Add-ons; the state goes to *Connecting* then
      *Running here* by itself, one line, its popover its name, version and address (`localhost:8090`), and the action is *Stop
      gateway*.
      A second gateway (*Set up* or *Create gateway* in another environment) says *The gateway of Development runs here now: starting
      this one stops it* in its form, and is started after a dialog with *Restarts*, *Serves* and *Stops serving*; the first then
      reads *Stopped here*. Delete the gateway that runs here in the fake: the row reads *Removed in Cloud* in red, with *Replace
      gateway*. A gateway that does not connect reads *Not connected* after 2 minutes, with its logs one click away.
    - *More* of a row: *Show logs*, *Copy address*, *Replace Cloud Token*, then *Remove Cloud Token*, *Remove gateway* (*Remove gateway of
      Development*) and *Stop using Development* in red, each asking first and saying what *Stays there*. There is no *Open console*
      in a row. Stop the fake: the card reads *Unreachable* and *Check again*, and keeps its table under *Showing what was read
      before*; a refused Account Token reads *Account Token refused* with *Replace Account Token*. *Show 2 more environments* with five
      not set up. With the keyboard only, Tab to a row's *More*, open it with Enter, choose *Check again* in the card's: the focus
      stays on a control of the card after each redraw.
    - *The key* of a card: *Connection of My Account*, the Account ID in mono (its copy button turns to a check), the address, *Set*;
      no token anywhere; *Replace Account Token* closes it and opens that form; Escape and *Close* close it. *More* has no *Replace
      Account Token*.
    - *Hybrid gateways*, under the environments: two gateways in Development and one in Production made in the fake (its `POST
      /management/accounts/<id>/gateways`), one created from the page: every one is a row of 48 px, the one that runs here first, with
      its environment, its state, its version and its domains (mono, whole in their tooltip), *Stop gateway* on the one here and *Start
      gateway* on one the Lab keeps, *More* on each. Development's Gateway says *2 gateways* after its state; Enter on it brings the list
      into view, the keyboard on its band. Remove one from its *More*: the confirmation of the environment's *Remove gateway*. An
      account with none: *No hybrid gateway* and *Set up Development*. At 900 px a row is three lines, at 375 px one cell over the
      other, never a sideways scroll.
17. **Deploy to.** On a fresh clone, as [Test your change like a new
    user](../CONTRIBUTING.md#test-your-change-like-a-new-user) says, with its own `COMPOSE_PROJECT_NAME` and `UI_PORT` so
    that it sees an empty Docker. The fake Cloud stands in for the platform; with a real account, call
    `curl http://localhost:8090/hotels/hotels?city=Paris` at the end of S2. With the fake Cloud, the gateway cannot
    connect, so the pass ends when the seed waits for it.
    - *S1, no platform registered.* Every page, dialog, note and button is as it was: the head of Acme Hostels API has
      *Open*, *Start* and *Stop* and no *Deploy to*; no row has a cloud mark; no port has a tooltip; the banner keeps its
      calm *stopped* line. The only differences are the *Gravitee Cloud* entry, which offers *Connect account*, and the Hybrid
      gateway row of Add-ons, which reads *Needs settings*.
    - *S2, a Cloud account and no local core.* The core stays stopped. *Connect account*. Acme Hostels API has a cloud mark
      and *Deploy to* in its head. Choose the environment: its tail reads *No hybrid gateway*, *Before you start*
      describes that place, and *Start* is disabled with *Set up a gateway in Development (My Account) first*. The plus
      creates the gateway with the environment chosen, the key adds the Cloud Token, and the lines move to *Done for you
      at start* and *Already in place*. *Start* opens a dialog whose steps are *Starts* and *Deploys to*, and no row of
      the core. The phases are Add-ons, Apps and services, Gateway and Seed. Containers lists the hybrid gateway and the
      apps, with the core's groups gray; the banner has no *stopped* line. *Details* lists *Deployed to*, and *Open* has a
      group named after the platform. Reload: the picker still shows the environment, on every playground.
    - *S3, the full core and a Cloud environment.* The core runs and Acme Hostels API is on *This machine*. Change *Deploy
      to* to the environment: the button reads *Deploy*, its dialog lists the hybrid gateway and the environment and no
      row of the core, and the core's containers keep their uptime. *Deployed to* lists both places. *Re-seed* acts on the
      place shown only. *Stop* lists a row *Stays* or *Deletes* for each place, and *Reset* says *Stays: Acme Hostels API
      in Development (My Account)*. On Acme Bakery the environments are disabled, with *Needs Access Management* as their
      tail.
    - Both themes, 1440 and 900 px, and Back and Forward through the playground's page.
