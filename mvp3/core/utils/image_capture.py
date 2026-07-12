# core/utils/image_capture.py

import pyautogui
from PIL import Image

def capture_region_image(window, region_config):
    """
    Captures a screenshot of a region in a given window.

    :param window: A pygetwindow window object
    :param region_config: Dict with x1_pct, y1_pct, x2_pct, y2_pct
    :return: PIL.Image object of the cropped region
    """
    if not window or not region_config:
        raise ValueError("Missing window or region config.")

    x1 = int(window.left + window.width * region_config["x1_pct"])
    y1 = int(window.top + window.height * region_config["y1_pct"])
    x2 = int(window.left + window.width * region_config["x2_pct"])
    y2 = int(window.top + window.height * region_config["y2_pct"])

    screenshot = pyautogui.screenshot()
    region_img = screenshot.crop((x1, y1, x2, y2))
    return region_img
