"""A stand-in for SAP PM's notification service, for the loop eval's write-back step (#751): the write-back queue's
`sap_pm` adapter (ops/writeback.py) talks to it over HTTP exactly as it would to SAP, and the eval reads back what
SAP now holds.

    POST /notifications/<number>/complete    {"status", "text"}   the notification completed, with its note
    POST /notifications                      {"reference", ...}   a follow-up notification, numbered by SAP

Each request carries the write-back's `Idempotency-Key`; a key it has seen gets the first answer again and changes
nothing. A notification it doesn't hold is a 404 with SAP's message, which the queue hands back to the approver.

    with MockSapPm({"10004711": "OSNO"}) as sap:
        ... sap.url, sap.notifications, sap.requests
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockSapPm:
    def __init__(self, notifications: dict[str, str] | None = None):
        self.notifications = {str(k): {"status": v, "texts": []} for k, v in (notifications or {}).items()}
        self.requests: list[dict] = []
        self._answers: dict[str, tuple[int, dict]] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        sap = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                code, answer = sap._answer(self.path, body, self.headers.get("Idempotency-Key"))
                data = json.dumps(answer).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # quiet in test output
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, name="mock-sap-pm", daemon=True).start()
        return self

    def __exit__(self, *exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def _answer(self, path: str, body: dict, key: str | None) -> tuple[int, dict]:
        with self._lock:
            self.requests.append({"path": path, "body": body, "key": key})
            if key and key in self._answers:
                return self._answers[key]
            parts = [p for p in path.split("/") if p]
            if parts[:1] != ["notifications"]:
                out = (404, {"error": f"no service at {path}"})
            elif len(parts) == 3 and parts[2] == "complete":
                n = self.notifications.get(parts[1])
                if n is None:
                    out = (404, {"error": f"notification {parts[1]} does not exist"})
                else:
                    n["status"] = body.get("status") or "NOCO"
                    n["texts"].append(body.get("text") or "")
                    out = (200, {"notification": parts[1], "status": n["status"]})
            elif len(parts) == 1:
                if str(body.get("reference") or "") not in self.notifications:
                    out = (404, {"error": f"reference notification {body.get('reference')} does not exist"})
                else:
                    nums = [int(k) for k in self.notifications if k.isdigit()]
                    number = str(max(nums) + 1) if nums else f"{body['reference']}-F"
                    self.notifications[number] = {"status": body.get("status") or "OSNO",
                                                  "texts": [body.get("text") or ""], "reference": body["reference"]}
                    out = (201, {"notification": number, "status": self.notifications[number]["status"]})
            else:
                out = (404, {"error": f"no service at {path}"})
            if key and out[0] < 400:
                self._answers[key] = out
            return out
