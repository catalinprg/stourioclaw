import secrets
import os

def generate_stourio_key():
    # Generate a secure 32-byte (256-bit) hex key
    new_key = secrets.token_hex(32)
    env_path = ".env"
    
    if not os.path.exists(env_path):
        print(f"Error: {env_path} not found. Run ./scripts/setup.sh first.")
        return

    with open(env_path, "r") as f:
        lines = f.readlines()

    key_exists = False
    with open(env_path, "w") as f:
        for line in lines:
            if line.startswith("STOURIO_API_KEY="):
                f.write(f"STOURIO_API_KEY={new_key}\n")
                key_exists = True
            else:
                f.write(line)
        
        if not key_exists:
            f.write(f"\n# Security\nSTOURIO_API_KEY={new_key}\n")

    print(f"Successfully generated and saved key: {new_key[:4]}...{new_key[-4:]}")

if __name__ == "__main__":
    generate_stourio_key()