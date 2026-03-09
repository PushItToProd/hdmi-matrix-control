import argparse
import sys
import time

import serial


DEFAULT_PORT = '/dev/ttyACM0'


def get_parser():
    parser = argparse.ArgumentParser(description='Communicate with the serial port')
    parser.add_argument('command')
    parser.add_argument('--port', default='/dev/ttyACM0')
    parser.add_argument('--bin', action='store_true', help='print the response as binary data instead of ASCII')
    return parser


def open_port(port='/dev/ttyACM0'):
    return serial.Serial(
        port=port,
        baudrate=57600,  # from your STA output
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1
    )


def send_command_raw(ser, cmd: str) -> bytes:
    ser.write(f'{cmd}\r'.encode('ascii'))
    # TODO: wait for the full response (ending with a carriage return) instead
    # of just sleeping
    time.sleep(0.1)
    return ser.read_all()

def send_command(ser, cmd: str) -> str | None:
    resp = send_command_raw(ser, cmd)
    if resp:
        return resp.decode('ascii')
    return None


def main():
    args = get_parser().parse_args()

    with open_port(args.port) as ser:
        # remove spaces in command
        cmd = args.command.replace(' ', '')
        if not cmd:
            print('No command provided')
            return
        if args.bin:
            resp = send_command_raw(ser, cmd)
            sys.stdout.buffer.write(resp)
        else:
            resp = send_command(ser, cmd)
            if resp:
                print(resp)
            else:
                print('No response received')


if __name__ == '__main__':
    main()
