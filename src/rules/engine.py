from __future__ import annotations
import re
import json
import logging
from sqlalchemy import select
from src.models.schemas import Rule, RuleAction, RiskLevel, new_id
from src.persistence.database import async_session, RuleRecord

logger = logging.getLogger("stourio.rules")

# In-memory cache, refreshed on changes
_rules_cache: list[Rule] | None = None


async def load_rules() -> list[Rule]:
    """Load active rules from the database."""
    global _rules_cache
    async with async_session() as session:
        result = await session.execute(
            select(RuleRecord).where(RuleRecord.active == True)
        )
        rows = result.scalars().all()
        _rules_cache = [
            Rule(
                id=r.id,
                name=r.name,
                pattern=r.pattern,
                pattern_type=r.pattern_type,
                action=RuleAction(r.action),
                risk_level=RiskLevel(r.risk_level) if r.risk_level else RiskLevel.MEDIUM,
                automation_id=r.automation_id,
                active=r.active,
            )
            for r in rows
        ]
    logger.info(f"Loaded {len(_rules_cache)} active rules")
    return _rules_cache


async def get_rules() -> list[Rule]:
    if _rules_cache is None:
        return await load_rules()
    return _rules_cache


async def add_rule(rule: Rule) -> Rule:
    """Add a new rule and refresh cache."""
    async with async_session() as session:
        record = RuleRecord(
            id=rule.id,
            name=rule.name,
            pattern=rule.pattern,
            pattern_type=rule.pattern_type,
            action=rule.action.value,
            risk_level=rule.risk_level.value,
            automation_id=rule.automation_id,
            active=rule.active,
        )
        session.add(record)
        await session.commit()
    await load_rules()
    logger.info(f"Rule added: {rule.name} ({rule.id})")
    return rule


async def remove_rule(rule_id: str) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(RuleRecord).where(RuleRecord.id == rule_id)
        )
        record = result.scalar_one_or_none()
        if record:
            await session.delete(record)
            await session.commit()
            await load_rules()
            return True
    return False


def _sanitize_and_normalize(text: str) -> str:
    """
    Strips obfuscation (comments, excessive whitespace) to prevent regex bypasses 
    on destructive commands before they reach the LLM.
    """
    # Remove C-style / SQL block comments
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    # Remove SQL line comments
    text = re.sub(r'--.*$', ' ', text, flags=re.MULTILINE)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def evaluate(content: str, rules: list[Rule]) -> Rule | None:
    """
    Evaluate content against rules. First match wins (priority by order).
    Implements structural sanitization to prevent injection bypasses.
    """
    normalized_content = _sanitize_and_normalize(content)

    # Extract JSON payload if the input is a structured WebhookSignal
    is_json = False
    parsed_payload = {}
    if "Payload: " in content:
        try:
            payload_str = content.split("Payload: ")[1]
            parsed_payload = json.loads(payload_str.replace("'", '"'))
            is_json = True
        except Exception as e:
            logger.debug(f"Failed to parse webhook payload for structural evaluation: {e}")

    for rule in rules:
        if not rule.active:
            continue

        matched = False

        # Structural matching for system events (e.g., pattern="severity:critical")
        if rule.pattern_type == "payload_match" and is_json:
            try:
                k, v = rule.pattern.split(":", 1)
                if str(parsed_payload.get(k, "")).lower() == v.lower():
                    matched = True
            except ValueError:
                logger.warning(f"Invalid payload_match pattern format in rule {rule.id}: {rule.pattern}")

        # Sanitized Regex matching
        elif rule.pattern_type == "regex":
            try:
                # Evaluate against both raw and normalized to catch all vectors
                if re.search(rule.pattern, normalized_content, re.IGNORECASE) or \
                   re.search(rule.pattern, content, re.IGNORECASE):
                    matched = True
            except re.error:
                logger.warning(f"Invalid regex in rule {rule.id}: {rule.pattern}")
                
        elif rule.pattern_type == "keyword":
            if rule.pattern.lower() in normalized_content.lower():
                matched = True
                
        elif rule.pattern_type == "event_type":
            # Event types are consistently formatted in the signal header
            if rule.pattern.lower() in content.lower():
                matched = True

        if matched:
            logger.info(f"Rule matched: {rule.name} ({rule.action.value})")
            return rule

    return None


async def seed_default_rules():
    """Seed initial safety rules if none exist."""
    rules = await get_rules()
    if rules:
        return

    defaults = [
        Rule(
            id=new_id(),
            name="prevent_db_drop",
            pattern=r"DROP\s+(DATABASE|TABLE)",
            pattern_type="regex",
            action=RuleAction.REQUIRE_APPROVAL,
            risk_level=RiskLevel.CRITICAL,
        ),
        Rule(
            id=new_id(),
            name="block_ssh_root",
            pattern=r"ssh\s+root@",
            pattern_type="regex",
            action=RuleAction.HARD_REJECT,
            risk_level=RiskLevel.CRITICAL,
        ),
        Rule(
            id=new_id(),
            name="block_rm_rf",
            pattern=r"rm\s+-rf\s+/",
            pattern_type="regex",
            action=RuleAction.HARD_REJECT,
            risk_level=RiskLevel.CRITICAL,
        ),
    ]

    for rule in defaults:
        await add_rule(rule)
    logger.info(f"Seeded {len(defaults)} default rules")