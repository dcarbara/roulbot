from PIL import Image
import sys
import pytesseract
import pyautogui
import os
import glob
import time
import logging
from datetime import datetime, timedelta
from PIL import ImageOps
import re

# Setup logging
logger = logging.getLogger(__name__)

# Global debug image counter to limit frequency
debug_image_counter = 0
debug_image_interval = 30  # Save every 30th image (much less frequent)

# Debug cleanup settings
DEBUG_CLEANUP_INTERVAL = 60  # Clean up every 1 minute (more frequent)
DEBUG_MAX_AGE_HOURS = 1  # Keep debug images for only 1 hour (much shorter)
last_cleanup_time = 0

def initialize_ocr(tesseract_path):
    """
    Initialize Tesseract OCR.
    Prioritizes bundled Tesseract if running in frozen mode (PyInstaller).
    Otherwise uses the provided path or configuration.
    """
    # 1. Check for bundled Tesseract (PyInstaller OneDir/OneFile)
    if getattr(sys, 'frozen', False):
        # If running as compiled exe
        if hasattr(sys, '_MEIPASS'):
            # OneFile mode - unpacked to temp dir
            bundled_path = os.path.join(sys._MEIPASS, "Tesseract-OCR", "tesseract.exe")
        else:
            # OneDir mode - typically in _internal or next to executable
            # Try next to executable first (cleaner structure)
            base_path = os.path.dirname(sys.executable)
            bundled_path = os.path.join(base_path, "Tesseract-OCR", "tesseract.exe")
            
            # Also try _internal/Tesseract-OCR if that's where we put it
            if not os.path.exists(bundled_path):
                bundled_path = os.path.join(base_path, "_internal", "Tesseract-OCR", "tesseract.exe")

        if os.path.exists(bundled_path):
            pytesseract.pytesseract.tesseract_cmd = bundled_path
            logger.info(f"Using bundled Tesseract: {bundled_path}")
            return True

    # 2. Check the user-configured path
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        logger.info(f"Using configured Tesseract: {tesseract_path}")
        return True
    
    # 3. Last fallback: Default install location
    default_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(default_path):
        pytesseract.pytesseract.tesseract_cmd = default_path
        logger.info(f"Using default system Tesseract: {default_path}")
        return True

    logger.error(f"Tesseract not found at {tesseract_path} or bundled paths.")
    return False

def cleanup_debug_screens():
    """
    Clean up old debug screens periodically to prevent disk space issues.
    Removes debug images older than DEBUG_MAX_AGE_HOURS.
    """
    global last_cleanup_time
    current_time = time.time()
    
    # Only run cleanup every DEBUG_CLEANUP_INTERVAL seconds
    if current_time - last_cleanup_time < DEBUG_CLEANUP_INTERVAL:
        return
    
    last_cleanup_time = current_time
    
    try:
        debug_dir = "debug_screens"
        if not os.path.exists(debug_dir):
            return
        
        # Calculate cutoff time
        cutoff_time = datetime.now() - timedelta(hours=DEBUG_MAX_AGE_HOURS)
        cutoff_timestamp = cutoff_time.timestamp()
        
        # Find all PNG files in debug directory
        pattern = os.path.join(debug_dir, "*.png")
        files_to_delete = []
        
        for file_path in glob.glob(pattern):
            try:
                file_timestamp = os.path.getmtime(file_path)
                if file_timestamp < cutoff_timestamp:
                    files_to_delete.append(file_path)
            except (OSError, IOError) as e:
                logger.warning(f"⚠️ Error checking file {file_path}: {e}")
                continue
        
        # Delete old files
        deleted_count = 0
        for file_path in files_to_delete:
            try:
                os.remove(file_path)
                deleted_count += 1
            except (OSError, IOError) as e:
                logger.error(f"❌ Error deleting {file_path}: {e}")
        
        if deleted_count > 0:
            logger.info(f"🧹 Cleaned up {deleted_count} old debug screens (older than {DEBUG_MAX_AGE_HOURS} hours)")
        
        # Check total disk usage
        total_files = len(glob.glob(pattern))
        if total_files > 100:  # Warn if too many files
            logger.warning(f"⚠️ Warning: {total_files} debug screens in directory")
            
    except Exception as e:
        logger.error(f"❌ Error during debug cleanup: {e}")

def capture_region_image(window, region):
    """
    Capture a screenshot of the specified region within the given window.
    """
    left = window.left + int(window.width * region["x1_pct"])
    top = window.top + int(window.height * region["y1_pct"])
    right = window.left + int(window.width * region["x2_pct"])
    bottom = window.top + int(window.height * region["y2_pct"])
    width = max(1, right - left)
    height = max(1, bottom - top)

    # print(f"📸 Capturing region: ({left}, {top}) to ({right}, {bottom}) - Size: {width}x{height}")
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    # Guard against tiny images that crash Tesseract (0x40000015)
    if screenshot.width < 10 or screenshot.height < 10:
        logger.warning(f"Captured image too small ({screenshot.width}x{screenshot.height}), skipping OCR")
        return None
    return screenshot

def save_debug_image(image, region_name):
    """
    Save captured image for debugging purposes.
    Optimized to save only every Nth image to reduce disk I/O.
    """
    global debug_image_counter
    debug_image_counter += 1
    
    # Only save every Nth image to reduce overhead
    if debug_image_counter % debug_image_interval != 0:
        return None
    
    try:
        debug_dir = "debug_screens"
        if not os.path.exists(debug_dir):
            os.makedirs(debug_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{debug_dir}/{region_name}_{timestamp}.png"
        image.save(filename)
        # logger.debug(f"💾 Saved debug image: {filename}")
        
        # Run cleanup periodically
        cleanup_debug_screens()
        
        return filename
    except Exception as e:
        logger.error(f"❌ Failed to save debug image: {e}")
        return None

def extract_recent_numbers(window, region):
    """
    Uses OCR to extract recent roulette numbers from a screen region.
    """
    image = capture_region_image(window, region)
    if image is None:
        return []
    text = pytesseract.image_to_string(image, config='--psm 7')
    # logger.debug(f"🧾 OCR Raw Text: {repr(text)}")

    # Extract digits
    numbers = [int(token) for token in text.split() if token.isdigit()]
    return numbers

def get_absolute_region_from_config(window, region_pct):
    left = window.left + int(window.width * region_pct["x1_pct"])
    top = window.top + int(window.height * region_pct["y1_pct"])
    right = window.left + int(window.width * region_pct["x2_pct"])
    bottom = window.top + int(window.height * region_pct["y2_pct"])
    width = right - left
    height = bottom - top
    return {"left": left, "top": top, "width": width, "height": height}

def extract_table_state(window, region, successful_configs=None):
    """
    Uses OCR to extract the table state (e.g., 'PLACE YOUR BETS') from a screen region.
    Optimized to cache successful OCR configurations.
    """
    try:
        image = capture_region_image(window, region)
        if image is None:
            return ""
        # save_debug_image(image, "table_state")

        # Try cached successful configuration first
        if successful_configs and successful_configs.get('table_state'):
            text = pytesseract.image_to_string(image, config=successful_configs['table_state'])
            text_upper = text.upper().strip()
            
            # Check for common variations in the entire text
            if "NEXT GAME SOON" in text_upper or "NEXT" in text_upper:
                # print(f"✅ Found 'NEXT GAME SOON' (cached config): {text.strip()}")
                return "NEXT GAME SOON"
            elif "PLACE YOUR BETS" in text_upper or "PLACE" in text_upper:
                # print(f"✅ Found 'PLACE YOUR BETS' (cached config): {text.strip()}")
                return "PLACE YOUR BETS"
            elif any(phrase in text_upper for phrase in ["BETS", "BETTING"]):
                # print(f"✅ Found table state (cached config): {text.strip()}")
                return text.strip()
        
        # Try different OCR configurations
        configs = [
            '--psm 7',  # Single text line
            '--psm 8',  # Single word
            '--psm 6',  # Uniform block of text
            '--psm 13'  # Raw line
        ]
        
        for config in configs:
            text = pytesseract.image_to_string(image, config=config)
            # print(f"🧾 Table State OCR ({config}): {repr(text)}")
            
            # Check for common variations in the entire text
            text_upper = text.upper().strip()
            
            # Check for "NEXT GAME SOON" or just "NEXT"
            if "NEXT GAME SOON" in text_upper or "NEXT" in text_upper:
                # logger.debug(f"✅ Found 'NEXT GAME SOON': {text.strip()}")
                if successful_configs:
                    successful_configs['table_state'] = config
                return "NEXT GAME SOON"
            
            # Check for "PLACE YOUR BETS" or just "PLACE"
            elif "PLACE YOUR BETS" in text_upper or "PLACE" in text_upper:
                # logger.debug(f"✅ Found 'PLACE YOUR BETS': {text.strip()}")
                if successful_configs:
                    successful_configs['table_state'] = config
                return "PLACE YOUR BETS"
            
            # Check for other betting states
            elif any(phrase in text_upper for phrase in ["BETS", "BETTING"]):
                # logger.debug(f"✅ Found table state: {text.strip()}")
                if successful_configs:
                    successful_configs['table_state'] = config
                return text.strip()
        
        # logger.debug("❌ No table state detected in any OCR attempt")
        return text.strip() if text.strip() else ""
        
    except Exception as e:
        # logger.error(f"❌ Error in extract_table_state: {e}")
        return ""

def extract_balance(window, region, successful_configs=None):
    """
    Uses OCR to extract balance amount from a screen region.
    Optimized to cache successful OCR configurations.
    """
    try:
        image = capture_region_image(window, region)
        if image is None:
            return None
        # save_debug_image(image, "balance")

        # Try cached successful configuration first
        if successful_configs and successful_configs.get('balance'):
            text = pytesseract.image_to_string(image, config=successful_configs['balance'])
            logger.debug(f"💰 Balance OCR (cached config): {repr(text)}")
            
            # Extract numbers (including decimals)
            import re
            numbers = re.findall(r'\d+\.?\d*', text)
            if numbers:
                try:
                    balance = float(numbers[0])
                    logger.debug(f"✅ Parsed balance (cached config): {balance}")
                    return balance
                except ValueError:
                    logger.warning(f"❌ Could not parse balance number: {numbers[0]}")
        
        # Try different OCR configurations for numbers
        configs = [
            '--psm 7 -c tessedit_char_whitelist=0123456789.',  # Numbers and decimal only
            '--psm 8 -c tessedit_char_whitelist=0123456789.',
            '--psm 6 -c tessedit_char_whitelist=0123456789.',
            '--psm 7',  # Default
            '--psm 8'   # Default
        ]
        
        for config in configs:
            text = pytesseract.image_to_string(image, config=config)
            logger.debug(f"💰 Balance OCR ({config}): {repr(text)}")
            
            # Extract numbers (including decimals)
            import re
            numbers = re.findall(r'\d+\.?\d*', text)
            if numbers:
                try:
                    balance = float(numbers[0])
                    logger.debug(f"✅ Parsed balance: {balance}")
                    if successful_configs:
                        successful_configs['balance'] = config
                    return balance
                except ValueError:
                    logger.warning(f"❌ Could not parse balance number: {numbers[0]}")
                    continue
        
        logger.warning("❌ No balance detected in any OCR attempt")
        return None
        
    except Exception as e:
        logger.error(f"❌ Error in extract_balance: {e}")
        return None

def extract_winning_number_from_table_state(window, region, successful_configs=None):
    """
    Uses OCR to extract the winning number and color from the table_state region.
    Preprocesses the image (grayscale + threshold), saves every screenshot, and logs all OCR results.
    """
    import re
    try:
        image = capture_region_image(window, region)
        if image is None:
            return None, None
        # Preprocess: convert to grayscale and apply binary threshold
        gray = ImageOps.grayscale(image)
        # Apply a simple threshold
        threshold = 128
        bw = gray.point(lambda x: 255 if x > threshold else 0, '1')
        # Save every screenshot for debugging
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # debug_dir = "debug_screens"
        # if not os.path.exists(debug_dir):
        #     os.makedirs(debug_dir)
        # bw.save(f"{debug_dir}/table_state_winning_number_{timestamp}.png")



        # Try all configs every time, log all OCR results
        configs = [
            '--psm 7',
            '--psm 8',
            '--psm 6',
            '--psm 13'
        ]
        # Try all configs on both Binary (BW) and Grayscale images
        # Grayscale often works better for "3 RED" where threshold might hide the 3.
        images_to_try = [bw, gray]
        
        for img_idx, img_source in enumerate(images_to_try):
            for config in configs:
                text = pytesseract.image_to_string(img_source, config=config)
                # Log every attempt to help debug "9 RED" issues
                if img_idx == 1:
                     # Only log grayscale attempts if they find something or debug is on, to reduce noise?
                     # Actually log everything for now.
                     logger.debug(f"🧾 Table State OCR (GRAYSCALE {config}): {repr(text)}")
                else:
                     logger.debug(f"🧾 Table State OCR (BW {config}): {repr(text)}")
                
                cleaned = clean_ocr_text(text)
                number, color = extract_number_and_color(cleaned)
                if number is not None:
                    logger.debug(f"✅ Found winning number in table_state ({'GRAY' if img_idx==1 else 'BW'}): {number} {color if color else ''} (from '{cleaned}')")
                    if successful_configs:
                        successful_configs['table_state'] = config
                    return number, color
        logger.debug("❌ No winning number detected in table_state region (all configs tried)")
        return None, None
    except Exception as e:
        logger.error(f"❌ Error in extract_winning_number_from_table_state: {e}")
        return None, None

def clean_ocr_text(text):
    # Safe cleaning: standardizes newlines and spaces, but avoids risky character swaps
    # unless strictly safe.
    text = text.upper()
    
    # Only safe replacements here (like O -> 0 is usually fine for roulette context, but even that is risky for 'ODD')
    # Let's handle O -> 0 in specific number context, not globally if possible.
    # But for winning numbers (e.g. 0 GREEN), 0 is common.
    # Let's stick to MINIMAL cleaning here.
    
    text = text.encode('ascii', errors='ignore').decode()
    text = re.sub(r'[^A-Z0-9 \n\.]', ' ', text) # Keep dots for safety
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_number_and_color(text):
    import difflib
    
    # 1. Fix known color typos (safe)
    text = text.replace("REO", "RED").replace("R ED", "RED")
    text = text.replace("BL ACK", "BLACK").replace("BLA CK", "BLACK")
    # Fix '0' variants near GREEN
    text = text.replace("O GREEN", "0 GREEN").replace("Q GREEN", "0 GREEN")

    # 2. Targeted regex logic for "9"
    # Detect 'g' or 'q' used as digits specifically when followed by color
    # CAUTION: 'q' -> 9 caused 7 RED -> 9 RED.
    # We will ONLY map 'g'/'G' to 9. 'q'/'Q' is suspicious.
    # If 7 is read as Q, we must NOT map Q->9.
    
    # Try generic digit match first
    match = re.search(r'(\d{1,2})\s*(BLACK|RED|GREEN)', text, re.IGNORECASE)
    if match:
        return int(match.group(1)), match.group(2).upper()
        
    # Try "G RED" -> 9 RED
    match_g = re.search(r'([G])\s*(RED|BLACK)', text, re.IGNORECASE)
    if match_g:
         return 9, match_g.group(2).upper()
    
    # Try "S RED" -> 5 RED (Only if S clearly stands alone? S is 5 in leetspeak)
    match_s = re.search(r'([S])\s*(RED|BLACK)', text, re.IGNORECASE)
    if match_s:
         return 5, match_s.group(2).upper()
         
    # Try "E RED" -> 3 RED (Common misread)
    match_e = re.search(r'([E])\s*(RED|BLACK)', text, re.IGNORECASE)
    if match_e:
        return 3, match_e.group(2).upper()

    
    # Try "O RED" or "0 RED" -> 9 RED (Since 0 is ALWAYS GREEN in Roulette, seeing 0 RED is a clear misread)
    # This safely handles cases where 9 is read as 0 or O.
    match_o = re.search(r'([O0])\s*(RED)', text, re.IGNORECASE)
    if match_o:
         return 9, match_o.group(2).upper()

    # Try "Q RED" -> 9 RED (User reported 9 read as Q previously)
    # WARN: This risks 7 RED -> Q RED -> 9 RED.
    # But since 7 is straight and Q is round, and user insists 9 fails, we enable this.
    match_q = re.search(r'([Q])\s*(RED)', text, re.IGNORECASE)
    if match_q:
         return 9, match_q.group(2).upper()

    # Try "B RED" -> 3 RED (Common misread, and SAFE because 8 is BLACK)
    # So if we see B + RED, it can't be 8.
    match_b = re.search(r'([B])\s*(RED)', text, re.IGNORECASE)
    if match_b:
        return 3, match_b.group(2).upper()

    # Fuzzy match for distorted '0 GREEN' (e.g., 'TO GREENT ee', '0 GREENT', '0 GREE', etc)
    # Look for a digit (possibly '0' or 'O') and a word similar to 'GREEN'
    tokens = text.split()
    for i, token in enumerate(tokens):
        # Accept '0', 'O', or 'TO' as zero
        if token in {'0', 'O', 'TO', 'Q'}: # O and Q often 0
             for j in range(i+1, min(i+3, len(tokens))):
                if difflib.SequenceMatcher(None, tokens[j], 'GREEN').ratio() > 0.7:
                    return 0, 'GREEN'
        # Accept '0GREEN', 'OGREEN', '0GREENT', etc
        if difflib.SequenceMatcher(None, token, '0GREEN').ratio() > 0.7:
            return 0, 'GREEN'
    if any(phrase in text for phrase in ["PLACE YOUR BETS", "PLACE BETS", "YOUR BETS"]):
        return None, None
    return None, None
