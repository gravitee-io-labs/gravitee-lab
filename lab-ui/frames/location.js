// Runs inside a platform UI that the Lab UI shows in a frame (x-ui-frames in docker-compose.yml). It tells the page that holds the
// UI which address it is on, so that Reload loads the page the person is on, not the one the UI started at: a page cannot read the
// address of a page served on another port, it can only be told it.
(function () {
  if (window.parent === window) return;
  function tell() {
    window.parent.postMessage({ type: "gravitee-lab:location", url: location.href }, "*");
  }
  ["pushState", "replaceState"].forEach(function (method) {
    var original = history[method];
    history[method] = function () {
      var result = original.apply(this, arguments);
      tell();
      return result;
    };
  });
  addEventListener("popstate", tell);
  addEventListener("hashchange", tell);
  tell();
})();
