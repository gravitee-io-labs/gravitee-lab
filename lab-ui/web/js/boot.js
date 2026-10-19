// The theme and the menu as they were left, set before anything is drawn, so that the page never flashes the other one. A classic script,
// run where index.html puts it, ahead of the modules, which wait: it is a file of the page's own origin and not an inline script, so that
// the policy of the page can refuse every script it did not load. Any theme but light or dark is the system's. A narrow window always
// has the collapsed menu (sidebar.js).

try {
  const theme = localStorage.getItem("theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  document.body.classList.toggle("nav-collapsed", localStorage.getItem("sidebar") === "collapsed");
} catch { /* storage unavailable */ }
if (matchMedia("(max-width: 1100px)").matches) document.body.classList.add("nav-collapsed");
