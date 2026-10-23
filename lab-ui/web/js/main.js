// Boot: the shell, the pages, the first state, then a refresh every three seconds, every second while something is
// on its way

import { openLab, paintLab, followLab } from "./dialogs/lab.js";
import { store, router } from "./core.js";
import { paintProgress, paintBusy, paintActions, paintMenuDot } from "./components/ui.js";
import { initSidebar, renderShell } from "./components/sidebar.js";
import { initJobs } from "./activity/jobs.js";
import { Combo } from "./components/combo.js";
import { License } from "./dialogs/license.js";
import { Welcome } from "./dialogs/welcome.js";
import { LabUpdated } from "./dialogs/layout.js";
import { dashboardPage } from "./pages/dashboard.js";
import { playgroundsPage, setupsPage, addonsPage } from "./pages/list.js";
import { playgroundPage } from "./pages/playground.js";
import { catalogsPage } from "./pages/catalogs.js";
import { stackPage } from "./pages/stack.js";
import { logsPage } from "./pages/logs.js";
import { imagesPage } from "./pages/images.js";
import { pluginsPage } from "./pages/plugins.js";
import { platformsPage } from "./pages/platforms.js";

// After the visible page: the shell and the dot of the Catalogs entry of its menu, then in everything drawn the progress
// bars, what waits for Docker or for the running job, and the buttons of the actions at work
const afterDraw = () => {
  renderShell();
  paintLab();
  followLab();
  paintMenuDot();
  paintProgress();
  paintBusy();
  paintActions();
};

// Skip to content takes the keyboard to the page on screen, which stays: its address is no page of the router
// Gravitee Lab itself: its version and what is newer, from the button of the menu's tools and the chip of a newer version
for (const id of ["lab", "lab-dot"]) document.getElementById(id).addEventListener("click", () => openLab());

document.querySelector(".skip").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("content").focus();
});

initSidebar();
initJobs();
Combo.init();
[dashboardPage, playgroundsPage, playgroundPage, setupsPage, addonsPage, catalogsPage, stackPage, logsPage, imagesPage, pluginsPage,
  platformsPage].forEach(router.register);

// The page shows at once: until the first state comes, it says that the lab is read, or why it cannot be (core.js)
router.start();
store.subscribe(afterDraw);
addEventListener("hashchange", afterDraw);
afterDraw();
await store.refresh();
Welcome.init();
LabUpdated.init();   // after the welcome, which it may open over: what moved is more urgent than how the Lab works
License.load();   // the banner and the pages draw again once it is read
// Every second while the server measures something on its way, a job or what it started that still passes its health
// checks, so that its progress shows as it comes. A tick is skipped while the last refresh still waits for its
// answer: a slow Docker is not asked again and again.
let ticks = 0, refreshing = null;
setInterval(() => {
  if (refreshing || document.hidden || !(store.measuring() || ++ticks % 3 === 0)) return;
  refreshing = store.refresh().finally(() => { refreshing = null; });
}, 1000);
