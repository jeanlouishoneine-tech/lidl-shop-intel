"""
One-time authentication script. Run once to obtain and store a refresh token:
    uv run python src/auth.py

After this, the dashboard and sync scripts use the saved token without a browser.
"""
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key
from lidlplus_api import LidlPlusApi

ENV_PATH = Path(__file__).parent.parent / ".env"


def main():
    load_dotenv(ENV_PATH)
    language = os.getenv("LIDL_LANGUAGE", "fr")
    country = os.getenv("LIDL_COUNTRY", "CH")

    print(f"Lidl Plus authentication ({language}/{country})")
    print("A browser window will open — log in and it will close automatically.\n")

    email = input("Lidl Plus email: ").strip()
    password = getpass.getpass("Lidl Plus password: ")

    api = LidlPlusApi(language, country)
    try:
        api.login(email, password)
    except Exception as exc:
        print(f"\nAuthentication failed: {exc}", file=sys.stderr)
        sys.exit(1)

    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "LIDL_REFRESH_TOKEN", api.refresh_token)
    print("\nRefresh token saved to .env — you won't need to run this again unless the token expires.")


if __name__ == "__main__":
    main()
