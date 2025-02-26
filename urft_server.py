import socket
import os
import sys
import struct

DEVMODE = True

class TCPficationServer:

    BUFFER_SIZE: int = 1024  # bytes
    TIMEOUT: int = 0.5  # seconds

    def __init__(self, host='0.0.0.0', port=6969) -> None:
        self.host: str = host
        self.port: int = port
        self.server_socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def start(self) -> None:
        self.server_socket.bind((self.host, self.port))
        print(f"🦊 Server listening on {self.host}:{self.port}")

        self.listen()

    def listen(self):
        while True:
            filename, sender_addr = self.server_socket.recvfrom(TCPficationServer.BUFFER_SIZE)
            seq_num, filename = struct.unpack("!I", filename[:4])[0], filename[4:].decode().strip()
            print(f"Receiving file: {filename} from {sender_addr}")

            file_path = os.path.join("./", filename)

            expected_seq_num = 1    # Start with 1 since 0 is used for the filename

            with open(file_path, "wb") as file:
                while True:
                    data, sender_addr = self.server_socket.recvfrom(TCPficationServer.BUFFER_SIZE + 4)
                    seq_num, data = struct.unpack("!I", data[:4])[0], data[4:]

                    if seq_num == expected_seq_num:
                        if data == b"EOF":
                            self.server_socket.sendto(struct.pack("!I", seq_num), sender_addr)
                            break
                        file.write(data)
                        expected_seq_num += 1

                    # Send acknowledgment for the received sequence number
                    self.server_socket.sendto(struct.pack("!I", seq_num), sender_addr)

                print(f"✅ File '{filename}' received successfully!")
                return

if __name__ == "__main__":
    try:
        host = sys.argv[1]
        port = int(sys.argv[2])

        server = TCPficationServer(host, port)
        server.start()
    except KeyboardInterrupt:
        print("🦊 Server shutting down...")