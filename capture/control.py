"""Internal operator CLI: arm GUILD_ID CHANNEL_ID | state | job ID | retry ID | demo."""
import argparse
import json
import os
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["arm", "state", "job", "retry", "demo"])
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    action = args.action
    if action == "arm":
        if len(args.ids) != 2:
            parser.error("arm requires GUILD_ID CHANNEL_ID after you manually join that call")
        path, method, body = "/arm", "POST", {"guild_id": args.ids[0], "channel_id": args.ids[1]}
    elif action in ("job", "retry"):
        if len(args.ids) != 1:
            parser.error(f"{action} requires a recording ID")
        path = f"/sessions/{args.ids[0]}" + ("/retry" if action == "retry" else "")
        method, body = ("POST", {}) if action == "retry" else ("GET", None)
    else:
        path = f"/{action}"
        method, body = ("POST", {}) if action == "demo" else ("GET", None)
    request = urllib.request.Request("http://localhost:8000" + path,
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": f"Bearer {os.environ['RECORDER_API_KEY']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(json.dumps(json.load(response), indent=2))
    except urllib.error.HTTPError as error:
        print(error.read().decode())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
