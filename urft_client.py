import socket
import os
import sys
import struct
import time

DEVMODE = True

class TCPficationClient:
    BUFFER_SIZE: int = 1024
    TIMEOUT: float = 0.5  # seconds

    def __init__(self, host='10.20.23.32', port=6969) -> None:
        self.host: str = host
        self.port: int = port
        self.client_socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client_socket.settimeout(self.TIMEOUT)

    def send_file(self, file_path):
        try:
            filename = os.path.basename(file_path)
            seq_num = 0
            
            # Send filename with sequence number
            self.client_socket.sendto(struct.pack("!I", seq_num) + filename.encode('utf-8'), (self.host, self.port))
            seq_num += 1
            
            with open(file_path, 'rb') as file:
                while chunk := file.read(self.BUFFER_SIZE):
                    while True:
                        self.client_socket.sendto(struct.pack("!I", seq_num) + chunk, (self.host, self.port))
                        try:
                            # wait for ack
                            ack, _ = self.client_socket.recvfrom(4)
                            ack_num = struct.unpack("!I", ack)[0]
                            if ack_num == seq_num:
                                break
                        except socket.timeout:
                            print(f"⚠️ Timeout, resending packet {seq_num}")
                    seq_num += 1
            
            # Send EOF with sequence number
            while True:
                self.client_socket.sendto(struct.pack("!I", seq_num) + b"EOF", (self.host, self.port))
                try:
                    ack, _ = self.client_socket.recvfrom(4)
                    ack_num = struct.unpack("!I", ack)[0]
                    if ack_num == seq_num:
                        break
                except socket.timeout:
                    print(f"⚠️ Timeout, resending EOF packet {seq_num}")
            
            print(f"✅ File '{file_path}' sent successfully!")
        except FileNotFoundError:
            print(f"❌ Error: File '{file_path}' not found.")
        except Exception as e:
            print(f"❌ Error: {e}")

def main():
    if len(sys.argv) < 3:
        sys.exit(1)

    file_path = sys.argv[1]
    server_ip = sys.argv[2]
    server_port = int(sys.argv[3])

    client = TCPficationClient(host=server_ip, port=server_port)
    client.send_file(file_path)
    
if __name__ == "__main__":
    main()
