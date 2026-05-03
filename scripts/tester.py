#!/usr/bin/env python3
"""Filtered TCP connectivity tester with address/port validation."""
import json, socket, time, os, sys, concurrent.futures, ipaddress
from datetime import datetime

# Common proxy ports (allowlist)
ALLOWED_PORTS = {
    80, 443, 8080, 8443, 9090, 9999,
    22, 2222, 3306, 3389, 5432, 5900, 5901,
    1080, 1088, 2082, 2083, 2086, 2087, 2095, 2096,
    3000, 3001, 4000, 5000, 6000, 7000, 8000, 8888, 9000,
    10000, 20000, 30000,
}

# Port ranges allowed (inclusive)
ALLOWED_PORT_RANGES = [(30000, 45000), (40000, 60000)]

def port_allowed(port):
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False
    if port in ALLOWED_PORTS:
        return True
    for lo, hi in ALLOWED_PORT_RANGES:
        if lo <= port <= hi:
            return True
    return False

def is_safe_address(host):
    """Reject private, loopback, link-local, reserved, multicast, unspecified."""
    try:
        ip = ipaddress.ip_address(host)
        if any([ip.is_private, ip.is_loopback, ip.is_link_local,
                ip.is_reserved, ip.is_multicast, ip.is_unspecified]):
            return False
        return True
    except ValueError:
        # Not an IP literal; could be a hostname, allow through
        return True

def test_tcp(server, port, timeout=5):
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((server, port))
        latency = round((time.time() - start) * 1000)
        sock.close()
        if result == 0:
            return True, latency
        return False, -1
    except Exception:
        return False, -1

def test_node(node):
    server = node.get("server", "")
    port = node.get("port", 0)
    node["alive"] = False
    node["latency"] = -1
    node["speed_rating"] = "Dead"
    node["skip_reason"] = None

    if not server or not port:
        node["skip_reason"] = "missing_server_or_port"
        return node
    if not is_safe_address(server):
        node["skip_reason"] = f"unsafe_address:{server}"
        return node
    if not port_allowed(port):
        node["skip_reason"] = f"port_not_allowed:{port}"
        return node

    alive, latency = test_tcp(server, port)
    node["alive"] = alive
    node["latency"] = latency
    if alive:
        if latency < 100:     node["speed_rating"] = "Fast"
        elif latency < 300:   node["speed_rating"] = "OK"
        elif latency < 800:   node["speed_rating"] = "Slow"
        else:                 node["speed_rating"] = "Very Slow"
    return node

def main():
    workspace = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
    input_path = os.path.join(workspace, "nodes_raw.json")
    output_path = os.path.join(workspace, "nodes_tested.json")
    if not os.path.exists(input_path):
        print("No nodes_raw.json found. Run scraper.py first.")
        sys.exit(1)
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    nodes = data.get("nodes", [])
    if not nodes:
        print("No nodes.")
        sys.exit(1)
    max_test = int(os.environ.get("MAX_TEST_NODES", 50))
    test_nodes = nodes[:max_test]

    # Pre-filter: show counts
    total = len(test_nodes)
    safe = sum(1 for n in test_nodes if is_safe_address(n.get("server","")) and port_allowed(n.get("port",0)))
    unsafe = total - safe
    print(f"Nodes to test: {total} total, {safe} safe, {unsafe} filtered out")
    if safe == 0:
        print("No testable nodes after address/port filtering.")
        sys.exit(1)
    if safe > 20:
        print(f"Will test {safe} endpoints from untrusted sources.")
        print("Type y + Enter to continue, or anything else to abort:")
        try:
            confirm = sys.stdin.readline().strip().lower()
        except:
            confirm = "n"
        if confirm != "y":
            print("Aborted by user.")
            sys.exit(0)

    tested = []
    alive_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(test_node, n): n for n in test_nodes}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            n = future.result()
            tested.append(n)
            done += 1
            if n.get("skip_reason"):
                print(f"  [{done}/{total}] {n.get('server','?')}:{n.get('port',0)} -> skipped ({n['skip_reason']})")
            elif n['alive']:
                alive_count += 1
                print(f"  [{done}/{total}] {n['server']}:{n['port']} -> {n['latency']}ms")
            else:
                print(f"  [{done}/{total}] {n['server']}:{n['port']} -> dead")

    tested.sort(key=lambda x: (
        x.get('skip_reason') is not None,
        not x['alive'],
        x['latency'] if x['latency'] > 0 else 99999
    ))
    print(f"\nAlive: {alive_count}, Dead: {len(tested) - alive_count}, Skipped: {unsafe}")
    result = {
        "tested_at": datetime.now().isoformat(),
        "total_tested": len(tested),
        "alive": alive_count,
        "dead": len(tested) - alive_count,
        "skipped": unsafe,
        "nodes": tested
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saved to {output_path}")

if __name__ == "__main__":
    main()
