from ib_insync import IB
import os
from dotenv import load_dotenv

load_dotenv()
HOST      = os.getenv('IB_HOST', '127.0.0.1')
PORT      = int(os.getenv('IB_PORT', 7497))
CLIENT_ID = int(os.getenv('IB_CLIENT_ID', 1))

def connect_ibkr():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    print(f"Connected to IBKR @ {HOST}:{PORT} (clientId={CLIENT_ID})")
    return ib

if __name__ == '__main__':
    ib = connect_ibkr()
    ib.disconnect()
