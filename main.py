import serial
import time


ser = serial.Serial(
    port='/dev/ttyACM0',
    baudrate=57600,  # from your STA output
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=1
)

def send_command(cmd: str) -> str:
    ser.write(f'{cmd}\r'.encode('ascii'))
    # TODO: wait for the full response instead of just sleeping
    time.sleep(0.5)
    return ser.read_all().decode('ascii')


def main():
    print(send_command('STA'))
    print()
    print()
    print(send_command('H'))
    print()
    ser.close()


if __name__ == '__main__':
    main()
