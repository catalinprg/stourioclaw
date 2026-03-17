import secrets
import os

def setup():
    env_path = ".env"
    # Generate a cryptographically secure 32-character string
    new_secret = secrets.token_urlsafe(32)
    
    if not os.path.exists(env_path):
        # Create a new .env if it doesn't exist
        with open(env_path, "w") as f:
            f.write(f"MCP_SHARED_SECRET={new_secret}\n")
            f.write("MCP_RATE_LIMIT=60\n")
        print(f"Created new .env file.")
    else:
        # Update existing .env
        with open(env_path, "r") as f:
            lines = f.readlines()
        
        with open(env_path, "w") as f:
            found = False
            for line in lines:
                if line.startswith("MCP_SHARED_SECRET="):
                    f.write(f"MCP_SHARED_SECRET={new_secret}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"MCP_SHARED_SECRET={new_secret}\n")
        print(f"Updated existing .env file.")

    print(f"\nIMPORTANT: Copy this secret to your Stourio Orchestrator .env later:")
    print(f"MCP_SHARED_SECRET={new_secret}")

if __name__ == "__main__":
    setup()