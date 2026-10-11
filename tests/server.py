"""A local web server that serves test files and can misbehave on purpose."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Behaviour:
    def __init__(self):
        self.files = {}          # path -> bytes
        self.ranges = True       # serve parts of files
        self.drop_after = None   # cut every response after this many bytes
        self.lie_offset = 0      # claim a different start in Content-Range
        self.requests = 0


def start(behaviour):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            b = behaviour
            b.requests += 1
            data = b.files.get(self.path.split("?")[0])
            if data is None:
                self.send_error(404)
                return
            rng = self.headers.get("Range")
            start, end = 0, len(data)
            if rng and b.ranges:
                first, _, last = rng.removeprefix("bytes=").partition("-")
                start = int(first)
                end = int(last) + 1 if last else len(data)
                if start >= len(data):
                    self.send_error(416)
                    return
                end = min(end, len(data))
                self.send_response(206)
                shown = start + b.lie_offset
                self.send_header("Content-Range", f"bytes {shown}-{shown + end - start - 1}/{len(data)}")
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(end - start))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            body = data[start:end]
            if b.drop_after is not None:
                body = body[:b.drop_after]
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
