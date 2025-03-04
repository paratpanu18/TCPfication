import socket
import os
import sys
import struct
import time
import hashlib

DEVMODE = False

class TCPficationClient:
    BUFFER_SIZE: int = 1450
    TIMEOUT: float = 1  # seconds
    WINDOW_SIZE: int = 10   # Number of packets that can be in flight
    STATUS_INTERVAL: float = 2.0  # seconds between status updates
    MAX_RETRIES: int = 20  # Maximum retries per packet

    def __init__(self, host='10.20.23.32', port=6969) -> None:
        self.host: str = host
        self.port: int = port
        self.client_socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client_socket.settimeout(self.TIMEOUT)
        self.retransmissions = 0
        self.packets_sent = 0
        self.lost_packets = set()  # Track packets that couldn't be delivered

    def send_file(self, file_path):
        try:
            filename = os.path.basename(file_path)
            seq_num = 0
            file_size = os.path.getsize(file_path)
            
            print(f"🔍 File: {filename}, Size: {file_size/1024:.2f} KiB")
            
            # Send filename with sequence number
            attempts = 0
            while attempts < 5:  # Try 5 times to send filename
                self.client_socket.sendto(struct.pack("!I", seq_num) + filename.encode('utf-8'), (self.host, self.port))
                self.packets_sent += 1
                
                # Wait for filename ACK
                try:
                    ack, _ = self.client_socket.recvfrom(4)
                    ack_num = struct.unpack("!I", ack)[0]
                    if ack_num == seq_num:
                        break
                    else:
                        print(f"⚠️ Unexpected ACK for filename: {ack_num}")
                except socket.timeout:
                    attempts += 1
                    print(f"⚠️ Timeout waiting for filename ACK, retry {attempts}/5")
            
            if attempts == 5:
                print("❌ Failed to establish connection after 5 attempts")
                return
            
            base_seq_num = 1  # First packet sequence number
            next_seq_num = 1  # Next sequence number to use
            window = {}       # Dictionary to track unacknowledged packets: {seq_num: (packet_data, sent_time)}
            
            start_time = time.time()
            last_status_time = start_time
            print(f"🚀 Starting file transfer: {filename}")
            
            with open(file_path, 'rb') as file:
                # Read all chunks into memory for easier pipelining
                chunks = []
                while chunk := file.read(self.BUFFER_SIZE):
                    chunks.append(chunk)
                
                chunk_index = 0
                total_chunks = len(chunks)
                retry_counts = {}  # Track retries per packet
                
                while chunk_index < total_chunks or window:
                    # Send packets to fill the window
                    while len(window) < self.WINDOW_SIZE and chunk_index < total_chunks:
                        seq_num = next_seq_num
                        chunk = chunks[chunk_index]
                        packet = struct.pack("!I", seq_num) + chunk
                        self.client_socket.sendto(packet, (self.host, self.port))
                        self.packets_sent += 1
                        window[seq_num] = (packet, time.time())
                        retry_counts[seq_num] = 0  # Initialize retry counter
                        next_seq_num += 1
                        chunk_index += 1
                    
                    # Try to receive ACKs with longer timeout when window is full
                    # This ensures we don't move forward too quickly
                    if len(window) >= self.WINDOW_SIZE or chunk_index >= total_chunks:
                        self.client_socket.settimeout(0.1)  # Longer timeout when waiting is important
                    else:
                        self.client_socket.settimeout(0.01)  # Shorter for quick polling
                    
                    # Process acknowledgments
                    try_receive = True
                    receive_start = time.time()
                    
                    # Try to receive multiple ACKs if available (up to 100ms worth of checking)
                    while try_receive and (time.time() - receive_start < 0.1):
                        try:
                            ack, _ = self.client_socket.recvfrom(4)
                            ack_num = struct.unpack("!I", ack)[0]
                            
                            # If we get an ACK, remove it from the window and reset retry counter
                            if ack_num in window:
                                del window[ack_num]
                                if ack_num in retry_counts:
                                    del retry_counts[ack_num]
                                
                            # Update base sequence number if possible
                            if ack_num == base_seq_num:
                                base_seq_num += 1
                                # Move base forward if consecutive packets are already acknowledged
                                while base_seq_num < next_seq_num and base_seq_num not in window:
                                    base_seq_num += 1
                        except socket.timeout:
                            try_receive = False  # Exit receive loop on timeout
                    
                    # Check for timeouts and resend 
                    current_time = time.time()
                    for seq_num, (packet, send_time) in list(window.items()):
                        if current_time - send_time > self.TIMEOUT:
                            if retry_counts.get(seq_num, 0) < self.MAX_RETRIES:
                                if DEVMODE:
                                    print(f"⚠️ Timeout, resending packet {seq_num} (retry {retry_counts.get(seq_num, 0)+1}/{self.MAX_RETRIES})")
                                self.client_socket.sendto(packet, (self.host, self.port))
                                self.packets_sent += 1
                                self.retransmissions += 1
                                window[seq_num] = (packet, current_time)
                                retry_counts[seq_num] = retry_counts.get(seq_num, 0) + 1
                            else:
                                # CRITICAL FIX: Send termination marker for packets that reached max retries
                                # This helps the server know not to expect this packet anymore
                                print(f"❌ Maximum retries reached for packet {seq_num}, sending SKIP marker")
                                self.lost_packets.add(seq_num)
                                # Send special SKIP marker to server so it knows to skip this sequence number
                                skip_packet = struct.pack("!I", seq_num) + b"SKIP_PACKET"
                                for _ in range(3):  # Send multiple times to ensure delivery
                                    self.client_socket.sendto(skip_packet, (self.host, self.port))
                                # Remove from window to unblock transfer
                                del window[seq_num]
                                if seq_num in retry_counts:
                                    del retry_counts[seq_num]
                    
                    # Wait a bit to prevent CPU overload
                    if not window:  # If window is empty, wait longer
                        time.sleep(0.01)
                        
                    # Print status update periodically
                    current_time = time.time()
                    if current_time - last_status_time >= self.STATUS_INTERVAL:
                        progress = min(chunk_index / total_chunks * 100, 100.0)
                        elapsed = current_time - start_time
                        speed = (chunk_index * self.BUFFER_SIZE) / elapsed / 1024 if elapsed > 0 else 0
                        retry_rate = (self.retransmissions / max(1, self.packets_sent)) * 100
                        
                        # Create progress bar
                        bar_length = 20
                        filled_length = int(progress / 100 * bar_length)
                        bar = '█' * filled_length + '░' * (bar_length - filled_length)
                        
                        print(f"📊 Progress: [{bar}] {progress:.1f}% | "
                            f"Speed: {speed:.2f} KiB/s | "
                            f"Window: {len(window)}/{self.WINDOW_SIZE} | "
                            f"Retries: {self.retransmissions} ({retry_rate:.1f}%)")
                        
                        last_status_time = current_time
            
            # Reset timeout for EOF handling
            self.client_socket.settimeout(self.TIMEOUT)
            
            # Send EOF with last sequence number
            eof_attempts = 0
            eof_seq_num = next_seq_num
            print("🏁 Finalizing transfer...")
            while eof_attempts < 10:  # Try 10 times
                # Include lost packet information in EOF message
                lost_packet_data = ",".join(map(str, sorted(self.lost_packets))) if self.lost_packets else "NONE"
                eof_message = f"EOF:{lost_packet_data}"
                
                self.client_socket.sendto(struct.pack("!I", eof_seq_num) + eof_message.encode(), (self.host, self.port))
                self.packets_sent += 1
                try:
                    ack, _ = self.client_socket.recvfrom(4)
                    ack_num = struct.unpack("!I", ack)[0]
                    if ack_num == eof_seq_num:
                        break
                except socket.timeout:
                    eof_attempts += 1
                    print(f"⚠️ Timeout, resending EOF packet {eof_seq_num} (attempt {eof_attempts}/10)")
            
            elapsed = time.time() - start_time
            speed = file_size / elapsed / 1024 if elapsed > 0 else 0
            retry_rate = (self.retransmissions / max(1, self.packets_sent)) * 100
            
            md5_hash = hashlib.md5()
            with open(file_path, 'rb') as file:
                for chunk in iter(lambda: file.read(self.BUFFER_SIZE), b''):
                    md5_hash.update(chunk)
            checksum = md5_hash.hexdigest()
            
            print(f"\n📈 Transfer Summary for '{filename}':")
            print(f"🔍 MD5 Checksum: {checksum}")
            print(f"✅ Transferred {file_size/1024:.2f} KiB in {elapsed:.2f} seconds")
            print(f"📦 Total packets sent: {self.packets_sent}")
            if self.lost_packets:
                print(f"❌ Lost packets: {len(self.lost_packets)} ({', '.join(map(str, sorted(self.lost_packets)))})")
            print(f"📊 Speed: {speed:.2f} KiB/s")
            
        except FileNotFoundError:
            print(f"❌ Error: File '{file_path}' not found.")
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

def main():
    if len(sys.argv) < 4:
        print("Usage: python urft_client.py <file_path> <server_ip> <server_port>")
        sys.exit(1)

    file_path = sys.argv[1]
    server_ip = sys.argv[2]
    server_port = int(sys.argv[3])

    client = TCPficationClient(host=server_ip, port=server_port)
    client.send_file(file_path)
    
if __name__ == "__main__":
    main()