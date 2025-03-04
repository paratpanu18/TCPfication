import socket
import os
import sys
import struct
import time
import hashlib

DEVMODE = False

class TCPficationServer:
    BUFFER_SIZE: int = 1450  # bytes
    TIMEOUT: float = 1.0  # seconds
    WINDOW_SIZE: int = 10  # Maximum out-of-order packets to buffer
    STATUS_INTERVAL: float = 2.0  # seconds between status updates
    INACTIVITY_TIMEOUT: float = 10.0  # Time to wait before declaring connection lost

    def __init__(self, host='0.0.0.0', port=6969) -> None:
        self.host: str = host
        self.port: int = port
        self.server_socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_socket.settimeout(self.TIMEOUT)  # Set socket timeout for non-blocking recv

    def start(self) -> None:
        self.server_socket.bind((self.host, self.port))
        print(f"🦊 Server listening on {self.host}:{self.port}")

        self.listen()

    def listen(self):
        while True:
            try:
                filename, sender_addr = self.server_socket.recvfrom(TCPficationServer.BUFFER_SIZE)
                seq_num, filename = struct.unpack("!I", filename[:4])[0], filename[4:].decode().strip()
                print(f"📩 Receiving file: {filename} from {sender_addr}")

                # Send acknowledgment for the filename (multiple times to ensure delivery)
                for _ in range(3):
                    self.server_socket.sendto(struct.pack("!I", seq_num), sender_addr)

                file_path = os.path.join(os.getcwd(), filename)

                # Process the file transfer
                self.receive_file(file_path, sender_addr)
            except socket.timeout:
                continue  # Keep waiting for connection
            except Exception as e:
                print(f"❌ Error in listen: {e}")
                import traceback
                traceback.print_exc()

    def receive_file(self, file_path, sender_addr):
        expected_seq_num = 1
        buffer = {}
        duplicate_count = 0
        out_of_order_count = 0
        packets_received = 0
        skipped_packets = set()  # Track packets that were explicitly skipped

        start_time = time.time()
        last_status_time = start_time
        last_activity_time = start_time
        received_bytes = 0
        
        with open(file_path, "wb") as file:
            while True:
                try:
                    # Check for inactivity timeout
                    current_time = time.time()
                    if current_time - last_activity_time > self.INACTIVITY_TIMEOUT:
                        print("⚠️ Connection appears to be lost (no data received)")
                        # Send ACKs for last received packets to help client
                        if expected_seq_num > 1:
                            self.server_socket.sendto(struct.pack("!I", expected_seq_num-1), sender_addr)
                        last_activity_time = current_time
                    
                    # Receive data
                    data, addr = self.server_socket.recvfrom(self.BUFFER_SIZE + 4)
                    last_activity_time = time.time()
                    packets_received += 1
                    
                    seq_num, data = struct.unpack("!I", data[:4])[0], data[4:]

                    # Always acknowledge the received packet (multiple times for important packets)
                    send_count = 3 if (seq_num == expected_seq_num or b"EOF:" in data or data == b"SKIP_PACKET") else 1
                    for _ in range(send_count):
                        self.server_socket.sendto(struct.pack("!I", seq_num), sender_addr)

                    # Handle SKIP_PACKET marker
                    if data == b"SKIP_PACKET":
                        print(f"⚠️ Client indicates packet {seq_num} should be skipped")
                        skipped_packets.add(seq_num)
                        
                        # If this was the expected packet, move forward
                        if seq_num == expected_seq_num:
                            expected_seq_num += 1
                            print(f"⏭️ Advancing expected sequence to {expected_seq_num}")
                            
                            # Process any buffered packets that come after the skip
                            while expected_seq_num in buffer:
                                file.write(buffer[expected_seq_num])
                                received_bytes += len(buffer[expected_seq_num])
                                del buffer[expected_seq_num]
                                expected_seq_num += 1
                            
                        continue

                    # Check if this is EOF packet
                    if b"EOF:" in data:
                        print("🏁 Received EOF signal")
                        # Check if EOF contains lost packet information
                        eof_data = data.decode().split(":")
                        if len(eof_data) > 1 and eof_data[1] != "NONE":
                            lost_packets = set(map(int, eof_data[1].split(",")))
                            print(f"ℹ️ Client reported {len(lost_packets)} lost packets: {eof_data[1]}")
                            # Add any reported lost packets to our skip list if not already there
                            for lost_seq in lost_packets:
                                if lost_seq not in skipped_packets:
                                    skipped_packets.add(lost_seq)
                        break

                    # Already processed packet - duplicate
                    if seq_num < expected_seq_num or seq_num in skipped_packets:
                        duplicate_count += 1
                        if DEVMODE and duplicate_count % 10 == 0:  # Don't flood logs
                            print(f"🔁 Duplicate packet received: {seq_num}")
                        continue

                    # In-order packet - process immediately
                    if seq_num == expected_seq_num:
                        file.write(data)
                        received_bytes += len(data)
                        expected_seq_num += 1

                        # Check if we have buffered packets that can now be processed
                        while expected_seq_num in buffer:
                            file.write(buffer[expected_seq_num])
                            received_bytes += len(buffer[expected_seq_num])
                            del buffer[expected_seq_num]
                            expected_seq_num += 1
                            
                        # Check if the next expected is in our skip list
                        while expected_seq_num in skipped_packets:
                            print(f"⏭️ Skipping previously marked packet {expected_seq_num}")
                            expected_seq_num += 1
                    
                    # Out-of-order packet - buffer it
                    elif seq_num > expected_seq_num:
                        out_of_order_count += 1
                        # Only store if we don't already have this packet
                        if seq_num not in buffer and seq_num not in skipped_packets:
                            buffer[seq_num] = data
                            if DEVMODE and out_of_order_count % 10 == 0:  # Reduce log spam
                                print(f"🔄 Out-of-order packet buffered: {seq_num}")
                    
                    # Print status periodically
                    current_time = time.time()
                    if current_time - last_status_time >= self.STATUS_INTERVAL:
                        elapsed = current_time - start_time
                        speed = received_bytes / elapsed / 1024 if elapsed > 0 else 0
                        
                        buffer_status = f"{len(buffer)} packets"
                        if len(buffer) > 0:
                            buffer_min = min(buffer.keys()) if buffer else 'N/A'
                            buffer_max = max(buffer.keys()) if buffer else 'N/A'
                            buffer_status += f" [{buffer_min}-{buffer_max}]"
                        
                        skip_info = f"Skipped: {len(skipped_packets)}" if skipped_packets else ""
                        
                        print(f"📊 Status: Received {received_bytes/1024:.2f} KiB | "
                              f"Speed: {speed:.2f} KiB/s | "
                              f"Expected seq: {expected_seq_num} | "
                              f"Buffer: {buffer_status} | "
                              f"Duplicates: {duplicate_count} | {skip_info}")
                        
                        last_status_time = current_time
                            
                except socket.timeout:
                    # Socket timeout is normal - just continue
                    continue
                except Exception as e:
                    print(f"❌ Error during receive: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

        # Calculate elapsed time and speed
        elapsed = time.time() - start_time
        speed = received_bytes / elapsed / 1024 if elapsed > 0 else 0
        
        # Calculate file checksum
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as file:
            for chunk in iter(lambda: file.read(self.BUFFER_SIZE), b''):
                md5_hash.update(chunk)
        checksum = md5_hash.hexdigest()
        
        # Print transfer summary
        print(f"\n📈 Transfer Summary for '{os.path.basename(file_path)}':")
        print(f"🔍 MD5 Checksum: {checksum}")
        print(f"✅ File received successfully in {elapsed:.2f} seconds")
        print(f"📊 Size: {received_bytes/1024:.2f} KiB, Speed: {speed:.2f} KiB/s")
        print(f"📦 Total packets received: {packets_received}")
        print(f"🔄 Out-of-order packets: {out_of_order_count}")
        print(f"🔁 Duplicate packets: {duplicate_count}")
        if skipped_packets:
            print(f"⏭️ Skipped packets: {len(skipped_packets)} ({', '.join(map(str, sorted(skipped_packets)))})")
        print(f"🗂️ Saved as: {file_path}")
        exit(0)

if __name__ == "__main__":
    try:
        host = '0.0.0.0'
        port = 6969
        
        if len(sys.argv) > 2:
            host = sys.argv[1]
            port = int(sys.argv[2])
            
        server = TCPficationServer(host, port)
        server.start()
    except KeyboardInterrupt:
        print("🦊 Server shutting down...")