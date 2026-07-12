import pygetwindow as gw
import time
from core.ocr_utils import extract_text_from_region

# Replace this with your actual window title from your coordinates
WINDOW_TITLE = "Stake"

# Replace this with your actual recorded coordinates from your config (example values below)
coordinates = {
    "winning_number": {
      "x1_pct": 0.2058,
      "y1_pct": 0.0427,
      "x2_pct": 0.6223,
      "y2_pct": 0.2366
    }
}

def main():
    # Step 1: Get the browser window
    win = next((w for w in gw.getAllWindows() if WINDOW_TITLE in w.title), None)
    if not win:
        print("❌ Could not find browser window.")
        return

    # Step 2: Pick the region
    region = coordinates.get("winning_number")
    if not region:
        print("❌ 'winning_number' region not found.")
        return

    # Step 3: Extract text + save debug image
    from core.ocr_utils import extract_text_from_region
    text = extract_text_from_region(win, region, debug_label="winning_number")

    # Step 4: Print the result
    print("🧾 Extracted Text:", repr(text))

if __name__ == "__main__":
    main()
