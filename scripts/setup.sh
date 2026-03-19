#!/bin/bash
# =============================================================================
# Stourio - Environment Bootstrap
# Generates secure passwords and API key on first run.
# =============================================================================

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BOLD}Initializing Stourio environment...${NC}\n"

if [ ! -f .env ]; then
    cp .env.example .env

    # Generate secure random passwords
    PG_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    # Replace placeholders in .env
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS sed
        sed -i '' "s|POSTGRES_PASSWORD=changeme|POSTGRES_PASSWORD=${PG_PASS}|g" .env
        sed -i '' "s|REDIS_PASSWORD=changeme|REDIS_PASSWORD=${REDIS_PASS}|g" .env
        sed -i '' "s|STOURIO_API_KEY=|STOURIO_API_KEY=${API_KEY}|" .env
        sed -i '' "s|stourio:changeme@postgres|stourio:${PG_PASS}@postgres|g" .env
        sed -i '' "s|redis://:changeme@redis|redis://:${REDIS_PASS}@redis|g" .env
    else
        # Linux sed
        sed -i "s|POSTGRES_PASSWORD=changeme|POSTGRES_PASSWORD=${PG_PASS}|g" .env
        sed -i "s|REDIS_PASSWORD=changeme|REDIS_PASSWORD=${REDIS_PASS}|g" .env
        sed -i "s|STOURIO_API_KEY=|STOURIO_API_KEY=${API_KEY}|" .env
        sed -i "s|stourio:changeme@postgres|stourio:${PG_PASS}@postgres|g" .env
        sed -i "s|redis://:changeme@redis|redis://:${REDIS_PASS}@redis|g" .env
    fi

    echo -e "${GREEN}✓ Created .env with secure passwords.${NC}"
    echo -e "${GREEN}✓ API Key: ${API_KEY:0:8}...${NC}"
    echo ""
    echo -e "${YELLOW}Save your API key. You will need it for every request:${NC}"
    echo -e "  ${BOLD}X-STOURIO-KEY: ${API_KEY}${NC}"
else
    echo -e "${YELLOW}⚠ .env already exists. Skipping to prevent overwriting keys.${NC}"
fi

echo ""
echo -e "${BOLD}Next Steps:${NC}"
echo "1. Add your LLM API key(s) to the .env file."
echo "   -> macOS: Do NOT double-click .env in Finder. Use a code editor."
echo "2. Run: docker-compose up --build"
echo "3. API docs: http://localhost:8000/docs"
echo ""
