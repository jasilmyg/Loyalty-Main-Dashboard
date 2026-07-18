import subprocess
import time
import os
import sys

def main():
    print("Starting MCP Server on port 8001...")
    
    # Run the MCP server in a separate process
    server_process = subprocess.Popen(
        [sys.executable, "mcp_server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    print("Starting localhost.run tunnel for MCP Server on port 8001...")
    with open("mcp_tunnel_stdout.txt", "w") as out, open("mcp_tunnel_stderr.txt", "w") as err:
        tunnel_process = subprocess.Popen(
            ['ssh', '-o', 'StrictHostKeyChecking=no', '-R', '80:localhost:8001', 'nokey@localhost.run'],
            stdout=out,
            stderr=err
        )
    
    print("\n" + "="*50)
    print("MCP Server and Tunnel are starting.")
    print("Please check mcp_tunnel_stdout.txt for the public HTTPS URL.")
    print("Configure this URL in the Gemini Spark Custom App as: <HTTPS_URL>/sse")
    print("Press Ctrl+C to stop both the server and the tunnel.")
    print("="*50 + "\n")

    try:
        while True:
            time.sleep(1)
            # You can check if the processes are still alive
            if server_process.poll() is not None:
                print("MCP Server stopped unexpectedly.")
                break
            if tunnel_process.poll() is not None:
                print("Tunnel stopped unexpectedly.")
                break
    except KeyboardInterrupt:
        print("\nStopping MCP Server and Tunnel...")
        server_process.terminate()
        tunnel_process.terminate()
        server_process.wait()
        tunnel_process.wait()
        print("Stopped successfully.")

if __name__ == "__main__":
    main()
