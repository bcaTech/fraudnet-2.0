# auth-lib

JWT validation, RBAC decorators, tenant scoping, step-up auth.

## Roles

| Role | Surface | Notes |
| --- | --- | --- |
| `FRAUD_ANALYST`, `FRAUD_LEAD`, `FRAUD_MANAGER`, `NOC_VIEWER` | api-noc | Investigator workbench |
| `CUSTOMER` | api-customer | Customer self-service via OTP |
| `ENTERPRISE_USER`, `ENTERPRISE_ADMIN` | api-enterprise (Phase 4) | B2B portal, tenant-scoped |
| `GROUP_ADMIN` | api-enterprise `/group/*` (Phase 4) | Cross-tenant analytics for MTN Group |
| `SYSTEM_ADMIN`, `AUDITOR` | api-admin, api-enterprise `/admin/*` | Platform operations + audit |
| `SERVICE` | service-to-service | Workload identity → role mapping |

## Decorators

```python
from fraudnet.auth import Role, require_role, require_step_up

@router.post("/admin/tenants")
@require_step_up()
@require_role(Role.SYSTEM_ADMIN)
async def create_tenant(...): ...
```

`require_step_up()` checks the `step_up_at_ms` claim is fresh (default
5 min). Required for: model promotion, role changes, data export,
takedown filing, tenant provisioning.

## Phase 4 notes

- B2B tenants live in their own Keycloak realm. Tokens carry
  `tenant_id` (a slug — same regex as `enterprise_tenants.slug`).
  `extract_principal` pulls the slug out of the token; downstream code
  uses `Principal.tenant_id` as the unit of B2B isolation.
- `GROUP_ADMIN` is a privileged role for cross-tenant analytics.
  Holders see aggregates across **all** tenants and the group view of
  cross-opco rings; never grant casually.
- The audit log records every check that fires, including denials. A
  spike in `fraudnet_auth_role_denied_total` for a particular role is a
  sign of either a misconfigured client or a probing attempt.
