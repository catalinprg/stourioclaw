# Stourio Command Panel — Implementation & Production Runbook

**Overview**
The Stourio Command Panel is a zero-build Single Page Application (SPA) served directly by the Stourio Core orchestration server. It provides a visual interface for managing the orchestrator's core APIs without relying on raw `curl` commands or the Swagger UI during operations.

## Architecture & Implementation

### 1. Delivery Mechanism
To eliminate Node.js build pipelines and CORS complexities, the SPA is implemented as a single `index.html` file using React and Tailwind CSS via CDN. 

It is mounted directly into the FastAPI application in `src/main.py`:
```python
from fastapi.staticfiles import StaticFiles
import os

os.makedirs("static", exist_ok=True)
app.mount("/admin", StaticFiles(directory="static", html=True), name="admin")
```

The panel is accessible at `/admin`.

### 2. Security

The interface is entirely client-side and requires the `STOURIO_API_KEY` to function.

* The key is stored temporarily in browser `sessionStorage` and drops when the tab closes.
* It is injected as the `X-STOURIO-KEY` header in all `fetch` requests.
* The UI polls `GET /api/status` to verify authentication; if a 401/403 is returned, the session is cleared.

### 3. API Mapping

The UI maps directly to the production endpoints:

| UI Component | Endpoint | Action |
| --- | --- | --- |
| **Kill Switch** | `POST /api/kill` & `/api/resume` | Sets/clears the Redis flag to halt orchestrator execution. |
| **Operator Console** | `POST /api/chat` | Sends human instructions to the LLM orchestrator. |
| **Pending Approvals** | `GET /api/approvals` | Polls for high-risk actions awaiting authorization. |
| **Approval Resolution** | `POST /api/approvals/{id}` | Authorizes or rejects an action within its TTL window. |
| **Rules Engine** | `GET /api/rules` | Lists deterministic routing rules. |
| **Rule Creation/Deletion** | `POST /api/rules` & `DELETE` | Mutates the PostgreSQL `rules` table and refreshes in-memory cache. |
| **Audit Trail** | `GET /api/audit` | Reads the immutable `audit_log` table. |

---

## Production Migration Runbook

The current configuration assumes local development (`localhost`). When deploying Stourio to a VPN-protected production server, you must execute the following three changes.

### 1. Expose Infrastructure Ports

By default, the `docker-compose.yml` binds n8n and Jaeger exclusively to `127.0.0.1` to prevent public access. To access them over the VPN, you must bind them to the host's network interface.

**Modify `docker-compose.yml`:**

```yaml
  jaeger:
    ports:
      - "16686:16686"  # Removed 127.0.0.1 restriction

  n8n:
    ports:
      - "5678:5678"    # Removed 127.0.0.1 restriction
```

*Note: PostgreSQL and Redis must remain isolated. Do not expose their ports.*

### 2. Update Environment Variables

The `.env` file on the production server must reflect the new network topology.

**Modify `.env`:**

* `CORS_ORIGINS`: Change from `http://localhost:8000` to your exact VPN IP or internal DNS (e.g., `http://10.0.1.20:8000`). If omitted, the browser will block API requests.
* `MCP_SERVER_URL`: Set this to the internal IP of **Server 2**, where the MCP Gateway runs (e.g., `http://10.0.1.21:8080`). The orchestrator cannot execute agent tools without this.

### 3. Update SPA Navigation Links

The external links in the SPA header currently hardcode `localhost`. They must be updated to dynamically resolve to the server's IP address.

**Modify `static/index.html` (Header component):**
Replace the hardcoded `href` values for n8n and Jaeger with template literals using `window.location.hostname`:

```jsx
<a href="/docs" target="_blank" rel="noopener noreferrer">
    API Docs ↗
</a>
<a href={`http://${window.location.hostname}:5678`} target="_blank" rel="noopener noreferrer">
    Automation ↗
</a>
<a href={`http://${window.location.hostname}:16686`} target="_blank" rel="noopener noreferrer">
    Tracing ↗
</a>
```

After making these changes, run `docker-compose down` followed by `docker-compose up -d --build` to apply the new network bindings.
