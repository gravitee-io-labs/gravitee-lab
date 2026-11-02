"""`make smoke`: which Lab UI it checks. The one that serves the folder when one runs, which a second one would only send
to; else one it starts on a free port, and stops after."""

import contextlib
import io
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from lab_ui import paths

from . import Case, smoke
from .server.test_api import served_elsewhere


class Answers(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class Which(Case):

    def setUp(self):
        self.folder(paths)

    def test_the_lab_ui_that_serves_the_folder_is_the_one_checked(self):
        # The person's Lab UI runs: a second one would say "Already running" and stop at once
        server = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        served_elsewhere(self, port)
        self.patch(smoke.subprocess, "Popen", side_effect=AssertionError("a second Lab UI was started"))
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            lab_ui = smoke.LabUI()
        self.assertEqual(printed.getvalue(), f"Checking the Lab UI that runs on http://localhost:{port}\n\n")
        self.assertEqual((lab_ui.port, lab_ui.wait()[0]), (port, 200))
        lab_ui.stop()   # it keeps running
        self.assertEqual(lab_ui.ask("/favicon.ico")[0], 200)

    def test_without_one_it_starts_one_on_a_free_port_and_stops_it(self):
        started = self.patch(smoke.subprocess, "Popen")
        lab_ui = smoke.LabUI()
        started.assert_called_once_with([sys.executable, str(smoke.LAB_UI / "server.py"), "--port", str(lab_ui.port),
                                         "--no-open"], stdout=mock.ANY)
        lab_ui.stop()
        started.return_value.terminate.assert_called_once_with()
