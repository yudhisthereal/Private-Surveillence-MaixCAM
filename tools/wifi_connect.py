from maix import network, err
import os
from dotenv import load_dotenv

load_dotenv()

def connect_wifi(ssid=None, password=None):
    # Load from environment if not provided
    if ssid is None:
        ssid = os.getenv("WIFI_SSID", "MaixCAM-Wifi")
    if password is None:
        password = os.getenv("WIFI_PASSWORD", "maixcamwifi")
    
    w = network.wifi.Wifi()
    e = w.connect(ssid, password, wait=True, timeout=60)
    err.check_raise(e, "connect wifi failed")
    ip = w.get_ip()
    print("Connect success, got ip:", ip)
    return ip
