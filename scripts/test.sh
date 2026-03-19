#!/bin/bash
# =============================================================================
# Stourio - Production-Hardened Test Script
# =============================================================================

BASE="http://localhost:8000/api"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

# Load API Key from .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Use the key defined in .env or fall back to a placeholder for testing
API_KEY=${STOURIO_API_KEY:-"change_me_in_env"}

# Helper function to inject the security header into curl
function s_curl() {
    curl -s -H "X-STOURIO-KEY: $API_KEY" "$@"
}

echo ""
echo -e "${BOLD}========================================${NC}"
echo -e "${BOLD}  STOURIO HARDENED PIPELINE TEST${NC}"
echo -e "${BOLD}========================================${NC}"
echo -e "Using API Key: ${YELLOW}${API_KEY:0:4}****${NC}"
echo ""

# --- 1. System status ---
echo -e "${BOLD}[1] System Status (Authenticated)${NC}"
s_curl $BASE/status | python3 -m json.tool
echo ""

# --- 2. Chat - simple question ---
echo -e "${BOLD}[2] Chat: Simple direct response${NC}"
s_curl -X POST $BASE/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What agents do you have available?"}' | python3 -m json.tool
echo ""

# --- 3. Chat - agent routing (Fenced & Logged) ---
echo -e "${BOLD}[3] Chat: Agent routing (Requires Fencing Token)${NC}"
s_curl -X POST $BASE/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Investigate why the EU-West API is slow."}' | python3 -m json.tool
echo ""

# --- 4. Webhook - system signal (Reliable Queue) ---
echo -e "${BOLD}[4] Webhook: Reliable Signal Ingestion${NC}"
s_curl -X POST $BASE/webhook \
  -H "Content-Type: application/json" \
  -d '{"source": "datadog", "event_type": "alert", "title": "CPU > 95%", "severity": "high"}' | python3 -m json.tool
echo ""

# --- 5. Security Test - Unauthorized Access ---
echo -e "${BOLD}[5] Security Test: Unauthorized Access (Should Fail)${NC}"
curl -s -o /dev/null -w "%{http_code}" -X GET $BASE/status | grep -q "403" && echo -e "${GREEN}✓ Correctly rejected unauthorized request (403)${NC}" || echo -e "${RED}✗ Security failure: request was not rejected${NC}"
echo ""

# --- 6. Check Audit Trail ---
echo -e "${BOLD}[6] Audit Trail (Hardenend Logs)${NC}"
s_curl "$BASE/audit?limit=5" | python3 -m json.tool
echo ""

echo -e "${GREEN}${BOLD}Hardenend tests complete.${NC}"