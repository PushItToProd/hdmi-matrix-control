import serial
import time


def open_port():
    return serial.Serial(
        port='/dev/ttyACM0',
        baudrate=57600,  # from your STA output
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1
    )

def send_command(ser, cmd: str) -> str:
    ser.write(f'{cmd}\r'.encode('ascii'))
    # TODO: wait for the full response instead of just sleeping
    time.sleep(0.5)
    return ser.read_all().decode('ascii')


def main():
    with open_port() as ser:
        print(send_command(ser, 'STA'))
        print()
        print()
        print(send_command(ser, 'H'))
        print()


if __name__ == '__main__':
    main()
