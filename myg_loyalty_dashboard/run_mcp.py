import os
import sys

port = int(os.environ.get("PORT", 8001))

try:
    import uvicorn
    from mcp_server import app
    print("Starting uvicorn server normally...")
    uvicorn.run(app, host="0.0.0.0", port=port)
except Exception as e:
    import traceback
    err = traceback.format_exc()
    print("CRASH DETECTED. Serving error trace on port", port)
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class ErrorHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(err.encode('utf-8'))
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(err.encode('utf-8'))
    
    server = HTTPServer(("0.0.0.0", port), ErrorHandler)
    server.serve_forever()
