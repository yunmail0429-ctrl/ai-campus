"""Local protocol fixture, NOT a real AI model or an AI-quality evaluation."""

import json
import re
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def provider_server(key):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append(payload)
            if self.path not in ("/v1/chat/completions", "/v1/embeddings"):
                self.send_error(404)
                return
            if self.headers.get("Authorization") != "Bearer " + key:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": {"message": "private upstream details " + key}}).encode()
                )
                return
            if payload["model"] == "rate-limit":
                self.send_response(429)
                self.end_headers()
                return
            if self.path == "/v1/embeddings":
                vectors = []
                for index, text in enumerate(payload["input"]):
                    # Deliberate deterministic protocol fixture, not a semantic model.
                    vector = [1.0, 0.0, 0.0] if re.search("函数|数学|单调", text) else [0.0, 1.0, 0.0]
                    if payload["model"] == "bad-dimension":
                        vector = [1.0, 0.0]
                    elif payload.get("dimensions", 3) > 3:
                        vector += [0.0] * (payload["dimensions"] - 3)
                    if payload["model"] == "nonfinite":
                        vector = [float("nan"), 0.0, 0.0]
                    vectors.append({"index": index, "embedding": vector})
                response = {
                    "data": list(reversed(vectors)),
                    "usage": {"prompt_tokens": max(1, sum(len(t) for t in payload["input"]) // 3)},
                }
                if payload["model"] == "embedding-no-usage":
                    response.pop("usage")
                raw = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            if payload["model"] == "timeout":
                time.sleep(0.3)
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "教学目标："}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": "理解函数单调性。"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            if payload["model"] == "rag-chat":
                chunks[1]["choices"][0]["delta"]["content"] = "理解函数单调性，参考课堂活动资料 [1]。"
            if payload["model"] != "missing-usage":
                chunks.append({"choices": [], "usage": {"prompt_tokens": 60, "completion_tokens": 20}})
            try:
                for chunk in chunks:
                    raw = ("data: " + json.dumps(chunk, ensure_ascii=False) + "\r\n\r\n").encode()
                    # Split in the middle of UTF-8 text to test decoder/chunk framing.
                    for start in range(0, len(raw), 7):
                        self.wfile.write(raw[start : start + 7])
                    self.wfile.flush()
                    if payload["model"] == "slow-chat":
                        time.sleep(0.8)
                if payload["model"] != "broken":
                    self.wfile.write(b"data: [DONE]\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
