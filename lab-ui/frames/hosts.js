// Runs first in each page of a platform UI (lab-ui/frames/hosts.sh). In a tab of the Lab UI shown as one of a playground's
// accounts, the frame is named "gravitee-lab-persona <persona> <the Lab UI's address>" (and the browser tab its Open in a new
// tab opens, the same name and " tab"), and what the UI asks of the Management
// API (localhost:8083, which the API also names itself) goes through the Lab UI's relay for that persona
// (lab_ui/server/personas.py), which keeps the persona's sign-in apart. Anywhere else it does nothing.
(function () {
  var named = /^gravitee-lab-persona ([a-z0-9][a-z0-9-]{0,62}) (http:\/\/(?:localhost|127\.0\.0\.1):\d{1,5})(?: tab)?$/.exec(window.name || "");
  if (!named) return;
  var from = "http://localhost:8083", to = named[2] + "/persona/" + named[1];
  function moved(url) {
    return url.indexOf(from) === 0 ? to + url.slice(from.length) : url;
  }
  var open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    var args = Array.prototype.slice.call(arguments);
    args[1] = moved(String(url));
    return open.apply(this, args);
  };
  var send = window.fetch;
  window.fetch = function (input, init) {
    if (input instanceof Request) {
      if (input.url.indexOf(from) === 0) input = new Request(moved(input.url), input);
    } else {
      input = moved(String(input));
    }
    return send.call(this, input, init);
  };
})();
