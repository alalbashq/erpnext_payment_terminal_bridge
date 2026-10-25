# Payment Terminal Bridge — staging installation

This package contains the Frappe app, separate from the Windows Electron agent. It records **agent attestations**, not independent bank verification. It never posts accounting payments or marks an invoice paid.

## Install in a staging bench

Use a supported Linux Frappe/ERPNext bench. Copy the contents of this directory into `apps/payment_terminal_bridge` (so `pyproject.toml` is at the app root), then from the bench directory run:

```sh
./env/bin/pip install -e apps/payment_terminal_bridge
grep -qxF payment_terminal_bridge sites/apps.txt || echo payment_terminal_bridge >> sites/apps.txt
bench --site staging.example install-app payment_terminal_bridge
bench --site staging.example migrate
```

Do not overwrite an existing app directory. Back up the site before installation or migration. Restart the deployment's web and worker processes after installation. Actual installation and database behavior must be verified on your target Frappe/ERPNext version; local pure-Python tests are not a bench integration test.

## Configure

1. Create a dedicated, enabled API user (not Administrator), assign only the `Payment Terminal Agent` role, and generate API key/secret credentials. The install hook provisions the role; it does not assign it or generate credentials.
2. As System Manager create a **Payment Terminal Agent** with an ASCII name of 3–80 characters, that user, the permitted company, and an administrator-approved registry. Start disabled and in test mode. Example registry:

   ```json
   [{"device_id":"d1","provider_id":"mock","terminal_id":"MOCK-001","enabled":true,"payment_mode":"Card"}]
   ```

3. Match these identities to the Windows agent's local device settings. Configure HTTPS site URL, credentials, and agent name in the agent bridge settings. Credentials belong in encrypted main-process storage, never renderer scripts or source control.
4. Enable the registry only after verifying routing. Heartbeats are diagnostics and do not change the approved registry. Agent users have no direct DocType read/write permissions; authenticated API methods enforce ownership.
5. Cashiers need invoice write permission and a Sales User, Sales Manager, or System Manager role. Only Sales Manager/System Manager can queue full refunds/reversals. No partial-refund accounting design is included.

## API and recovery

POST methods live under `payment_terminal_bridge.api.agent`: `create_request`, `cancel_request`, `heartbeat`, `claim_requests`, and `report_result`. `get_status` returns cashier status after checking invoice permissions. Send a stable unique idempotency key for each logical operation; reuse it on network retries. Realtime messages are wakeups only; authenticated REST is authoritative.

Requests and allowlisted result evidence are durable. Claims rotate by last claim time so unresolved requests do not permanently hide later work. The Windows journal prevents redelivery from recharging; preserve the journal, request links, and result outbox together. Do not clear them, change agent ownership, or rotate device identities while work is unresolved. Rejected reports remain in the outbox for investigation.

### Server and manual operations

The desktop connection form now needs only HTTPS URL, API Key and API Secret. Deploy the updated `resolve_connection` endpoint before using it: it authenticates the dedicated agent role and requires exactly one enabled agent for that API user. Ambiguous or missing mappings are rejected rather than selecting another terminal agent. The actual Frappe site namespace is returned by the server; operators no longer enter site or agent names. Existing encrypted connections keep their stored routing until replaced.

`create_request` accepts `payment_mode` and checks it against the administrator-approved device registry. If omitted it uses the registered mode. The amount is computed from the invoice on the server, never trusted from the caller. Claims include the invoice, amount, payment mode and device routing. Configure the exact same payment mode on the local device. The desktop server tab shows locally received attempts, not the complete server queue.

After upgrading, run `bench --site staging.example migrate` to add the request payment-mode field. Add `payment_mode` to every existing agent device registry and use a valid ERPNext Mode of Payment. Do not change routing while requests are outstanding; reconcile old requests before upgrading. The controller now requires this field when saving the registry.

Manual payment uses the same durable manager and physical-device locks as server payments, but does not create a server request or automatically post accounting entries. A physical-device manual payment can move real money and requires explicit operator confirmation plus existing live-provider/device activation. The separate connection-check button sends no sale. Only Mock devices use simulation scenarios.

`UNKNOWN` and `MANUAL_REVIEW` are not declines. Do not create a replacement payment: inquire using the original reference and reconcile against terminal/acquirer evidence. Cancellation is a request, not proof that a transaction was cancelled. A separate, permission-controlled and idempotent accounting workflow must reconcile verified outcomes before posting any payment.

## Validation and release gate

From this directory:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

Before deployment, test real bench install/migrate, role isolation, company/invoice permissions, parallel claims and cancellation, paid-invoice adjustment retries, and restart/result redelivery. Nami live use remains gated by `TERMINAL_LIVE_PROVIDERS=nami` and explicit device compatibility confirmation. Its numeric result-code field and signature assumptions still require official vendor confirmation and simulator/device UAT. Do not enable live mode based only on these tests.