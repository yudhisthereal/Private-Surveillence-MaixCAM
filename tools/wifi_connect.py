from maix import network, err

def connect_wifi(ssid, password):
    w = network.wifi.Wifi()
    e = w.connect(ssid, password, wait=True, timeout=60)
    err.check_raise(e, "connect wifi failed")
    ip = w.get_ip()
    print("Connect success, got ip:", ip)
    return ip
