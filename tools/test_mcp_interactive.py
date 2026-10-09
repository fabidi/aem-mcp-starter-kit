"""
Quick CLI tester for AEM Content Intelligence MCP.
Allows running MCP tool calls directly via stdio JSON-RPC without needing an external client.

Usage:
    python tools/test_mcp_interactive.py <tool_name> [json_args]

Examples:
    python tools/test_mcp_interactive.py domain_list
    python tools/test_mcp_interactive.py domain_get_active
    python tools/test_mcp_interactive.py domain_switch '{"domain":"automotive"}'
    python tools/test_mcp_interactive.py aem_audit_domain_rules '{"domain":"automotive"}'
    python tools/test_mcp_interactive.py aem_querybuilder '{"path":"/content/novaria","type":"cq:Page","p.limit":"3"}'
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = ROOT_DIR / ".venv" / "Scripts" / "python.exe"


def call_tool(tool_name: str, args: dict):
    proc = subprocess.Popen(
        [str(PYTHON_EXE), "-u", "-m", "aem_mcp.server"],
        cwd=str(ROOT_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # 1. Initialize
    init_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cli-tester", "version": "1.0"}
        }
    })
    proc.stdin.write(init_msg + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # 2. Initialized notification
    notif_msg = json.dumps({
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    })
    proc.stdin.write(notif_msg + "\n")
    proc.stdin.flush()

    # 3. Call tool
    call_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": args
        }
    })
    proc.stdin.write(call_msg + "\n")
    proc.stdin.flush()

    response_line = proc.stdout.readline()
    proc.kill()

    try:
        resp = json.loads(response_line)
        if "error" in resp:
            print(f"\n[ERROR] {resp['error']}")
            return
        result = resp.get("result", {})
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                try:
                    parsed = json.loads(text)
                    print(json.dumps(parsed, indent=2))
                except Exception:
                    print(text)
    except Exception as e:
        print(f"Raw Response: {response_line}")
        print(f"Parse error: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    tool_name = sys.argv[1]
    raw_args = sys.argv[2] if len(sys.argv) > 2 else "{}"
    try:
        args = json.loads(raw_args)
    except Exception:
        try:
            import ast
            parsed = ast.literal_eval(raw_args)
            args = parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            print(f"Error parsing JSON arguments: {e}")
            sys.exit(1)

    print(f"\n--- Calling '{tool_name}' with args: {args} ---")
    call_tool(tool_name, args)


if __name__ == "__main__":
    main()
