// What hands a prompt to an AI agent from a dialog: the Claude app, Claude Code, Codex and Cursor, each opened with the prompt written and
// not sent (the person reads it and presses Enter), and a button that copies the prompt for any other agent. None is the default: they
// are neutral buttons, in the order of their names. The apps that take a folder (Claude, Claude Code, Codex) are opened on the Lab's;
// Cursor opens on the window it has, so the prompt says the folder. The links are those of each product's own documentation. The folder
// and a word on how to run the lab's commands come from the server (`state.agent`): in WSL the apps of Windows open the folder by its
// Windows path and run the commands in the distribution.

import { esc, icon, store } from "../core.js";

const CLI_LIMIT = 4800;   // Claude Code takes at most 5,000 characters in a link

const link = (href, glyph, words, tip = "") => `<a class="btn" href="${esc(href)}"${tip ? ` data-tip="${esc(tip)}"` : ""}>${icon(glyph)}${words}</a>`;

// The folder an agent opens, and a prompt with the word on the lab's commands that the agent needs where it runs
export const agentFolder = () => store.state?.agent?.folder || store.state?.root || "";
export const forAgent = (prompt) => (store.state?.agent?.note ? `${prompt} ${store.state.agent.note}` : prompt);

export function agentLinks(prompt, folder) {
  const text = encodeURIComponent(prompt), dir = folder ? encodeURIComponent(folder) : "";
  const cursor = encodeURIComponent(folder ? `${prompt} Work in ${folder}.` : prompt);
  return `<button type="button" class="btn" data-copy="${esc(prompt)}">Copy prompt</button>`
    + link(`claude://code/new?q=${text}${dir ? `&folder=${dir}` : ""}`, "claude", "Open in Claude app")
    + (prompt.length <= CLI_LIMIT ? link(`claude-cli://open?q=${text}${dir ? `&cwd=${dir}` : ""}`, "terminal", "Open in Claude Code", "Claude Code, in a terminal") : "")
    + link(`codex://new?prompt=${text}${dir ? `&path=${dir}` : ""}`, "openai", "Open in Codex")
    + link(`cursor://anysphere.cursor-deeplink/prompt?text=${cursor}`, "cursor", "Open in Cursor");
}
