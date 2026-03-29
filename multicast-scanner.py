import logging
import signal
import concurrent.futures
import argparse
import socket
import select
import os
import platform
import subprocess
import json
import sys
import ipaddress

# Set up the command line argument parsing
parser = argparse.ArgumentParser(description='Script to check the IPTV UDP streams from m3u playlist')

parser.add_argument("--range", help="Range of IPs to scan.", required=True)
parser.add_argument("--size", help="Size of the subnets to divide.", required=False, default='32')
parser.add_argument("--playlist", help="Playlist *.m3u file with UDP streams", required=False)
parser.add_argument("--nic", help="network interface IP address with UDP stream", required=False, default='0.0.0.0')
parser.add_argument("--udp_timeout", help="Time to wait in seconds for the UPD port reply", required=False, default=1)
parser.add_argument("--port", help="addtional UDP port to scan. Default: 1234", required=False, default=['1234'], nargs='+')
parser.add_argument("--info_timeout", help="Time to wait in seconds for the stream's info", required=False, default=6)
parser.add_argument("--threads", help="Number of threads to use", required=False, default=10)

# Define the variable for the channels dictionary
channels_dictionary = []

# Define the variable for the list of the channels without names
unnamed_channels_dictionary = []

def create_file(ip_range):
    """ Prepare the resulting playlist file """
    current_path = os.path.dirname(os.path.realpath(__file__))
    network = ipaddress.IPv4Network(ip_range)
    file_name = f'scan_results_range_{network.network_address}-{network.prefixlen}.m3u'
    file = os.path.join(current_path, file_name)
    with open(file, 'w') as f:
        f.write('#EXTM3U\n')
    return file_name, file

def playlist_add(ip, port, name, file, channels_dict): # TODO: fix adding external playlist
    """ Add the given IP and port to the playlist file"""
    if isinstance(name,str):
        channel_string = f'#EXTINF:2,{name}\n'
    else:
        channel_string = f'#EXTINF:2,Channel: {ip}:{port}\n'

    with open(file, 'a') as f:
        if args.playlist:
            if f'{str(ip)}:{port}' not in list(channels_dict.values()):
                f.write(channel_string)
                f.write(f'udp://{ip}:{port}\n')
                print(f'[*] !!! Channel added to the playlist. {ip}:{port} >>> {name} !!!')
                return 0
            print(f'[*] The channel is already in the playlist: {ip}:{port} >>> {name}')
            return 0
        f.write(channel_string)
        f.write(f'udp://{ip}:{port}\n')
    # print(f'[*] !!! Channel added to the playlist. {ip}:{port} >>> {name} !!!')
    return 0

def socket_creator(nick, port):
    """Create a socket for the multicast address"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if platform.system() == 'Darwin':
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((nick, int(port)))
    return sock

def channel_checker(sock, arguments):
    """Check the channel for the stream info"""
    try:
        timeout=int(arguments.info_timeout)
        ready = select.select([sock],[],[],timeout)
        if ready[0]:
            sock.close()
            return True
        sock.close()
        return False
    except (socket.error, select.error) as exception:
        print(f"Error: {exception}")
        return False

def get_ffprobe(address,port,nic,info_timeout):
    """ To get the json data from ip:port """

    cmd = [
        'ffprobe', 
        '-v', 'quiet', 
        '-print_format', 'json', 
        '-show_programs', 
        '-localaddr', nic,
        f'udp://{address}:{port}'
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=info_timeout,
            check=True
        )

        # json_string = json.loads(str(result.stdout))
        channel_name = json.loads(result.stdout)['programs'][0]['tags']['service_name']
        print(f'[*] Channel: {channel_name} found on {address}:{port}')
        return channel_name
    except subprocess.TimeoutExpired:
        print(f"[*] No channel found on {address}:{port} in {info_timeout} seconds")
    except subprocess.CalledProcessError as e:
        print(f"FFprobe failed with exit code {e.returncode}: {e.stderr}")
    except json.JSONDecodeError:
        print("Failed to parse FFprobe output as JSON")
    return None # TODO: eliminate all newline Nones in output

def ip_scanner(ip_addresses, ports):
    """Scan the IP address for the UDP streams"""
    for ip in ip_addresses:
        for port in ports:
            sock = socket_creator(args.nic, port)
            result = channel_checker(sock, args)
            if result:
                # print(f'[*] Channel found on {ip}:{port}')
                channel_name = get_ffprobe(ip, port, args.nic, args.info_timeout)
                if channel_name:
                    playlist_add(ip, port, channel_name, playlist_file, channels_dictionary)
                # else:
                    # print(f'[*] Channel found on {ip}:{port} but no name found.')
                    # print(f'[*] You can add the channel manually to the playlist.')
            else:
                print(f'[*] No channel found on {ip}:{port}')


def handler(signalname):
    """Handle the signals"""
    def _handler(signum, frame):
        raise KeyboardInterrupt(f'[*] Signal {signalname} received. Exiting...')
    return _handler

signal.signal(signal.SIGINT, handler("SIGINT"))

args = parser.parse_args()
port_list = []

if args.port:
    port_list = port_list + args.port
    port_list = list(set(port_list))

try:
    ip_list = ipaddress.IPv4Network(args.range)
except ValueError:
    print('\n[*] Please define a proper IP range.\n[*] >>> Example: 224.0.0.0/24\n')
    sys.exit()

if ip_list.is_multicast:
    pass
else:
    print('[*] IPs provided are not multicast. Please try again.')
    sys.exit()

try:
    subnets = ip_list.subnets(new_prefix=int(args.size))
    subnets = list(subnets)
except ValueError:
    print(f'[*] ERROR: the new prefix --size: "{args.size}" must be longer than the given IPs subnet: "/{ip_list.prefixlen}"\n')
    sys.exit()

try:
    arg_max_workers = int(args.threads)
except ValueError:
    print('[*] ERROR: the number of threads must be an integer.\n')
    sys.exit()

playlist_file_name, playlist_file = create_file(args.range)

try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=arg_max_workers) as executor:
        results = [executor.submit(ip_scanner, subnet, port_list) for subnet in subnets]
        for item in concurrent.futures.as_completed(results):
            print(item.result())
except (KeyboardInterrupt, SystemExit):
    print('[*] Exiting...')
    raise
except Exception as e:
    print(f'[*] ERROR: {e}')
    sys.exit()
