"""Standalone license diagnostic.

Runs the same auto-login + validation path the bot does at startup, but
prints the exact reason validate_license() returns False — and dumps the
session-token comparison so you can spot mismatches.

Usage:
    cd engine/mvp2
    python diagnose_license.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.security.license_manager import get_license_manager


def main():
    print("=" * 60)
    print("SpinEdge License Diagnostic")
    print("=" * 60)

    lm = get_license_manager()

    # 1. Supabase wired up?
    print(f"\n[1] Supabase client: {'OK' if lm.supabase else 'NOT INITIALIZED'}")
    if not lm.supabase:
        print("    -> Supabase URL/KEY not set. License management disabled entirely.")
        return

    # 2. Try to load stored auth token + auto-login
    print(f"\n[2] Attempting auto-login from keyring (Windows Credential Manager)...")
    auto_ok = lm.try_auto_login()
    print(f"    auto_login returned: {auto_ok}")
    print(f"    is_authenticated:    {lm.is_authenticated}")
    print(f"    is_licensed:         {lm.is_licensed}")
    print(f"    current_user:        "
          f"{getattr(lm.current_user, 'email', None) if lm.current_user else 'None'}")
    print(f"    user_id:             "
          f"{getattr(lm.current_user, 'id', None) if lm.current_user else 'None'}")

    if not lm.is_authenticated:
        print("\n    -> NOT logged in. Either no token stored, or token expired.")
        print("    -> Fix: open the GUI and log in via the auth screen.")
        return

    # 3. Show local session token (truncated)
    local_st = lm.session_token
    print(f"\n[3] Local session_token (from keyring):")
    print(f"    {local_st[:16] + '...' if local_st else '(none)'}")

    # 4. Force validation and capture the message
    print(f"\n[4] Calling validate_license(force_refresh=True)...")
    valid, msg = lm.validate_license(force_refresh=True)
    print(f"    valid: {valid}")
    print(f"    msg:   {msg}")

    # 5. Inspect the licenses row in DB and compare session tokens
    if lm.current_user:
        try:
            print(f"\n[5] Querying licenses table for user_id={lm.current_user.id}...")
            result = lm.supabase.table('licenses').select('*').eq(
                'user_id', lm.current_user.id).execute()
            if not result.data:
                print("    NO ROW FOUND in licenses table.")
                print("    -> Fix: contact support; you may need to be re-provisioned.")
            else:
                lic = result.data[0]
                print(f"    Row keys: {list(lic.keys())}")
                db_token = lic.get('active_session_token')
                print(f"    DB active_session_token: {db_token[:16] + '...' if db_token else '(none)'}")
                if local_st and db_token and local_st != db_token:
                    print()
                    print("    *** SESSION TOKEN MISMATCH ***")
                    print("    Your bot's local session_token does NOT match the DB.")
                    print("    This typically happens when you logged in elsewhere")
                    print("    (web app, another desktop instance, etc.) — that login")
                    print("    replaced the DB token, kicking this session.")
                    print()
                    print("    Fix: in the GUI, click 'Log out & restart' (or run the")
                    print("    refresh-license button) and sign in fresh. That will")
                    print("    register a new session token in both your keyring AND")
                    print("    the DB.")
                elif not local_st:
                    print()
                    print("    *** NO LOCAL SESSION TOKEN ***")
                    print("    Auto-login restored auth but didn't restore the session")
                    print("    token. Sign in fresh in the GUI to register one.")
                elif not db_token:
                    print()
                    print("    *** DB has no active_session_token ***")
                    print("    Sign in fresh in the GUI to register one.")
                else:
                    print("    Session tokens MATCH. License row exists.")
                # Show other useful fields
                for k in ('subscription_tier', 'valid_until', 'created_at',
                         'updated_at', 'session_started_at'):
                    if k in lic:
                        print(f"    {k}: {lic.get(k)}")
        except Exception as e:
            print(f"    Could not query licenses: {e}")

    # 6. Summary verdict
    print("\n" + "=" * 60)
    if lm.is_licensed:
        print("VERDICT: License is VALID. The bot should run normally.")
    else:
        print("VERDICT: License is INVALID. See [4] above for the exact reason.")
        print()
        print("Common fixes:")
        print("  1. Sign out + sign in via the GUI (most common: session-token")
        print("     mismatch from logging in elsewhere)")
        print("  2. Set DEBUG_BYPASS=True on the LicenseManager for testing only")
        print("     (in core/security/license_manager.py, in the LicenseManager")
        print("     __init__: self.DEBUG_BYPASS = True). DO NOT ship this.")
        print("  3. Confirm with support that your license row exists and is active.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nDiagnostic crashed: {e}")
        import traceback
        traceback.print_exc()
