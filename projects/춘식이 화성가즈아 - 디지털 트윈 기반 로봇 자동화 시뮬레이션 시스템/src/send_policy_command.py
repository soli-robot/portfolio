import socket
import sys

HOST = "192.168.10.36"
PORT = 8765

def main():
    if len(sys.argv) < 2:
        print("usage: python send_policy_command.py [start|stop|reset|status]")
        return

    cmd = sys.argv[1]

    with socket.create_connection((HOST, PORT), timeout=3.0) as sock:
        sock.sendall(cmd.encode("utf-8"))
        resp = sock.recv(1024).decode("utf-8")
        print(resp.strip())

if __name__ == "__main__":
    main()
