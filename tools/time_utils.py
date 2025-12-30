import time

def get_timestamp_str():
    """
    Returns a timestamp string in the format: YYYY-MM-DD_HH-MM-SS
    """
    tm = time.localtime()
    return f"{tm[0]:04d}-{tm[1]:02d}-{tm[2]:02d}_{tm[3]:02d}-{tm[4]:02d}-{tm[5]:02d}"