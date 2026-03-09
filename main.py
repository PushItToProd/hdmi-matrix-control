import argparse
import serial
import time


DEFAULT_PORT = '/dev/ttyACM0'


def get_parser():
    parser = argparse.ArgumentParser(description='Communicate with the serial port')
    parser.add_argument('command')
    parser.add_argument('--port', default='/dev/ttyACM0')
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

def send_command(ser, cmd: str) -> str:
    ser.write(f'{cmd}\r'.encode('ascii'))
    # TODO: wait for the full response (ending with a carriage return) instead
    # of just sleeping
    time.sleep(0.5)
    return ser.read_all().decode('ascii')


def main():
    args = get_parser().parse_args()

    with open_port(args.port) as ser:
        # remove spaces in command
        cmd = args.command.replace(' ', '')
        if not cmd:
            print('No command provided')
            return
        print(send_command(ser, cmd))


if __name__ == '__main__':
    main()
