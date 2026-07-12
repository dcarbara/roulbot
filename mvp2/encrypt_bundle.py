"""
SpinEdge Bundle Encryption Tool (Admin Only)
=============================================
Encrypts a plain .json bundle into an encrypted .spine file for distribution.

Usage:
    python encrypt_bundle.py <input.json> [output.spine]

If no output path is given, creates <input_name>.spine in the same directory.

Examples:
    python encrypt_bundle.py config/bundles/aggressive.json
    python encrypt_bundle.py my_strategy.json dist/aggressive_pro.spine
"""

import sys
import os
import json

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from core.encryption import encrypt_strategy_data, decrypt_strategy_data


def encrypt_bundle(input_path, output_path=None):
    """Encrypt a JSON bundle file into an encrypted .spine file."""
    
    if not os.path.exists(input_path):
        print(f"❌ File not found: {input_path}")
        return False
    
    # Read and validate JSON
    try:
        with open(input_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}")
        return False
    
    # Show what we're encrypting
    name = data.get("name", os.path.splitext(os.path.basename(input_path))[0])
    print(f"📦 Bundle: {name}")
    print(f"   Strategy: {data.get('strategy_config', {}).get('strategy_name', 'N/A')}")
    print(f"   Tier Required: {data.get('tier_required', 'ANY')}")
    
    # Encrypt
    encrypted = encrypt_strategy_data(data)
    if encrypted is None:
        print("❌ Encryption failed!")
        return False
    
    # Determine output path
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + ".spine"
    
    # Write encrypted bytes
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(encrypted)
    
    # Verify by decrypting
    with open(output_path, "rb") as f:
        verify = decrypt_strategy_data(f.read())
    
    if verify is None:
        print("❌ Verification failed! Encrypted file is corrupt.")
        os.remove(output_path)
        return False
    
    file_size = os.path.getsize(output_path)
    print(f"✅ Encrypted: {output_path} ({file_size} bytes)")
    print(f"   Verification: PASSED ✓")
    return True


def batch_encrypt(input_dir, output_dir=None):
    """Encrypt all .json bundles in a directory."""
    if output_dir is None:
        output_dir = input_dir
    
    os.makedirs(output_dir, exist_ok=True)
    
    json_files = [f for f in os.listdir(input_dir) if f.endswith(".json")]
    if not json_files:
        print(f"No .json files found in {input_dir}")
        return
    
    print(f"Encrypting {len(json_files)} bundles...\n")
    
    success = 0
    for filename in json_files:
        input_path = os.path.join(input_dir, filename)
        output_name = os.path.splitext(filename)[0] + ".spine"
        output_path = os.path.join(output_dir, output_name)
        
        if encrypt_bundle(input_path, output_path):
            success += 1
        print()
    
    print(f"Done: {success}/{len(json_files)} bundles encrypted.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("Quick actions:")
        print("  --all     Encrypt all bundles in config/bundles/")
        sys.exit(1)
    
    if sys.argv[1] == "--all":
        bundles_dir = os.path.join(os.path.dirname(__file__), "config", "bundles")
        batch_encrypt(bundles_dir)
    else:
        input_file = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
        encrypt_bundle(input_file, output_file)
