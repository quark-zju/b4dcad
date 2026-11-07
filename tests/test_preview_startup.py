import argparse
import errno
import io
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from b4dcad.cli import (
    PreviewServer,
    _create_preview_server,
    _open_preview_browser,
    _serve_preview,
    main,
    preview_command,
)


class PreviewStartupTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.script = Path(self.directory.name) / "model.py"
        self.script.write_text("from b4dcad import box\npart = box(1, 2, 3)\n")

    def args(self, **changes):
        values = dict(
            script=str(self.script),
            host="127.0.0.1",
            port=None,
            object_name=None,
            write_stl=None,
            no_open=False,
        )
        values.update(changes)
        return argparse.Namespace(**values)

    def test_both_entry_points_share_defaults_and_explicit_flags(self):
        original_path = sys.path[:]
        self.addCleanup(setattr, sys, "path", original_path)
        for command in (preview_command, lambda args: main(["preview", *args])):
            with (
                self.subTest(command=command),
                patch("b4dcad.cli._serve_preview") as serve,
            ):
                command([str(self.script)])
                args = serve.call_args.args[0]
                self.assertIsNone(args.port)
                self.assertFalse(args.no_open)
                command([str(self.script), "--port", "8765", "--no-open"])
                args = serve.call_args.args[0]
                self.assertEqual(args.port, 8765)
                self.assertTrue(args.no_open)

    def test_implicit_port_retries_sequentially(self):
        server = object()
        with patch(
            "b4dcad.cli.PreviewServer",
            side_effect=[
                OSError(errno.EADDRINUSE, "busy"),
                OSError(errno.EADDRINUSE, "busy"),
                server,
            ],
        ) as create:
            self.assertIs(_create_preview_server(self.args()), server)
        self.assertEqual(
            [call.args[0][1] for call in create.call_args_list], [8765, 8766, 8767]
        )

    def test_implicit_port_stops_after_exactly_32_candidates(self):
        with patch(
            "b4dcad.cli.PreviewServer", side_effect=OSError(errno.EADDRINUSE, "busy")
        ) as create:
            with self.assertRaisesRegex(OSError, "All 32 preview ports"):
                _create_preview_server(self.args())
        self.assertEqual(
            [call.args[0][1] for call in create.call_args_list], list(range(8765, 8797))
        )

    def test_explicit_port_and_non_conflict_errors_do_not_retry(self):
        for port, error in ((8765, errno.EADDRINUSE), (None, errno.EACCES)):
            with (
                self.subTest(port=port, error=error),
                patch(
                    "b4dcad.cli.PreviewServer", side_effect=OSError(error, "failed")
                ) as create,
            ):
                with self.assertRaises(OSError) as caught:
                    _create_preview_server(self.args(port=port))
                self.assertEqual(caught.exception.errno, error)
                create.assert_called_once()

    def test_real_port_conflict_does_not_execute_model(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with patch("b4dcad.cli.build_preview_scene") as build:
                with self.assertRaises(OSError) as caught:
                    _create_preview_server(self.args(port=occupied.getsockname()[1]))
                self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
                build.assert_not_called()

    def test_browser_opens_actual_bound_port_and_server_closes(self):
        opened = threading.Event()
        urls, servers = [], []

        def browser(url):
            urls.append(url)
            opened.set()
            return True

        def serve(server):
            servers.append(server)
            self.assertTrue(opened.wait(2))
            self.assertEqual(urls, [f"http://127.0.0.1:{server.server_port}/"])
            raise KeyboardInterrupt

        with (
            patch("b4dcad.cli.webbrowser.open", side_effect=browser),
            patch.object(
                PreviewServer, "serve_forever", autospec=True, side_effect=serve
            ),
            redirect_stdout(io.StringIO()),
        ):
            _serve_preview(self.args(port=0, host="0.0.0.0"))
        self.assertTrue(servers[0]._stop_watcher.is_set())
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_no_open_skips_browser_thread(self):
        server = Mock(server_port=8765, script=str(self.script))
        with (
            patch("b4dcad.cli._create_preview_server", return_value=server),
            patch("b4dcad.cli.threading.Thread") as thread,
            redirect_stdout(io.StringIO()),
        ):
            _serve_preview(self.args(no_open=True))
        thread.assert_not_called()
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()

    def test_browser_failure_reports_url_without_raising(self):
        for result in (False, OSError("browser unavailable")):
            with (
                self.subTest(result=result),
                patch("b4dcad.cli.webbrowser.open") as browser,
            ):
                if isinstance(result, Exception):
                    browser.side_effect = result
                else:
                    browser.return_value = result
                output = io.StringIO()
                with redirect_stdout(output):
                    _open_preview_browser("http://127.0.0.1:8767/")
                self.assertIn("http://127.0.0.1:8767/", output.getvalue())


if __name__ == "__main__":
    unittest.main()
