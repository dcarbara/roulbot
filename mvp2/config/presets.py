# engine/mvp2/config/presets.py

"""
Casino Presets Configuration
Stores normalized coordinates (0.0 to 1.0) for various casino layouts.
"""

CASINO_PRESETS = {
    "Evolution Gaming (Standard)": {
        "description": "Standard Evolution Gaming interface. Align window so that the video feed covers most of the area.",
        "coordinates": {
            # --- Chips (Bottom Center usually) ---
            "chip_100":  {"x_pct": 0.665, "y_pct": 0.92}, # Example
            "chip_25":   {"x_pct": 0.625, "y_pct": 0.92},
            "chip_5":    {"x_pct": 0.585, "y_pct": 0.92},
            "chip_1":    {"x_pct": 0.545, "y_pct": 0.92},
            "chip_.5":   {"x_pct": 0.505, "y_pct": 0.92},
            "chip_.1":   {"x_pct": 0.465, "y_pct": 0.92},
            
            # --- Outside Bets ---
            "red":       {"x_pct": 0.490, "y_pct": 0.72},
            "black":     {"x_pct": 0.535, "y_pct": 0.72},
            "even":      {"x_pct": 0.400, "y_pct": 0.72},
            "odd":       {"x_pct": 0.625, "y_pct": 0.72},
            "low":       {"x_pct": 0.310, "y_pct": 0.72}, # 1-18
            "high":      {"x_pct": 0.715, "y_pct": 0.72}, # 19-36
            
            "1st 12":    {"x_pct": 0.310, "y_pct": 0.64},
            "2nd 12":    {"x_pct": 0.512, "y_pct": 0.64},
            "3rd 12":    {"x_pct": 0.715, "y_pct": 0.64},
            
            # --- Regions (Box: x1, y1, x2, y2) ---
            "winning_number_region": {
                "x1_pct": 0.02, "y1_pct": 0.15, 
                "x2_pct": 0.06, "y2_pct": 0.22
            },
            "balance_region": {
                "x1_pct": 0.02, "y1_pct": 0.90,
                "x2_pct": 0.15, "y2_pct": 0.95
            },
            
            # --- Zero ---
            "0": {"x_pct": 0.15, "y_pct": 0.5} 
            # (Full number mapping would be added here in a real production preset)
        }
    }
}

def get_preset_names():
    return list(CASINO_PRESETS.keys())

def get_preset(name):
    return CASINO_PRESETS.get(name)
