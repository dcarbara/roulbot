import sys
import os
import json
import argparse

# Add parent directory to path to import core
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.encryption import encrypt_strategy_data

def pack_strategy(input_file, output_file=None):
    print(f"📦 Packing Strategy: {input_file}")
    
    if not os.path.exists(input_file):
        print("❌ Error: Input file not found.")
        return

    try:
        with open(input_file, 'r') as f:
            data = json.load(f)
            
        encrypted_bytes = encrypt_strategy_data(data)
        
        if not encrypted_bytes:
            print("❌ Error: Encryption failed.")
            return

        if not output_file:
            output_file = input_file.replace('.json', '.spine')
            
        with open(output_file, 'wb') as f:
            f.write(encrypted_bytes)
            
        print(f"✅ Success! Encrypted strategy saved to: {output_file}")
        
    except Exception as e:
        print(f"❌ Error during packing: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pack a JSON strategy into a .spine file")
    parser.add_argument("input", help="Path to input .json file")
    parser.add_argument("-o", "--output", help="Path to output .spine file")
    
    args = parser.parse_args()
    pack_strategy(args.input, args.output)
