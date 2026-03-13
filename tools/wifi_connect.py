from maix import network, err
import os
import time as py_time

def connect_wifi(ssid=None, password=None, timeout_s=10, retry_interval_s=1, max_retries=None):
    # Load from environment if not provided
    if ssid is None:
        ssid = os.getenv("WIFI_SSID", "MaixCAM-Wifi")
    if password is None:
        password = os.getenv("WIFI_PASSWORD", "maixcamwifi")

    attempt = 0
    while True:
        attempt += 1
        w = network.wifi.Wifi()
        try:
            e = w.connect(ssid, password, wait=True, timeout=timeout_s)
            err.check_raise(e, "connect wifi failed")
            ip = w.get_ip()
            print("Connect success, got ip:", ip)
            return ip
        except Exception as e:
            print(f"Wi-Fi connect attempt {attempt} failed (timeout={timeout_s}s): {e}")

            if max_retries is not None and attempt >= max_retries:
                raise

            py_time.sleep(retry_interval_s)
