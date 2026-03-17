from __future__ import annotations
import logging
import httpx
from datetime import datetime
from src.config import settings
from src.models.schemas import AutomationWorkflow, AutomationExecution, ExecutionStatus, new_id
from src.persistence import audit

logger = logging.getLogger("stourio.automation")


# --- Built-in automation workflows ---

WORKFLOWS: dict[str, AutomationWorkflow] = {
    "auto_scale_horizontal": AutomationWorkflow(
        id="auto_scale_horizontal",
        name="Horizontal Auto-Scale",
        description="Scale up instances when CPU exceeds threshold",
        steps=[
            {"action": "get_current_instance_count", "target": "{{service}}"},
            {"action": "scale_to", "target": "{{service}}", "count": "+2"},
            {"action": "verify_health", "target": "{{service}}", "timeout": 60},
        ],
    ),
    "restart_service": AutomationWorkflow(
        id="restart_service",
        name="Rolling Restart",
        description="Perform a rolling restart of a service",
        steps=[
            {"action": "drain_instance", "target": "{{service}}", "instance": "oldest"},
            {"action": "restart_instance", "target": "{{service}}", "instance": "oldest"},
            {"action": "verify_health", "target": "{{service}}", "timeout": 30},
            {"action": "resume_traffic", "target": "{{service}}"},
        ],
    ),
    "flush_cdn_cache": AutomationWorkflow(
        id="flush_cdn_cache",
        name="CDN Cache Flush",
        description="Purge CDN cache for a region or globally",
        steps=[
            {"action": "purge_cdn", "scope": "{{region}}", "confirm": False},
            {"action": "verify_origin_response", "timeout": 15},
        ],
    ),
}


def get_workflow(workflow_id: str) -> AutomationWorkflow | None:
    return WORKFLOWS.get(workflow_id)


def list_workflows() -> list[AutomationWorkflow]:
    return list(WORKFLOWS.values())


async def execute_workflow(
    workflow_id: str,
    trigger_context: str,
    input_id: str | None = None,
) -> AutomationExecution:
    """
    Production execution: Sends workflow payload to the configured external automation engine.
    Enforces a 30-second timeout to prevent API locking.
    """
    workflow = get_workflow(workflow_id)
    if not workflow:
        return AutomationExecution(
            workflow_id=workflow_id,
            trigger_context=trigger_context,
            status=ExecutionStatus.FAILED,
            result=f"Unknown workflow: {workflow_id}",
        )

    execution = AutomationExecution(
        id=new_id(),
        workflow_id=workflow_id,
        trigger_context=trigger_context,
        status=ExecutionStatus.RUNNING,
    )

    await audit.log(
        "AUTOMATION_STARTED",
        f"Workflow '{workflow.name}' triggered via API: {trigger_context}",
        input_id=input_id,
        execution_id=execution.id,
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "execution_id": execution.id,
                "workflow_id": workflow_id,
                "context": trigger_context,
                "steps": workflow.steps
            }
            response = await client.post(
                settings.automation_webhook_url,
                json=payload
            )
            response.raise_for_status()
            
            try:
                result_data = response.json()
            except ValueError:
                result_data = {"message": response.text}

        execution.status = ExecutionStatus.COMPLETED
        execution.result = f"Engine acknowledged. Response: {result_data.get('message', 'Success')}"

        await audit.log(
            "AUTOMATION_COMPLETED",
            execution.result,
            input_id=input_id,
            execution_id=execution.id,
        )

    except httpx.HTTPError as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Engine unreachable or timeout: {str(e)}"
        logger.error(f"Automation engine network failure for {workflow_id}: {e}")
        await audit.log(
            "AUTOMATION_FAILED",
            execution.result,
            execution_id=execution.id,
        )
    except Exception as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Workflow orchestration error: {str(e)}"
        logger.exception(f"Workflow {workflow_id} failed: {e}")
        await audit.log(
            "AUTOMATION_FAILED",
            execution.result,
            execution_id=execution.id,
        )

    return execution